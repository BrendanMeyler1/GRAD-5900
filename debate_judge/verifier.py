import os
import json
from pathlib import Path
from openai import OpenAI
from tools.wikipedia_tool import WikipediaTool
from tools.duckduckgo_tool import DuckDuckGoTool
import router

# Prompt stored next to this file — works regardless of working directory
_PROMPT_PATH = Path(__file__).parent / "prompts" / "verify.txt"


class ClaimVerifier:
    def __init__(self, api_key=None):
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        self.wiki_tool = WikipediaTool()
        self.ddg_tool  = DuckDuckGoTool(max_results=3)
        self.system_prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")

    def _generate_search_query(self, claim: dict, context: dict = None) -> str:
        """
        Uses a cheap LLM call to convert a claim into a focused Wikipedia
        search query, using speaker and claim type as context so the query
        is topically grounded rather than literal.
        Passes debate context (year and topic) if available.
        Falls back to the raw claim text if the call fails.
        """
        claim_text = claim.get("text", "")
        speaker    = claim.get("speaker", "Unknown")
        claim_type = claim.get("type", "FACTUAL")

        context_str = ""
        if context and (context.get("year") != "Unknown" or context.get("topic") != "Debate"):
            context_str = f"Debate Context: {context.get('year')} {context.get('topic')}\n"

        prompt = (
            f"{context_str}"
            f"Speaker: {speaker}\n"
            f"Claim type: {claim_type}\n"
            f"Claim: {claim_text}\n\n"
            "Generate a concise 3-6 word Wikipedia search query that would find "
            "evidence to verify or refute this specific claim. "
            "Focus on the key verifiable fact, not filler words. "
            "Return ONLY the query, no punctuation, no explanation."
        )

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert fact-checker. Given a political claim and its speaker, "
                            "produce the most targeted Wikipedia search query to find relevant evidence. "
                            "Use proper nouns, legislation names, and specific topics — avoid vague words."
                        )
                    },
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                max_tokens=20
            )
            query = response.choices[0].message.content.strip()
            return query if query else claim_text
        except Exception:
            return claim_text

    def verify_claim(self, claim: dict, debate_context: dict = None) -> dict:
        """
        Verifies a single claim dictionary using debate context for better searches.
        Selects the model based on claim complexity (router.select_model).
        Returns a dictionary with verification results.
        """
        claim_text = claim["text"]

        # Generate a context-aware search query from the full claim object
        search_query = self._generate_search_query(claim, context=debate_context)
        print(f"     [Search query: \"{search_query}\"]")

        # ── Evidence waterfall: Smart Routing ─────────────────────────────
        evidence        = None
        evidence_source = None

        claim_type = claim.get("type", "FACTUAL")
        prefer_web = claim_type == "STATISTICAL"

        if prefer_web:
            evidence = self.ddg_tool.search_summary(search_query)
            if evidence:
                evidence_source = "DuckDuckGo"
                print(f"     [Source: DuckDuckGo (preferred for {claim_type})]")
            else:
                print(f"     [DuckDuckGo: no result — trying Wikipedia…]")
                evidence = self.wiki_tool.search_summary(search_query)
                if evidence:
                    evidence_source = "Wikipedia"
                    print(f"     [Source: Wikipedia]")
        else:
            evidence = self.wiki_tool.search_summary(search_query)
            if evidence:
                evidence_source = "Wikipedia"
                print(f"     [Source: Wikipedia]")
            else:
                print(f"     [Wikipedia: no result — trying DuckDuckGo…]")
                evidence = self.ddg_tool.search_summary(search_query)
                if evidence:
                    evidence_source = "DuckDuckGo"
                    print(f"     [Source: DuckDuckGo]")

        if not evidence:
            return {
                "claim": claim_text,
                "status": "INSUFFICIENT",
                "reasoning": "No relevant evidence found in Wikipedia or web search.",
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
                "evidence_source": f"{evidence_source}: {evidence.split(chr(10))[0]}"
            }

        except Exception as e:
            print(f"Error checking claim '{claim_text}': {e}")
            return {
                "claim": claim_text,
                "status": "INSUFFICIENT",
                "reasoning": f"Error during verification: {str(e)}",
                "evidence_source": "Error"
            }
