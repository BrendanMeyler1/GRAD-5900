"""Phase 2 wiring tests for graph.workflow nodes."""

import asyncio

from graph.state import ApplicationState
from graph.workflow import (
    evaluate_fit_node,
    fill_form_node,
    generate_documents_node,
    interpret_form_node,
    validate_upload_node,
)


def test_evaluate_fit_node_uses_fit_scorer(monkeypatch, sample_persona, sample_listing):
    def fake_score_fit(persona, listing, router=None):
        assert persona["persona_id"] == sample_persona["persona_id"]
        assert listing["listing_id"] == sample_listing["listing_id"]
        return {
            "overall_score": 86,
            "breakdown": {"skills_match": 90},
            "gaps": [],
            "strengths": [],
            "talking_points": [],
            "recommendation": "APPLY",
        }

    monkeypatch.setattr("agents.fit_scorer.score_fit", fake_score_fit)

    state = ApplicationState(persona=sample_persona, listing=sample_listing)
    result = asyncio.run(evaluate_fit_node(state))

    assert result["fit_score"]["overall_score"] == 86
    assert result["status_history"][-1]["status"] == "EVALUATING"


def test_evaluate_fit_node_falls_back_when_scorer_fails(monkeypatch, sample_persona, sample_listing):
    def fake_score_fit(*args, **kwargs):
        raise RuntimeError("fit parser failed")

    monkeypatch.setattr("agents.fit_scorer.score_fit", fake_score_fit)

    state = ApplicationState(persona=sample_persona, listing=sample_listing)
    result = asyncio.run(evaluate_fit_node(state))

    assert result.get("fit_fallback_used") is True
    assert result["fit_score"]["overall_score"] >= 0
    assert result["fit_score"]["recommendation"] in {"APPLY", "MAYBE", "SKIP"}
    assert result["last_fit_failure"]["failure_step"] == "evaluate_fit"
    assert any(item.get("type") == "fit_scorer" for item in result["human_escalations"])
    assert result["status_history"][-1]["status"] == "EVALUATING"


def test_generate_documents_node_uses_tailor_and_cover(monkeypatch, sample_persona, sample_listing):
    def fake_tailor_resume(persona, listing, fit_score=None, router=None, master_bullets_path=None):
        return {"resume_text": "# {{FULL_NAME}}\nTailored resume content"}

    def fake_generate_cover_letter(persona, listing, fit_score=None, router=None):
        return {"cover_letter_text": "Tailored cover letter text"}

    monkeypatch.setattr("agents.resume_tailor.tailor_resume", fake_tailor_resume)
    monkeypatch.setattr("agents.cover_letter.generate_cover_letter", fake_generate_cover_letter)

    state = ApplicationState(
        persona=sample_persona,
        listing=sample_listing,
        fit_score={"overall_score": 88},
    )
    result = asyncio.run(generate_documents_node(state))

    assert result["tailored_resume_tokenized"].startswith("# {{FULL_NAME}}")
    assert result["cover_letter_tokenized"] == "Tailored cover letter text"
    assert result["status_history"][-1]["status"] == "GENERATING_DOCS"


def test_generate_documents_node_falls_back_when_llm_generation_fails(
    monkeypatch, sample_persona, sample_listing
):
    def fake_tailor_resume(*args, **kwargs):
        raise RuntimeError("json parse failed")

    def fake_generate_cover_letter(*args, **kwargs):  # pragma: no cover
        raise RuntimeError("should not be called")

    monkeypatch.setattr("agents.resume_tailor.tailor_resume", fake_tailor_resume)
    monkeypatch.setattr("agents.cover_letter.generate_cover_letter", fake_generate_cover_letter)

    state = ApplicationState(
        persona=sample_persona,
        listing=sample_listing,
        fit_score={"overall_score": 88},
    )
    result = asyncio.run(generate_documents_node(state))

    assert result.get("status") != "FAILED"
    assert result["document_fallback_used"] is True
    assert result["tailored_resume_tokenized"].startswith("# ")
    assert result["cover_letter_tokenized"].startswith("Dear ")
    assert result["last_document_failure"]["failure_step"] == "generate_documents"
    assert any(item.get("type") == "document_generator" for item in result["human_escalations"])
    assert result["status_history"][-1]["status"] == "GENERATING_DOCS"


def test_interpret_form_node_generates_question_responses(monkeypatch, sample_persona, sample_listing):
    def fake_interpret_form(listing, form_html, persona=None, template_path=None, router=None, allow_llm_assist=False):
        return {
            "fill_plan_id": "fp-1",
            "listing_id": listing["listing_id"],
            "ats_type": "greenhouse",
            "url": listing["apply_url"],
            "fields": [
                {
                    "field_id": "why_work_here",
                    "label": "Why do you want to work here?",
                    "type": "textarea",
                    "selector": "#q1",
                    "selector_strategy": "label_based_xpath",
                    "value": "QUESTION_RESPONDER:why_work_here",
                    "pii_level": "NONE",
                    "confidence": 0.65,
                    "requires_question_responder": True,
                }
            ],
            "escalations": [
                {
                    "field_id": "salary_expectation",
                    "reason": "HIGH sensitivity + low confidence",
                    "priority": "BLOCKING",
                    "label": "Expected Salary (USD)",
                }
            ],
        }

    def fake_generate_question_response(
        listing,
        field_id,
        question_text,
        persona,
        fit_score=None,
        router=None,
        company_memory_db_path=None,
        allow_cache=True,
    ):
        return {
            "question_id": "q-1",
            "listing_id": listing["listing_id"],
            "field_id": field_id,
            "question_text": question_text,
            "response_text": "I align with your mission and platform challenges.",
            "grounded_in": ["persona.summary"],
            "cached_from_company_memory": False,
            "generated_at": "2026-04-10T00:00:00Z",
        }

    monkeypatch.setattr("agents.form_interpreter.interpret_form", fake_interpret_form)
    monkeypatch.setattr("agents.question_responder.generate_question_response", fake_generate_question_response)

    listing = dict(sample_listing)
    listing["form_html"] = "<form><textarea id='q1'></textarea></form>"
    state = ApplicationState(
        persona=sample_persona,
        listing=listing,
        fit_score={"overall_score": 87},
    )

    result = asyncio.run(interpret_form_node(state))
    assert len(result["question_responses"]) == 1
    assert result["fill_plan"]["fields"][0]["value"].startswith("I align with your mission")
    assert result["human_escalations"][0]["priority"] == "BLOCKING"


def test_fill_and_validate_nodes_wire_account_and_validator(monkeypatch, sample_listing):
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

    def fake_validate_post_upload(fill_plan, observed_fields, normalizer=None, vault=None):
        return {
            "corrections": [
                {
                    "field_id": "phone",
                    "expected_value": "{{PHONE}}",
                    "observed_value": "bad",
                    "suggested_value": "{{PHONE}}",
                    "severity": "major",
                    "reason": "Autofill mismatch",
                }
            ],
            "needs_human_review": True,
            "summary": {"mismatches": 1},
        }

    monkeypatch.setattr("agents.account_manager.manage_account", fake_manage_account)
    monkeypatch.setattr("agents.post_upload_validator.validate_post_upload", fake_validate_post_upload)

    state = ApplicationState(
        listing=sample_listing,
        fill_plan={
            "fields": [
                {
                    "field_id": "phone",
                    "label": "Phone",
                    "type": "text_input",
                    "value": "{{PHONE}}",
                    "selector": "#phone",
                    "selector_strategy": "exact_css",
                    "confidence": 0.95,
                },
                {
                    "field_id": "resume_upload",
                    "label": "Resume",
                    "type": "file_upload",
                    "value": "generated_resume.pdf",
                },
            ]
        },
    )

    fill_result = asyncio.run(fill_form_node(state))
    assert fill_result["account_status"] == "existing"
    assert fill_result["session_context_id"] == "ctx-1"
    assert len(fill_result["fields_filled"]) == 1
    assert fill_result["fields_filled"][0]["field_id"] == "phone"

    state_after_fill = state.model_copy(update=fill_result)
    validate_result = asyncio.run(validate_upload_node(state_after_fill))
    assert len(validate_result["post_upload_corrections"]) == 1
    assert validate_result["human_escalations"][0]["priority"] == "BLOCKING"
