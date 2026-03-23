import json
from typing import Dict, List

from openai import OpenAI

from backend.core.config import settings
from backend.graph.schema import ALLOWED_ENTITY_TYPES, ALLOWED_RELATIONSHIP_TYPES


class FinancialGraphExtractor:
    def __init__(self):
        self.client = OpenAI(api_key=settings.openai_api_key)

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
                return json.loads(text[start:end + 1])
            raise

    def extract_from_chunk(self, chunk: Dict) -> Dict:
        """
        Extract schema-constrained entities and relationships from a text chunk.
        """
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

        response = self.client.chat.completions.create(
            model=settings.chat_model,
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt.strip()},
                {"role": "user", "content": user_prompt.strip()},
            ],
        )

        parsed = self._safe_json_loads(response.choices[0].message.content)

        entities = parsed.get("entities", [])
        relationships = parsed.get("relationships", [])

        valid_entities = []
        seen_entity_keys = set()

        for entity in entities:
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
        }
