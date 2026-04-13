"""
Tests for Profile Analyst Agent.

Tests resume parsing, LLM-driven extraction, PII storage,
and tokenized persona generation.

All tests mock the LLM router — no real API calls.
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.profile_analyst import (
    analyze_resume,
    extract_resume_text,
    _load_prompt,
    _store_pii,
)
from pii.vault import PIIVault
from pii.normalizer import Normalizer


# Sample LLM response matching the expected output schema
MOCK_LLM_RESPONSE = {
    "contact": {
        "full_name": "{{FULL_NAME}}",
        "email": "{{EMAIL}}",
        "phone": "{{PHONE}}",
        "address": "{{ADDRESS}}",
        "linkedin": "{{LINKEDIN}}",
        "github": "{{GITHUB}}",
    },
    "summary": "5+ years of software engineering experience focused on web applications",
    "skills": {
        "languages": ["Python", "JavaScript", "SQL"],
        "frameworks": ["Django", "React", "FastAPI"],
        "infrastructure": ["AWS", "Docker"],
        "domains": ["web development", "API design"],
    },
    "experience": [
        {
            "employer": "{{EMPLOYER_1}}",
            "title": "Software Engineer",
            "start_date": "2021-03",
            "end_date": "present",
            "bullets": [
                "Built REST APIs serving 100K+ requests/day",
                "Reduced deployment time by 50% with CI/CD pipeline",
            ],
        }
    ],
    "education": [
        {
            "institution": "{{SCHOOL}}",
            "degree": "B.S. Computer Science",
            "graduation_date": "2020-05",
            "gpa": "3.5",
        }
    ],
    "years_of_experience": 5,
    "work_authorization": "US Citizen",
    "pii_extracted": {
        "full_name": "Alex Synthetic",
        "first_name": "Alex",
        "last_name": "Synthetic",
        "email": "alex.synthetic@testmail.com",
        "phone": "555-999-0000",
        "address": "789 Fake Blvd, TestVille, TS 99999",
        "linkedin": "https://linkedin.com/in/alexsynthetic",
        "github": "https://github.com/alexsynthetic",
        "schools": [
            {
                "token": "{{SCHOOL}}",
                "canonical": "Synthetic State University",
                "variants": ["SSU", "SynState"],
            }
        ],
        "employers": [
            {
                "token": "{{EMPLOYER_1}}",
                "canonical": "FakeCompany Inc",
                "variants": ["FakeCo", "FC Inc"],
            }
        ],
    },
}


class TestResumeTextExtraction:
    """Test extracting text from various file formats."""

    def test_extract_from_txt(self, temp_dir):
        txt_path = os.path.join(temp_dir, "resume.txt")
        Path(txt_path).write_text("Alex Synthetic\nSoftware Engineer\nPython, Django")

        text = extract_resume_text(txt_path)
        assert "Alex Synthetic" in text
        assert "Python" in text

    def test_extract_from_md(self, temp_dir):
        md_path = os.path.join(temp_dir, "resume.md")
        Path(md_path).write_text("# Alex Synthetic\n## Software Engineer")

        text = extract_resume_text(md_path)
        assert "Alex Synthetic" in text

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            extract_resume_text("/nonexistent/resume.pdf")

    def test_unsupported_format(self, temp_dir):
        bad_path = os.path.join(temp_dir, "resume.xyz")
        Path(bad_path).write_text("content")

        with pytest.raises(ValueError, match="Unsupported"):
            extract_resume_text(bad_path)

    def test_empty_file(self, temp_dir):
        empty_path = os.path.join(temp_dir, "empty.txt")
        Path(empty_path).write_text("")

        text = extract_resume_text(empty_path)
        assert text == ""


class TestPromptLoading:
    """Test loading and parsing the prompt template."""

    def test_load_prompt_returns_string(self):
        prompt = _load_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 100  # Should be a substantial prompt

    def test_prompt_contains_key_instructions(self):
        prompt = _load_prompt()
        assert "Profile Analyst" in prompt
        assert "{{FULL_NAME}}" in prompt
        assert "PII" in prompt


class TestPIIStorage:
    """Test that PII extraction stores data correctly in the vault."""

    def test_stores_simple_fields(self, vault):
        normalizer = Normalizer(vault)
        pii_data = MOCK_LLM_RESPONSE["pii_extracted"]

        _store_pii(pii_data, vault, normalizer)

        assert vault.get_token("{{FULL_NAME}}") == "Alex Synthetic"
        assert vault.get_token("{{FIRST_NAME}}") == "Alex"
        assert vault.get_token("{{LAST_NAME}}") == "Synthetic"
        assert vault.get_token("{{EMAIL}}") == "alex.synthetic@testmail.com"
        assert vault.get_token("{{PHONE}}") == "555-999-0000"

    def test_stores_school_normalization(self, vault):
        normalizer = Normalizer(vault)
        pii_data = MOCK_LLM_RESPONSE["pii_extracted"]

        _store_pii(pii_data, vault, normalizer)

        # Check normalized names were registered
        names = vault.get_normalized_names("{{SCHOOL}}")
        assert names["canonical"] == "Synthetic State University"
        assert "SSU" in names["variants"]
        assert "SynState" in names["variants"]

    def test_stores_employer_normalization(self, vault):
        normalizer = Normalizer(vault)
        pii_data = MOCK_LLM_RESPONSE["pii_extracted"]

        _store_pii(pii_data, vault, normalizer)

        names = vault.get_normalized_names("{{EMPLOYER_1}}")
        assert names["canonical"] == "FakeCompany Inc"
        assert "FakeCo" in names["variants"]

    def test_handles_missing_fields(self, vault):
        """PII storage should handle partial data gracefully."""
        normalizer = Normalizer(vault)
        pii_data = {
            "full_name": "Partial Person",
            "email": "partial@test.com",
            # No phone, no address, no schools, no employers
        }

        _store_pii(pii_data, vault, normalizer)

        assert vault.get_token("{{FULL_NAME}}") == "Partial Person"
        assert vault.get_token("{{EMAIL}}") == "partial@test.com"
        assert vault.get_token("{{PHONE}}") is None


class TestAnalyzeResume:
    """Test the full analyze_resume pipeline (with mocked LLM)."""

    def test_full_pipeline(self, temp_dir, vault):
        """End-to-end test with mocked LLM router."""
        # Create a test resume
        resume_path = os.path.join(temp_dir, "test_resume.txt")
        Path(resume_path).write_text(
            "Alex Synthetic\n"
            "Software Engineer\n"
            "alex.synthetic@testmail.com\n"
            "555-999-0000\n\n"
            "Experience:\n"
            "FakeCompany Inc - Software Engineer (2021-present)\n"
            "- Built REST APIs serving 100K+ requests/day\n\n"
            "Education:\n"
            "Synthetic State University - B.S. Computer Science (2020)\n"
        )

        # Mock the LLM router
        mock_router = MagicMock()
        mock_router.route_json.return_value = MOCK_LLM_RESPONSE.copy()

        persona = analyze_resume(
            file_path=resume_path,
            router=mock_router,
            vault=vault,
        )

        # Verify persona structure
        assert "persona_id" in persona
        assert "created_at" in persona
        assert persona["summary"] is not None

        # Verify PII is tokenized in persona
        assert persona["contact"]["full_name"] == "{{FULL_NAME}}"
        assert persona["contact"]["email"] == "{{EMAIL}}"

        # Verify skills are preserved
        assert "Python" in persona["skills"]["languages"]

        # Verify PII was stored in vault
        assert vault.get_token("{{FULL_NAME}}") == "Alex Synthetic"
        assert vault.get_token("{{EMAIL}}") == "alex.synthetic@testmail.com"

        # Verify pii_extracted was removed from persona
        assert "pii_extracted" not in persona

    def test_empty_resume_raises(self, temp_dir, vault):
        """Empty resume should raise ValueError."""
        empty_path = os.path.join(temp_dir, "empty.txt")
        Path(empty_path).write_text("")

        mock_router = MagicMock()

        with pytest.raises(ValueError, match="empty"):
            analyze_resume(file_path=empty_path, router=mock_router, vault=vault)

    def test_llm_called_with_resume_text(self, temp_dir, vault):
        """Verify the LLM is called with the resume content."""
        resume_path = os.path.join(temp_dir, "resume.txt")
        Path(resume_path).write_text("Test resume content here")

        mock_router = MagicMock()
        mock_router.route_json.return_value = {
            "contact": {"full_name": "{{FULL_NAME}}"},
            "summary": "Test",
            "skills": {"languages": []},
            "experience": [],
            "education": [],
            "years_of_experience": 0,
            "pii_extracted": {},
        }

        analyze_resume(file_path=resume_path, router=mock_router, vault=vault)

        # Verify route_json was called
        mock_router.route_json.assert_called_once()
        call_args = mock_router.route_json.call_args

        # Check task type
        assert call_args[1]["task_type"] == "profile_analysis" or call_args[0][0] == "profile_analysis"

        # Check resume text is in the user prompt
        kwargs = call_args[1] if call_args[1] else {}
        args = call_args[0] if call_args[0] else ()
        user_prompt = kwargs.get("user_prompt", args[2] if len(args) > 2 else "")
        assert "Test resume content here" in user_prompt
