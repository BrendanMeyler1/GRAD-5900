import json
from typing import Dict, Any, Optional

from openai import OpenAI

from backend.core.config import settings


class LLMJudge:
    def __init__(self, query_cache=None):
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.query_cache = query_cache

    def _safe_json_loads(self, text: str) -> Dict[str, Any]:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start:end + 1])
            raise

    def judge_answer(
        self,
        question: str,
        ideal_answer: str,
        retrieved_context: str,
        graph_context: str,
        candidate_answer: str,
        mode: str,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        Judge a candidate answer using the retrieved context, graph context, and ideal answer.
        Returns structured JSON with 0-5 scores and short rationales.
        """
        cache_key = None
        if self.query_cache is not None and use_cache:
            cache_key = self.query_cache.make_judge_key(
                question=question,
                mode=mode,
                candidate_answer=candidate_answer,
                retrieved_context=retrieved_context,
                graph_context=graph_context,
                ideal_answer=ideal_answer,
                version="v1",
            )
            cached = self.query_cache.load(cache_key)
            if cached is not None:
                return cached

        system_prompt = """
You are an expert evaluator for a financial RAG and GraphRAG system.

You will judge an answer using:
1. The user's question
2. An ideal reference answer
3. Retrieved document context
4. Graph context (if present)
5. The candidate answer

Your task is to score the candidate answer on these dimensions from 0 to 5:

- faithfulness: Is the answer supported by the provided retrieved/graph context?
- relevance: Does it directly answer the user's question?
- completeness: Does it cover the main important points?
- groundedness: Does it avoid unsupported speculation?
- graph_usefulness: Did the answer meaningfully benefit from graph context?
- reasoning_quality: Is the reasoning coherent, useful, and well-structured?
- overall: Overall quality considering the above dimensions

Scoring guidance:
- 0 = very poor
- 1 = poor
- 2 = weak
- 3 = acceptable
- 4 = strong
- 5 = excellent

Return ONLY valid JSON in this exact format:
{
  "scores": {
    "faithfulness": 0,
    "relevance": 0,
    "completeness": 0,
    "groundedness": 0,
    "graph_usefulness": 0,
    "reasoning_quality": 0,
    "overall": 0
  },
  "rationales": {
    "faithfulness": "short string",
    "relevance": "short string",
    "completeness": "short string",
    "groundedness": "short string",
    "graph_usefulness": "short string",
    "reasoning_quality": "short string",
    "overall": "short string"
  },
  "summary": "2-4 sentence summary of strengths and weaknesses"
}

Important rules:
- Be strict but fair.
- Do not reward unsupported claims.
- Use graph_usefulness = 0 if graph context is absent or not used meaningfully.
- Judge based on the provided context, not outside knowledge.
"""

        user_prompt = f"""Mode:
{mode}

Question:
{question}

Ideal Answer:
{ideal_answer}

Retrieved Context:
{retrieved_context}

Graph Context:
{graph_context if graph_context else "No graph context provided."}

Candidate Answer:
{candidate_answer}
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

        scores = parsed.get("scores", {})
        rationales = parsed.get("rationales", {})
        summary = parsed.get("summary", "")

        normalized_scores = {}
        score_keys = [
            "faithfulness",
            "relevance",
            "completeness",
            "groundedness",
            "graph_usefulness",
            "reasoning_quality",
            "overall",
        ]

        for key in score_keys:
            value = scores.get(key, 0)
            try:
                value = int(value)
            except Exception:
                value = 0
            normalized_scores[key] = max(0, min(5, value))

        normalized_rationales = {}
        for key in score_keys:
            normalized_rationales[key] = str(rationales.get(key, "")).strip()

        result = {
            "scores": normalized_scores,
            "rationales": normalized_rationales,
            "summary": str(summary).strip(),
        }

        if self.query_cache is not None and cache_key is not None:
            self.query_cache.save(cache_key, result)

        return result
