# Job Finder — AI-Powered Job Application Automation

An autonomous job application pipeline that scouts listings, tailors resumes and cover letters, fills out ATS forms via browser automation, and tracks application status — all orchestrated through an LLM-driven workflow with a human-in-the-loop approval gate.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [Starting the Backend](#starting-the-backend)
  - [Starting the Dashboard](#starting-the-dashboard)
  - [End-to-End Workflow](#end-to-end-workflow)
- [Key Concepts](#key-concepts)
- [API Reference](#api-reference)
- [Testing](#testing)

---

## Overview

Job Finder automates the repetitive parts of job hunting while keeping a human in control of every actual submission. The system works in three phases:

1. **Scout & Analyze** — Scrape a job URL, detect the ATS type, and extract listing details. Upload your resume to build a reusable persona profile.
2. **Tailor & Prepare** — An LLM scores your fit, generates a tailored resume and cover letter, interprets the application form fields, and injects your PII (name, email, phone) into the fill plan.
3. **Review & Submit** — A shadow run fills the form without clicking submit so you can review. Once approved, a live submission drives the browser to complete the application.

All personally identifiable information is tokenized before it ever reaches an LLM, stored in an encrypted vault, and only de-tokenized at the final PII injection step.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Dashboard (React + Vite)                │
│  Decision Queue · Application Tracker · Docs Preview · etc  │
└────────────────────────┬───────────────┬────────────────────┘
                    REST │          WebSocket │
┌────────────────────────▼───────────────▼────────────────────┐
│                    FastAPI Backend                           │
│  Routes: /api/jobs · /api/apply · /api/applications · ...   │
│  Middleware: CORS · PII Guard                               │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                LangGraph Workflow (graph/)                   │
│  fit_score → tailor_docs → interpret_form → inject_pii →    │
│  fill_form → human_review → submission → record_outcome     │
└────┬──────────┬──────────┬──────────┬───────────────────────┘
     │          │          │          │
┌────▼───┐ ┌───▼────┐ ┌───▼───┐ ┌───▼──────────┐
│  LLM   │ │  PII   │ │Agents │ │   Browser    │
│ Router │ │ Vault  │ │       │ │  Automation  │
│        │ │        │ │       │ │ (Playwright) │
└────────┘ └────────┘ └───────┘ └──────────────┘
```

| Component | Purpose |
|-----------|---------|
| **Dashboard** | React SPA for reviewing listings, approving submissions, and tracking outcomes. |
| **FastAPI API** | REST + WebSocket backend that orchestrates the pipeline and serves the dashboard. |
| **LangGraph Workflow** | Stateful, node-based DAG that moves each application through the pipeline. |
| **LLM Router** | Model-agnostic layer supporting Anthropic (Claude), OpenAI, and local Ollama models. |
| **PII Vault** | Fernet-encrypted SQLite store — PII never leaves the vault until the browser needs it. |
| **Agents** | Specialized modules: profile analyst, fit scorer, resume tailor, cover letter writer, form interpreter, PII injector, submitter, status tracker. |
| **Browser Automation** | Playwright-driven strategies for Greenhouse, Lever, and a universal fallback for other ATS platforms. |

---

## Project Structure

```
job_finder/
├── api/                    # FastAPI routes, middleware, and entry point
│   ├── main.py             # Application entry point
│   ├── routes/             # REST + WebSocket route handlers
│   └── middleware/         # PII guard middleware
├── agents/                 # LLM-powered agent modules
│   ├── profile_analyst.py  # Resume parsing and persona extraction
│   ├── fit_scorer.py       # Job-candidate fit scoring
│   ├── resume_tailor.py    # Tailored resume generation
│   ├── cover_letter.py     # Tailored cover letter generation
│   ├── form_interpreter.py # ATS form field interpretation
│   ├── pii_injector.py     # Token → real value replacement
│   ├── submitter.py        # Browser submission orchestrator
│   └── status_tracker.py   # Email-based application status tracking
├── browser/                # Playwright browser automation
│   ├── playwright_driver.py# Core browser driver wrapper
│   ├── humanizer.py        # Human-like typing/delay simulation
│   ├── react_select.py     # React Select dropdown handler
│   └── ats_strategies/     # Per-ATS execution strategies
│       ├── greenhouse.py
│       ├── lever.py
│       └── universal.py
├── graph/                  # LangGraph workflow
│   ├── workflow.py         # Full DAG: fit → tailor → fill → submit
│   └── state.py            # ApplicationState schema
├── llm_router/             # Model routing and configuration
│   ├── router.py           # Unified LLM interface
│   └── config.yaml         # Model selection and routing rules
├── pii/                    # PII protection layer
│   ├── vault.py            # Encrypted PII storage
│   ├── tokenizer.py        # Tokenization (PII → {{TOKEN}})
│   ├── account_vault.py    # ATS login credential storage
│   └── sanitizer.py        # Output sanitization
├── dashboard/              # React + Vite frontend
│   └── src/
│       ├── App.jsx         # Main dashboard application
│       └── components/     # UI components
├── data/                   # Runtime data (gitignored)
│   ├── raw/                # Uploaded resumes
│   ├── processed/          # Generated documents
│   └── outcomes.db         # SQLite application records
├── feedback/               # Learning loop databases
├── prompts/                # LLM prompt templates
├── tests/                  # Pytest test suites
├── requirements.txt        # Python dependencies
└── .env.example            # Environment variable template
```

---

## Prerequisites

- **Python 3.11+**
- **Node.js 18+** (for the dashboard)
- **Playwright browsers** (installed via the setup step below)
- An **Anthropic** or **OpenAI** API key

---

## Installation

### 1. Clone and enter the project

```bash
cd job_finder
```

### 2. Create a Python virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Install Playwright browsers

```bash
python -m playwright install chromium
```

### 5. Install dashboard dependencies

```bash
cd dashboard
npm install
cd ..
```

### 6. Initialize databases

```bash
python -c "from setup.init_db import init_all; init_all()"
```

---

## Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

### Required Variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key (or `OPENAI_API_KEY` for OpenAI) |
| `PRIMARY_MODEL` | LLM model to use (e.g. `claude-haiku-4-5-20251001`, `gpt-4o`) |
| `PII_VAULT_KEY` | Fernet encryption key for PII storage |
| `ACCOUNT_VAULT_KEY` | Separate Fernet key for ATS login credentials |

Generate Fernet keys with:

```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DAILY_APPLICATION_CAP` | `10` | Max submissions per day |
| `PER_ATS_HOURLY_CAP` | `3` | Max submissions per ATS platform per hour |
| `HEADLESS` | `true` | Set to `false` to see the browser during automation |
| `DASHBOARD_URL` | `http://localhost:5173` | Frontend URL for CORS |
| `IMAP_HOST` / `IMAP_USER` | — | Email config for status tracking |

---

## Usage

### Starting the Backend

```bash
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

The API will be available at `http://localhost:8000`. Verify with:

```
GET http://localhost:8000/api/health
```

### Starting the Dashboard

In a separate terminal:

```bash
cd dashboard
npm run dev
```

The dashboard opens at `http://localhost:5173`.

### End-to-End Workflow

#### 1. Upload Your Resume

Click **"Upload & Parse Resume"** in the dashboard, or use the API:

```bash
curl -X POST http://localhost:8000/api/persona/upload \
  -F "file=@/path/to/your_resume.pdf"
```

This parses your resume, extracts a structured persona profile, and stores PII securely in the encrypted vault.

#### 2. Add a Job Listing

Paste a job URL into **"Paste Job URL"** and click **"Scrape & Queue Job"**:

The system will:
- Open a browser to detect the ATS type (Greenhouse, Lever, etc.)
- Extract job details and queue the listing

#### 3. Start a Shadow Run

Click **"Start Shadow Run"** on a queued listing. The pipeline will:

1. **Score fit** — LLM evaluates how well your profile matches the role
2. **Tailor documents** — Generates a customized resume and cover letter
3. **Interpret form** — Maps ATS form fields to your data
4. **Inject PII** — Replaces `{{FULL_NAME}}`, `{{EMAIL}}`, etc. with real values
5. **Dry-fill form** — Validates the fill plan without browser interaction
6. **Await approval** — Pauses for your review

The tailored resume and cover letter appear in the **"Tailored Docs Preview"** panel.

#### 4. Review and Approve

Inspect the generated documents and escalation messages. When satisfied, click **"Approve + Submit Live"**:

- A Playwright browser opens and navigates to the application form
- Fields are filled with human-like typing delays
- Resume and cover letter are uploaded
- The submit button is clicked
- A confirmation screenshot is captured

#### 5. Track Status

The **Application Tracker** shows all applications with their current status. For email-based status tracking, configure the IMAP settings in `.env` and click **"Sync Statuses"** to scan your inbox for recruiter responses.

---

## Key Concepts

### PII Protection

All personal data flows through a tokenization pipeline:

```
Resume → Profile Analyst → Tokenizer → {{FULL_NAME}}, {{EMAIL}}, ...
                                ↓
                          PII Vault (Fernet-encrypted SQLite)
                                ↓
           PII Injector (only at submission time) → Real values in browser
```

LLMs never see your real name, email, phone number, or address. They work exclusively with tokenized placeholders.

### Submission Modes

| Mode | Browser | Submit Click | Use Case |
|------|---------|-------------|----------|
| `shadow` | Yes (fills form) | No | Preview and validate before real submission |
| `live` | Yes (fills + submits) | Yes | Actual application submission |

### Rate Limiting

Built-in humanizer controls prevent suspicious automation patterns:

- **Daily cap**: Max 10 applications per day (configurable)
- **Per-ATS cap**: Max 3 per platform per hour (configurable)
- **Typing delays**: Randomized keystroke timing to mimic human behavior

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/persona/upload` | Upload and parse a resume |
| `POST` | `/api/jobs/add-by-url` | Scrape and queue a job listing |
| `GET` | `/api/jobs/queue` | List queued job listings |
| `POST` | `/api/apply/{listing_id}` | Start a shadow run for a listing |
| `POST` | `/api/apply/{app_id}/approve` | Approve and submit live |
| `POST` | `/api/apply/{app_id}/abort` | Abort an application |
| `GET` | `/api/applications` | List all applications |
| `GET` | `/api/applications/{app_id}` | Get full application detail |
| `GET` | `/api/batch/candidates` | Get batch processing candidates |
| `GET` | `/api/insights/overview` | Dashboard analytics overview |
| `GET` | `/api/insights/failures` | Recent failure analysis |
| `WS` | `/ws/application/{app_id}` | Real-time application status updates |
| `WS` | `/ws/queue` | Real-time job queue updates |

Full interactive docs available at `http://localhost:8000/docs` (Swagger UI).

---

## Testing

```bash
# Run full test suite
pytest

# Run specific test modules
pytest tests/test_agents/
pytest tests/test_pii/
pytest tests/test_browser/

# Run with verbose output
pytest -v
```

---

## License

This project was developed as part of GRAD 5900 coursework.
