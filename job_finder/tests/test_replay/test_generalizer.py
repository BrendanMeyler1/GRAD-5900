"""Tests for replay.generalizer."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from feedback.company_memory_store import CompanyMemoryStore
from replay.generalizer import ReplayGeneralizer, build_submission_trace


def _workspace() -> tuple[Path, ReplayGeneralizer]:
    root = Path(".tmp") / f"replay_{uuid4().hex}"
    traces_dir = root / "replay" / "traces"
    store = CompanyMemoryStore(db_path=str(root / "feedback" / "company_memory.db"))
    generalizer = ReplayGeneralizer(
        traces_dir=str(traces_dir),
        company_store=store,
    )
    return root, generalizer


def _trace_payload() -> dict:
    listing = {
        "listing_id": "listing-001",
        "ats_type": "greenhouse",
        "company": {"name": "Acme Corp"},
        "apply_url": "https://boards.greenhouse.io/acme/jobs/001#app",
    }
    fill_plan = {
        "fields": [
            {
                "field_id": "first_name",
                "label": "First Name",
                "type": "text_input",
                "selector": "#first_name",
                "selector_strategy": "exact_css",
                "confidence": 0.98,
                "pii_level": "LOW",
                "source": "template",
            },
            {
                "field_id": "education_school",
                "label": "School / University",
                "type": "text_input",
                "selector": "[name=\"education_school\"]",
                "selector_strategy": "aria_label_match",
                "confidence": 0.71,
                "pii_level": "LOW",
                "source": "llm_interpreted",
            },
            {
                "field_id": "salary_expectation",
                "label": "Expected Salary (USD)",
                "type": "text_input",
                "selector": "[name=\"salary_expectation\"]",
                "selector_strategy": "label_based_xpath",
                "confidence": 0.42,
                "pii_level": "HIGH",
                "source": "template_resolved",
            },
        ]
    }
    execution = {
        "executed_actions": [
            {
                "field_id": "first_name",
                "action": "fill_text",
                "selector": "#first_name",
                "strategy_used": "exact_css",
                "confidence": 0.98,
            },
            {
                "field_id": "education_school",
                "action": "fill_text",
                "selector": "[name=\"education_school\"]",
                "strategy_used": "aria_label_match",
                "confidence": 0.71,
            },
        ]
    }
    dom_snapshot = {
        "fields": [
            {
                "selector": "#first_name",
                "label": "First Name",
                "input_type": "text_input",
                "aria_label": "First name",
                "placeholder": "First name",
            },
            {
                "selector": "[name=\"education_school\"]",
                "label": "School / University",
                "input_type": "text_input",
                "aria_label": "School",
                "placeholder": "University",
            },
        ]
    }
    return build_submission_trace(
        listing=listing,
        fill_plan=fill_plan,
        execution=execution,
        dom_snapshot=dom_snapshot,
        application_id="app-001",
    )


def test_generalize_trace_builds_semantic_descriptors():
    _, generalizer = _workspace()
    trace = _trace_payload()

    generalized = generalizer.generalize_trace(trace, save=False)
    assert generalized["descriptor_count"] == 3
    assert generalized["ats_type"] == "greenhouse"
    assert generalized["strategy_stats"]["exact_css"] == 1

    first = [d for d in generalized["descriptors"] if d["field_id"] == "first_name"][0]
    assert first["relative_position"] == "top_form"
    assert first["selector_that_worked"] == "#first_name"
    assert first["strategy_used"] == "exact_css"
    assert first["confidence_band"] == "AUTO_FILL"

    last = [d for d in generalized["descriptors"] if d["field_id"] == "salary_expectation"][0]
    assert last["relative_position"] == "bottom_form"
    assert last["confidence_band"] == "ESCALATE"


def test_save_raw_and_generalized_trace_and_company_ref():
    root, generalizer = _workspace()
    trace = _trace_payload()

    saved = generalizer.save_raw_trace(trace)
    trace_id = saved["trace_id"]
    raw_path = Path(saved["path"])
    assert raw_path.exists()
    loaded_raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert loaded_raw["trace_id"] == trace_id

    generalized = generalizer.generalize_trace(loaded_raw, trace_id=trace_id, save=True)
    gen_path = root / "replay" / "traces" / "generalized" / f"{trace_id}.json"
    assert gen_path.exists()
    loaded_gen = json.loads(gen_path.read_text(encoding="utf-8"))
    assert loaded_gen["trace_id"] == generalized["trace_id"]

    refs = generalizer.company_store.get_replay_refs("Acme Corp")
    assert trace_id in refs


def test_remap_to_dom_matches_semantically():
    _, generalizer = _workspace()
    trace = _trace_payload()
    generalized = generalizer.generalize_trace(trace, save=False)

    dom_fields = [
        {
            "selector": "#candidate_first_name",
            "label": "Candidate First Name",
            "input_type": "text_input",
            "aria_label": "First Name",
            "placeholder": "",
        },
        {
            "selector": "#candidate_school",
            "label": "University",
            "input_type": "text_input",
            "aria_label": "School / University",
            "placeholder": "School",
        },
    ]
    remap = generalizer.remap_to_dom(generalized_trace=generalized, dom_fields=dom_fields)

    assert remap["first_name"]["selector"] == "#candidate_first_name"
    assert remap["education_school"]["selector"] == "#candidate_school"
    assert remap["first_name"]["strategy"] == "semantic_remap"

