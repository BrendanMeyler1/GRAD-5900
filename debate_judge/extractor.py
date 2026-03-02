import os
import json
from pathlib import Path
from openai import OpenAI

# Prompt stored next to this file — works regardless of working directory
_PROMPT_PATH = Path(__file__).parent / "prompts" / "extract.txt"


class ClaimExtractor:
    def __init__(self, api_key=None):
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        self.system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    def extract_claims(self, text: str) -> list[dict]:
        """
        Extracts claims from the provided text using the LLM.
        Returns a list of claim dictionaries.
        """
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": text}
                ],
                temperature=0
            )
            data = json.loads(response.choices[0].message.content)
            return data.get("claims", [])
        except Exception as e:
            print(f"Error extracting claims: {e}")
            return []

    def explain_result(self, scores: dict, details: dict,
                       verified_claims: list, fallacies: list,
                       winner: str | None) -> str:
        """
        Generates a natural-language explanation of the debate result.
        Kept here so callers never need to touch self.client directly.
        """
        prompt = (
            f"Based on the following debate analysis, explain the result.\n\n"
            f"Scores: {json.dumps(scores)}\n"
            f"Details: {json.dumps(details)}\n\n"
            f"Claims Analysis:\n{json.dumps(verified_claims)}\n\n"
            f"Fallacies:\n{json.dumps(fallacies)}\n\n"
            f"Winner: {f'Speaker {winner}' if winner else 'Tie'}"
        )
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a Debate Judge. Explain the winner "
                            "based on the provided scores and evidence."
                        )
                    },
                    {"role": "user", "content": prompt}
                ]
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Could not generate explanation: {e}"
