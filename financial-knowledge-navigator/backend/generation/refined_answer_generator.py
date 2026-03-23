from typing import List, Dict

from backend.core.clients import openai_client
from backend.core.config import settings
from backend.eval.context_builder import build_structured_facts_context_text


class RefinedAnswerGenerator:
    def __init__(self):
        self.client = openai_client

    def build_retrieval_context(self, retrieved_chunks: List[Dict]) -> str:
        blocks = []
        for i, chunk in enumerate(retrieved_chunks, start=1):
            blocks.append(
                f"[Retrieved Source {i}] {chunk['source']} | {chunk['chunk_id']}\n{chunk['text']}"
            )
        return "\n\n".join(blocks)

    def build_graph_context(self, graph_context: str) -> str:
        return graph_context.strip() if graph_context else "No graph context available."

    def build_facts_context(self, structured_facts: List[Dict] | None) -> str:
        return build_structured_facts_context_text(structured_facts)

    def generate_refined_answer(
        self,
        question: str,
        preliminary_answer: str,
        retrieved_chunks: List[Dict],
        graph_context: str,
        structured_facts: List[Dict] | None = None,
    ) -> str:
        retrieval_context = self.build_retrieval_context(retrieved_chunks)
        graph_context_block = self.build_graph_context(graph_context)
        facts_context_block = self.build_facts_context(structured_facts)

        system_prompt = """
You are a financial reasoning assistant using retrieved document context, structured financial facts, and graph context.

Your job is to produce a refined answer that:
- remains grounded in the retrieved document evidence
- uses structured facts when they sharpen numeric or period-specific claims
- uses the graph context to clarify relationships and multi-hop reasoning
- does not invent facts not supported by the retrieved context or graph context
- explicitly states uncertainty where needed

At the end, include:
1. A short section titled "Reasoning Path" summarizing the relationship chain if helpful.
2. A short section titled "Sources Used" listing the Source or Fact labels used.
"""

        user_prompt = f"""Question:
{question}

Preliminary Answer:
{preliminary_answer}

Retrieved Context:
{retrieval_context}

Structured Facts:
{facts_context_block}

Graph Context:
{graph_context_block}

Instructions:
- Improve the preliminary answer using the graph context.
- Use structured facts to make numerical claims more concrete when they align with the retrieved evidence.
- Keep the answer precise and grounded.
- If the graph context adds useful cross-document relationships, use them.
- Do not overstate conclusions.
"""

        response = self.client.chat.completions.create(
            model=settings.chat_model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt.strip()},
                {"role": "user", "content": user_prompt.strip()},
            ],
        )

        return response.choices[0].message.content
