"""
ApplicationState — Central state schema for the LangGraph workflow.

Every agent reads from and writes to this state. The orchestrator
passes it between nodes. See Appendix C.1 for the full specification.

All fields are optional (except defaults) because the state is
progressively built as the application moves through the pipeline.
"""

from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class ApplicationState(BaseModel):
    """Central state passed through the LangGraph workflow.
    Every agent reads what it needs and writes its output."""

    # --- Identity ---
    application_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # --- Phase: Profile (set once at startup) ---
    persona: Optional[dict] = None                 # B.1 schema
    resume_raw_path: Optional[str] = None          # path to uploaded file

    # --- Phase: Discovery ---
    listing: Optional[dict] = None                 # B.2 schema
    alive_score: Optional[dict] = None             # subset of B.2

    # --- Phase: Evaluation ---
    fit_score: Optional[dict] = None               # B.3 schema

    # --- Phase: Document Generation ---
    tailored_resume_tokenized: Optional[str] = None     # file path
    tailored_resume_final: Optional[str] = None         # after PII injection
    cover_letter_tokenized: Optional[str] = None
    cover_letter_final: Optional[str] = None
    question_responses: list[dict] = Field(default_factory=list)  # B.5 schema

    # --- Phase: Form Filling ---
    fill_plan: Optional[dict] = None               # B.4 schema
    account_status: Optional[Literal["existing", "created", "failed"]] = None
    session_context_id: Optional[str] = None       # browser session for verification

    # --- Phase: Submission ---
    submission_mode: Literal["dry_run", "shadow", "live"] = "shadow"
    use_browser_automation: bool = False
    headless: bool = True
    apply_url: Optional[str] = None
    artifact_paths: Optional[dict[str, str]] = None
    humanizer_config: Optional[dict[str, Any]] = None
    fields_filled: list[dict] = Field(default_factory=list)
    post_upload_corrections: list[dict] = Field(default_factory=list)
    human_escalations: list[dict] = Field(default_factory=list)
    screenshot_path: Optional[str] = None

    # --- Phase: Outcome ---
    status: Literal[
        "QUEUED", "APPROVED", "AWAITING_APPROVAL", "FILLING", "SHADOW_REVIEW",
        "SUBMITTED", "RECEIVED", "REJECTED",
        "INTERVIEW_SCHEDULED", "FOLLOW_UP_NEEDED", "OFFER", "NO_RESPONSE_30D",
        "FAILED", "ABORTED"
    ] = "QUEUED"
    status_history: list[dict] = Field(default_factory=list)
    failure_record: Optional[dict] = None          # B.7 schema if failed

    # --- Metadata ---
    replay_trace_id: Optional[str] = None
    time_to_apply_seconds: Optional[int] = None
    human_interventions: int = 0
    attempt_number: int = 0
    current_attempt: Optional[dict] = None
    attempt_history: list[dict] = Field(default_factory=list)

    class Config:
        """Pydantic config for the state model."""
        # Allow extra fields for forward compatibility
        extra = "allow"
