"""
api/routes/profile.py — Profile CRUD + resume upload.

Endpoints:
    GET  /api/profile                  → FullProfile
    PUT  /api/profile                  → update top-level fields
    POST /api/profile/resume           → upload PDF/DOCX; extracts profile
    GET  /api/profile/education
    POST /api/profile/education
    GET  /api/profile/experience
    POST /api/profile/experience
    GET  /api/profile/skills
    POST /api/profile/skills           → batch upsert
    GET  /api/profile/qa
    POST /api/profile/qa
    GET  /api/profile/completeness     → {pct, missing_fields}
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

from api.dependencies import get_profile_builder, get_store
from config import settings
from db.store import Education, FullProfile, QA, Skill, Store, UserProfile, WorkExperience
from utils.text import extract_resume_text

log = logging.getLogger(__name__)
router = APIRouter()


class ProfileUpdate(BaseModel):
    """Partial profile update payload."""

    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    country: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    authorized_to_work: bool | None = None
    requires_sponsorship: bool | None = None
    visa_status: str | None = None
    target_salary_min: int | None = None
    target_salary_max: int | None = None
    remote_preference: str | None = None
    willing_to_relocate: bool | None = None
    availability_weeks: int | None = None
    gender: str | None = None
    race_ethnicity: str | None = None
    veteran_status: str | None = None
    disability_status: str | None = None


class EducationCreate(BaseModel):
    institution: str
    degree: str | None = None
    field: str | None = None
    graduation_year: int | None = None
    gpa: float | None = None
    relevant_coursework: str | None = None


class ExperienceCreate(BaseModel):
    company: str
    title: str | None = None
    employment_type: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    description: str | None = None
    achievements: str | None = None


class SkillCreate(BaseModel):
    name: str
    category: str | None = None
    proficiency: str | None = None


class SkillsBatch(BaseModel):
    skills: list[SkillCreate]


class QACreate(BaseModel):
    question: str
    answer: str
    category: str | None = None


@router.get("", summary="Get complete user profile", response_model=FullProfile)
async def get_profile(store: Store = Depends(get_store)) -> FullProfile:
    return store.get_full_profile()


@router.put("", summary="Update profile top-level fields", response_model=UserProfile)
async def update_profile(
    update: ProfileUpdate, store: Store = Depends(get_store)
) -> UserProfile:
    fields = {k: v for k, v in update.model_dump(exclude_unset=True).items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail={"error": "no_fields"})
    return store.upsert_profile(fields)


@router.post(
    "/resume",
    summary="Upload resume PDF/DOCX, extract profile data",
)
async def upload_resume(
    file: UploadFile = File(...),
    store: Store = Depends(get_store),
    profile_builder=Depends(get_profile_builder),
) -> dict[str, Any]:
    """
    Persist the uploaded file to data/resumes/ and run profile extraction.

    Returns extracted fields + the file path. Profile is updated in the DB.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail={"error": "no_filename"})
    ext = Path(file.filename).suffix.lower()
    if ext not in {".pdf", ".docx", ".doc"}:
        raise HTTPException(
            status_code=400,
            detail={"error": "unsupported_type", "accepted": ["pdf", "docx"]},
        )

    dest = Path(settings.resumes_dir) / file.filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    dest.write_bytes(content)
    log.info(
        "profile.resume_saved",
        extra={"file": str(dest), "size_kb": len(content) // 1024},
    )

    try:
        text = extract_resume_text(dest)
    except Exception as exc:  # noqa: BLE001
        log.exception("profile.extract_failed", extra={"error": str(exc)})
        raise HTTPException(
            status_code=400,
            detail={"error": "extraction_failed", "message": str(exc)},
        ) from exc

    # Save raw text + run structured extraction
    store.upsert_profile({"resume_file_path": str(dest), "resume_raw_text": text})
    extracted = await profile_builder.extract_from_resume(text)

    name = f"{extracted.first_name or ''} {extracted.last_name or ''}".strip() or None
    skills_list = [
        s.get("name", "") for s in (extracted.skills or []) if s.get("name")
    ]
    education_list = [
        {
            "degree": e.get("degree"),
            "institution": e.get("institution"),
            "field": e.get("field"),
            "year": e.get("graduation_year"),
        }
        for e in (extracted.education or [])
    ]
    experience_list = [
        {
            "title": x.get("title"),
            "company": x.get("company"),
            "duration": (
                f"{x.get('start_date') or ''} – "
                f"{'Present' if x.get('is_current') else (x.get('end_date') or '')}"
            ).strip(" –"),
        }
        for x in (extracted.experience or [])
    ]

    return {
        "file_path": str(dest),
        "text_chars": len(text),
        # Top-level fields consumed by the dashboard StepPreview
        "name": name,
        "email": extracted.email,
        "phone": extracted.phone,
        "skills": skills_list,
        "education": education_list,
        "experience": experience_list,
        # Legacy shape (kept for tests / scripted callers)
        "extracted": {
            "name": name or "",
            "email": extracted.email,
            "phone": extracted.phone,
            "education_count": len(education_list),
            "experience_count": len(experience_list),
            "skills_count": len(skills_list),
        },
    }


@router.get("/education", summary="List education records", response_model=list[Education])
async def list_education(store: Store = Depends(get_store)) -> list[Education]:
    return store.get_education()


@router.post("/education", summary="Add education record", response_model=Education)
async def add_education(
    edu: EducationCreate, store: Store = Depends(get_store)
) -> Education:
    return store.add_education(edu.model_dump())


@router.get(
    "/experience",
    summary="List work experience",
    response_model=list[WorkExperience],
)
async def list_experience(store: Store = Depends(get_store)) -> list[WorkExperience]:
    return store.get_experience()


@router.post(
    "/experience",
    summary="Add work experience",
    response_model=WorkExperience,
)
async def add_experience(
    exp: ExperienceCreate, store: Store = Depends(get_store)
) -> WorkExperience:
    return store.add_experience(exp.model_dump())


@router.get("/skills", summary="List skills", response_model=list[Skill])
async def list_skills(store: Store = Depends(get_store)) -> list[Skill]:
    return store.get_skills()


@router.post("/skills", summary="Upsert a batch of skills", response_model=list[Skill])
async def upsert_skills(
    payload: SkillsBatch, store: Store = Depends(get_store)
) -> list[Skill]:
    return store.upsert_skills([s.model_dump() for s in payload.skills])


@router.get("/qa", summary="List Q&A notes", response_model=list[QA])
async def list_qa(
    category: str | None = None, store: Store = Depends(get_store)
) -> list[QA]:
    return store.get_qa(category=category)


@router.post("/qa", summary="Add a Q&A note", response_model=QA)
async def add_qa(payload: QACreate, store: Store = Depends(get_store)) -> QA:
    return store.add_qa(
        question=payload.question, answer=payload.answer, category=payload.category
    )


@router.get("/completeness", summary="Profile completeness + missing fields")
async def profile_completeness(store: Store = Depends(get_store)) -> dict[str, Any]:
    profile = store.get_full_profile()
    p = profile.profile
    missing: list[str] = []
    if not p.first_name or not p.last_name:
        missing.append("name")
    if not p.email:
        missing.append("email")
    if not p.phone:
        missing.append("phone")
    if not p.city or not p.state:
        missing.append("location")
    if p.target_salary_min is None:
        missing.append("target_salary_min")
    if not p.remote_preference:
        missing.append("remote_preference")
    if not profile.experience:
        missing.append("experience")
    if not profile.education:
        missing.append("education")
    if not profile.skills:
        missing.append("skills")
    return {
        "completion_pct": profile.completion_pct,
        "missing_fields": missing,
        "can_apply": profile.is_complete_enough,
    }
