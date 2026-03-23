from typing import List, Dict
from openai import OpenAI

from backend.core.config import settings


class AnswerGenerator:
    def __init__(self):
        self.client = OpenAI(api_key=settings.openai_api_key)

    def build_context(self, retrieved_chunks: List[Dict]) -> str:
        blocks = []
        for i, chunk in enumerate(retrieved_chunks, start=1):
            blocks.append(
                f"[Source {i}] {chunk['source']} | {chunk['chunk_id']}\n{chunk['text']}"
            )
        return "\n\n".join(blocks)

    def generate_answer(self, question: str, retrieved_chunks: List[Dict]) -> str:
        context = self.build_context(retrieved_chunks)

        system_prompt = (
            "You are a financial document assistant. "
            "Answer ONLY using the supplied context. "
            "If the context is insufficient, say so clearly. "
            "Be precise, grounded, and concise. "
            "At the end, include a short 'Sources Used' section listing the source labels you relied on."
        )

        user_prompt = f"""Question:
{question}

Context:
{context}

Instructions:
- Use only the provided context.
- Do not invent facts.
- If there is uncertainty or missing information, say that directly.
- Explain in a way that is clear to a finance-oriented user.
"""

        response = self.client.chat.completions.create(
            model=settings.chat_model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        return response.choices[0].message.content
