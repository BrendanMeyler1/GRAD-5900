"""
Shared test fixtures for job_finder tests.

All fixtures use SYNTHETIC data only — no real PII.
See Appendix I of the implementation plan.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root is on the path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Set test environment variables before any imports
os.environ.setdefault("PII_VAULT_KEY", "dGVzdC1rZXktZm9yLXRlc3RpbmctMTIzNDU2Nzg5MA==")
os.environ.setdefault("PRIMARY_MODEL", "test-model")
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ.setdefault("OLLAMA_MODEL", "phi3")

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def temp_dir():
    """Provide a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def vault_key():
    """Generate a fresh Fernet key for testing."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


@pytest.fixture
def vault(temp_dir, vault_key):
    """Create a fresh PII vault for testing."""
    from pii.vault import PIIVault
    db_path = os.path.join(temp_dir, "test_vault.db")
    return PIIVault(db_path=db_path, encryption_key=vault_key)


@pytest.fixture
def populated_vault(vault):
    """Vault pre-populated with synthetic PII data."""
    vault.store_token("{{FULL_NAME}}", "Jane TestPerson", "LOW")
    vault.store_token("{{FIRST_NAME}}", "Jane", "LOW")
    vault.store_token("{{LAST_NAME}}", "TestPerson", "LOW")
    vault.store_token("{{EMAIL}}", "jane.test@example.com", "LOW")
    vault.store_token("{{PHONE}}", "555-000-1234", "MEDIUM")
    vault.store_token("{{ADDRESS}}", "456 Test Ave, TestCity, TS 00000", "MEDIUM")
    vault.store_token("{{LINKEDIN}}", "https://linkedin.com/in/janetestperson", "LOW")
    vault.store_token("{{GITHUB}}", "https://github.com/janetestperson", "LOW")
    vault.store_token("{{SCHOOL}}", "University of Testing", "LOW")
    vault.store_token("{{EMPLOYER_1}}", "TestCorp International", "LOW")
    vault.store_token("{{EMPLOYER_2}}", "Synthetic Labs Inc", "LOW")
    return vault


@pytest.fixture
def normalizer(vault):
    """Create a Normalizer instance backed by the test vault."""
    from pii.normalizer import Normalizer
    return Normalizer(vault)


@pytest.fixture
def populated_normalizer(populated_vault):
    """Normalizer with pre-registered name variants."""
    from pii.normalizer import Normalizer
    norm = Normalizer(populated_vault)
    norm.register(
        "{{SCHOOL}}",
        canonical="University of Testing",
        variants=["UTest", "UTEST", "U of Testing"],
    )
    norm.register(
        "{{EMPLOYER_1}}",
        canonical="TestCorp International",
        variants=["TestCorp", "TCI"],
    )
    return norm


@pytest.fixture
def tokenizer(populated_vault):
    """Create a PIITokenizer backed by the populated vault."""
    from pii.tokenizer import PIITokenizer
    return PIITokenizer(populated_vault)


@pytest.fixture
def sample_persona():
    """Load a synthetic test persona."""
    path = FIXTURES_DIR / "synthetic_persona.json"
    if path.exists():
        return json.loads(path.read_text())
    # Inline fallback
    return {
        "persona_id": "test-persona-001",
        "created_at": "2026-04-09T14:00:00Z",
        "contact": {
            "full_name": "{{FULL_NAME}}",
            "email": "{{EMAIL}}",
            "phone": "{{PHONE}}",
            "address": "{{ADDRESS}}",
            "linkedin": "{{LINKEDIN}}",
            "github": "{{GITHUB}}",
        },
        "summary": "8+ years software engineering experience with focus on distributed systems",
        "skills": {
            "languages": ["Python", "Go", "TypeScript"],
            "frameworks": ["FastAPI", "Django", "React"],
            "infrastructure": ["AWS", "Kubernetes", "Docker"],
            "domains": ["distributed systems", "API design", "data pipelines"],
        },
        "experience": [
            {
                "employer": "{{EMPLOYER_1}}",
                "title": "Senior Software Engineer",
                "start_date": "2022-01",
                "end_date": "present",
                "bullets": [
                    "Designed event-driven microservices processing 2M+ events/day",
                    "Reduced API latency by 40% through caching layer redesign",
                ],
            }
        ],
        "education": [
            {
                "institution": "{{SCHOOL}}",
                "degree": "B.S. Computer Science",
                "graduation_date": "2018-05",
                "gpa": "3.7",
            }
        ],
        "years_of_experience": 8,
        "work_authorization": "US Citizen",
    }


@pytest.fixture
def sample_listing():
    """Load a synthetic job listing."""
    path = FIXTURES_DIR / "synthetic_listing.json"
    if path.exists():
        return json.loads(path.read_text())
    # Inline fallback
    return {
        "listing_id": "test-listing-001",
        "source": "greenhouse",
        "source_url": "https://boards.greenhouse.io/testcorp/jobs/12345",
        "company": {
            "name": "Acme Test Corp",
            "size": "500-1000",
            "industry": "fintech",
        },
        "role": {
            "title": "Senior Backend Engineer",
            "department": "Platform",
            "location": "Remote US",
            "requirements": [
                "5+ years Python or Go",
                "Experience with distributed systems",
                "AWS/GCP proficiency",
            ],
        },
        "alive_score": {"composite": 0.85},
        "ats_type": "greenhouse",
    }


@pytest.fixture
def mock_router():
    """Mock LLM router that returns predetermined responses."""
    router = MagicMock()
    router.route = MagicMock(return_value='{"test": "response"}')
    router.route_json = MagicMock(return_value={"test": "response"})
    return router
