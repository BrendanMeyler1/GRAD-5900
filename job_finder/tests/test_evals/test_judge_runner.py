"""Tests for evals.judge_runner."""

from unittest.mock import MagicMock

from evals.judge_runner import evaluate_cover_letter, evaluate_resume, run_judges


def test_evaluate_resume_normalizes_response(sample_persona, sample_listing):
    router = MagicMock()
    router.route_json.return_value = {
        "overall_score": 82,
        "dimension_scores": {
            "relevance": 88,
            "specificity": 80,
            "truthfulness": 92,
            "ats_readability": 70,
        },
        "strengths": ["Strong requirement alignment"],
        "issues": [
            {"severity": "minor", "message": "Could use tighter bullets", "fix": "Trim wording"}
        ],
        "pass": True,
        "summary": "Good resume quality overall.",
    }

    result = evaluate_resume(
        persona=sample_persona,
        listing=sample_listing,
        resume_text="# Resume",
        router=router,
    )

    assert result["judge"] == "resume"
    assert result["overall_score"] == 82
    assert result["dimension_scores"]["truthfulness"] == 92
    assert result["pass"] is True
    assert result["issues"][0]["severity"] == "minor"


def test_evaluate_cover_letter_fails_on_major_issue(sample_persona, sample_listing):
    router = MagicMock()
    router.route_json.return_value = {
        "overall_score": 78,
        "dimension_scores": {
            "role_alignment": 85,
            "specificity": 70,
            "truthfulness": 40,
            "writing_quality": 82,
        },
        "strengths": ["Clear tone"],
        "issues": [
            {"severity": "major", "message": "Unsupported claim", "fix": "Remove claim"}
        ],
        "pass": True,
        "summary": "Major factual issue detected.",
    }

    result = evaluate_cover_letter(
        persona=sample_persona,
        listing=sample_listing,
        cover_letter_text="Text",
        router=router,
    )
    assert result["judge"] == "cover_letter"
    assert result["pass"] is False
    assert result["issues"][0]["severity"] == "major"


def test_run_judges_combines_outputs(sample_persona, sample_listing):
    router = MagicMock()
    router.route_json.side_effect = [
        {
            "overall_score": 90,
            "dimension_scores": {
                "relevance": 92,
                "specificity": 86,
                "truthfulness": 96,
                "ats_readability": 86,
            },
            "strengths": [],
            "issues": [],
            "pass": True,
            "summary": "Strong.",
        },
        {
            "overall_score": 80,
            "dimension_scores": {
                "role_alignment": 83,
                "specificity": 79,
                "truthfulness": 85,
                "writing_quality": 73,
            },
            "strengths": [],
            "issues": [],
            "pass": True,
            "summary": "Good.",
        },
    ]

    result = run_judges(
        persona=sample_persona,
        listing=sample_listing,
        tailored_resume_text="resume",
        cover_letter_text="cover",
        router=router,
    )

    assert result["overall_score"] == 85
    assert result["pass"] is True
    assert result["resume"]["overall_score"] == 90
    assert result["cover_letter"]["overall_score"] == 80
