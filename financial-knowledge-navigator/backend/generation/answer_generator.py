from typing import List, Dict

from backend.core.clients import openai_client
from backend.core.config import settings
from backend.eval.context_builder import build_structured_facts_context_text


class AnswerGenerator:
    def __init__(self):
        self.client = openai_client

    def build_context(self, retrieved_chunks: List[Dict]) -> str:
        blocks = []
        for i, chunk in enumerate(retrieved_chunks, start=1):
            blocks.append(
                f"[Source {i}] {chunk['source']} | {chunk['chunk_id']}\n{chunk['text']}"
            )
        return "\n\n".join(blocks)

    def generate_answer(
        self,
        question: str,
        retrieved_chunks: List[Dict],
        structured_facts: List[Dict] | None = None,
    ) -> str:
        context = self.build_context(retrieved_chunks)
        facts_context = build_structured_facts_context_text(structured_facts)

        system_prompt = (
            "You are a financial document assistant. "
            "Answer ONLY using the supplied retrieved context and structured facts. "
            "If the context is insufficient, say so clearly. "
            "Be precise, grounded, and concise. "
            "Treat structured facts as extracted evidence, not free-standing truth. "
            "At the end, include a short 'Sources Used' section listing the Source or Fact labels you relied on."
        )

        user_prompt = f"""Question:
{question}

Retrieved Context:
{context}

Structured Facts:
{facts_context}

Instructions:
- Use only the provided context.
- Use structured facts when they directly help answer the question.
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
