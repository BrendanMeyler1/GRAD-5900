"""
Persona Routes — Upload resume and manage Experience Persona.

Endpoints:
    POST /api/persona/upload   — Upload resume → trigger Profile Analyst
    GET  /api/persona/current  — Get current Experience Persona
"""

import logging
import shutil
from pathlib import Path
from typing import Optional, Any

from fastapi import APIRouter, HTTPException, Request, Body

logger = logging.getLogger("job_finder.api.routes.persona")

router = APIRouter()

# In-memory storage for the current persona (Phase 2: move to DB)
_current_persona: Optional[dict] = None

# Upload directory
UPLOAD_DIR = Path("data/raw")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/persona/upload", summary="Upload resume for profile analysis")
async def upload_resume(request: Request):
    """Upload a resume file and run the Profile Analyst agent.

    Accepts PDF, DOCX, or TXT files. The agent extracts a structured
    Experience Persona, tokenizes PII, and stores personal data
    securely in the local vault.

    Returns the tokenized persona (no real PII in response).
    """
    global _current_persona

    # Parse multipart form payload. This avoids hard dependency checks at import time
    # and returns a clear runtime error if multipart support is unavailable.
    try:
        form = await request.form()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=(
                "Multipart parsing unavailable. Install 'python-multipart' to "
                f"enable resume uploads. ({type(e).__name__}: {e})"
            ),
        )

    file = form.get("file")
    if file is None or not hasattr(file, "filename") or not hasattr(file, "file"):
        raise HTTPException(
            status_code=400,
            detail="Missing uploaded file field 'file' in multipart form payload.",
        )

    # Validate file type
    allowed_extensions = {".pdf", ".docx", ".doc", ".txt", ".md"}
    suffix = Path(file.filename).suffix.lower()
    if suffix not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {suffix}. "
                   f"Accepted: {', '.join(allowed_extensions)}",
        )

    # Save uploaded file
    file_path = UPLOAD_DIR / file.filename
    try:
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        logger.info(f"Resume saved: {file_path}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    # Run Profile Analyst
    try:
        from agents.profile_analyst import analyze_resume
        persona = analyze_resume(str(file_path))
        _current_persona = persona
        return {
            "status": "success",
            "message": "Resume analyzed and persona created",
            "persona": persona,
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Profile analysis failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Profile analysis failed: {type(e).__name__}: {e}",
        )


@router.get("/persona/current", summary="Get current Experience Persona")
async def get_current_persona():
    """Return the current tokenized Experience Persona.

    Returns the persona created from the most recently uploaded resume.
    The response contains only tokenized data — no real PII.
    """
    if _current_persona is None:
        raise HTTPException(
            status_code=404,
            detail="No persona loaded. Upload a resume first via POST /api/persona/upload",
        )
    return {
        "status": "success",
        "persona": _current_persona,
    }


@router.post("/persona/debug-set", summary="[DEBUG] Directly set the current persona")
async def debug_set_persona(persona_payload: dict[str, Any] = Body(...)):
    """Bypasses the LLM Profile Analyst to directly inject a persona JSON.
    
    Used exclusively for test scripts to populate the in-memory persona state.
    """
    global _current_persona
    _current_persona = persona_payload
    return {
        "status": "success",
        "message": "Persona directly injected for testing.",
    }
