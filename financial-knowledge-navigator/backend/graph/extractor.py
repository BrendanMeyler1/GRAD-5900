import asyncio
import json
import re
import sys
from typing import Dict, List

from backend.core.clients import openai_client, async_openai_client
from backend.core.config import settings
from backend.graph.schema import ALLOWED_ENTITY_TYPES, ALLOWED_RELATIONSHIP_TYPES


# Issue #4 fix: removed [A-Z]{2,5} which matched everything, and removed
# re.IGNORECASE so ticker-like patterns only match actual uppercase sequences.
FINANCIAL_REGEX = re.compile(
    r"(?:\$|€|£|¥|\d+%|\b(?:quarter|revenue|profit|loss|margin|earnings|ebitda|"
    r"nasdaq|nyse|inc\.|corp\.|ltd\.|company|market|share|dividend|fiscal|"
    r"assets|liabilities|equity|debt|interest|cash\s*flow|sec|filing|10-k|10-q)\b)",
    re.IGNORECASE,
)

# Separate pattern for uppercase ticker-like symbols (case-sensitive, 2-5 uppercase letters)
TICKER_REGEX = re.compile(r"\b[A-Z]{2,5}\b")


class FinancialGraphExtractor:
    def __init__(self):
        self.client = openai_client
        self.async_client = async_openai_client
        # Create the semaphore lazily per running event loop so a Streamlit
        # rerun does not reuse a limiter bound to an earlier loop.
        self._rate_limiter = None
        self._rate_limiter_loop = None

    def _get_rate_limiter(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._rate_limiter is None or self._rate_limiter_loop is not loop:
            self._rate_limiter = asyncio.Semaphore(5)
            self._rate_limiter_loop = loop
        return self._rate_limiter

    def _safe_json_loads(self, text: str) -> Dict:
        """
        Tries to parse model output as JSON.
        Falls back to extracting the outermost JSON object if needed.
        """
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass
            return {}

    def _has_financial_entities(self, text: str) -> bool:
        """
        Fast local heuristic to skip LLM extraction if there are zero
        keywords that could possibly be financial entities.
        """
        if FINANCIAL_REGEX.search(text):
            return True
        if TICKER_REGEX.search(text):
            return True
        return False

    def should_extract_chunk(self, chunk: Dict) -> bool:
        return self._has_financial_entities(chunk.get("text", ""))

    def _build_prompts(self, chunk: Dict) -> tuple[str, str]:
        system_prompt = f"""
You are a financial knowledge graph extraction system.

Return ONLY valid JSON in this exact format:
{{
  "entities": [
    {{
      "name": "string",
      "type": "one of: {sorted(ALLOWED_ENTITY_TYPES)}"
    }}
  ],
  "relationships": [
    {{
      "source": "entity name",
      "target": "entity name",
      "type": "one of: {sorted(ALLOWED_RELATIONSHIP_TYPES)}"
    }}
  ]
}}

Rules:
- Use only the allowed entity and relationship types.
- Extract only financially meaningful entities.
- Prefer canonical names.
- Do not invent facts not present in the text.
- If nothing useful is found, return empty lists.
- Every relationship source and target must refer to entities found in the chunk.
"""
        user_prompt = f"""
Source: {chunk.get("source")}
Chunk ID: {chunk.get("chunk_id")}

Text:
{chunk.get("text")}
"""
        return system_prompt.strip(), user_prompt.strip()

    def _parse_llm_response(self, text_response: str, chunk: Dict) -> Dict:
        parsed = self._safe_json_loads(text_response)

        entities = parsed.get("entities", [])
        relationships = parsed.get("relationships", [])

        valid_entities = []
        seen_entity_keys = set()

        for entity in entities:
            if not isinstance(entity, dict):
                continue
            name = str(entity.get("name", "")).strip()
            entity_type = str(entity.get("type", "")).strip()

            if not name or entity_type not in ALLOWED_ENTITY_TYPES:
                continue

            key = (name.lower(), entity_type)
            if key in seen_entity_keys:
                continue

            valid_entities.append({"name": name, "type": entity_type})
            seen_entity_keys.add(key)

        valid_entity_names = {entity["name"] for entity in valid_entities}

        valid_relationships = []
        seen_rel_keys = set()

        for rel in relationships:
            if not isinstance(rel, dict):
                continue
            source = str(rel.get("source", "")).strip()
            target = str(rel.get("target", "")).strip()
            rel_type = str(rel.get("type", "")).strip()

            if (
                not source
                or not target
                or rel_type not in ALLOWED_RELATIONSHIP_TYPES
                or source not in valid_entity_names
                or target not in valid_entity_names
                or source == target
            ):
                continue

            key = (source.lower(), target.lower(), rel_type)
            if key in seen_rel_keys:
                continue

            valid_relationships.append(
                {
                    "source": source,
                    "target": target,
                    "type": rel_type,
                }
            )
            seen_rel_keys.add(key)

        return {
            "chunk_id": chunk.get("chunk_id"),
            "source": chunk.get("source"),
            "entities": valid_entities,
            "relationships": valid_relationships,
            "skipped": False,
        }

    def _empty_response(self, chunk: Dict, skipped: bool = False) -> Dict:
        return {
            "chunk_id": chunk.get("chunk_id"),
            "source": chunk.get("source"),
            "entities": [],
            "relationships": [],
            "skipped": skipped,
        }

    def extract_from_chunk(self, chunk: Dict) -> Dict:
        """
        Synchronous fallback extraction.
        """
        text = chunk.get("text", "")
        if not self._has_financial_entities(text):
            return self._empty_response(chunk, skipped=True)

        sys_prompt, user_prompt = self._build_prompts(chunk)

        try:
            response = self.client.chat.completions.create(
                model=settings.chat_model,
                temperature=0,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return self._parse_llm_response(response.choices[0].message.content, chunk)
        except Exception as e:
            print(f"[WARN] Extraction failed for {chunk.get('chunk_id')}: {e}", file=sys.stderr)
            return self._empty_response(chunk)

    async def extract_from_chunk_async(self, chunk: Dict) -> Dict:
        """
        Asynchronous extraction with concurrency limits.
        """
        text = chunk.get("text", "")
        if not self._has_financial_entities(text):
            return self._empty_response(chunk, skipped=True)

        sys_prompt, user_prompt = self._build_prompts(chunk)

        async with self._get_rate_limiter():
            try:
                response = await self.async_client.chat.completions.create(
                    model=settings.chat_model,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                return self._parse_llm_response(response.choices[0].message.content, chunk)
            except Exception as e:
                print(f"[WARN] Async extraction failed for {chunk.get('chunk_id')}: {e}", file=sys.stderr)
                return self._empty_response(chunk)
