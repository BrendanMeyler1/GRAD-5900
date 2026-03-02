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

    # Maximum lines per chunk sent to the LLM
    _CHUNK_SIZE = 100
    # Overlap between chunks so fallacies at boundaries are not missed
    _CHUNK_OVERLAP = 10

    def detect_fallacies(self, text: str) -> list[dict]:
        """
        Detects fallacies in the given debate text.
        Large transcripts are automatically split into chunks so the
        LLM response never gets truncated mid-JSON.
        Returns a de-duplicated list of fallacy dictionaries.
        """
        lines = text.splitlines()

        # If the transcript is short enough, process it in one shot
        if len(lines) <= self._CHUNK_SIZE:
            return self._detect_chunk(text)

        # Otherwise process in overlapping chunks and merge
        all_fallacies: list[dict] = []
        seen_quotes: set[str] = set()
        step = self._CHUNK_SIZE - self._CHUNK_OVERLAP
        chunk_num = 0

        for start in range(0, len(lines), step):
            chunk_lines = lines[start: start + self._CHUNK_SIZE]
            chunk_text = "\n".join(chunk_lines)
            chunk_num += 1
            print(f"  [FallacyDetector] Chunk {chunk_num} "
                  f"(lines {start + 1}–{start + len(chunk_lines)})…")

            chunk_fallacies = self._detect_chunk(chunk_text)

            for fallacy in chunk_fallacies:
                key = fallacy.get("quote", "").strip().lower()
                if key and key not in seen_quotes:
                    seen_quotes.add(key)
                    all_fallacies.append(fallacy)

        return all_fallacies

    def _detect_chunk(self, text: str) -> list[dict]:
        """Send a single chunk to the LLM and return parsed fallacies."""
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"Text to Analyze:\n{text}"}
                ],
                temperature=0,
                max_tokens=4096
            )
            data = json.loads(response.choices[0].message.content)
            return data.get("fallacies", [])
        except Exception as e:
            print(f"  [FallacyDetector] Error on chunk: {e}")
            return []
