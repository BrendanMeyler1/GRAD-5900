"""
LangGraph workflow orchestration for job_finder.

Phase 2 Step 8 wiring:
- Fit scorer integration
- Resume tailor + cover letter integration
- Form interpreter + question responder integration
- Account manager + post-upload validator integration
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from errors import AccountError
from feedback.failures_store import FailureStore
from graph.state import ApplicationState

logger = logging.getLogger("job_finder.graph.workflow")


def route_by_fit_score(state: ApplicationState) -> str:
    """Route based on fit score threshold (Appendix C.3)."""
    if state.status == "APPROVED" or state.submission_mode in ("shadow", "live"):
        return "apply"
    if state.fit_score and state.fit_score.get("overall_score", 0) >= 50:
        return "apply"
    return "skip"


def route_by_approval(state: ApplicationState) -> str:
    """Route based on human review decision (Appendix C.3)."""
    if state.status == "APPROVED":
        return "approved"
    if state.status == "ABORTED":
        return "aborted"
    if state.status == "AWAITING_APPROVAL":
        return "await"
    return "edit"


def route_by_mode(state: ApplicationState) -> str:
    """Route based on submission mode + failure state (Appendix C.3)."""
    if state.status == "FAILED":
        return "failed"
    if state.status == "SHADOW_REVIEW" or state.submission_mode == "shadow":
        return "shadow"
    return "live"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _add_status(state: ApplicationState, new_status: str) -> dict[str, str]:
    return {"status": new_status, "timestamp": _utc_now()}


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _compute_time_to_apply_seconds(state: ApplicationState) -> int | None:
    """
    Compute time-to-apply from current attempt start when available.
    """
    existing = state.time_to_apply_seconds
    if existing is not None:
        try:
            value = int(existing)
            if value >= 0:
                return value
        except (TypeError, ValueError):
            pass

    started_at = None
    current_attempt = _state_value(state, "current_attempt")
    if isinstance(current_attempt, dict):
        started_at = _parse_datetime(current_attempt.get("started_at"))
    if started_at is None:
        started_at = _parse_datetime(state.created_at)
    if started_at is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - started_at.astimezone(timezone.utc)).total_seconds()))


def _company_name(listing: dict[str, Any] | None) -> str:
    if not listing:
        return "unknown_company"
    company = listing.get("company", {})
    if isinstance(company, dict) and company.get("name"):
        return str(company["name"])
    return str(listing.get("company") or "unknown_company")


def _role_title(listing: dict[str, Any] | None) -> str:
    listing = listing or {}
    role = listing.get("role", {})
    if isinstance(role, dict) and role.get("title"):
        return str(role["title"])
    return str(listing.get("role_title") or "Role")


def _clamp_int_score(value: Any, default: int = 0) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = float(default)
    return int(max(0, min(100, round(numeric))))


def _fallback_fit_score(state: ApplicationState) -> dict[str, Any]:
    listing = state.listing or {}
    persona = state.persona or {}
    hint = None
    if isinstance(listing, dict):
        hint = listing.get("fit_hint_score")
    overall = _clamp_int_score(hint, default=60)
    recommendation = "APPLY" if overall >= 75 else ("MAYBE" if overall >= 50 else "SKIP")
    role_title = _role_title(listing)
    company = _company_name(listing)
    summary = str(persona.get("summary") or "").strip()

    talking_points = []
    if summary:
        talking_points.append(summary[:180])
    talking_points.append(f"Fallback fit estimate for {role_title} at {company}.")

    return {
        "overall_score": overall,
        "breakdown": {
            "skills_match": overall,
            "experience_level": overall,
            "domain_relevance": overall,
            "culture_signals": overall,
            "location_match": overall,
        },
        "gaps": [],
        "strengths": [],
        "talking_points": talking_points,
        "recommendation": recommendation,
        "fallback_used": True,
    }


def _fallback_resume_text(state: ApplicationState) -> str:
    persona = state.persona or {}
    listing = state.listing or {}
    contact = persona.get("contact_info", {}) if isinstance(persona.get("contact_info"), dict) else {}
    name = contact.get("name") or "{{FULL_NAME}}"
    email = contact.get("email") or "{{EMAIL}}"
    phone = contact.get("phone") or "{{PHONE}}"
    linkedin = contact.get("linkedin") or "{{LINKEDIN}}"
    github = contact.get("github") or "{{GITHUB}}"
    location = contact.get("location") or "Remote, US"
    role_title = _role_title(listing)
    summary = str(persona.get("summary") or "").strip() or "Experienced engineer with strong backend and distributed systems skills."

    languages = []
    skills = persona.get("skills")
    if isinstance(skills, dict):
        raw_langs = skills.get("languages") or []
        languages = [str(item) for item in raw_langs if str(item).strip()]

    lines = [
        f"# {name}",
        f"**{role_title}**  ",
        f"{email} | {phone} | {linkedin} | {github}  ",
        f"{location}",
        "",
        "## Professional Summary",
        summary,
        "",
        "## Technical Skills",
        f"**Languages:** {', '.join(languages) if languages else 'Python, SQL'}",
        "",
        "## Experience",
    ]

    experiences = persona.get("experience") if isinstance(persona.get("experience"), list) else []
    if experiences:
        for exp in experiences[:2]:
            if not isinstance(exp, dict):
                continue
            employer = str(exp.get("employer") or "{{EMPLOYER}}")
            title = str(exp.get("title") or "Engineer")
            start_date = str(exp.get("start_date") or "")
            end_date = str(exp.get("end_date") or "Present")
            lines.append(f"**{title}** | {employer} | {start_date} - {end_date}")
            bullets = exp.get("bullets") if isinstance(exp.get("bullets"), list) else []
            if not bullets:
                bullets = exp.get("achievements") if isinstance(exp.get("achievements"), list) else []
            for bullet in bullets[:4]:
                lines.append(f"- {str(bullet)}")
            lines.append("")
    else:
        lines.extend(
            [
                "- Built and operated backend services with reliability and performance focus.",
                "- Collaborated with cross-functional teams to deliver customer-facing features.",
                "",
            ]
        )

    return "\n".join(lines).strip()


def _fallback_cover_letter_text(state: ApplicationState) -> str:
    listing = state.listing or {}
    persona = state.persona or {}
    company = _company_name(listing)
    role_title = _role_title(listing)
    summary = str(persona.get("summary") or "").strip()
    talking_points = []
    fit_score = state.fit_score or {}
    if isinstance(fit_score, dict):
        raw_points = fit_score.get("talking_points") or []
        talking_points = [str(item) for item in raw_points if str(item).strip()]

    lead_point = talking_points[0] if talking_points else "My background aligns well with your role requirements."
    close = "I would welcome the chance to discuss how I can contribute to your team."

    lines = [
        f"Dear {company} Hiring Team,",
        "",
        f"I am excited to apply for the {role_title} position.",
    ]
    if summary:
        lines.append(summary)
    lines.extend(
        [
            "",
            lead_point,
            "",
            close,
            "",
            "Best regards,",
            "{{FULL_NAME}}",
        ]
    )
    return "\n".join(lines).strip()


def _ensure_submission_artifact_paths(state: ApplicationState) -> dict[str, str] | None:
    """
    Ensure uploadable resume/cover-letter files exist for browser submissions.

    If caller did not provide artifact paths, materialize text artifacts so
    file-upload actions do not fail with missing-file errors.
    """
    existing = _state_value(state, "artifact_paths", {})
    artifact_paths: dict[str, str] = dict(existing) if isinstance(existing, dict) else {}
    app_id = str(state.application_id or "application")
    generated_dir = Path("data/processed/generated")

    def _resolve_or_write(key: str, suffix: str, content: str | None, fallback_func) -> None:
        candidate = artifact_paths.get(key)
        if candidate:
            candidate_path = Path(str(candidate))
            if candidate_path.exists() and candidate_path.is_file():
                artifact_paths[key] = str(candidate_path.resolve())
                return
        if not content:
            content = fallback_func(state)
            
        try:
            from utils.md2docx import markdown_to_docx
            generated_dir.mkdir(parents=True, exist_ok=True)
            output_path = (generated_dir / f"{app_id}_{suffix}.docx").resolve()
            markdown_to_docx(str(content), output_path)
            artifact_paths[key] = str(output_path)
        except Exception as e:
            logger.warning(f"Failed to generate docx for {key}: {e}")
            # Fallback to plain txt if docx generation fails
            generated_dir.mkdir(parents=True, exist_ok=True)
            output_path = (generated_dir / f"{app_id}_{suffix}.txt").resolve()
            output_path.write_text(str(content), encoding="utf-8")
            artifact_paths[key] = str(output_path)

    _resolve_or_write(
        "resume",
        "resume",
        state.tailored_resume_final or state.tailored_resume_tokenized,
        _fallback_resume_text,
    )
    _resolve_or_write(
        "cover_letter",
        "cover_letter",
        state.cover_letter_final or state.cover_letter_tokenized,
        _fallback_cover_letter_text,
    )

    if artifact_paths.get("resume") and "resume_upload" not in artifact_paths:
        artifact_paths["resume_upload"] = artifact_paths["resume"]
    if artifact_paths.get("cover_letter") and "cover_letter_upload" not in artifact_paths:
        artifact_paths["cover_letter_upload"] = artifact_paths["cover_letter"]

    return artifact_paths or None


def _merge_escalations(
    existing: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen = set()
    merged: list[dict[str, Any]] = []
    for item in [*(existing or []), *(new_items or [])]:
        key = (
            item.get("type"),
            item.get("field_id"),
            item.get("priority"),
            item.get("message") or item.get("reason"),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _has_blocking_escalations(items: list[dict[str, Any]] | None) -> bool:
    for item in items or []:
        priority = str(item.get("priority") or "").strip().upper()
        if priority == "BLOCKING":
            return True
    return False


def _state_value(state: ApplicationState, key: str, default: Any = None) -> Any:
    """Read normal or extra pydantic fields safely."""
    try:
        return getattr(state, key)
    except AttributeError:
        return default


def _apply_replay_selector_hints(
    state: ApplicationState,
    fill_plan: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """
    Apply replay-derived selector hints for repeat ATS applications.

    Current Phase 3 target:
    - Greenhouse selector reuse from most recent generalized trace.
    """
    listing = state.listing or {}
    ats_type = str(listing.get("ats_type") or fill_plan.get("ats_type") or "").lower()
    if ats_type != "greenhouse":
        return fill_plan, None

    try:
        from replay.generalizer import ReplayGeneralizer

        company = _company_name(state.listing)
        generalizer = ReplayGeneralizer()
        refs = list(generalizer.company_store.get_replay_refs(company_name=company))
        if not refs:
            return fill_plan, None

        applied_trace_id = None
        generalized = None
        for trace_id in reversed(refs):
            try:
                candidate = generalizer.load_generalized_trace(trace_id)
            except Exception:
                continue
            if str(candidate.get("ats_type", "")).lower() != ats_type:
                continue
            generalized = candidate
            applied_trace_id = trace_id
            break

        if generalized is None:
            return fill_plan, None

        selector_map: dict[str, str] = {}
        for item in generalized.get("descriptors", []) or []:
            if not isinstance(item, dict):
                continue
            field_id = str(item.get("field_id", "")).strip()
            selector = str(item.get("selector_that_worked", "")).strip()
            if field_id and selector:
                selector_map[field_id] = selector

        if not selector_map:
            return fill_plan, applied_trace_id

        updated = dict(fill_plan)
        fields = []
        for field in fill_plan.get("fields", []) or []:
            if not isinstance(field, dict):
                continue
            row = dict(field)
            field_id = str(row.get("field_id", "")).strip()
            replay_selector = selector_map.get(field_id)
            confidence = float(row.get("confidence", 0.0) or 0.0)
            if replay_selector and (not row.get("selector") or confidence < 0.8):
                row["selector"] = replay_selector
                row["selector_strategy"] = "replay_semantic"
                row["source"] = "replay_trace"
                row["confidence"] = max(confidence, 0.82)
                row["explanation"] = "Selector reused from generalized replay trace."
            fields.append(row)
        updated["fields"] = fields
        if applied_trace_id:
            updated["replay_trace_used"] = applied_trace_id
        return updated, applied_trace_id
    except Exception:
        logger.warning("Replay selector hinting unavailable.", exc_info=True)
        return fill_plan, None


def _record_failure(
    state: ApplicationState,
    step: str,
    error: Exception,
    field_name: str | None = None,
) -> dict[str, Any]:
    """Persist structured failure and return failure_record payload."""
    listing = state.listing or {}
    ats_type = str(listing.get("ats_type") or "unknown")
    company = _company_name(state.listing)

    store = FailureStore()
    failure_id = store.log_failure(
        application_id=state.application_id,
        ats_type=ats_type,
        company=company,
        failure_step=step,
        error_type=type(error).__name__,
        error_message=str(error),
        field_name=field_name,
    )
    return {
        "failure_id": failure_id,
        "application_id": state.application_id,
        "ats_type": ats_type,
        "company": company,
        "failure_step": step,
        "error_type": type(error).__name__,
        "field_name": field_name,
        "error_message": str(error),
        "timestamp": _utc_now(),
    }


async def evaluate_fit_node(state: ApplicationState) -> dict[str, Any]:
    """Evaluate candidate-job fit via Fit Scorer."""
    logger.info("[%s] Evaluating fit...", state.application_id)
    try:
        if not state.persona or not state.listing:
            return {
                "fit_score": {
                    "overall_score": 0,
                    "breakdown": {},
                    "gaps": [
                        {
                            "requirement": "missing_input",
                            "severity": "major",
                            "mitigation": "Provide persona and listing before scoring.",
                        }
                    ],
                    "strengths": [],
                    "talking_points": [],
                    "recommendation": "SKIP",
                },
                "status_history": state.status_history + [_add_status(state, "EVALUATING")],
            }

        from agents.fit_scorer import score_fit

        fit_score = score_fit(persona=state.persona, listing=state.listing)
        return {
            "fit_score": fit_score,
            "status_history": state.status_history + [_add_status(state, "EVALUATING")],
        }
    except Exception as exc:
        logger.error("Fit scoring failed: %s", exc, exc_info=True)
        failure = _record_failure(state=state, step="evaluate_fit", error=exc)
        escalation = {
            "type": "fit_scorer",
            "priority": "IMPORTANT",
            "message": (
                f"Primary fit scoring failed ({type(exc).__name__}). "
                "Used deterministic fallback fit score."
            ),
            "failure_id": failure.get("failure_id"),
        }
        return {
            "fit_score": _fallback_fit_score(state),
            "fit_fallback_used": True,
            "last_fit_failure": failure,
            "human_escalations": _merge_escalations(state.human_escalations, [escalation]),
            "status_history": state.status_history + [_add_status(state, "EVALUATING")],
        }


async def generate_documents_node(state: ApplicationState) -> dict[str, Any]:
    """Generate tailored resume and cover letter with Phase 2 agents."""
    logger.info("[%s] Generating tailored docs...", state.application_id)
    try:
        if not state.persona or not state.listing:
            raise ValueError("Missing persona/listing for document generation.")

        from agents.cover_letter import generate_cover_letter
        from agents.resume_tailor import tailor_resume

        tailored_resume = tailor_resume(
            persona=state.persona,
            listing=state.listing,
            fit_score=state.fit_score or {},
        )
        cover_letter = generate_cover_letter(
            persona=state.persona,
            listing=state.listing,
            fit_score=state.fit_score or {},
        )

        result = {
            "tailored_resume_tokenized": tailored_resume.get("resume_text"),
            "cover_letter_tokenized": cover_letter.get("cover_letter_text"),
            "status_history": state.status_history + [_add_status(state, "GENERATING_DOCS")],
        }
        logger.info(
            "[%s] Doc generation output: resume_len=%s, cover_len=%s",
            state.application_id,
            len(result["tailored_resume_tokenized"] or ""),
            len(result["cover_letter_tokenized"] or ""),
        )
        return result
    except Exception as exc:
        logger.error("Document generation failed: %s", exc, exc_info=True)
        failure = _record_failure(state=state, step="generate_documents", error=exc)
        fallback_resume = state.tailored_resume_tokenized or _fallback_resume_text(state)
        fallback_cover = state.cover_letter_tokenized or _fallback_cover_letter_text(state)
        escalation = {
            "type": "document_generator",
            "priority": "IMPORTANT",
            "message": (
                f"Primary document generation failed ({type(exc).__name__}). "
                "Used deterministic fallback documents."
            ),
            "failure_id": failure.get("failure_id"),
        }
        return {
            "tailored_resume_tokenized": fallback_resume,
            "cover_letter_tokenized": fallback_cover,
            "document_fallback_used": True,
            "last_document_failure": failure,
            "human_escalations": _merge_escalations(state.human_escalations, [escalation]),
            "status_history": state.status_history + [_add_status(state, "GENERATING_DOCS")],
        }


async def interpret_form_node(state: ApplicationState) -> dict[str, Any]:
    """Interpret ATS form and build fill plan."""
    logger.info("[%s] Interpreting form...", state.application_id)
    try:
        if not state.listing:
            raise ValueError("Missing listing for form interpretation.")

        from agents.form_interpreter import interpret_form
        from agents.question_responder import generate_question_response
        from llm_router.router import LLMRouter

        listing = dict(state.listing)
        form_html = str(
            listing.get("form_html")
            or _state_value(state, "form_html", "")
            or ""
        )
        fill_plan = interpret_form(
            listing=listing,
            form_html=form_html,
            persona=state.persona,
            allow_llm_assist=True,
            router=LLMRouter(),
        )
        fill_plan, replay_trace_id = _apply_replay_selector_hints(state=state, fill_plan=fill_plan)

        question_responses = list(state.question_responses or [])
        for field in fill_plan.get("fields", []):
            if not isinstance(field, dict):
                continue
            if not field.get("requires_question_responder"):
                continue
            response = generate_question_response(
                listing=listing,
                field_id=str(field.get("field_id", "")),
                question_text=str(field.get("label", "")),
                persona=state.persona or {},
                fit_score=state.fit_score or {},
            )
            question_responses.append(response)
            field["value"] = response.get("response_text", field.get("value"))
            field["question_response_id"] = response.get("question_id")

        new_escalations = [
            {
                "type": "form_interpreter",
                "field_id": item.get("field_id"),
                "priority": item.get("priority", "IMPORTANT"),
                "message": item.get("reason", "Form interpretation escalation"),
                "label": item.get("label"),
            }
            for item in fill_plan.get("escalations", [])
        ]

        return {
            "fill_plan": fill_plan,
            "replay_trace_id": replay_trace_id or state.replay_trace_id,
            "question_responses": question_responses,
            "human_escalations": _merge_escalations(state.human_escalations, new_escalations),
            "status_history": state.status_history + [_add_status(state, "INTERPRETING_FORM")],
        }
    except Exception as exc:
        logger.error("Form interpretation failed: %s", exc, exc_info=True)
        return {
            "status": "FAILED",
            "failure_record": _record_failure(state=state, step="interpret_form", error=exc),
            "status_history": state.status_history + [_add_status(state, "FAILED")],
        }


async def inject_pii_node(state: ApplicationState) -> dict[str, Any]:
    """
    Inject PII into tokenized docs.

    Phase 2.5 wiring:
    - resolve resume/cover/fill-plan tokens locally via PII Injector
    - surface blocked HIGH-sensitivity fields as human escalations
    """
    logger.info("[%s] Injecting PII...", state.application_id)
    logger.info(
        "[%s] inject_pii_node input: resume_tokenized_len=%s, cover_tokenized_len=%s",
        state.application_id,
        len(state.tailored_resume_tokenized or ""),
        len(state.cover_letter_tokenized or ""),
    )
    try:
        from agents.pii_injector import inject_application_artifacts

        injected = inject_application_artifacts(
            tailored_resume_tokenized=state.tailored_resume_tokenized,
            cover_letter_tokenized=state.cover_letter_tokenized,
            fill_plan=state.fill_plan or {"fields": [], "escalations": []},
            allow_high_sensitivity=False,
            use_local_llm=False,
        )
        escalation_items: list[dict[str, Any]] = []
        for field_id in injected.get("blocked_fields", []):
            escalation_items.append(
                {
                    "type": "pii_injector",
                    "field_id": field_id,
                    "priority": "BLOCKING",
                    "message": "HIGH sensitivity field requires manual approval.",
                }
            )
        for field_id in injected.get("unresolved_fields", []):
            escalation_items.append(
                {
                    "type": "pii_injector",
                    "field_id": field_id,
                    "priority": "IMPORTANT",
                    "message": "PII token unresolved in local vault.",
                }
            )

        return {
            "tailored_resume_final": injected.get("tailored_resume_final"),
            "cover_letter_final": injected.get("cover_letter_final"),
            "fill_plan": injected.get("fill_plan_final", state.fill_plan),
            "human_escalations": _merge_escalations(state.human_escalations, escalation_items),
            "status_history": state.status_history + [_add_status(state, "PII_INJECTED")],
        }
    except Exception as exc:
        logger.warning(
            "PII injector unavailable, falling back to tokenized artifacts: %s",
            exc,
        )
        return {
            "tailored_resume_final": state.tailored_resume_tokenized,
            "cover_letter_final": state.cover_letter_tokenized,
            "status_history": state.status_history + [_add_status(state, "PII_INJECTED")],
        }


async def fill_form_node(state: ApplicationState) -> dict[str, Any]:
    """
    Fill ATS form.

    Phase 2.5 behavior:
    - default: simulated fill for safe local/test execution
    - optional browser automation path when `use_browser_automation=True`
      (runs submitter in shadow mode and stores replay traces)
    """
    logger.info("[%s] Filling form...", state.application_id)
    listing = state.listing or {}
    company = _company_name(state.listing)
    ats_type = str(listing.get("ats_type") or "unknown")

    account_status = state.account_status
    session_context_id = state.session_context_id
    new_escalations: list[dict[str, Any]] = []

    try:
        from agents.account_manager import manage_account

        creds = _state_value(state, "account_credentials", {}) or {}
        account_result = manage_account(
            company=company,
            ats_type=ats_type,
            username=creds.get("username"),
            password=creds.get("password"),
            session_cookies=creds.get("session_cookies"),
            browser_context=creds.get("browser_context"),
            signals=_state_value(state, "account_signals", {}),
            allow_llm_assist=False,
        )
        account_status = account_result.get("account_status", account_status)
        session_context_id = account_result.get("session_context_id", session_context_id)
        if account_result.get("requires_human"):
            new_escalations.append(
                {
                    "type": "account_manager",
                    "priority": "BLOCKING",
                    "message": account_result.get("reason", "Account flow needs human review."),
                }
            )
    except AccountError as exc:
        account_status = "failed"
        priority = "BLOCKING" if state.submission_mode == "live" else "IMPORTANT"
        new_escalations.append(
            {
                "type": "account_manager",
                "priority": priority,
                "message": str(exc),
            }
        )
    except Exception as exc:
        logger.error("Account manager integration failed: %s", exc, exc_info=True)
        account_status = account_status or "failed"
        new_escalations.append(
            {
                "type": "account_manager",
                "priority": "IMPORTANT",
                "message": "Account manager call failed; continuing with simulated fill.",
            }
        )

    fields_filled: list[dict[str, Any]] = []
    for field in (state.fill_plan or {}).get("fields", []):
        if not isinstance(field, dict):
            continue
        if str(field.get("type", "")).lower() == "file_upload":
            continue
        fields_filled.append(
            {
                "field_id": field.get("field_id"),
                "label": field.get("label"),
                "value": field.get("value"),
                "selector": field.get("selector"),
                "selector_strategy": field.get("selector_strategy"),
                "confidence": field.get("confidence"),
            }
        )

    replay_trace_id = state.replay_trace_id
    screenshot_path = None
    time_to_apply_seconds = state.time_to_apply_seconds
    artifact_paths = _ensure_submission_artifact_paths(state)
    use_browser_automation = bool(_state_value(state, "use_browser_automation", False))
    merged_escalations = _merge_escalations(state.human_escalations, new_escalations)
    if use_browser_automation and _has_blocking_escalations(merged_escalations):
        merged_escalations = _merge_escalations(
            merged_escalations,
            [
                {
                    "type": "submitter",
                    "field_id": "__preflight__",
                    "priority": "BLOCKING",
                    "message": (
                        "Browser execution skipped until BLOCKING escalations are resolved."
                    ),
                }
            ],
        )
        return {
            "status": "FILLING",
            "account_status": account_status,
            "session_context_id": session_context_id,
            "artifact_paths": artifact_paths,
            "fields_filled": fields_filled,
            "replay_trace_id": replay_trace_id,
            "screenshot_path": screenshot_path,
            "time_to_apply_seconds": time_to_apply_seconds,
            "human_escalations": merged_escalations,
            "status_history": state.status_history + [_add_status(state, "FILLING")],
        }

    if use_browser_automation and state.fill_plan:
        try:
            from agents.submitter import submit_application
            from browser.humanizer import HumanizerConfig
            from replay.generalizer import ReplayGeneralizer, build_submission_trace

            cfg = _state_value(state, "humanizer_config") or {}
            humanizer_config = HumanizerConfig(
                daily_cap=int(cfg.get("daily_cap", 10)),
                per_ats_limit=int(cfg.get("per_ats_limit", 3)),
                per_ats_window_seconds=int(cfg.get("per_ats_window_seconds", 3600)),
            )
            submit_result = await submit_application(
                listing=listing,
                fill_plan=state.fill_plan or {"fields": []},
                submission_mode="shadow",
                artifact_paths=artifact_paths,
                apply_url=_state_value(state, "apply_url"),
                headless=bool(_state_value(state, "headless", True)),
                humanizer_config=humanizer_config,
            )

            if submit_result.get("fields_filled"):
                fields_filled = list(submit_result["fields_filled"])
            session_context_id = submit_result.get("session_context_id", session_context_id)
            screenshot_path = submit_result.get("screenshot_path")
            if submit_result.get("time_to_apply_seconds") is not None:
                time_to_apply_seconds = int(submit_result.get("time_to_apply_seconds"))
            merged_escalations = _merge_escalations(
                merged_escalations,
                submit_result.get("human_escalations", []),
            )

            if submit_result.get("status") == "FAILED":
                failure_payload = submit_result.get("failure_record") or {
                    "error_type": "SubmissionFailed",
                    "error_message": "Shadow submission failed.",
                    "timestamp": _utc_now(),
                }
                if isinstance(failure_payload, dict) and not failure_payload.get("failure_step"):
                    failure_payload["failure_step"] = "fill_form"
                return {
                    "status": "FAILED",
                    "account_status": account_status,
                    "session_context_id": session_context_id,
                    "artifact_paths": artifact_paths,
                    "fields_filled": fields_filled,
                    "screenshot_path": screenshot_path,
                    "human_escalations": _merge_escalations(
                        merged_escalations,
                        [],
                    ),
                    "failure_record": failure_payload,
                    "status_history": state.status_history + [_add_status(state, "FAILED")],
                }

            execution = submit_result.get("execution")
            if isinstance(execution, dict):
                trace = build_submission_trace(
                    listing=listing,
                    fill_plan=state.fill_plan or {"fields": []},
                    execution=execution,
                    application_id=state.application_id,
                )
                generalizer = ReplayGeneralizer()
                saved = generalizer.save_raw_trace(trace)
                trace["trace_id"] = saved.get("trace_id")
                generalized = generalizer.generalize_trace(
                    trace=trace,
                    trace_id=trace["trace_id"],
                    save=True,
                )
                replay_trace_id = generalized.get("trace_id", replay_trace_id)

        except Exception as exc:
            logger.error("Shadow browser execution failed: %s", exc, exc_info=True)
            merged_escalations = _merge_escalations(
                merged_escalations,
                [
                    {
                        "type": "submitter",
                        "priority": "IMPORTANT",
                        "message": "Browser shadow execution unavailable; using simulated fill.",
                    }
                ],
            )

    return {
        "status": "FILLING",
        "account_status": account_status,
        "session_context_id": session_context_id,
        "artifact_paths": artifact_paths,
        "fields_filled": fields_filled,
        "replay_trace_id": replay_trace_id,
        "screenshot_path": screenshot_path,
        "time_to_apply_seconds": time_to_apply_seconds,
        "human_escalations": merged_escalations,
        "status_history": state.status_history + [_add_status(state, "FILLING")],
    }


async def validate_upload_node(state: ApplicationState) -> dict[str, Any]:
    """Validate post-upload autofill using Post-Upload Validator."""
    logger.info("[%s] Validating uploaded fields...", state.application_id)
    try:
        from agents.post_upload_validator import validate_post_upload
        from pii.normalizer import Normalizer
        from pii.vault import PIIVault

        observed_map = {
            str(item.get("field_id")): item.get("value")
            for item in (state.fields_filled or [])
            if isinstance(item, dict) and item.get("field_id")
        }

        normalizer = None
        try:
            vault = PIIVault()
            normalizer = Normalizer(vault)
        except Exception:
            normalizer = None

        validation = validate_post_upload(
            fill_plan=state.fill_plan or {"fields": []},
            observed_fields=observed_map,
            normalizer=normalizer,
        )
        corrections = list(validation.get("corrections", []))
        correction_escalations = [
            {
                "type": "post_upload_validator",
                "field_id": item.get("field_id"),
                "priority": "BLOCKING" if item.get("severity") == "major" else "IMPORTANT",
                "message": item.get("reason", "Post-upload correction required"),
            }
            for item in corrections
        ]

        return {
            "post_upload_corrections": corrections,
            "human_escalations": _merge_escalations(
                state.human_escalations,
                correction_escalations,
            ),
            "status_history": state.status_history + [_add_status(state, "VALIDATED")],
        }
    except Exception as exc:
        logger.error("Post-upload validation failed: %s", exc, exc_info=True)
        return {
            "failure_record": _record_failure(state=state, step="validate_upload", error=exc),
            "status_history": state.status_history + [_add_status(state, "VALIDATION_FAILED")],
        }


async def human_review_node(state: ApplicationState) -> dict[str, Any]:
    """
    Human review node.

    Phase 3 behavior:
    - Wait for explicit APPROVED/ABORTED status updates from API/UI.
    - Do not auto-approve.
    """
    logger.info("[%s] Human review gate...", state.application_id)
    if state.status == "APPROVED":
        return {
            "status": "APPROVED",
            "status_history": state.status_history + [_add_status(state, "APPROVED")],
        }
    if state.status == "ABORTED":
        return {
            "status": "ABORTED",
            "status_history": state.status_history + [_add_status(state, "ABORTED")],
        }
    return {
        "status": "AWAITING_APPROVAL",
        "status_history": state.status_history + [_add_status(state, "AWAITING_APPROVAL")],
    }


async def submission_node(state: ApplicationState) -> dict[str, Any]:
    """Submission mode logic."""
    logger.info("[%s] Submitting in mode=%s", state.application_id, state.submission_mode)
    if state.submission_mode == "shadow":
        from agents.submitter import check_submission_rate_limit
        from browser.humanizer import HumanizerConfig

        cfg = _state_value(state, "humanizer_config") or {}
        humanizer_config = HumanizerConfig(
            daily_cap=int(cfg.get("daily_cap", 10)),
            per_ats_limit=int(cfg.get("per_ats_limit", 3)),
            per_ats_window_seconds=int(cfg.get("per_ats_window_seconds", 3600)),
        )
        ats_type = str((state.listing or {}).get("ats_type") or "unknown")
        rate_status = check_submission_rate_limit(
            ats_type=ats_type,
            humanizer_config=humanizer_config,
        )
        if not rate_status.allowed:
            message = (
                f"Submission blocked by rate limit: {rate_status.reason}. "
                f"Retry after {rate_status.retry_after_seconds}s."
            )
            return {
                "status": "FAILED",
                "human_escalations": _merge_escalations(
                    state.human_escalations,
                    [
                        {
                            "type": "submitter",
                            "priority": "BLOCKING",
                            "message": message,
                        }
                    ],
                ),
                "failure_record": {
                    "error_type": "RateLimitBlocked",
                    "error_message": message,
                    "timestamp": _utc_now(),
                },
                "time_to_apply_seconds": _compute_time_to_apply_seconds(state),
                "status_history": state.status_history + [_add_status(state, "FAILED")],
            }

        # Shadow mode intentionally stops before final submit.
        target_status = "SHADOW_REVIEW"
        return {
            "status": target_status,
            "status_history": state.status_history + [_add_status(state, target_status)],
        }

    if state.submission_mode == "live" and _has_blocking_escalations(state.human_escalations):
        message = "Submission blocked until BLOCKING escalations are resolved."
        return {
            "status": "FAILED",
            "human_escalations": _merge_escalations(
                state.human_escalations,
                [
                    {
                        "type": "submitter",
                        "field_id": "__preflight__",
                        "priority": "BLOCKING",
                        "message": message,
                    }
                ],
            ),
            "failure_record": {
                "error_type": "SubmissionBlocked",
                "error_message": message,
                "timestamp": _utc_now(),
                "failure_step": "submission",
            },
            "time_to_apply_seconds": _compute_time_to_apply_seconds(state),
            "status_history": state.status_history + [_add_status(state, "FAILED")],
        }

    use_browser_automation = bool(_state_value(state, "use_browser_automation", False))
    if use_browser_automation and state.listing and state.fill_plan:
        try:
            from browser.humanizer import HumanizerConfig
            from agents.submitter import submit_application

            cfg = _state_value(state, "humanizer_config") or {}
            artifact_paths = _ensure_submission_artifact_paths(state)
            humanizer_config = HumanizerConfig(
                daily_cap=int(cfg.get("daily_cap", 10)),
                per_ats_limit=int(cfg.get("per_ats_limit", 3)),
                per_ats_window_seconds=int(cfg.get("per_ats_window_seconds", 3600)),
            )
            submit_result = await submit_application(
                listing=state.listing,
                fill_plan=state.fill_plan,
                submission_mode="live",
                artifact_paths=artifact_paths,
                apply_url=_state_value(state, "apply_url"),
                headless=bool(_state_value(state, "headless", True)),
                humanizer_config=humanizer_config,
            )
            status = str(submit_result.get("status") or "FAILED")
            payload = {
                "status": status,
                "status_history": state.status_history + [_add_status(state, status)],
                "fields_filled": submit_result.get("fields_filled", state.fields_filled),
                "screenshot_path": submit_result.get("screenshot_path"),
                "time_to_apply_seconds": submit_result.get("time_to_apply_seconds")
                or _compute_time_to_apply_seconds(state),
                "artifact_paths": artifact_paths,
            }
            if status == "FAILED":
                failure_payload = submit_result.get("failure_record") or {
                    "error_type": "SubmissionFailed",
                    "error_message": "One or more submission actions failed.",
                    "timestamp": _utc_now(),
                }
                if isinstance(failure_payload, dict) and not failure_payload.get("failure_step"):
                    failure_payload["failure_step"] = "submission"
                payload["failure_record"] = failure_payload
                payload["human_escalations"] = _merge_escalations(
                    state.human_escalations,
                    submit_result.get("human_escalations", []),
                )
            return payload
        except Exception as exc:
            logger.error("Live submission failed: %s", exc, exc_info=True)
            return {
                "status": "FAILED",
                "failure_record": _record_failure(state=state, step="submission", error=exc),
                "time_to_apply_seconds": _compute_time_to_apply_seconds(state),
                "status_history": state.status_history + [_add_status(state, "FAILED")],
            }

    if state.submission_mode == "live":
        from agents.submitter import check_submission_rate_limit
        from browser.humanizer import HumanizerConfig

        cfg = _state_value(state, "humanizer_config") or {}
        humanizer_config = HumanizerConfig(
            daily_cap=int(cfg.get("daily_cap", 10)),
            per_ats_limit=int(cfg.get("per_ats_limit", 3)),
            per_ats_window_seconds=int(cfg.get("per_ats_window_seconds", 3600)),
        )
        ats_type = str((state.listing or {}).get("ats_type") or "unknown")
        rate_status = check_submission_rate_limit(
            ats_type=ats_type,
            humanizer_config=humanizer_config,
        )
        if not rate_status.allowed:
            message = (
                f"Submission blocked by rate limit: {rate_status.reason}. "
                f"Retry after {rate_status.retry_after_seconds}s."
            )
            return {
                "status": "FAILED",
                "human_escalations": _merge_escalations(
                    state.human_escalations,
                    [
                        {
                            "type": "submitter",
                            "priority": "BLOCKING",
                            "message": message,
                        }
                    ],
                ),
                "failure_record": {
                    "error_type": "RateLimitBlocked",
                    "error_message": message,
                    "timestamp": _utc_now(),
                },
                "time_to_apply_seconds": _compute_time_to_apply_seconds(state),
                "status_history": state.status_history + [_add_status(state, "FAILED")],
            }

    return {
        "status": "SUBMITTED",
        "time_to_apply_seconds": _compute_time_to_apply_seconds(state),
        "status_history": state.status_history + [_add_status(state, "SUBMITTED")],
    }


async def record_outcome_node(state: ApplicationState) -> dict[str, Any]:
    """Record outcome hook (lightweight in Phase 2)."""
    logger.info("[%s] Recording outcome state=%s", state.application_id, state.status)
    return {
        "status_history": state.status_history + [_add_status(state, "OUTCOME_RECORDED")]
    }


def build_workflow(checkpointer=None):
    """Build and compile the LangGraph workflow graph."""
    from langgraph.graph import END, StateGraph

    workflow = StateGraph(ApplicationState)

    workflow.add_node("evaluate_fit", evaluate_fit_node)
    workflow.add_node("generate_documents", generate_documents_node)
    workflow.add_node("interpret_form", interpret_form_node)
    workflow.add_node("inject_pii", inject_pii_node)
    workflow.add_node("fill_form", fill_form_node)
    workflow.add_node("validate_upload", validate_upload_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("submit", submission_node)
    workflow.add_node("record_outcome", record_outcome_node)

    workflow.set_entry_point("evaluate_fit")

    workflow.add_conditional_edges(
        "evaluate_fit",
        route_by_fit_score,
        {"apply": "generate_documents", "skip": "record_outcome"},
    )

    workflow.add_edge("generate_documents", "interpret_form")
    workflow.add_edge("interpret_form", "inject_pii")
    workflow.add_edge("inject_pii", "fill_form")
    workflow.add_edge("fill_form", "validate_upload")
    workflow.add_edge("validate_upload", "human_review")

    workflow.add_conditional_edges(
        "human_review",
        route_by_approval,
        {
            "approved": "submit",
            "edit": "generate_documents",
            "aborted": "record_outcome",
            "await": "record_outcome",
        },
    )

    workflow.add_conditional_edges(
        "submit",
        route_by_mode,
        {"shadow": "record_outcome", "live": "record_outcome", "failed": "record_outcome"},
    )

    workflow.add_edge("record_outcome", END)

    compile_kwargs = {}
    if checkpointer:
        compile_kwargs["checkpointer"] = checkpointer
    app = workflow.compile(**compile_kwargs)
    logger.info("Workflow compiled successfully")
    return app
