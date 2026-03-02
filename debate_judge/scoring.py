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
        
        Note: fallacies_data handling requires mapping fallacies to speakers.
        The current fallacy detector might need to return speaker info or we associate it by segment.
        Assuming 'fallacies_data' is a dict {speaker: [fallacy list]}.
        Or simpler: we just subtract 3 for every fallacy found in a speaker's segment.
        """
        
        speaker_scores = {}
        speaker_details = {}

        # Process Claims
        for claim in claims_data:
            speaker = claim.get("speaker")
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
            speaker = fallacy.get("speaker")
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
