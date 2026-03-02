import os
import json
from pathlib import Path
from openai import OpenAI

# Prompt stored next to this file — works regardless of working directory
_PROMPT_PATH = Path(__file__).parent / "prompts" / "fallacy.txt"


class FallacyDetector:
    def __init__(self, api_key=None):
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        self.system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    def detect_fallacies(self, text: str) -> list[dict]:
        """
        Detects fallacies in the given debate text.
        Returns a list of fallacy dictionaries.
        """
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"Text to Analyze:\n{text}"}
                ],
                temperature=0
            )
            data = json.loads(response.choices[0].message.content)
            return data.get("fallacies", [])
        except Exception as e:
            print(f"Error detecting fallacies: {e}")
            return []
