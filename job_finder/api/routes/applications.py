"""
Application routes - workflow execution and lifecycle management.

Endpoints:
    POST /api/apply/{listing_id}
    GET  /api/apply/{app_id}/status
    POST /api/apply/{app_id}/approve
    POST /api/apply/{app_id}/edit
    POST /api/apply/{app_id}/abort
    POST /api/apply/{app_id}/resume
    POST /api/apply/{app_id}/escalation/{field_id}/resolve
    POST /api/applications/status-sync
    GET  /api/applications
    GET  /api/applications/{app_id}
"""

from __future__ import annotations

import copy
import json
import logging
import re
import sqlite3
import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException, BackgroundTasks
from pydantic import BaseModel

from agents.status_tracker import track_status_updates
from api.routes import persona as persona_routes
from api.routes import settings as settings_routes
from api.routes.jobs import get_listing_by_id
from pii.account_vault import AccountVault
from pii.vault import PIIVault
from graph.checkpoints import get_checkpointer
from graph.state import ApplicationState
from graph.workflow import build_workflow, record_outcome_node, submission_node
from setup.init_db import init_outcomes_db

router = APIRouter()
logger = logging.getLogger("job_finder.api.routes.applications")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTCOMES_DB_PATH = str(PROJECT_ROOT / "data" / "outcomes.db")
ATTEMPT_SCOPED_KEYS = (
    "human_escalations",
    "failure_record",
    "fields_filled",
    "post_upload_corrections",
    "screenshot_path",
    "time_to_apply_seconds",
    "account_status",
    "session_context_id",
)

# In-memory cache for websocket polling and fast lookups.
_applications: dict[str, dict[str, Any]] = {}
_TOKEN_PATTERN = re.compile(r"^\{\{[A-Z0-9_]+\}\}$")
# Concurrency guard: tracks application IDs with an active background task.
_executing: set[str] = set()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# Initialize the outcomes database once at module load
init_outcomes_db(db_path=OUTCOMES_DB_PATH)

def _connect_outcomes(db_path: str = OUTCOMES_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS application_states (
            application_id TEXT PRIMARY KEY,
            state_json     TEXT NOT NULL,
            updated_at     TEXT NOT NULL
        )
        """
    )
    return conn


def _extract_state_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, ApplicationState):
        return value.model_dump()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    raise ValueError(f"Unexpected workflow output type: {type(value)}")


async def _execute_workflow(state: ApplicationState) -> dict[str, Any]:
    # Use AsyncSqliteSaver to safely run async nodes while persisting checkpoints to SQLite
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        
        async with AsyncSqliteSaver.from_conn_string("data/checkpoints.db") as checkpointer:
            workflow = build_workflow(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": str(state.application_id)}}
            result = await workflow.ainvoke(state, config=config)
            return _extract_state_payload(result)
    except ImportError:
        # Fallback to MemorySaver if aiosqlite is not installed yet
        from langgraph.checkpoint.memory import MemorySaver
        workflow = build_workflow(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": str(state.application_id)}}
        result = await workflow.ainvoke(state, config=config)
        return _extract_state_payload(result)


def _extract_company(listing: dict[str, Any] | None) -> str:
    listing = listing or {}
    company = listing.get("company")
    if isinstance(company, dict):
        return str(company.get("name") or "unknown_company")
    return str(company or "unknown_company")


def _extract_role_title(listing: dict[str, Any] | None) -> str:
    listing = listing or {}
    role = listing.get("role")
    if isinstance(role, dict):
        return str(role.get("title") or "unknown_role")
    return str(listing.get("role_title") or "unknown_role")


def _extract_ats_type(listing: dict[str, Any] | None) -> str:
    listing = listing or {}
    return str(listing.get("ats_type") or "unknown")


def _extract_fit_overall(fit_score: Any) -> int | None:
    if isinstance(fit_score, dict):
        value = fit_score.get("overall_score")
    else:
        value = fit_score
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _extract_alive_composite(value: Any) -> float | None:
    if isinstance(value, dict):
        candidate = value.get("composite")
    else:
        candidate = value
    if candidate is None:
        return None
    try:
        return float(candidate)
    except (TypeError, ValueError):
        return None


def _submitted_at_from_history(status_history: list[dict[str, Any]]) -> str | None:
    for item in reversed(status_history or []):
        if str(item.get("status", "")).upper() != "SUBMITTED":
            continue
        ts = str(item.get("timestamp", "")).strip()
        if ts:
            return ts
    return None


def _normalize_status_history(history: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).strip().upper()
        ts = str(item.get("timestamp", "")).strip() or _utc_now()
        if not status:
            continue
        result.append({"status": status, "timestamp": ts})
    return result


def _merge_escalations(
    base: list[dict[str, Any]] | None,
    additions: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """
    Merge escalation lists while preventing duplicate logical entries.

    Dedupe key is type + field_id + priority (message text can vary but should not
    create duplicate blockers for the same escalation target).
    """
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in list(base or []) + list(additions or []):
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("type", "")).strip(),
            str(item.get("field_id", "")).strip(),
            str(item.get("priority", "")).strip().upper(),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _append_status(app_record: dict[str, Any], status: str) -> None:
    status = str(status).upper()
    app_record["status"] = status
    app_record.setdefault("status_history", []).append(
        {"status": status, "timestamp": _utc_now()}
    )


def _field_token_map(state_payload: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    fill_plan = state_payload.get("fill_plan")
    if not isinstance(fill_plan, dict):
        return mapping
    for field in fill_plan.get("fields", []) or []:
        if not isinstance(field, dict):
            continue
        field_id = str(field.get("field_id") or "").strip()
        value = str(field.get("value") or "").strip()
        if field_id and _TOKEN_PATTERN.match(value):
            mapping[field_id] = value
    return mapping


def _normalize_token_key(raw_key: str, field_token_map: dict[str, str]) -> str:
    key = str(raw_key or "").strip()
    if not key:
        return ""
    if key in field_token_map:
        return field_token_map[key]
    if _TOKEN_PATTERN.match(key):
        return key
    normalized = key.strip("{} ").upper()
    if not normalized:
        return ""
    return f"{{{{{normalized}}}}}"


def _infer_token_category(state_payload: dict[str, Any], token_key: str) -> str:
    fill_plan = state_payload.get("fill_plan")
    if not isinstance(fill_plan, dict):
        return "LOW"
    for field in fill_plan.get("fields", []) or []:
        if not isinstance(field, dict):
            continue
        value = str(field.get("value") or "").strip()
        if value != token_key:
            continue
        level = str(field.get("pii_level") or "LOW").upper()
        if level in {"LOW", "MEDIUM", "HIGH"}:
            return level
    return "LOW"


def _build_recovery_checkpoint(
    state_payload: dict[str, Any],
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """
    Build a compact recovery checkpoint snapshot for observability and resume.
    """
    listing = state_payload.get("listing") if isinstance(state_payload.get("listing"), dict) else {}
    history = _normalize_status_history(state_payload.get("status_history", []))
    last_history = history[-1] if history else None
    failure_record = state_payload.get("failure_record")
    failure_step = failure_record.get("failure_step") if isinstance(failure_record, dict) else None
    status = str(state_payload.get("status") or "").upper()
    browser_url = (
        state_payload.get("apply_url")
        or listing.get("apply_url")
        or listing.get("source_url")
    )

    return {
        "captured_at": updated_at or _utc_now(),
        "last_status": status or "UNKNOWN",
        "last_status_at": (
            str(last_history.get("timestamp"))
            if isinstance(last_history, dict) and last_history.get("timestamp")
            else (updated_at or _utc_now())
        ),
        "failure_step": str(failure_step) if failure_step else None,
        "browser_url": str(browser_url) if browser_url else None,
        "fields_filled_count": len(state_payload.get("fields_filled", []) or []),
        "has_generated_docs": bool(
            state_payload.get("tailored_resume_final") or state_payload.get("cover_letter_final")
        ),
        "account_status": state_payload.get("account_status"),
        "session_context_id": state_payload.get("session_context_id"),
        "attempt_number": int(state_payload.get("attempt_number", 0) or 0),
    }


def _begin_attempt(
    state_payload: dict[str, Any],
    trigger: str,
    archived_final_status: str | None = None,
) -> dict[str, Any]:
    """
    Start a fresh execution attempt and clear attempt-scoped transient state.

    Historical attempt data is retained as compact summaries in attempt_history.
    """
    payload = copy.deepcopy(state_payload)
    now = _utc_now()

    current_attempt = payload.get("current_attempt")
    if isinstance(current_attempt, dict):
        history = list(payload.get("attempt_history", []))
        failure = payload.get("failure_record")
        escalations = payload.get("human_escalations", []) or []
        history.append(
            {
                "attempt_id": current_attempt.get("attempt_id"),
                "attempt_number": current_attempt.get("attempt_number"),
                "trigger": current_attempt.get("trigger"),
                "started_at": current_attempt.get("started_at"),
                "ended_at": now,
                "final_status": (
                    str(archived_final_status).upper()
                    if archived_final_status
                    else str(payload.get("status", "")).upper() or "UNKNOWN"
                ),
                "failure_error_type": (
                    str(failure.get("error_type"))
                    if isinstance(failure, dict) and failure.get("error_type")
                    else None
                ),
                "escalation_count": len(escalations),
            }
        )
        payload["attempt_history"] = history

    attempt_number = int(payload.get("attempt_number", 0) or 0) + 1
    payload["attempt_number"] = attempt_number
    payload["current_attempt"] = {
        "attempt_id": f"{payload.get('application_id', 'application')}:{attempt_number}",
        "attempt_number": attempt_number,
        "trigger": trigger,
        "started_at": now,
    }

    for key in ATTEMPT_SCOPED_KEYS:
        if key == "human_escalations":
            payload[key] = []
        elif key == "failure_record":
            payload[key] = None
        elif key in {"fields_filled", "post_upload_corrections"}:
            payload[key] = []
        else:
            payload[key] = None

    return payload


async def _resume_from_submission_failure(
    current_state: ApplicationState,
    *,
    failure_step_hint: str | None = None,
    allow_fast_path: bool = True,
) -> dict[str, Any] | None:
    """
    Recovery fast path: when submission itself failed, retry only submit/outcome.
    """
    if not allow_fast_path:
        return None
    if str(current_state.status or "").upper() != "FAILED":
        return None
    failure = current_state.failure_record if isinstance(current_state.failure_record, dict) else {}
    failure_step = str(failure.get("failure_step") or failure_step_hint or "").strip().lower()
    if failure_step != "submission":
        return None
    if not current_state.listing or not current_state.fill_plan:
        return None

    submission_update = await submission_node(current_state)
    post_submit = current_state.model_copy(update=submission_update)
    outcome_update = await record_outcome_node(post_submit)
    return post_submit.model_copy(update=outcome_update).model_dump()


def _save_application_record(app_record: dict[str, Any], db_path: str = OUTCOMES_DB_PATH) -> None:
    state = app_record.get("state", {}) or {}
    listing = state.get("listing") if isinstance(state.get("listing"), dict) else {}
    listing_id = str(app_record.get("listing_id") or state.get("listing_id") or listing.get("listing_id") or "unknown_listing")
    company = str(app_record.get("company") or _extract_company(listing))
    role_title = str(app_record.get("role_title") or _extract_role_title(listing))
    ats_type = str(state.get("ats_type") or _extract_ats_type(listing))
    status = str(app_record.get("status") or state.get("status") or "QUEUED").upper()
    created_at = str(app_record.get("created_at") or state.get("created_at") or _utc_now())
    updated_at = str(app_record.get("updated_at") or _utc_now())
    if isinstance(state, dict):
        state["recovery_checkpoint"] = _build_recovery_checkpoint(state, updated_at=updated_at)
    fit_score = _extract_fit_overall(state.get("fit_score"))
    alive_score = _extract_alive_composite(state.get("alive_score"))
    if alive_score is None and isinstance(listing, dict):
        alive_score = _extract_alive_composite(listing.get("alive_score"))
    submitted_at = (
        str(state.get("submitted_at")).strip()
        if state.get("submitted_at")
        else _submitted_at_from_history(app_record.get("status_history", []))
    )

    with contextlib.closing(_connect_outcomes(db_path=db_path)) as conn:
        with conn:
            existing = conn.execute(
                "SELECT created_at FROM applications WHERE application_id = ?",
                (app_record["application_id"],),
            ).fetchone()
            if existing and existing[0]:
                created_at = str(existing[0])

            conn.execute(
                """
                INSERT OR REPLACE INTO applications (
                    application_id, listing_id, company, role_title, ats_type, fit_score,
                    alive_score, status, resume_version, cover_letter_ver, time_to_apply_s,
                    human_interventions, submitted_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    app_record["application_id"],
                    listing_id,
                    company,
                    role_title,
                    ats_type,
                    fit_score,
                    alive_score,
                    status,
                    state.get("tailored_resume_final"),
                    state.get("cover_letter_final"),
                    state.get("time_to_apply_seconds"),
                    int(state.get("human_interventions") or 0),
                    submitted_at,
                    created_at,
                ),
            )

            normalized_history = _normalize_status_history(app_record.get("status_history", []))
            conn.execute(
                "DELETE FROM status_history WHERE application_id = ?",
                (app_record["application_id"],),
            )
            for item in normalized_history:
                conn.execute(
                    """
                    INSERT INTO status_history (application_id, status, timestamp)
                    VALUES (?, ?, ?)
                    """,
                    (app_record["application_id"], item["status"], item["timestamp"]),
                )

            conn.execute(
                """
                INSERT OR REPLACE INTO application_states (application_id, state_json, updated_at)
                VALUES (?, ?, ?)
                """,
                (
                    app_record["application_id"],
                    json.dumps(state, default=str),
                    updated_at,
                ),
            )


def _load_application_record(app_id: str, db_path: str = OUTCOMES_DB_PATH) -> dict[str, Any] | None:
    with contextlib.closing(_connect_outcomes(db_path=db_path)) as conn:
        app_row = conn.execute(
            """
            SELECT application_id, listing_id, company, role_title, status, created_at
            FROM applications
            WHERE application_id = ?
            """,
            (app_id,),
        ).fetchone()
        if not app_row:
            return None

        state_row = conn.execute(
            """
            SELECT state_json, updated_at
            FROM application_states
            WHERE application_id = ?
            """,
            (app_id,),
        ).fetchone()

        history_rows = conn.execute(
            """
            SELECT status, timestamp
            FROM status_history
            WHERE application_id = ?
            ORDER BY id ASC
            """,
            (app_id,),
        ).fetchall()

    state: dict[str, Any] = {}
    updated_at = _utc_now()
    if state_row:
        try:
            state = json.loads(state_row[0]) if state_row[0] else {}
        except json.JSONDecodeError:
            state = {}
        updated_at = str(state_row[1] or updated_at)

    status_history = [
        {"status": str(row[0]).upper(), "timestamp": str(row[1])}
        for row in history_rows
    ]

    # Keep DB status/history authoritative.
    state["status"] = str(app_row[4]).upper()
    state["status_history"] = status_history

    return {
        "application_id": str(app_row[0]),
        "listing_id": str(app_row[1]),
        "company": str(app_row[2]),
        "role_title": str(app_row[3]),
        "created_at": str(app_row[5]),
        "status": str(app_row[4]).upper(),
        "status_history": status_history,
        "state": state,
        "updated_at": updated_at,
    }


def _ensure_application(app_id: str) -> dict[str, Any]:
    app = _applications.get(app_id)
    if app is not None:
        return app
    loaded = _load_application_record(app_id)
    if loaded is not None:
        _applications[app_id] = loaded
        return loaded
    raise HTTPException(status_code=404, detail=f"Application not found: {app_id}")


def get_application_record(app_id: str) -> dict[str, Any] | None:
    """Shared accessor for websocket route."""
    try:
        return _ensure_application(app_id)
    except HTTPException:
        return None


def reset_applications_state() -> None:
    """Test helper: clear in-memory and persisted applications state."""
    _applications.clear()
    with contextlib.closing(_connect_outcomes()) as conn:
        with conn:
            conn.execute("DELETE FROM status_history")
            conn.execute("DELETE FROM applications")
            conn.execute("DELETE FROM application_states")


class StartApplicationRequest(BaseModel):
    submission_mode: Literal["dry_run", "shadow", "live"] | None = None
    run_now: bool = True
    use_browser_automation: bool | None = None
    headless: bool | None = None
    apply_url: str | None = None
    artifact_paths: dict[str, str] | None = None


class ApproveRequest(BaseModel):
    run_now: bool = False
    submission_mode: Literal["dry_run", "shadow", "live"] | None = None
    use_browser_automation: bool | None = None
    headless: bool | None = None


class EditRequest(BaseModel):
    resume_text: str | None = None
    cover_letter_text: str | None = None
    question_responses: list[dict[str, Any]] | None = None


class EscalationResolveRequest(BaseModel):
    value: Any
    note: str | None = None


class ResumeRequest(BaseModel):
    submission_mode: Literal["dry_run", "shadow", "live"] | None = None
    use_browser_automation: bool | None = None
    headless: bool | None = None
    apply_url: str | None = None
    artifact_paths: dict[str, str] | None = None


class StatusSyncRequest(BaseModel):
    since_days: int = 30
    include_no_response: bool = True
    no_response_days: int = 30
    persist: bool = True
    query: str | None = None


class BlockerResolutionRequest(BaseModel):
    pii_values: dict[str, str] | None = None
    token_categories: dict[str, Literal["LOW", "MEDIUM", "HIGH"]] | None = None
    account_username: str | None = None
    account_password: str | None = None
    salary_expectation: str | int | None = None
    field_values: dict[str, Any] | None = None
    run_now: bool = False
    submission_mode: Literal["dry_run", "shadow", "live"] | None = None
    use_browser_automation: bool | None = None
    headless: bool | None = None
    apply_url: str | None = None
    artifact_paths: dict[str, str] | None = None


async def _execute_attempt_in_background(app_record: dict[str, Any], current_payload: dict[str, Any], can_fast_path: bool = False) -> None:
    app_id = str(app_record.get("application_id", ""))
    if app_id in _executing:
        logger.warning("Skipping duplicate background execution for %s", app_id)
        return
    _executing.add(app_id)
    try:
        current_state = ApplicationState(**current_payload)
        try:
            if can_fast_path:
                submission_update = await submission_node(current_state)
                post_submit = current_state.model_copy(update=submission_update)
                outcome_update = await record_outcome_node(post_submit)
                resumed = post_submit.model_copy(update=outcome_update).model_dump()
            else:
                resumed = await _execute_workflow(current_state)
        except Exception as exc:
            resumed = current_state.model_dump()
            resumed["status"] = "FAILED"
            resumed["failure_record"] = {
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "timestamp": _utc_now(),
            }

        app_record["state"] = resumed
        app_record["status"] = str(resumed.get("status", app_record.get("status", "FAILED"))).upper()
        app_record["status_history"] = _normalize_status_history(resumed.get("status_history", app_record.get("status_history", [])))
        app_record["updated_at"] = _utc_now()

        # Diagnostic: log doc presence in workflow result
        has_resume = bool(resumed.get("tailored_resume_final"))
        has_cover = bool(resumed.get("cover_letter_final"))
        has_resume_tok = bool(resumed.get("tailored_resume_tokenized"))
        has_cover_tok = bool(resumed.get("cover_letter_tokenized"))
        logger.info(
            "Workflow result for %s: status=%s, has_resume_final=%s, has_cover_final=%s, "
            "has_resume_tokenized=%s, has_cover_tokenized=%s, keys_sample=%s",
            app_id, resumed.get("status"), has_resume, has_cover,
            has_resume_tok, has_cover_tok,
            [k for k in resumed.keys() if "resume" in k.lower() or "cover" in k.lower() or "tailor" in k.lower()],
        )

        _applications[app_record["application_id"]] = app_record
        _save_application_record(app_record)
    finally:
        _executing.discard(app_id)


def _settings_fallbacks() -> dict[str, Any]:
    return settings_routes.get_settings_snapshot()


@router.post("/apply/{listing_id}", summary="Start application workflow")
async def start_application(listing_id: str, request: StartApplicationRequest, background_tasks: BackgroundTasks):
    # Check if any existing application for this listing is already executing
    for existing_app in _applications.values():
        if existing_app.get("listing_id") == listing_id and existing_app.get("application_id") in _executing:
            raise HTTPException(
                status_code=409,
                detail="An application workflow is already running for this listing. Please wait for it to complete.",
            )
    persona = persona_routes._current_persona  # noqa: SLF001
    if persona is None:
        raise HTTPException(
            status_code=400,
            detail="No persona available. Upload resume first.",
        )

    listing = get_listing_by_id(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"Listing not found: {listing_id}")

    settings_defaults = _settings_fallbacks()
    submission_mode = request.submission_mode or settings_defaults["default_submission_mode"]
    use_browser_automation = (
        request.use_browser_automation
        if request.use_browser_automation is not None
        else settings_defaults["use_browser_automation"]
    )
    headless = request.headless if request.headless is not None else settings_defaults["headless"]

    state = ApplicationState(
        persona=copy.deepcopy(persona),
        listing=copy.deepcopy(listing),
        submission_mode=submission_mode,
        use_browser_automation=use_browser_automation,
        headless=headless,
        apply_url=request.apply_url,
        artifact_paths=copy.deepcopy(request.artifact_paths) if request.artifact_paths else None,
        humanizer_config={
            "daily_cap": settings_defaults["daily_application_cap"],
            "per_ats_limit": settings_defaults["per_ats_hourly_cap"],
            "per_ats_window_seconds": settings_defaults["cooldown_seconds"],
        },
    )

    state_payload = state.model_dump()
    if request.run_now:
        state_payload = _begin_attempt(state_payload, trigger="start_application")
    app_id = str(state_payload.get("application_id") or state.application_id)
    app_record = {
        "application_id": app_id,
        "listing_id": listing_id,
        "company": _extract_company(listing),
        "role_title": _extract_role_title(listing),
        "created_at": str(state_payload.get("created_at") or _utc_now()),
        "status": str(state_payload.get("status", "QUEUED")).upper(),
        "status_history": _normalize_status_history(state_payload.get("status_history", [])),
        "state": state_payload,
        "updated_at": _utc_now(),
    }
    _applications[app_id] = app_record
    _save_application_record(app_record)

    if request.run_now:
        background_tasks.add_task(
            _execute_attempt_in_background,
            app_record=app_record,
            current_payload=state_payload,
            can_fast_path=False,
        )

    return {
        "status": "success",
        "application_id": app_id,
        "workflow_status": app_record["status"],
    }


@router.get("/apply/{app_id}/status", summary="Get current workflow state")
async def get_application_status(app_id: str):
    app = _ensure_application(app_id)
    state = app["state"]
    return {
        "status": "success",
        "application_id": app_id,
        "workflow_status": app["status"],
        "human_escalations": state.get("human_escalations", []),
        "post_upload_corrections": state.get("post_upload_corrections", []),
        "failure_record": state.get("failure_record"),
        "status_history": app.get("status_history", []),
    }


@router.post("/apply/{app_id}/approve", summary="Approve submission")
async def approve_application(app_id: str, background_tasks: BackgroundTasks, request: ApproveRequest | None = None):
    if app_id in _executing:
        raise HTTPException(
            status_code=409,
            detail="This application is already being processed. Please wait for the current run to complete.",
        )
    request = request or ApproveRequest()
    app = _ensure_application(app_id)
    previous_status = str(app.get("status", "")).upper()
    previous_failure_record = (
        app.get("state", {}).get("failure_record")
        if isinstance(app.get("state"), dict)
        else {}
    )
    previous_failure_step = (
        str(previous_failure_record.get("failure_step", "")).strip().lower()
        if isinstance(previous_failure_record, dict)
        else ""
    )

    _append_status(app, "APPROVED")
    app["state"]["status"] = "APPROVED"

    if request.submission_mode is not None:
        app["state"]["submission_mode"] = request.submission_mode
    if request.use_browser_automation is not None:
        app["state"]["use_browser_automation"] = request.use_browser_automation
    if request.headless is not None:
        app["state"]["headless"] = request.headless

    if request.run_now:
        should_begin_new_attempt = previous_status != "AWAITING_APPROVAL"
        current_payload = dict(app["state"])
        if should_begin_new_attempt:
            current_payload = _begin_attempt(
                current_payload,
                trigger="approve_run_now",
                archived_final_status=previous_status,
            )
            # _begin_attempt resets status, but we explicitly requested approval.
            # Re-apply APPROVED so the workflow bypasses the human_review gate.
            current_payload["status"] = "APPROVED"
        else:
            # Even when not beginning a new attempt (AWAITING_APPROVAL fast-path),
            # clear transient escalations/failures from the shadow run so the
            # submission_node doesn't immediately reject due to stale blockers.
            current_payload["human_escalations"] = []
            current_payload["failure_record"] = None
            current_payload["fields_filled"] = []

        app["state"] = current_payload
        _applications[app_id] = app
        _save_application_record(app)

        can_fast_path_submission = (
            previous_status in ("AWAITING_APPROVAL", "SHADOW_REVIEW", "APPROVED")
        )
        background_tasks.add_task(
            _execute_attempt_in_background,
            app_record=app,
            current_payload=current_payload,
            can_fast_path=can_fast_path_submission,
        )
    else:
        app["status"] = "APPROVED"
        app["status_history"] = _normalize_status_history(app["status_history"])
        app["updated_at"] = _utc_now()
        _save_application_record(app)

    return {"status": "success", "application_id": app_id, "workflow_status": app["status"]}


@router.post("/apply/{app_id}/edit", summary="Edit application artifacts")
async def edit_application(app_id: str, request: EditRequest):
    app = _ensure_application(app_id)
    state = app["state"]
    if request.resume_text is not None:
        state["tailored_resume_tokenized"] = request.resume_text
        state["tailored_resume_final"] = request.resume_text
    if request.cover_letter_text is not None:
        state["cover_letter_tokenized"] = request.cover_letter_text
        state["cover_letter_final"] = request.cover_letter_text
    if request.question_responses is not None:
        state["question_responses"] = request.question_responses

    _append_status(app, "QUEUED")
    state["status"] = "QUEUED"
    app["updated_at"] = _utc_now()
    _save_application_record(app)
    return {"status": "success", "application_id": app_id, "workflow_status": app["status"]}


@router.post("/apply/{app_id}/abort", summary="Abort application")
async def abort_application(app_id: str):
    app = _ensure_application(app_id)
    _append_status(app, "ABORTED")
    app["state"]["status"] = "ABORTED"
    app["updated_at"] = _utc_now()
    _save_application_record(app)
    return {"status": "success", "application_id": app_id, "workflow_status": app["status"]}


@router.post("/apply/{app_id}/resume", summary="Resume from checkpoint/failure")
async def resume_application(app_id: str, background_tasks: BackgroundTasks, request: ResumeRequest | None = None):
    request = request or ResumeRequest()
    app = _ensure_application(app_id)
    previous_failure = app["state"].get("failure_record", {}) if isinstance(app["state"], dict) else {}
    failure_step_hint = (
        str(previous_failure.get("failure_step"))
        if isinstance(previous_failure, dict) and previous_failure.get("failure_step")
        else None
    )

    state_payload = _begin_attempt(dict(app["state"]), trigger="resume")
    if request.submission_mode is not None:
        state_payload["submission_mode"] = request.submission_mode
    if request.use_browser_automation is not None:
        state_payload["use_browser_automation"] = request.use_browser_automation
    if request.headless is not None:
        state_payload["headless"] = request.headless
    if request.apply_url is not None:
        state_payload["apply_url"] = request.apply_url
    if request.artifact_paths is not None:
        state_payload["artifact_paths"] = copy.deepcopy(request.artifact_paths)

    state_payload["recovery_attempts"] = int(state_payload.get("recovery_attempts", 0) or 0) + 1
    state_payload["last_recovery_at"] = _utc_now()
    current_state = ApplicationState(**state_payload)
    try:
        allow_submission_fast_path = (
            request.apply_url is None and request.artifact_paths is None
        )
        recovered = await _resume_from_submission_failure(
            current_state,
            failure_step_hint=failure_step_hint,
            allow_fast_path=allow_submission_fast_path,
        )
        state_payload = recovered if recovered is not None else current_state.model_dump()
    except Exception as exc:
        state_payload = current_state.model_dump()
        state_payload["status"] = "FAILED"
        state_payload["failure_record"] = {
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "timestamp": _utc_now(),
        }

    app["state"] = state_payload
    app["status"] = str(state_payload.get("status", app["status"])).upper()
    app["status_history"] = _normalize_status_history(state_payload.get("status_history", app["status_history"]))
    app["updated_at"] = _utc_now()
    _applications[app_id] = app
    _save_application_record(app)

    background_tasks.add_task(
        _execute_attempt_in_background,
        app_record=app,
        current_payload=state_payload,
        can_fast_path=False,
    )

    return {"status": "success", "application_id": app_id, "workflow_status": app["status"]}


@router.post(
    "/apply/{app_id}/escalation/{field_id}/resolve",
    summary="Resolve a human escalation",
)
async def resolve_escalation(app_id: str, field_id: str, request: EscalationResolveRequest):
    app = _ensure_application(app_id)
    state = app["state"]
    escalations = list(state.get("human_escalations", []))

    unresolved: list[dict[str, Any]] = []
    resolved_count = 0
    for item in escalations:
        if str(item.get("field_id", "")) == field_id:
            resolved_count += 1
            continue
        unresolved.append(item)

    if resolved_count == 0:
        raise HTTPException(status_code=404, detail=f"Escalation field not found: {field_id}")

    # If no non-preflight blocking escalations remain, remove stale preflight markers.
    blocking_non_preflight = [
        item
        for item in unresolved
        if str(item.get("priority", "")).upper() == "BLOCKING"
        and str(item.get("field_id", "")) != "__preflight__"
    ]
    if not blocking_non_preflight:
        unresolved = [
            item for item in unresolved if str(item.get("field_id", "")) != "__preflight__"
        ]

    state["human_escalations"] = unresolved
    state.setdefault("escalation_resolutions", []).append(
        {
            "field_id": field_id,
            "value": request.value,
            "note": request.note,
            "resolved_at": _utc_now(),
        }
    )

    # Apply resolved value directly into fill plan when field exists.
    fill_plan = state.get("fill_plan")
    if isinstance(fill_plan, dict):
        for field in fill_plan.get("fields", []) or []:
            if not isinstance(field, dict):
                continue
            if str(field.get("field_id", "")) == field_id:
                field["value"] = request.value
                break

    app["updated_at"] = _utc_now()
    _save_application_record(app)
    return {
        "status": "success",
        "application_id": app_id,
        "resolved_field_id": field_id,
        "remaining_escalations": len(unresolved),
    }


@router.post(
    "/apply/{app_id}/blockers/resolve",
    summary="Resolve account/PII/form blockers in bulk",
)
async def resolve_blockers(app_id: str, request: BlockerResolutionRequest):
    app = _ensure_application(app_id)
    state = app["state"]

    resolved: list[str] = []
    warnings: list[str] = []

    field_values = dict(request.field_values or {})
    if request.salary_expectation is not None:
        field_values["salary_expectation"] = request.salary_expectation
    if request.apply_url is not None:
        state["apply_url"] = request.apply_url
    if request.artifact_paths is not None:
        state["artifact_paths"] = copy.deepcopy(request.artifact_paths)

    # Apply direct field overrides to fill plan.
    fill_plan = state.get("fill_plan")
    if field_values and isinstance(fill_plan, dict):
        for field in fill_plan.get("fields", []) or []:
            if not isinstance(field, dict):
                continue
            field_id = str(field.get("field_id", "")).strip()
            if field_id in field_values:
                field["value"] = field_values[field_id]
                resolved.append(f"field:{field_id}")

    # Persist account credentials for next run.
    account_resolved = False
    if request.account_username and request.account_password:
        creds = dict(state.get("account_credentials") or {})
        creds["username"] = request.account_username
        creds["password"] = request.account_password
        state["account_credentials"] = creds

        try:
            listing = state.get("listing") if isinstance(state.get("listing"), dict) else {}
            company = _extract_company(listing)
            ats_type = _extract_ats_type(listing)
            AccountVault().store_account(
                company=company,
                ats_type=ats_type,
                username=request.account_username,
                password=request.account_password,
                status="active",
            )
            resolved.append("account:stored")
            account_resolved = True
        except Exception as exc:
            warnings.append(f"Failed to persist account credentials in vault: {exc}")
    elif request.account_username or request.account_password:
        warnings.append("Both account_username and account_password are required to resolve account blocker.")

    # Persist PII values into vault.
    resolved_tokens: set[str] = set()
    resolved_pii_fields: set[str] = set()
    pii_values = request.pii_values or {}
    if pii_values:
        token_map = _field_token_map(state)
        token_to_fields: dict[str, set[str]] = {}
        for candidate_field_id, token_key in token_map.items():
            token_to_fields.setdefault(token_key, set()).add(str(candidate_field_id).strip().lower())
        category_overrides = request.token_categories or {}
        normalized_categories: dict[str, str] = {}
        for key, category in category_overrides.items():
            token_key = _normalize_token_key(key, token_map)
            if token_key:
                normalized_categories[token_key] = str(category).upper()

        try:
            vault = PIIVault()
            for raw_key, raw_value in pii_values.items():
                value = str(raw_value or "").strip()
                if not value:
                    continue
                raw_key_normalized = str(raw_key or "").strip().lower()
                if raw_key_normalized and not _TOKEN_PATTERN.match(raw_key_normalized):
                    resolved_pii_fields.add(raw_key_normalized)
                token_key = _normalize_token_key(raw_key, token_map)
                if not token_key:
                    continue
                category = normalized_categories.get(token_key) or _infer_token_category(state, token_key)
                vault.store_token(token_key=token_key, value=value, category=category)
                resolved_tokens.add(token_key)
                resolved_pii_fields.update(token_to_fields.get(token_key, set()))
                resolved.append(f"token:{token_key}")
        except Exception as exc:
            warnings.append(f"Failed to persist PII values: {exc}")

    # Recompute escalations after updates.
    escalations = list(state.get("human_escalations", []))
    token_map = _field_token_map(state)
    token_presence: dict[str, bool] = {}
    if token_map:
        try:
            vault = PIIVault()
            for token in set(token_map.values()):
                token_presence[token] = bool(vault.get_token(token))
        except Exception:
            token_presence = {}

    filtered: list[dict[str, Any]] = []
    had_preflight = False
    for item in escalations:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        field_id = str(item.get("field_id", ""))
        normalized_field_id = field_id.strip().lower()
        remove = False

        if item_type == "submitter" and field_id == "__preflight__":
            had_preflight = True
            continue
        if item_type == "account_manager" and account_resolved:
            remove = True
        if field_id and field_id in field_values:
            remove = True
        if item_type == "pii_injector" and field_id:
            if normalized_field_id in resolved_pii_fields:
                remove = True
            token = token_map.get(field_id)
            if token and (token in resolved_tokens or token_presence.get(token, False)):
                remove = True

        if remove:
            resolved.append(f"escalation:{item_type}:{field_id or 'global'}")
            continue
        filtered.append(item)

    # Restore preflight marker only if blocking escalations remain.
    blocking_remaining = [
        item for item in filtered if str(item.get("priority", "")).upper() == "BLOCKING"
    ]
    if blocking_remaining:
        filtered = _merge_escalations(
            filtered,
            [
                {
                    "type": "submitter",
                    "field_id": "__preflight__",
                    "priority": "BLOCKING",
                    "message": "Browser execution skipped until BLOCKING escalations are resolved.",
                }
            ],
        )
    elif had_preflight:
        resolved.append("escalation:submitter:__preflight__")

    state["human_escalations"] = filtered
    app["updated_at"] = _utc_now()
    _save_application_record(app)

    if request.run_now:
        current_status = str(app.get("status") or "").upper()
        if current_status == "AWAITING_APPROVAL":
            execution_payload = await approve_application(
                app_id=app_id,
                request=ApproveRequest(
                    run_now=True,
                    submission_mode=request.submission_mode,
                    use_browser_automation=request.use_browser_automation,
                    headless=request.headless,
                ),
            )
        else:
            execution_payload = await resume_application(
                app_id=app_id,
                request=ResumeRequest(
                    submission_mode=request.submission_mode,
                    use_browser_automation=request.use_browser_automation,
                    headless=request.headless,
                    apply_url=request.apply_url,
                    artifact_paths=request.artifact_paths,
                ),
            )
        refreshed = _ensure_application(app_id)
        return {
            "status": "success",
            "application_id": app_id,
            "workflow_status": execution_payload.get("workflow_status"),
            "resolved": sorted(set(resolved)),
            "warnings": warnings,
            "remaining_escalations": len(refreshed["state"].get("human_escalations", [])),
            "remaining_blocking_escalations": len(
                [
                    item
                    for item in refreshed["state"].get("human_escalations", [])
                    if str(item.get("priority", "")).upper() == "BLOCKING"
                ]
            ),
        }

    return {
        "status": "success",
        "application_id": app_id,
        "resolved": sorted(set(resolved)),
        "warnings": warnings,
        "remaining_escalations": len(filtered),
        "remaining_blocking_escalations": len(blocking_remaining),
    }


def _load_all_application_rows(db_path: str = OUTCOMES_DB_PATH) -> list[dict[str, Any]]:
    with contextlib.closing(_connect_outcomes(db_path=db_path)) as conn:
        rows = conn.execute(
            """
            SELECT application_id, listing_id, company, role_title, status, created_at
            FROM applications
            ORDER BY created_at DESC
            """
        ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "application_id": str(row[0]),
                "listing_id": str(row[1]),
                "company": str(row[2]),
                "role_title": str(row[3]),
                "status": str(row[4]).upper(),
                "created_at": str(row[5]),
            }
        )
    return result


@router.post("/applications/status-sync", summary="Scan inbox and apply status updates")
async def sync_application_statuses(request: StatusSyncRequest | None = Body(default=None)):
    request = request or StatusSyncRequest()
    rows = _load_all_application_rows()
    if not rows:
        return {"status": "success", "scanned": 0, "updates": []}

    records: list[dict[str, Any]] = []
    for row in rows:
        app = _ensure_application(row["application_id"])
        records.append(
            {
                "application_id": row["application_id"],
                "listing_id": row["listing_id"],
                "company": row["company"],
                "role_title": row["role_title"],
                "status": row["status"],
                "submitted_at": app["state"].get("submitted_at"),
                "status_history": app.get("status_history", []),
            }
        )

    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(request.since_days)))
    updates = track_status_updates(
        applications=records,
        since=since,
        query=request.query,
        include_no_response=request.include_no_response,
        no_response_days=request.no_response_days,
        persist=request.persist,
        outcomes_db_path=OUTCOMES_DB_PATH,
    )

    # Refresh cache for updated records.
    touched_ids = {str(item.get("application_id", "")) for item in updates if item.get("application_id")}
    for app_id in touched_ids:
        loaded = _load_application_record(app_id)
        if loaded:
            _applications[app_id] = loaded

    return {
        "status": "success",
        "scanned": len(records),
        "updates": updates,
    }


@router.get("/applications", summary="List all applications")
async def list_applications():
    rows = _load_all_application_rows()
    for row in rows:
        cached = _applications.get(row["application_id"])
        if cached:
            row["updated_at"] = cached.get("updated_at")
        else:
            loaded = _load_application_record(row["application_id"])
            row["updated_at"] = loaded.get("updated_at") if loaded else None
    return {"status": "success", "count": len(rows), "applications": rows}

@router.get("/applications/{app_id}", summary="Get full application detail")
async def get_application_detail(app_id: str):
    app = _ensure_application(app_id)
    state = app.get("state", {})
    has_resume = bool(state.get("tailored_resume_final") or state.get("tailored_resume_tokenized"))
    has_cover = bool(state.get("cover_letter_final") or state.get("cover_letter_tokenized"))
    logger.info(
        "Detail fetch for %s — has_resume=%s, has_cover=%s, status=%s",
        app_id, has_resume, has_cover, app.get("status"),
    )
    return {
        "status": "success",
        "application": app,
    }
