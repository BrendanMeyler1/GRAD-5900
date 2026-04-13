"""Tests for Post-Upload Validator agent."""

from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet

from agents.post_upload_validator import validate_post_upload
from pii.normalizer import Normalizer
from pii.vault import PIIVault


def _normalizer() -> Normalizer:
    tmp_dir = Path(".tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_dir / f"pii_validator_{uuid4().hex}.db"
    key = Fernet.generate_key().decode()
    vault = PIIVault(db_path=str(db_path), encryption_key=key)

    vault.store_token("{{SCHOOL}}", "University of Connecticut", "LOW")
    normalizer = Normalizer(vault)
    normalizer.register(
        "{{SCHOOL}}",
        canonical="University of Connecticut",
        variants=["UConn", "UCONN", "U of Connecticut"],
    )
    return normalizer


def test_validator_accepts_normalized_variant_match():
    normalizer = _normalizer()
    fill_plan = {
        "fields": [
            {
                "field_id": "education_school",
                "label": "School / University",
                "value": "{{SCHOOL}}",
                "pii_level": "LOW",
                "confidence": 0.72,
                "type": "text_input",
            }
        ]
    }
    observed = {"education_school": "UCONN"}

    result = validate_post_upload(
        fill_plan=fill_plan,
        observed_fields=observed,
        normalizer=normalizer,
    )

    assert result["needs_human_review"] is False
    assert result["summary"]["mismatches"] == 0
    assert result["corrections"] == []


def test_validator_flags_high_sensitivity_mismatch_as_major():
    fill_plan = {
        "fields": [
            {
                "field_id": "salary_expectation",
                "label": "Expected Salary (USD)",
                "value": "180000",
                "pii_level": "HIGH",
                "confidence": 0.93,
                "type": "text_input",
            }
        ]
    }
    observed = {"salary_expectation": "0"}

    result = validate_post_upload(
        fill_plan=fill_plan,
        observed_fields=observed,
    )

    assert result["summary"]["mismatches"] == 1
    assert result["summary"]["major_mismatches"] == 1
    assert result["needs_human_review"] is True
    assert result["corrections"][0]["severity"] == "major"


def test_validator_flags_missing_field():
    fill_plan = {
        "fields": [
            {
                "field_id": "first_name",
                "label": "First Name",
                "value": "{{FIRST_NAME}}",
                "pii_level": "LOW",
                "confidence": 0.95,
                "type": "text_input",
            }
        ]
    }

    result = validate_post_upload(
        fill_plan=fill_plan,
        observed_fields={},
    )

    assert result["summary"]["mismatches"] == 1
    assert result["corrections"][0]["reason"] == "Field missing after upload/autofill"
