class Scorer:
    def __init__(self):
        self.scores = {
            "SUPPORTED": 2,
            "CONTRADICTED": -2,
            "INSUFFICIENT": -1,  # -1 per README scoring table
            "FALLACY": -3,
            "CITATION": 1
        }

    def calculate_scores(self, claims_data, fallacies_data):
        """
        Calculates the score for each speaker.
        claims_data: List of verified claims.
        fallacies_data: List of fallacies with speaker attribution.

        Speaker names are normalized before aggregation so that
        capitalization variants (BIDEN / Biden) and role suffixes
        (JAKE TAPPER, CNN MODERATOR) are handled consistently.
        Moderators are excluded from scoring entirely.
        """

        speaker_scores = {}
        speaker_details = {}

        # Process Claims
        for claim in claims_data:
            speaker = self._normalize_speaker(claim.get("speaker"))
            if speaker is None:
                continue
            if speaker not in speaker_scores:
                speaker_scores[speaker] = 0
                speaker_details[speaker] = {
                    "supported": 0,
                    "contradicted": 0,
                    "insufficient": 0,
                    "fallacies": 0,
                    "citations": 0
                }
            
            # Verification points
            status = claim.get("verification_status", "UNVERIFIED")
            if status in self.scores:
                speaker_scores[speaker] += self.scores[status]
                
                if status == "SUPPORTED":
                    speaker_details[speaker]["supported"] += 1
                elif status == "CONTRADICTED":
                    speaker_details[speaker]["contradicted"] += 1
                elif status == "INSUFFICIENT":
                    speaker_details[speaker]["insufficient"] += 1

            # Citation points
            if claim.get("has_citation", False):
                speaker_scores[speaker] += self.scores["CITATION"]
                speaker_details[speaker]["citations"] += 1

        # Process Fallacies
        # Expected input: list of objects { "speaker": "A", "fallacy": "Ad Hominem" }
        for fallacy in fallacies_data:
            speaker = self._normalize_speaker(fallacy.get("speaker"))
            if speaker is None:
                continue
            if speaker not in speaker_scores:
                speaker_scores[speaker] = 0
                speaker_details[speaker] = {
                    "supported": 0,
                    "contradicted": 0,
                    "insufficient": 0,
                    "fallacies": 0,
                    "citations": 0
                }
            
            speaker_scores[speaker] += self.scores["FALLACY"]
            speaker_details[speaker]["fallacies"] += 1

        return {
            "scores": speaker_scores,
            "details": speaker_details
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    # Known moderator keywords — any normalized name containing one of these
    # will be excluded from scoring.
    _MODERATOR_KEYWORDS = {
        "tapper", "bash", "moderator", "host", "anchor", "cnn"
    }

    def _normalize_speaker(self, raw: str | None) -> str | None:
        """
        Normalize a raw speaker label to a clean, consistent key:
          - Strips role suffixes like ", CNN MODERATOR"
          - Converts to Title Case
          - Reduces multi-word names to last name only
            so "Joe Biden" == "Biden" == "BIDEN"
          - Returns None if the speaker is a moderator (excluded from scoring)
        """
        if not raw:
            return None

        # Strip everything after the first comma (e.g. "JAKE TAPPER, CNN MODERATOR")
        name = raw.split(",")[0].strip()

        # Title-case for consistent comparison
        name = name.title()

        # Reduce to last name so "Joe Biden" merges with "Biden"
        parts = name.split()
        if len(parts) > 1:
            name = parts[-1]

        # Check against moderator keywords (case-insensitive)
        name_lower = name.lower()
        if any(kw in name_lower for kw in self._MODERATOR_KEYWORDS):
            return None

        return name

