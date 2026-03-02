import os
import json
from pathlib import Path
from openai import OpenAI

# Prompts stored next to this file — works regardless of working directory
_PROMPT_PATH = Path(__file__).parent / "prompts" / "extract.txt"
_CONTEXT_PROMPT_PATH = Path(__file__).parent / "prompts" / "context.txt"

class ClaimExtractor:
    def __init__(self, api_key=None):
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        self.system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
        self.context_prompt = _CONTEXT_PROMPT_PATH.read_text(encoding="utf-8")

    def extract_context(self, text: str) -> dict:
        """
        Extracts debate context (year, topic) and true participants from the start of the transcript.
        Passes only the first 2000 characters to keep context clean and cheap.
        """
        preview_text = text[:2000]
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": self.context_prompt},
                    {"role": "user", "content": preview_text}
                ],
                temperature=0,
                max_tokens=150
            )
            data = json.loads(response.choices[0].message.content)
            return {
                "year": data.get("year", "Unknown"),
                "topic": data.get("topic", "Debate"),
                "participants": data.get("participants", [])
            }
        except Exception as e:
            print(f"Error extracting context: {e}")
            return {"year": "Unknown", "topic": "Debate", "participants": []}


    # Maximum lines per chunk sent to the LLM
    _CHUNK_SIZE = 100
    # Overlap between chunks so claims at boundaries are not missed
    _CHUNK_OVERLAP = 10

    def extract_claims(self, text: str) -> list[dict]:
        """
        Extracts claims from the provided text using the LLM.
        Large transcripts are automatically split into chunks so the
        LLM response never gets truncated mid-JSON.
        Returns a de-duplicated list of claim dictionaries.
        """
        lines = text.splitlines()

        # If the transcript is short enough, process it in one shot
        if len(lines) <= self._CHUNK_SIZE:
            return self._extract_chunk(text)

        # Otherwise process in overlapping chunks and merge
        all_claims: list[dict] = []
        seen_texts: set[str] = set()
        step = self._CHUNK_SIZE - self._CHUNK_OVERLAP
        chunk_num = 0

        for start in range(0, len(lines), step):
            chunk_lines = lines[start: start + self._CHUNK_SIZE]
            chunk_text = "\n".join(chunk_lines)
            chunk_num += 1
            print(f"  [Extractor] Chunk {chunk_num} "
                  f"(lines {start + 1}–{start + len(chunk_lines)})…")

            chunk_claims = self._extract_chunk(chunk_text)

            for claim in chunk_claims:
                key = claim.get("text", "").strip().lower()
                if key and key not in seen_texts:
                    seen_texts.add(key)
                    all_claims.append(claim)

        return all_claims

    def _extract_chunk(self, text: str) -> list[dict]:
        """Send a single chunk to the LLM and return parsed claims."""
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": text}
                ],
                temperature=0,
                max_tokens=4096
            )
            data = json.loads(response.choices[0].message.content)
            return data.get("claims", [])
        except Exception as e:
            print(f"  [Extractor] Error on chunk: {e}")
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
            f"Winner: {winner if winner else 'Tie'}\n\n"
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
