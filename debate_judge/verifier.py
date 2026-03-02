import os
import json
from pathlib import Path
from openai import OpenAI
from tools.wikipedia_tool import WikipediaTool
import router

# Prompt stored next to this file — works regardless of working directory
_PROMPT_PATH = Path(__file__).parent / "prompts" / "verify.txt"


class ClaimVerifier:
    def __init__(self, api_key=None):
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        self.wiki_tool = WikipediaTool()
        self.system_prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")

    def _generate_search_query(self, claim_text: str) -> str:
        """
        Uses a cheap LLM call to convert a full claim sentence into a concise
        3-5 word Wikipedia search query (e.g. "nuclear deaths per TWh").
        Falls back to the raw claim text if the call fails.
        """
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a search query generator. "
                            "Convert the user's claim into a concise 3-5 word Wikipedia search query. "
                            "Return ONLY the query string, no punctuation, no explanation."
                        )
                    },
                    {"role": "user", "content": claim_text}
                ],
                temperature=0,
                max_tokens=20
            )
            query = response.choices[0].message.content.strip()
            return query if query else claim_text
        except Exception:
            return claim_text

    def verify_claim(self, claim: dict) -> dict:
        """
        Verifies a single claim dictionary.
        Selects the model based on claim complexity (router.select_model).
        Returns a dictionary with verification results.
        """
        claim_text = claim["text"]

        # Generate a clean search query from the claim text
        search_query = self._generate_search_query(claim_text)
        print(f"     [Search query: \"{search_query}\"]")

        # Search Wikipedia for evidence
        evidence = self.wiki_tool.search_summary(search_query)

        if not evidence:
            return {
                "claim": claim_text,
                "status": "INSUFFICIENT",
                "reasoning": "No relevant evidence found in Wikipedia search.",
                "evidence_source": "None"
            }

        # Select model based on claim complexity
        model = router.select_model(claim)

        # Format prompt with claim + evidence
        system_prompt = self.system_prompt_template.format(
            claim_text=claim_text,
            evidence_text=evidence
        )

        try:
            response = self.client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Verify this claim: {claim_text}"}
                ],
                temperature=0
            )
            result = json.loads(response.choices[0].message.content)

            return {
                "claim": claim_text,
                "status": result.get("status", "INSUFFICIENT"),
                "reasoning": result.get("reasoning", "Analysis failed"),
                "evidence_source": evidence.split("\n")[0] if evidence else "None"
            }

        except Exception as e:
            print(f"Error checking claim '{claim_text}': {e}")
            return {
                "claim": claim_text,
                "status": "INSUFFICIENT",
                "reasoning": f"Error during verification: {str(e)}",
                "evidence_source": "Error"
            }
