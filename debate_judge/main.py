import os
import json
from dotenv import load_dotenv
from colorama import init, Fore, Style

import router
from extractor import ClaimExtractor
from verifier import ClaimVerifier
from fallacy import FallacyDetector
from scoring import Scorer
from tools.web_scraper import WebScraper

# Load environment variables
load_dotenv()
init(autoreset=True)


def get_debate_text() -> str:
    """
    Ask the user whether they want to supply a URL or paste text manually.
    Returns the raw debate text string, or an empty string if nothing was provided.
    """
    print("\nHow would you like to provide the debate?")
    print("  [1] Enter a URL (Reddit thread, forum post, etc.)")
    print("  [2] Paste text manually")

    while True:
        choice = input("\nChoice (1 or 2): ").strip()
        if choice in ("1", "2"):
            break
        print(Fore.YELLOW + "Please enter 1 or 2.")

    if choice == "1":
        url = input("\nEnter URL: ").strip()
        if not url:
            print(Fore.YELLOW + "No URL provided.")
            return ""

        print(Fore.CYAN + f"\nFetching content from: {url}")
        scraper = WebScraper()
        try:
            text = scraper.scrape(url)
            line_count = text.count("\n") + 1
            print(Fore.GREEN + f"Successfully extracted ~{line_count} lines of text.")
            print(Fore.CYAN + "\n--- Preview (first 10 lines) ---")
            preview_lines = text.splitlines()[:10]
            for line in preview_lines:
                print(f"  {line}")
            if len(text.splitlines()) > 10:
                print(f"  ... (+{len(text.splitlines()) - 10} more lines)")
            return text
        except Exception as e:
            print(Fore.RED + f"Error fetching URL: {e}")
            return ""

    else:
        print("\nPaste the debate transcript below (press Enter twice to finish):")
        lines = []
        while True:
            try:
                line = input()
                if not line:
                    break
                lines.append(line)
            except EOFError:
                break
        return "\n".join(lines)


def main():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print(Fore.RED + "Error: OPENAI_API_KEY not found in environment variables.")
        print("Please create a .env file with your API key.")
        return

    print(Fore.CYAN + "Initializing Debate Judge...")

    extractor = ClaimExtractor(api_key)
    verifier = ClaimVerifier(api_key)
    fallacy_detector = FallacyDetector(api_key)
    scorer = Scorer()

    print(Fore.GREEN + "Ready!")

    debate_text = get_debate_text()
    if not debate_text.strip():
        print(Fore.YELLOW + "No text provided. Exiting.")
        return

    # ── Stage 1: Extraction ─────────────────────────────────────────────────
    print(Fore.CYAN + "\n--- Executing Stage 1: Extraction ---")
    claims = extractor.extract_claims(debate_text)
    print(f"Extracted {len(claims)} claims.")

    # ── Stage 2: Verification ───────────────────────────────────────────────
    verified_claims = []
    print(Fore.CYAN + "\n--- Executing Stage 2: Verification ---")

    for claim in claims:
        print(f"\nProcessing claim by {claim.get('speaker')}: \"{claim.get('text')}\"")

        if router.should_verify(claim):
            model = router.select_model(claim)
            print(Fore.YELLOW + f"  -> Verifying (Type: {claim.get('type')} | Model: {model})")
            result = verifier.verify_claim(claim)

            claim["verification_status"] = result["status"]
            claim["verification_reason"] = result["reasoning"]
            claim["evidence_source"] = result["evidence_source"]

            color = Fore.GREEN if result["status"] == "SUPPORTED" else Fore.RED
            print(color + f"  Result: {result['status']}")
            print(f"  Reason: {result['reasoning']}")
        else:
            print(Fore.BLUE + f"  -> Skipping Verification (Type: {claim.get('type')})")
            claim["verification_status"] = "UNVERIFIED"

        verified_claims.append(claim)

    # ── Stage 3: Fallacy Detection ───────────────────────────────────────────
    print(Fore.CYAN + "\n--- Executing Stage 3: Fallacy Detection ---")
    fallacies = fallacy_detector.detect_fallacies(debate_text)
    if fallacies:
        for f in fallacies:
            print(Fore.RED + f"Detected {f.get('fallacy_name')} by {f.get('speaker')}: {f.get('quote')}")
    else:
        print(Fore.GREEN + "No fallacies detected.")

    # ── Stage 4: Scoring ─────────────────────────────────────────────────────
    print(Fore.CYAN + "\n--- Executing Stage 4: Scoring ---")
    score_result = scorer.calculate_scores(verified_claims, fallacies)
    scores = score_result["scores"]
    details = score_result["details"]

    print("\n" + "=" * 30)
    print("FINAL JUDGMENT")
    print("=" * 30)

    for speaker, score in scores.items():
        print(f"\n{speaker}: {score} points")
        d = details.get(speaker, {})
        print(f"  Supported:    {d.get('supported', 0)}")
        print(f"  Contradicted: {d.get('contradicted', 0)}")
        print(f"  Insufficient: {d.get('insufficient', 0)}")
        print(f"  Citations:    {d.get('citations', 0)}")
        print(f"  Fallacies:    {d.get('fallacies', 0)}")

    # ── Determine Winner (with tie handling) ─────────────────────────────────
    winner: str | None = None
    if not scores:
        print("\nNo scores to determine a winner.")
    else:
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_score = sorted_scores[0][1]
        leaders = [spk for spk, sc in sorted_scores if sc == top_score]

        if len(leaders) > 1:
            print(Fore.YELLOW + f"\nResult: TIE between {', '.join(leaders)} ({top_score} pts each)")
        else:
            winner = leaders[0]
            print(Fore.GREEN + f"\nWinner: {winner}")

    # ── Stage 5: LLM Explanation ─────────────────────────────────────────────
    print(Fore.CYAN + "\n--- Generating Explanation ---")
    explanation = extractor.explain_result(scores, details, verified_claims, fallacies, winner)
    print(explanation)


if __name__ == "__main__":
    main()
