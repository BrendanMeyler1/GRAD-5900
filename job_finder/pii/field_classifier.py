"""
Field Classifier — Determines PII sensitivity level for form fields.

Classifies fields as LOW, MEDIUM, or HIGH based on their label/name,
controlling auto-fill behavior:

    LOW:    Auto-fill without prompting (name, email, LinkedIn, GitHub)
    MEDIUM: Auto-fill with notification (address, phone, work auth)
    HIGH:   Manual approval required (SSN, DOB, gov ID, salary history)

See §5.5 of the implementation plan.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger("job_finder.pii.field_classifier")


# Field label patterns → sensitivity levels
# Order matters: checked top-down, first match wins
_FIELD_PATTERNS: list[tuple[str, list[str]]] = [
    # HIGH sensitivity — always require manual approval
    ("HIGH", [
        r"\bssn\b", r"\bsocial\s*security\b",
        r"\bdate\s*of\s*birth\b", r"\bdob\b", r"\bbirthday\b", r"\bbirth\s*date\b",
        r"\bgovernment\s*id\b", r"\bgov\w*\s*id\b", r"\bpassport\b",
        r"\bdriver.?s?\s*licen[sc]e\b", r"\bnational\s*id\b",
        r"\bsalary\s*history\b", r"\bprevious\s*(?:salary|compensation)\b",
        r"\bcurrent\s*(?:salary|compensation)\b",
        r"\bdesired\s*(?:salary|compensation)\b", r"\bexpected\s*(?:salary|compensation)\b",
        r"\bsalary\s*(?:expectation|requirement)\b",
        r"\bbank\b", r"\brouting\s*number\b", r"\baccount\s*number\b",
        r"\bcredit\s*card\b",
        r"\bvisa\s*(?:number|status)\b",
        r"\bimmigration\b",
        r"\bethnicity\b", r"\brace\b", r"\bgender\b", r"\bdisability\b",
        r"\bveteran\b",
    ]),
    # MEDIUM sensitivity — auto-fill but notify user
    ("MEDIUM", [
        r"\baddress\b", r"\bstreet\b", r"\bcity\b", r"\bstate\b", r"\bzip\b",
        r"\bpostal\b", r"\bmailing\b",
        r"\bphone\b", r"\bmobile\b", r"\bcell\b", r"\btelephone\b",
        r"\bwork\s*auth\b", r"\bauthoriz\b", r"\bsponsorship\b",
        r"\bwilling\s*to\s*relocate\b", r"\brelocation\b",
    ]),
    # LOW sensitivity — auto-fill freely
    ("LOW", [
        r"\bname\b", r"\bfirst\s*name\b", r"\blast\s*name\b",
        r"\bemail\b", r"\be-?mail\b",
        r"\blinkedin\b",
        r"\bgithub\b", r"\bportfolio\b", r"\bwebsite\b", r"\burl\b",
        r"\bschool\b", r"\buniversity\b", r"\bcollege\b", r"\binstitution\b",
        r"\bdegree\b", r"\bmajor\b", r"\bgpa\b",
        r"\bresume\b", r"\bcv\b", r"\bcover\s*letter\b",
        r"\bhow\s*did\s*you\s*hear\b", r"\breferral\b",
    ]),
]


class FieldClassifier:
    """Classifies form field sensitivity based on label text.

    Used by the Form Interpreter to determine auto-fill behavior
    and by the PII guard middleware to flag sensitive data.
    """

    @staticmethod
    def classify(field_label: str) -> str:
        """Classify a form field label into a sensitivity level.

        Args:
            field_label: The field's label text (e.g. "Social Security Number")

        Returns:
            "LOW", "MEDIUM", or "HIGH"
        """
        label_lower = field_label.lower().strip()

        for level, patterns in _FIELD_PATTERNS:
            for pattern in patterns:
                if re.search(pattern, label_lower):
                    logger.debug(
                        f"Field '{field_label}' classified as {level} "
                        f"(matched: {pattern})"
                    )
                    return level

        # Default: MEDIUM for unknown fields (err on the side of caution)
        logger.debug(f"Field '{field_label}' classified as MEDIUM (no pattern match)")
        return "MEDIUM"

    @staticmethod
    def is_blocking(field_label: str) -> bool:
        """Check if a field requires manual approval (HIGH sensitivity).

        Args:
            field_label: The field's label text.

        Returns:
            True if the field is HIGH sensitivity.
        """
        return FieldClassifier.classify(field_label) == "HIGH"

    @staticmethod
    def classify_token(token_key: str) -> str:
        """Classify a PII token key into a sensitivity level.

        Args:
            token_key: Token key, e.g. "{{FULL_NAME}}" or "{{SSN}}"

        Returns:
            "LOW", "MEDIUM", or "HIGH"
        """
        # Map well-known tokens directly
        token_map = {
            "{{FULL_NAME}}": "LOW",
            "{{FIRST_NAME}}": "LOW",
            "{{LAST_NAME}}": "LOW",
            "{{EMAIL}}": "LOW",
            "{{LINKEDIN}}": "LOW",
            "{{GITHUB}}": "LOW",
            "{{SCHOOL}}": "LOW",
            "{{PHONE}}": "MEDIUM",
            "{{ADDRESS}}": "MEDIUM",
            "{{WORK_AUTH}}": "MEDIUM",
            "{{SSN}}": "HIGH",
            "{{DOB}}": "HIGH",
            "{{GOV_ID}}": "HIGH",
            "{{SALARY_HISTORY}}": "HIGH",
        }

        # Check exact match
        if token_key in token_map:
            return token_map[token_key]

        # Check employer tokens ({{EMPLOYER_N}})
        if token_key.startswith("{{EMPLOYER"):
            return "LOW"

        return "MEDIUM"
