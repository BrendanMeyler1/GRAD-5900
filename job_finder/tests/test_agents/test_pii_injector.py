"""Tests for PII Injector agent."""

from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet

from agents.pii_injector import (
    inject_application_artifacts,
    inject_pii_fill_plan,
    inject_pii_text,
)
from pii.normalizer import Normalizer
from pii.vault import PIIVault


def _vault_and_normalizer() -> tuple[PIIVault, Normalizer]:
    tmp_dir = Path(".tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_dir / f"pii_injector_{uuid4().hex}.db"
    key = Fernet.generate_key().decode()

    vault = PIIVault(db_path=str(db_path), encryption_key=key)
    vault.store_token("{{FIRST_NAME}}", "Jane", "LOW")
    vault.store_token("{{PHONE}}", "555-000-1234", "MEDIUM")
    vault.store_token("{{SALARY_HISTORY}}", "200000", "HIGH")
    vault.store_token("{{SCHOOL}}", "University of Connecticut", "LOW")

    normalizer = Normalizer(vault)
    normalizer.register(
        "{{SCHOOL}}",
        canonical="University of Connecticut",
        variants=["UConn", "UCONN"],
    )
    return vault, normalizer


def test_inject_pii_text_resolves_tokens_and_notifies_medium():
    vault, normalizer = _vault_and_normalizer()
    result = inject_pii_text(
        tokenized_text="Name: {{FIRST_NAME}}, Phone: {{PHONE}}",
        vault=vault,
        normalizer=normalizer,
    )
    assert "Jane" in result["resolved_text"]
    assert "555-000-1234" in result["resolved_text"]
    assert "{{PHONE}}" in result["medium_sensitivity_tokens"]
    assert result["needs_human_review"] is False


def test_inject_pii_text_blocks_high_sensitivity_by_default():
    vault, normalizer = _vault_and_normalizer()

    blocked = inject_pii_text(
        tokenized_text="Compensation: {{SALARY_HISTORY}}",
        vault=vault,
        normalizer=normalizer,
    )
    assert "{{SALARY_HISTORY}}" in blocked["resolved_text"]
    assert "{{SALARY_HISTORY}}" in blocked["blocked_tokens"]
    assert blocked["needs_human_review"] is True

    allowed = inject_pii_text(
        tokenized_text="Compensation: {{SALARY_HISTORY}}",
        vault=vault,
        normalizer=normalizer,
        allow_high_sensitivity=True,
    )
    assert "200000" in allowed["resolved_text"]
    assert allowed["blocked_tokens"] == []
    assert allowed["needs_human_review"] is False


def test_inject_pii_fill_plan_uses_normalization_context_and_blocks_high():
    vault, normalizer = _vault_and_normalizer()
    fill_plan = {
        "fields": [
            {
                "field_id": "education_school",
                "label": "School / University",
                "type": "text_input",
                "value": "{{SCHOOL}}",
                "pii_level": "LOW",
                "normalization_context": "abbreviation",
            },
            {
                "field_id": "salary_expectation",
                "label": "Expected Salary (USD)",
                "type": "text_input",
                "value": "{{SALARY_HISTORY}}",
                "pii_level": "HIGH",
            },
        ],
        "escalations": [],
    }

    result = inject_pii_fill_plan(
        fill_plan=fill_plan,
        vault=vault,
        normalizer=normalizer,
    )
    fields = result["fill_plan"]["fields"]
    school = [f for f in fields if f["field_id"] == "education_school"][0]
    salary = [f for f in fields if f["field_id"] == "salary_expectation"][0]

    assert school["value"] == "UConn"
    assert salary["value"] == "{{SALARY_HISTORY}}"
    assert "salary_expectation" in result["blocked_fields"]
    assert result["needs_human_review"] is True
    assert any(
        esc["field_id"] == "salary_expectation" and esc["priority"] == "BLOCKING"
        for esc in result["fill_plan"]["escalations"]
    )


def test_inject_application_artifacts_aggregates_outputs():
    vault, normalizer = _vault_and_normalizer()
    payload = inject_application_artifacts(
        tailored_resume_tokenized="Candidate: {{FIRST_NAME}}, Phone: {{PHONE}}",
        cover_letter_tokenized="School: {{SCHOOL}}",
        fill_plan={
            "fields": [
                {
                    "field_id": "salary_expectation",
                    "label": "Expected Salary",
                    "type": "text_input",
                    "value": "{{SALARY_HISTORY}}",
                    "pii_level": "HIGH",
                }
            ],
            "escalations": [],
        },
        vault=vault,
        normalizer=normalizer,
    )

    assert "Jane" in payload["tailored_resume_final"]
    assert "University of Connecticut" in payload["cover_letter_final"]
    assert payload["needs_human_review"] is True
    assert payload["blocked_fields"] == ["salary_expectation"]

