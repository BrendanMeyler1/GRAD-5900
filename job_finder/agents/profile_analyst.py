"""
Profile Analyst Agent — Extracts structured Experience Persona from resumes.

This is the entry point of the pipeline. It:
1. Parses the uploaded resume (PDF/DOCX)
2. Sends the text to the LLM for structured extraction
3. Separates PII from the persona (tokenization)
4. Stores PII in the vault
5. Registers normalized name variants
6. Returns the tokenized Experience Persona

Input:  Resume file path (PDF or DOCX)
Output: Tokenized Experience Persona dict (B.1 schema)

See §3.1 of the implementation plan.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from errors import LLMParseError, PIILeakError
from llm_router.router import LLMRouter
from pii.normalizer import Normalizer
from pii.tokenizer import PIITokenizer
from pii.vault import PIIVault

logger = logging.getLogger("job_finder.agents.profile_analyst")

# Path to the prompt template
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "profile_analyst.md"


def _load_prompt() -> str:
    """Load and extract the system prompt from the prompt file."""
    raw = PROMPT_PATH.read_text(encoding="utf-8")

    # Extract everything between "## System Prompt" and the next "## "
    match = re.search(
        r"## System Prompt\s*\n(.*?)(?=\n## (?!System))", raw, re.DOTALL
    )
    if match:
        return match.group(1).strip()
    # Fallback: return everything after System Prompt
    idx = raw.find("## System Prompt")
    if idx != -1:
        return raw[idx + len("## System Prompt"):].strip()
    return raw


def extract_text_from_pdf(file_path: str) -> str:
    """Extract text content from a PDF file."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        text_parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        return "\n\n".join(text_parts)
    except ImportError:
        raise ImportError(
            "pypdf is required for PDF parsing. Install with: pip install pypdf"
        )


def extract_text_from_docx(file_path: str) -> str:
    """Extract text content from a DOCX file."""
    try:
        from docx import Document
        doc = Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
    except ImportError:
        raise ImportError(
            "python-docx is required for DOCX parsing. Install with: pip install python-docx"
        )


def extract_resume_text(file_path: str) -> str:
    """Extract text from a resume file (PDF, DOCX, or TXT).

    Args:
        file_path: Path to the resume file.

    Returns:
        Extracted text content.

    Raises:
        ValueError: If the file format is unsupported.
        FileNotFoundError: If the file doesn't exist.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Resume file not found: {file_path}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_text_from_pdf(file_path)
    elif suffix in (".docx", ".doc"):
        return extract_text_from_docx(file_path)
    elif suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8")
    else:
        raise ValueError(
            f"Unsupported resume format: {suffix}. "
            f"Supported: .pdf, .docx, .txt, .md"
        )


def _store_pii(
    pii_data: dict,
    vault: PIIVault,
    normalizer: Normalizer,
) -> None:
    """Store extracted PII in the vault and register normalizations.

    Args:
        pii_data: The "pii_extracted" section from the LLM response.
        vault: PII vault instance.
        normalizer: Normalizer instance.
    """
    # Simple token → value mappings
    simple_fields = {
        "{{FULL_NAME}}": ("full_name", "LOW"),
        "{{FIRST_NAME}}": ("first_name", "LOW"),
        "{{LAST_NAME}}": ("last_name", "LOW"),
        "{{EMAIL}}": ("email", "LOW"),
        "{{PHONE}}": ("phone", "MEDIUM"),
        "{{ADDRESS}}": ("address", "MEDIUM"),
        "{{LINKEDIN}}": ("linkedin", "LOW"),
        "{{GITHUB}}": ("github", "LOW"),
        "{{LOCATION}}": ("location", "LOW"),
    }

    for token_key, (field_name, category) in simple_fields.items():
        value = pii_data.get(field_name)
        if value:
            vault.store_token(token_key, value, category)

    # Schools with normalization
    schools = pii_data.get("schools", [])
    for school in schools:
        token = school.get("token", "{{SCHOOL}}")
        canonical = school.get("canonical", "")
        variants = school.get("variants", [])
        if canonical:
            vault.store_token(token, canonical, "LOW")
            normalizer.register(token, canonical, variants)

    # Employers with normalization
    employers = pii_data.get("employers", [])
    for employer in employers:
        token = employer.get("token", "{{EMPLOYER_1}}")
        canonical = employer.get("canonical", "")
        variants = employer.get("variants", [])
        if canonical:
            vault.store_token(token, canonical, "LOW")
            normalizer.register(token, canonical, variants)


def analyze_resume(
    file_path: str,
    router: Optional[LLMRouter] = None,
    vault: Optional[PIIVault] = None,
) -> dict:
    """Analyze a resume and produce a tokenized Experience Persona.

    This is the main entry point for the Profile Analyst agent.

    Args:
        file_path: Path to the resume file (PDF, DOCX, or TXT).
        router: LLM router instance (created if not provided).
        vault: PII vault instance (created if not provided).

    Returns:
        Tokenized Experience Persona dict (B.1 schema) with added
        persona_id and created_at fields.

    Raises:
        LLMParseError: If the LLM response cannot be parsed.
        FileNotFoundError: If the resume file doesn't exist.
        ValueError: If the file format is unsupported.
    """
    # Initialize dependencies
    if router is None:
        router = LLMRouter()
    if vault is None:
        vault = PIIVault()
    normalizer = Normalizer(vault)

    # Extract text from resume
    logger.info(f"Extracting text from: {file_path}")
    resume_text = extract_resume_text(file_path)

    if not resume_text.strip():
        raise ValueError("Resume file is empty or text could not be extracted.")

    # Load prompt and call LLM
    system_prompt = _load_prompt()
    user_prompt = f"resume_text:\n\n{resume_text}"

    logger.info("Calling LLM for profile analysis...")
    result = router.route_json(
        task_type="profile_analysis",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    # Extract and store PII
    pii_data = result.pop("pii_extracted", {})
    if pii_data:
        logger.info("Storing PII in vault...")
        _store_pii(pii_data, vault, normalizer)
    else:
        logger.warning("No PII extracted from resume — vault not updated")

    # Build the Experience Persona
    persona = {
        "persona_id": str(uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }

    # Verify no PII leaked into the persona
    tokenizer = PIITokenizer(vault)
    persona_str = json.dumps(persona)
    if tokenizer.has_pii(persona_str):
        logger.critical("PII LEAK in persona output — re-tokenizing")
        # Attempt to fix by re-tokenizing
        persona = tokenizer.tokenize_dict(persona)

    logger.info(
        f"Profile analysis complete. Persona ID: {persona['persona_id']}"
    )
    return persona
