import json
from typing import Dict, List

from backend.core.clients import openai_client
from backend.core.config import settings
from backend.graph.schema import ALLOWED_ENTITY_TYPES


class QueryGraphLinker:
    def __init__(self):
        self.client = openai_client

    def _safe_json_loads(self, text: str) -> Dict:
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
            return {"entities": []}

    def extract_query_entities(self, query: str) -> List[Dict]:
        """
        Extract schema-constrained entities from the user query.
        """
        system_prompt = f"""
You are a financial query entity extraction system.

Return ONLY valid JSON in this exact format:
{{
  "entities": [
    {{
      "name": "string",
      "type": "one of: {sorted(ALLOWED_ENTITY_TYPES)}"
    }}
  ]
}}

Rules:
- Use only the allowed entity types.
- Extract only financially meaningful entities from the query.
- Prefer canonical names.
- If no relevant entities are present, return an empty list.
"""

        user_prompt = f"Query: {query}"

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

        valid_entities = []
        seen = set()

        for entity in entities:
            name = str(entity.get("name", "")).strip()
            entity_type = str(entity.get("type", "")).strip()

            if not name or entity_type not in ALLOWED_ENTITY_TYPES:
                continue

            key = (name.lower(), entity_type)
            if key in seen:
                continue

            valid_entities.append({"name": name, "type": entity_type})
            seen.add(key)

        return valid_entities
