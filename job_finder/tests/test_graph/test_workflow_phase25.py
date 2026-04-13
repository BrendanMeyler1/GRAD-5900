"""Phase 2.5 workflow wiring tests (shadow mode + PII injector + replay)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from browser.humanizer import RateLimitStatus
from errors import AccountError
from graph.state import ApplicationState
from graph.workflow import (
    fill_form_node,
    human_review_node,
    inject_pii_node,
    interpret_form_node,
    route_by_approval,
    route_by_mode,
    submission_node,
)


def test_submission_node_shadow_routes_to_record_outcome_path():
    state = ApplicationState(submission_mode="shadow")
    result = asyncio.run(submission_node(state))
    updated = state.model_copy(update=result)

    assert result["status"] == "SHADOW_REVIEW"
    assert route_by_mode(updated) == "shadow"


def test_inject_pii_node_uses_local_injector(monkeypatch):
    def fake_inject_application_artifacts(
        tailored_resume_tokenized=None,
        cover_letter_tokenized=None,
        fill_plan=None,
        allow_high_sensitivity=False,
        use_local_llm=False,
        **kwargs,
    ):
        return {
            "tailored_resume_final": "Resolved Resume",
            "cover_letter_final": "Resolved Cover",
            "fill_plan_final": {"fields": [], "escalations": []},
            "blocked_fields": ["salary_expectation"],
            "unresolved_fields": [],
        }

    monkeypatch.setattr(
        "agents.pii_injector.inject_application_artifacts",
        fake_inject_application_artifacts,
    )

    state = ApplicationState(
        submission_mode="shadow",
        tailored_resume_tokenized="{{FIRST_NAME}} Resume",
        cover_letter_tokenized="{{FIRST_NAME}} Cover",
        fill_plan={"fields": [], "escalations": []},
    )
    result = asyncio.run(inject_pii_node(state))

    assert result["tailored_resume_final"] == "Resolved Resume"
    assert result["cover_letter_final"] == "Resolved Cover"
    assert any(
        item.get("field_id") == "salary_expectation" and item.get("priority") == "BLOCKING"
        for item in result["human_escalations"]
    )


def test_fill_form_node_shadow_browser_opt_in_saves_replay(monkeypatch, sample_listing):
    def fake_manage_account(
        company,
        ats_type,
        username=None,
        password=None,
        session_cookies=None,
        browser_context=None,
        vault=None,
        signals=None,
        router=None,
        allow_llm_assist=False,
    ):
        return {
            "account_status": "existing",
            "action": "use_existing",
            "account_id": "acct-1",
            "session_context_id": "ctx-1",
            "requires_human": False,
            "reason": "ok",
        }

    async def fake_submit_application(
        listing,
        fill_plan,
        submission_mode="shadow",
        artifact_paths=None,
        apply_url=None,
        headless=True,
        humanizer_config=None,
    ):
        return {
            "status": "SHADOW_REVIEW",
            "fields_filled": [
                {
                    "field_id": "first_name",
                    "label": "First Name",
                    "value": "Jane",
                    "selector": "#first_name",
                    "selector_strategy": "exact_css",
                    "confidence": 0.98,
                }
            ],
            "human_escalations": [],
            "session_context_id": "ctx-browser",
            "screenshot_path": "replay/traces/screenshots/mock.png",
            "execution": {"executed_actions": []},
            "time_to_apply_seconds": 91,
        }

    class FakeGeneralizer:
        def __init__(self, *args, **kwargs):
            pass

        def save_raw_trace(self, trace):
            return {"trace_id": "trace-raw-1", "path": "replay/traces/raw/trace-raw-1.json"}

        def generalize_trace(self, trace, trace_id=None, save=True):
            return {"trace_id": "trace-generalized-1"}

    def fake_build_submission_trace(
        listing,
        fill_plan,
        execution=None,
        dom_snapshot=None,
        application_id=None,
    ):
        return {
            "trace_id": "trace-seed",
            "listing": listing,
            "fill_plan": fill_plan,
            "execution": execution or {},
            "application_id": application_id,
        }

    monkeypatch.setattr("agents.account_manager.manage_account", fake_manage_account)
    monkeypatch.setattr("agents.submitter.submit_application", fake_submit_application)
    monkeypatch.setattr("replay.generalizer.ReplayGeneralizer", FakeGeneralizer)
    monkeypatch.setattr("replay.generalizer.build_submission_trace", fake_build_submission_trace)

    state = ApplicationState(
        listing=sample_listing,
        fill_plan={
            "fields": [
                {
                    "field_id": "first_name",
                    "label": "First Name",
                    "type": "text_input",
                    "value": "Jane",
                    "selector": "#first_name",
                    "selector_strategy": "exact_css",
                    "confidence": 0.98,
                }
            ]
        },
        submission_mode="shadow",
        use_browser_automation=True,
    )

    result = asyncio.run(fill_form_node(state))
    assert result["status"] == "FILLING"
    assert result["account_status"] == "existing"
    assert result["session_context_id"] == "ctx-browser"
    assert result["fields_filled"][0]["field_id"] == "first_name"
    assert result["replay_trace_id"] == "trace-generalized-1"
    assert result["time_to_apply_seconds"] == 91


def test_fill_form_node_materializes_artifacts_when_missing(monkeypatch, sample_listing):
    workdir = Path("tests/.tmp_fill_form_artifacts")
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workdir)

    def fake_manage_account(*args, **kwargs):
        return {
            "account_status": "existing",
            "session_context_id": "ctx-1",
            "requires_human": False,
            "reason": "ok",
        }

    async def fake_submit_application(
        listing,
        fill_plan,
        submission_mode="shadow",
        artifact_paths=None,
        apply_url=None,
        headless=True,
        humanizer_config=None,
    ):
        assert artifact_paths is not None
        assert Path(artifact_paths["resume"]).exists()
        assert Path(artifact_paths["cover_letter"]).exists()
        return {
            "status": "SHADOW_REVIEW",
            "fields_filled": [],
            "human_escalations": [],
            "execution": {"executed_actions": []},
            "time_to_apply_seconds": 3,
        }

    monkeypatch.setattr("agents.account_manager.manage_account", fake_manage_account)
    monkeypatch.setattr("agents.submitter.submit_application", fake_submit_application)

    state = ApplicationState(
        listing=sample_listing,
        fill_plan={"fields": []},
        tailored_resume_final="# Resume\nLine 1",
        cover_letter_final="Dear Team,\nHello",
        submission_mode="shadow",
        use_browser_automation=True,
    )
    result = asyncio.run(fill_form_node(state))

    assert result["artifact_paths"]["resume"].endswith(".txt")
    assert result["artifact_paths"]["cover_letter"].endswith(".txt")
    assert Path(result["artifact_paths"]["resume"]).read_text(encoding="utf-8").startswith("# Resume")
    assert Path(result["artifact_paths"]["cover_letter"]).read_text(encoding="utf-8").startswith("Dear Team")


def test_human_review_node_waits_for_explicit_approval():
    state = ApplicationState(submission_mode="live", status="QUEUED")
    result = asyncio.run(human_review_node(state))
    updated = state.model_copy(update=result)
    assert updated.status == "AWAITING_APPROVAL"
    assert route_by_approval(updated) == "await"


def test_interpret_form_node_applies_replay_selector_hints(monkeypatch, sample_persona, sample_listing):
    def fake_interpret_form(
        listing,
        form_html,
        persona=None,
        template_path=None,
        router=None,
        allow_llm_assist=False,
    ):
        return {
            "fill_plan_id": "fp-r1",
            "listing_id": listing["listing_id"],
            "ats_type": "greenhouse",
            "url": listing.get("apply_url"),
            "fields": [
                {
                    "field_id": "first_name",
                    "label": "First Name",
                    "type": "text_input",
                    "selector": None,
                    "selector_strategy": "unknown",
                    "value": "{{FIRST_NAME}}",
                    "confidence": 0.2,
                    "source": "template",
                }
            ],
            "escalations": [],
        }

    class _FakeCompanyStore:
        @staticmethod
        def get_replay_refs(company_name: str):
            return ["trace-greenhouse-1"]

    class FakeGeneralizer:
        def __init__(self, *args, **kwargs):
            self.company_store = _FakeCompanyStore()

        @staticmethod
        def load_generalized_trace(trace_id: str):
            return {
                "trace_id": trace_id,
                "ats_type": "greenhouse",
                "descriptors": [
                    {
                        "field_id": "first_name",
                        "selector_that_worked": "#first_name",
                    }
                ],
            }

    monkeypatch.setattr("agents.form_interpreter.interpret_form", fake_interpret_form)
    monkeypatch.setattr("replay.generalizer.ReplayGeneralizer", FakeGeneralizer)

    state = ApplicationState(
        persona=sample_persona,
        listing=sample_listing,
    )
    result = asyncio.run(interpret_form_node(state))
    field = result["fill_plan"]["fields"][0]
    assert field["selector"] == "#first_name"
    assert field["selector_strategy"] == "replay_semantic"
    assert result["replay_trace_id"] == "trace-greenhouse-1"


def test_fill_form_shadow_downgrades_missing_account_credentials(monkeypatch, sample_listing):
    def fake_manage_account(*args, **kwargs):
        raise AccountError(
            "No existing account found and missing username/password for account creation."
        )

    monkeypatch.setattr("agents.account_manager.manage_account", fake_manage_account)

    state = ApplicationState(
        listing=sample_listing,
        fill_plan={"fields": []},
        submission_mode="shadow",
        use_browser_automation=False,
    )

    result = asyncio.run(fill_form_node(state))
    escalation = next(
        (item for item in result["human_escalations"] if item.get("type") == "account_manager"),
        None,
    )
    assert escalation is not None
    assert escalation["priority"] == "IMPORTANT"


def test_fill_form_live_skips_browser_when_blocking_escalations_exist(monkeypatch, sample_listing):
    def fake_manage_account(*args, **kwargs):
        raise AccountError(
            "No existing account found and missing username/password for account creation."
        )

    async def fake_submit_application(*args, **kwargs):  # pragma: no cover
        raise AssertionError("browser should be skipped when blocking escalations exist")

    monkeypatch.setattr("agents.account_manager.manage_account", fake_manage_account)
    monkeypatch.setattr("agents.submitter.submit_application", fake_submit_application)

    state = ApplicationState(
        listing=sample_listing,
        fill_plan={"fields": []},
        submission_mode="live",
        use_browser_automation=True,
    )

    result = asyncio.run(fill_form_node(state))
    assert result["status"] == "FILLING"
    assert any(
        item.get("type") == "account_manager" and item.get("priority") == "BLOCKING"
        for item in result["human_escalations"]
    )
    assert any(
        item.get("type") == "submitter" and item.get("field_id") == "__preflight__"
        for item in result["human_escalations"]
    )


def test_submission_node_live_non_browser_respects_rate_limits(monkeypatch, sample_listing):
    def fake_check_submission_rate_limit(*, ats_type, humanizer_config=None, outcomes_db_path="data/outcomes.db"):
        return RateLimitStatus(
            allowed=False,
            reason="daily_cap_reached",
            retry_after_seconds=1200,
            daily_used=10,
            daily_remaining=0,
            ats_used=2,
            ats_remaining=1,
        )

    monkeypatch.setattr("agents.submitter.check_submission_rate_limit", fake_check_submission_rate_limit)

    state = ApplicationState(
        listing=sample_listing,
        submission_mode="live",
        use_browser_automation=False,
        human_escalations=[],
    )
    result = asyncio.run(submission_node(state))

    assert result["status"] == "FAILED"
    assert result["failure_record"]["error_type"] == "RateLimitBlocked"
    assert any(item.get("type") == "submitter" for item in result["human_escalations"])
    assert isinstance(result["time_to_apply_seconds"], int)


def test_submission_node_shadow_respects_rate_limits(monkeypatch, sample_listing):
    def fake_check_submission_rate_limit(*, ats_type, humanizer_config=None, outcomes_db_path="data/outcomes.db"):
        return RateLimitStatus(
            allowed=False,
            reason="per_ats_cooldown",
            retry_after_seconds=900,
            daily_used=3,
            daily_remaining=7,
            ats_used=1,
            ats_remaining=0,
        )

    monkeypatch.setattr("agents.submitter.check_submission_rate_limit", fake_check_submission_rate_limit)

    state = ApplicationState(
        listing=sample_listing,
        submission_mode="shadow",
        use_browser_automation=False,
        human_escalations=[],
    )
    result = asyncio.run(submission_node(state))

    assert result["status"] == "FAILED"
    assert result["failure_record"]["error_type"] == "RateLimitBlocked"
    assert any(item.get("type") == "submitter" for item in result["human_escalations"])
    assert isinstance(result["time_to_apply_seconds"], int)


def test_submission_node_live_blocks_when_blocking_escalations_exist(monkeypatch, sample_listing):
    async def fake_submit_application(*args, **kwargs):  # pragma: no cover
        raise AssertionError("submission should be blocked before submitter call")

    monkeypatch.setattr("agents.submitter.submit_application", fake_submit_application)

    state = ApplicationState(
        listing=sample_listing,
        fill_plan={"fields": []},
        submission_mode="live",
        use_browser_automation=True,
        human_escalations=[
            {
                "type": "form_interpreter",
                "field_id": "salary_expectation",
                "priority": "BLOCKING",
                "message": "Needs approval",
            }
        ],
    )
    result = asyncio.run(submission_node(state))

    assert result["status"] == "FAILED"
    assert result["failure_record"]["error_type"] == "SubmissionBlocked"
    assert any(
        item.get("type") == "submitter" and item.get("field_id") == "__preflight__"
        for item in result["human_escalations"]
    )


def test_submission_node_live_non_browser_computes_time_to_apply(sample_listing):
    state = ApplicationState(
        listing=sample_listing,
        submission_mode="live",
        use_browser_automation=False,
        current_attempt={
            "attempt_id": "app-1:1",
            "attempt_number": 1,
            "trigger": "resume",
            "started_at": "2026-04-10T00:00:00+00:00",
        },
    )

    result = asyncio.run(submission_node(state))
    assert result["status"] == "SUBMITTED"
    assert isinstance(result["time_to_apply_seconds"], int)
    assert result["time_to_apply_seconds"] > 0


def test_submission_node_browser_failure_sets_submission_failure_step(monkeypatch, sample_listing):
    async def fake_submit_application(
        listing,
        fill_plan,
        submission_mode="live",
        artifact_paths=None,
        apply_url=None,
        headless=True,
        humanizer_config=None,
    ):
        return {
            "status": "FAILED",
            "failure_record": {
                "error_type": "SubmissionFailed",
                "error_message": "failed in submitter",
            },
            "human_escalations": [],
            "fields_filled": [],
            "time_to_apply_seconds": 12,
        }

    monkeypatch.setattr("agents.submitter.submit_application", fake_submit_application)

    state = ApplicationState(
        listing=sample_listing,
        fill_plan={"fields": []},
        submission_mode="live",
        use_browser_automation=True,
    )
    result = asyncio.run(submission_node(state))

    assert result["status"] == "FAILED"
    assert result["failure_record"]["failure_step"] == "submission"


def test_submission_node_live_materializes_artifacts_for_browser(monkeypatch, sample_listing):
    workdir = Path("tests/.tmp_submission_artifacts")
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workdir)

    async def fake_submit_application(
        listing,
        fill_plan,
        submission_mode="live",
        artifact_paths=None,
        apply_url=None,
        headless=True,
        humanizer_config=None,
    ):
        assert artifact_paths is not None
        assert Path(artifact_paths["resume"]).exists()
        assert Path(artifact_paths["cover_letter"]).exists()
        return {
            "status": "SUBMITTED",
            "fields_filled": [],
            "human_escalations": [],
            "time_to_apply_seconds": 9,
        }

    monkeypatch.setattr("agents.submitter.submit_application", fake_submit_application)

    state = ApplicationState(
        listing=sample_listing,
        fill_plan={"fields": []},
        tailored_resume_final="# Resume\nGenerated",
        cover_letter_final="Dear Acme,\nGenerated",
        submission_mode="live",
        use_browser_automation=True,
    )
    result = asyncio.run(submission_node(state))

    assert result["status"] == "SUBMITTED"
    assert Path(result["artifact_paths"]["resume"]).exists()
    assert Path(result["artifact_paths"]["cover_letter"]).exists()


def test_state_updates_preserve_attempt_metadata():
    state = ApplicationState(
        submission_mode="shadow",
        attempt_number=2,
        current_attempt={
            "attempt_id": "app-1:2",
            "attempt_number": 2,
            "trigger": "resume",
            "started_at": "2026-04-11T00:00:00Z",
        },
        attempt_history=[
            {
                "attempt_id": "app-1:1",
                "attempt_number": 1,
                "trigger": "start_application",
                "final_status": "FAILED",
            }
        ],
    )

    result = asyncio.run(human_review_node(state))
    updated = state.model_copy(update=result)
    payload = updated.model_dump()

    assert payload["attempt_number"] == 2
    assert payload["current_attempt"]["attempt_id"] == "app-1:2"
    assert payload["attempt_history"][0]["attempt_id"] == "app-1:1"
