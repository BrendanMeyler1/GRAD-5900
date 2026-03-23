import json
from typing import Dict, Any
from openai import OpenAI
from backend.core.config import settings

class SelfCorrector:
    def __init__(self):
        self.client = OpenAI(api_key=settings.openai_api_key)

    def grade_relevance(self, query: str, context: str) -> bool:
        """Evaluate if the context is sufficiently relevant to answer the query."""
        system_prompt = """You are a relevance grader. You determine if the provided context contains sufficient information to answer the user's query.
Return ONLY valid JSON with a single boolean field 'is_relevant'.
If the context has enough information to partially or fully answer the query, return {"is_relevant": true}.
If the context is entirely unrelated, unhelpful, or lacks key information, return {"is_relevant": false}.
"""
        user_prompt = f"Query: {query}\n\nContext:\n{context}\n\nIs the context relevant?"

        try:
            response = self.client.chat.completions.create(
                model=settings.chat_model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
            )
            data = json.loads(response.choices[0].message.content)
            return data.get("is_relevant", True)
        except Exception:
            return True  # Failsafe to not break pipeline

    def rewrite_query(self, original_query: str) -> str:
        """Rewrite the query to be more effective for search if the first attempt failed."""
        system_prompt = """You are a query rewriting assistant. The user's original query failed to return relevant documents.
Rewrite the query to be better suited for semantic and keyword search. 
Often, this means expanding acronyms, adding synonyms, or focusing on the core entities.
Return ONLY valid JSON with a single string field 'rewritten_query'.
Example: {"rewritten_query": "apple revenue 2023 financial results Q4"}"""

        user_prompt = f"Original Query: {original_query}\n\nProvide the 'rewritten_query'."

        try:
            response = self.client.chat.completions.create(
                model=settings.chat_model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
            )
            data = json.loads(response.choices[0].message.content)
            return data.get("rewritten_query", original_query)
        except Exception:
            return original_query
