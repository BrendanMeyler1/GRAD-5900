"""
setup/seed.py — Populate the DB with realistic demo data.

After running `python -m setup.init_db`, run this to get an immediately
usable app:

    - Complete fictional profile (John Smith, MS CS student)
    - 3 education records, 2 experience entries, ~25 skills
    - 8 job listings with pre-computed fit scores (mix of high/mid/low)
    - 2 applications in shadow_review (so the Apply view has content)
    - 10 chat messages showing a typical conversation flow
    - Sample Q&A notes

Idempotent: checks if profile already exists and bails out unless
--force is passed.

Usage:
    python -m setup.seed           # skip if already seeded
    python -m setup.seed --force   # wipe chat + jobs + apps first
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure project root is on sys.path when run as `python setup/seed.py`
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from config import settings  # noqa: E402
from db.encryption import get_encryptor
from db.schema import init_db
from db.store import Store

log = logging.getLogger("setup.seed")


# ─── Demo data ────────────────────────────────────────────────────────────────

DEMO_PROFILE: dict = {
    "first_name": "John",
    "last_name": "Smith",
    "email": "john.smith@example.com",
    "phone": "(617) 555-0142",
    "address": "127 Tremont St Apt 4",
    "city": "Boston",
    "state": "MA",
    "zip": "02116",
    "country": "US",
    "linkedin_url": "https://linkedin.com/in/johnsmithexample",
    "github_url": "https://github.com/johnsmithexample",
    "portfolio_url": "https://johnsmith.dev",
    "authorized_to_work": True,
    "requires_sponsorship": False,
    "target_salary_min": 95_000,
    "target_salary_max": 130_000,
    "remote_preference": "hybrid",
    "willing_to_relocate": False,
    "availability_weeks": 2,
    "gender": "Prefer not to say",
    "race_ethnicity": "Prefer not to say",
    "veteran_status": "No",
    "disability_status": "Prefer not to say",
    "resume_raw_text": (
        "John Smith\nBoston, MA · john.smith@example.com · (617) 555-0142\n\n"
        "EDUCATION\n"
        "Northeastern University — MS in Computer Science (Expected May 2026, GPA 3.8)\n"
        "University of Vermont — BS in Computer Science (2023, GPA 3.6)\n\n"
        "EXPERIENCE\n"
        "TechCorp — Software Engineer Intern (Jun 2024 – Aug 2024)\n"
        "  • Built REST APIs with FastAPI + PostgreSQL serving 40k req/day\n"
        "  • Reduced median query latency by 40% via indexing + connection pooling\n"
        "  • Wrote pytest suite covering 87% of new code\n\n"
        "NEU AI Lab — Research Assistant (Sep 2023 – May 2024)\n"
        "  • Fine-tuned BERT variants on legal document classification\n"
        "  • Co-authored workshop paper accepted at NAACL-SRW 2024\n\n"
        "SKILLS\n"
        "Python, TypeScript, React, FastAPI, PostgreSQL, Docker, AWS (EC2/S3/Lambda), "
        "Git, pytest, Playwright, Anthropic SDK"
    ),
}

DEMO_EDUCATION: list[dict] = [
    {
        "institution": "Northeastern University",
        "degree": "MS",
        "field": "Computer Science",
        "graduation_year": 2026,
        "gpa": 3.8,
        "relevant_coursework": "Distributed Systems, Machine Learning, Algorithms, Databases",
    },
    {
        "institution": "University of Vermont",
        "degree": "BS",
        "field": "Computer Science",
        "graduation_year": 2023,
        "gpa": 3.6,
        "relevant_coursework": "OS, Compilers, Networks, Discrete Math",
    },
]

DEMO_EXPERIENCE: list[dict] = [
    {
        "company": "TechCorp",
        "title": "Software Engineer Intern",
        "employment_type": "internship",
        "start_date": "2024-06-01",
        "end_date": "2024-08-30",
        "is_current": False,
        "description": "Built REST APIs with FastAPI and PostgreSQL.",
        "achievements": "Reduced query latency by 40%. Wrote pytest suite covering 87% of new code.",
    },
    {
        "company": "Northeastern AI Lab",
        "title": "Graduate Research Assistant",
        "employment_type": "part_time",
        "start_date": "2023-09-01",
        "end_date": "2024-05-15",
        "is_current": False,
        "description": "Fine-tuned transformer models for legal NLP.",
        "achievements": "Co-authored workshop paper accepted at NAACL-SRW 2024.",
    },
]

DEMO_SKILLS: list[dict] = [
    {"name": "Python", "category": "technical", "proficiency": "expert"},
    {"name": "TypeScript", "category": "technical", "proficiency": "intermediate"},
    {"name": "React", "category": "technical", "proficiency": "intermediate"},
    {"name": "FastAPI", "category": "technical", "proficiency": "expert"},
    {"name": "PostgreSQL", "category": "technical", "proficiency": "intermediate"},
    {"name": "SQLite", "category": "technical", "proficiency": "intermediate"},
    {"name": "Docker", "category": "technical", "proficiency": "intermediate"},
    {"name": "AWS", "category": "technical", "proficiency": "intermediate"},
    {"name": "Git", "category": "technical", "proficiency": "expert"},
    {"name": "pytest", "category": "technical", "proficiency": "expert"},
    {"name": "Playwright", "category": "technical", "proficiency": "intermediate"},
    {"name": "Anthropic SDK", "category": "technical", "proficiency": "intermediate"},
    {"name": "LLM prompting", "category": "technical", "proficiency": "expert"},
    {"name": "SQL", "category": "technical", "proficiency": "intermediate"},
    {"name": "REST API design", "category": "technical", "proficiency": "expert"},
    {"name": "CI/CD", "category": "technical", "proficiency": "intermediate"},
    {"name": "Bash", "category": "technical", "proficiency": "intermediate"},
    {"name": "Communication", "category": "soft", "proficiency": "expert"},
    {"name": "Technical writing", "category": "soft", "proficiency": "expert"},
    {"name": "Mentoring", "category": "soft", "proficiency": "intermediate"},
]

DEMO_QA: list[dict] = [
    {
        "question": "What's your preferred work style?",
        "answer": "Hybrid — I enjoy in-person collaboration but do my deepest work from home.",
        "category": "preference",
    },
    {
        "question": "Do you require sponsorship?",
        "answer": "No, I'm authorized to work in the US.",
        "category": "background",
    },
    {
        "question": "How did you hear about this role?",
        "answer": "Company careers page",
        "category": "preference",
    },
]


def _now_iso(delta_hours: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).isoformat()


DEMO_JOBS: list[dict] = [
    {
        "id": str(uuid.uuid4()),
        "source": "jsearch",
        "ats_type": "greenhouse",
        "title": "Software Engineer - Platform",
        "company": "Stripe",
        "location": "New York, NY",
        "remote_ok": False,
        "description": (
            "Build payment infrastructure used by millions. We need a backend "
            "engineer with strong Python + database fundamentals. Experience "
            "with FastAPI, PostgreSQL, and distributed systems is a plus. "
            "You will own services end-to-end and collaborate with product, "
            "design, and risk."
        ),
        "apply_url": "https://boards.greenhouse.io/stripe/jobs/123456",
        "posted_at": _now_iso(-48),
        "fit_score": 84.0,
        "fit_summary": "Strong match — backend Python role aligns closely with intern experience.",
        "fit_strengths": ["Python + FastAPI background", "PostgreSQL experience", "Strong testing discipline"],
        "fit_gaps": ["No prior fintech domain experience"],
        "interview_likelihood": "medium-high",
        "status": "new",
    },
    {
        "id": str(uuid.uuid4()),
        "source": "greenhouse",
        "ats_type": "greenhouse",
        "title": "Backend Engineer",
        "company": "Klaviyo",
        "location": "Boston, MA",
        "remote_ok": True,
        "description": (
            "Build customer-data infrastructure at scale. Looking for engineers "
            "comfortable across Python, SQL, and distributed systems. Great fit "
            "for early-career engineers who want ownership and mentorship."
        ),
        "apply_url": "https://boards.greenhouse.io/klaviyo/jobs/7654321",
        "posted_at": _now_iso(-24),
        "fit_score": 79.0,
        "fit_summary": "Great Boston-based backend role — matches location, stack, and career stage.",
        "fit_strengths": ["Boston location match", "Python + API design", "Ready for new-grad role"],
        "fit_gaps": ["No prior marketing/martech experience"],
        "interview_likelihood": "medium-high",
        "status": "new",
    },
    {
        "id": str(uuid.uuid4()),
        "source": "lever",
        "ats_type": "lever",
        "title": "Full-Stack Engineer",
        "company": "Ramp",
        "location": "New York, NY",
        "remote_ok": True,
        "description": "React + TypeScript on the frontend, Python on the backend. Build tools for finance teams.",
        "apply_url": "https://jobs.lever.co/ramp/abc123",
        "posted_at": _now_iso(-72),
        "fit_score": 72.0,
        "fit_summary": "Solid match — full-stack role touches all primary skills.",
        "fit_strengths": ["Python + TypeScript + React stack match", "Product-minded engineering"],
        "fit_gaps": ["Full-stack ownership expectation may favor more senior engineers"],
        "interview_likelihood": "medium",
        "status": "new",
    },
    {
        "id": str(uuid.uuid4()),
        "source": "jsearch",
        "ats_type": "universal",
        "title": "Software Engineer I",
        "company": "HubSpot",
        "location": "Cambridge, MA",
        "remote_ok": True,
        "description": "Build features across the HubSpot platform. Strong Java + Python candidates welcome.",
        "apply_url": "https://www.hubspot.com/careers/software-engineer-i-123",
        "posted_at": _now_iso(-12),
        "fit_score": 68.0,
        "fit_summary": "Decent match but Java-heavy — candidate's Python strength is a partial fit.",
        "fit_strengths": ["Location + career-stage match", "Python background"],
        "fit_gaps": ["No Java experience", "HubSpot's stack is primarily JVM"],
        "interview_likelihood": "medium",
        "status": "new",
    },
    {
        "id": str(uuid.uuid4()),
        "source": "greenhouse",
        "ats_type": "greenhouse",
        "title": "Machine Learning Engineer",
        "company": "Anthropic",
        "location": "San Francisco, CA",
        "remote_ok": False,
        "description": (
            "Train and evaluate frontier models. Requires strong ML research "
            "background and ability to work with very large distributed training runs."
        ),
        "apply_url": "https://boards.greenhouse.io/anthropic/jobs/999999",
        "posted_at": _now_iso(-6),
        "fit_score": 42.0,
        "fit_summary": "Weak match — research role wants PhD/senior ML engineers.",
        "fit_strengths": ["Some NLP research experience", "Strong Python fundamentals"],
        "fit_gaps": ["Role targets senior ML researchers", "No distributed training experience", "Not local to SF"],
        "interview_likelihood": "low",
        "status": "new",
    },
    {
        "id": str(uuid.uuid4()),
        "source": "lever",
        "ats_type": "lever",
        "title": "Backend Engineer (New Grad)",
        "company": "Plaid",
        "location": "New York, NY",
        "remote_ok": True,
        "description": "Entry-level backend role building financial data pipelines.",
        "apply_url": "https://jobs.lever.co/plaid/new-grad-backend",
        "posted_at": _now_iso(-36),
        "fit_score": 76.0,
        "fit_summary": "Strong new-grad match — Python backend + data pipelines align well.",
        "fit_strengths": ["New-grad track", "Python + API design", "Data pipeline experience from lab work"],
        "fit_gaps": ["No direct fintech exposure"],
        "interview_likelihood": "medium-high",
        "status": "queued",
    },
    {
        "id": str(uuid.uuid4()),
        "source": "jsearch",
        "ats_type": "workday",
        "title": "Senior Software Engineer",
        "company": "Salesforce",
        "location": "San Francisco, CA",
        "remote_ok": True,
        "description": "Senior role on a platform team. 5+ years experience required.",
        "apply_url": "https://salesforce.wd1.myworkdayjobs.com/example",
        "posted_at": _now_iso(-96),
        "fit_score": 28.0,
        "fit_summary": "Poor match — role targets senior engineers with 5+ years.",
        "fit_strengths": ["Python background"],
        "fit_gaps": ["Role requires 5+ years experience", "Senior scope mismatch"],
        "interview_likelihood": "low",
        "status": "skipped",
    },
    {
        "id": str(uuid.uuid4()),
        "source": "greenhouse",
        "ats_type": "greenhouse",
        "title": "Software Engineer - Developer Tools",
        "company": "DigitalOcean",
        "location": "Remote",
        "remote_ok": True,
        "description": (
            "Build developer tools for a cloud platform. Python + Go background "
            "preferred. Remote-friendly, strong async culture."
        ),
        "apply_url": "https://boards.greenhouse.io/digitalocean/jobs/555555",
        "posted_at": _now_iso(-18),
        "fit_score": 71.0,
        "fit_summary": "Strong remote-friendly role with Python focus.",
        "fit_strengths": ["Remote match", "Python + developer tools interest"],
        "fit_gaps": ["No Go experience yet"],
        "interview_likelihood": "medium",
        "status": "new",
    },
]


DEMO_CHAT: list[tuple[str, str]] = [
    ("user", "Hey! I just uploaded my resume. Can you find me some Python backend jobs?"),
    ("assistant", "I found 8 jobs that match. The top three by fit score are Stripe (84), Klaviyo (79), and Plaid (76). Want me to tailor your resume for any of them?"),
    ("user", "Let's start with Klaviyo — I'm in Boston."),
    ("assistant", "Good pick. Klaviyo is a strong match (79/100): location aligns, they use Python + PostgreSQL, and they hire for your career stage. I'll tailor the resume now."),
    ("user", "What about Stripe? They had the highest score."),
    ("assistant", "Stripe is great but NYC-only. Still worth shadow-applying to — fit is 84. Want me to run a shadow apply for both Klaviyo and Stripe?"),
    ("user", "Yes, shadow apply to both please."),
    ("assistant", "Kicked off both. This usually takes 1-3 minutes per application. I'll let you know when they're ready for review in the Apply tab."),
    ("user", "Thanks! Any replies from the ones I already applied to?"),
    ("assistant", "Not yet — I'll check email in a few minutes and let you know if anything new comes in."),
]


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Seed the DB with demo data.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Wipe chat + jobs + applications before seeding.",
    )
    args = parser.parse_args()

    # Guarantee DB exists
    init_db(settings.db_path)

    store = Store(settings.db_path, get_encryptor())

    existing = store.get_profile()
    if existing and existing.first_name and not args.force:
        log.info("profile already exists — skipping. Pass --force to re-seed.")
        return 0

    if args.force:
        log.info("--force: wiping chat, summary, jobs, applications, email events")
        conn = store._get_conn()  # noqa: SLF001
        conn.execute("DELETE FROM chat_messages")
        conn.execute("DELETE FROM conversation_summary")
        conn.execute("DELETE FROM email_events")
        conn.execute("DELETE FROM applications")
        conn.execute("DELETE FROM job_listings")
        conn.commit()

    # Profile
    store.upsert_profile(DEMO_PROFILE)
    log.info("profile seeded: %s", DEMO_PROFILE["email"])

    for edu in DEMO_EDUCATION:
        store.add_education(edu)
    log.info("education seeded: %d records", len(DEMO_EDUCATION))

    for exp in DEMO_EXPERIENCE:
        store.add_experience(exp)
    log.info("experience seeded: %d records", len(DEMO_EXPERIENCE))

    store.upsert_skills(DEMO_SKILLS)
    log.info("skills seeded: %d records", len(DEMO_SKILLS))

    for qa in DEMO_QA:
        store.add_qa(qa["question"], qa["answer"], qa.get("category"))
    log.info("qa seeded: %d records", len(DEMO_QA))

    # Jobs
    for job in DEMO_JOBS:
        store.upsert_job(job)
    log.info("jobs seeded: %d records", len(DEMO_JOBS))

    # Two applications in shadow_review (so the Apply view has content)
    stripe_job = next(j for j in DEMO_JOBS if j["company"] == "Stripe")
    klaviyo_job = next(j for j in DEMO_JOBS if j["company"] == "Klaviyo")

    store.create_application(
        job_id=stripe_job["id"],
        status="shadow_review",
        resume_tailored_text=(
            "# John Smith\n\n*Boston, MA · john.smith@example.com · (617) 555-0142*\n\n"
            "## Summary\n\nBackend-focused software engineer with internship experience "
            "building Python + PostgreSQL payment systems at scale. MS Computer Science "
            "candidate with strong testing and API design instincts. Eager to contribute "
            "to Stripe's platform engineering.\n\n"
            "## Experience\n\n"
            "**TechCorp — Software Engineer Intern** (Jun 2024 – Aug 2024)\n"
            "- Built REST APIs with FastAPI + PostgreSQL serving 40k requests/day\n"
            "- Reduced median query latency by 40% via targeted indexing\n"
            "- Wrote pytest suite covering 87% of new code\n"
        ),
        cover_letter_text=(
            "Dear Stripe Platform team,\n\n"
            "Reading Stripe's recent engineering blog post on Metered Billing left me thinking "
            "about the sheer scale of invariants the system holds — that's the kind of problem "
            "I want to work on.\n\n"
            "Last summer at TechCorp I built the API layer for our new billing surface: FastAPI, "
            "PostgreSQL, and a lot of tuning to keep P99 query time under 40ms. I wrote the "
            "pytest suite that now runs on every PR. I'm comfortable owning a service end-to-end "
            "and pushing on the performance numbers that matter.\n\n"
            "I'd love to talk about the Platform role. I'm available on 2 weeks notice and based "
            "in Boston but happy to come out to NYC for onsites.\n\n"
            "— John Smith"
        ),
        shadow_screenshots=[],
        fill_log=[
            {"action": "fill", "selector": "input[name=first_name]", "value": "John"},
            {"action": "fill", "selector": "input[name=email]", "value": "john.smith@example.com"},
            {"action": "upload", "selector": "input[type=file]", "value": "resume.pdf"},
        ],
        custom_qa={
            "Why do you want to work at Stripe?": (
                "The scale and correctness-criticality of payment infrastructure is uniquely "
                "appealing, and your engineering culture of writing extensive tests matches how I work."
            )
        },
    )
    store.create_application(
        job_id=klaviyo_job["id"],
        status="shadow_review",
        resume_tailored_text=(
            "# John Smith\n\n*Boston, MA*\n\n"
            "Backend engineer building Python + PostgreSQL systems. Recent internship "
            "experience designing REST APIs at scale. Boston-local, ready for new-grad role."
        ),
        cover_letter_text=(
            "Hi Klaviyo team,\n\nI'd love to join your backend team — Python, PostgreSQL, "
            "and a Boston office is exactly where I want to be. I spent last summer "
            "building high-throughput REST services at TechCorp and shipped pytest coverage "
            "that kept regressions out of production. Happy to chat further.\n\n— John"
        ),
        shadow_screenshots=[],
        fill_log=[
            {"action": "fill", "selector": "input[name=first_name]", "value": "John"},
        ],
        custom_qa={},
    )
    log.info("applications seeded: 2 in shadow_review")

    # Chat history
    for role, content in DEMO_CHAT:
        store.add_message(role, content)
    log.info("chat seeded: %d messages", len(DEMO_CHAT))

    log.info("✓ seed complete. Start the server: uvicorn api.main:app --reload")
    return 0


if __name__ == "__main__":
    sys.exit(main())
