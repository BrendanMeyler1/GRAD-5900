# Job Finder v2

An AI-powered job application assistant built for GRAD 5900. Demonstrates graduate-level mastery of the Model Context Protocol, multi-agent Manager/Worker systems, Human-in-the-Loop design, and persistent memory — while functioning as a real tool that can draft, fill out, and submit job applications.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         React Dashboard                             │
│  /onboard   /discover   /apply   /chat                              │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ HTTP + SSE
┌──────────────────────────▼──────────────────────────────────────────┐
│                    FastAPI  (api/)                                   │
│  /api/profile  /api/jobs  /api/apply  /api/chat  /api/applications  │
│  /api/email    /api/tasks                                           │
│                                                                     │
│  Middleware: RequestLogging, CORS, GlobalExceptionHandler           │
│  Background: TaskRegistry (asyncio), Email sync loop               │
└──────┬─────────────────┬────────────────────┬────────────────────┘
       │                 │                    │
┌──────▼──────┐  ┌───────▼───────┐  ┌────────▼────────┐
│ Orchestrator│  │  MCP Servers  │  │    Pipeline     │
│  (Manager)  │  │  ┌──────────┐ │  │  tailor → fill  │
│             │  │  │ profile  │ │  │  → persist      │
│ Tool-use    │  │  ├──────────┤ │  └─────────────────┘
│ loop with   │  │  │  jobs   │ │
│ max 8 iters │  │  ├──────────┤ │  ┌─────────────────┐
└──────┬──────┘  │  │  files  │ │  │  ConversationMem │
       │         └──┴──────────┘ │  │  Rolling summary│
       │ delegates               │  └─────────────────┘
┌──────▼──────────────────────┐  │
│         Workers             │  │  ┌─────────────────┐
│  ┌──────────────────────┐   │  │  │ SQLite (WAL)    │
│  │ JobScout             │   │  │  │  user_profile   │
│  │  JSearch + GH + Lever│   │  │  │  job_listings   │
│  ├──────────────────────┤   │  │  │  applications   │
│  │ ResumeWriter         │   │  │  │  chat_messages  │
│  │  tailor + cover ltr  │   │  │  │  email_events   │
│  ├──────────────────────┤   │  │  │  (Fernet PII)   │
│  │ FormFillerAgent      │───┘  │  └─────────────────┘
│  │  UniversalFiller     │      │
│  │  Playwright + Vision │      │
│  ├──────────────────────┤      │
│  │ ProfileBuilder       │      │
│  ├──────────────────────┤      │
│  │ EmailTracker         │      │
│  │  Outlook IMAP        │      │
│  └──────────────────────┘      │
└────────────────────────────────┘
```

---

## Setup

### Prerequisites

- Python 3.11+
- Node.js 20+
- Anthropic API key
- (Optional) JSearch RapidAPI key for broad job search
- (Optional) Outlook email + App Password for email tracking

### 1. Clone and install Python dependencies

```bash
git clone https://github.com/BrendanMeyler1/GRAD-5900
cd job_finder_v2
pip install -r requirements.txt
```

### 2. Install Playwright browsers

```bash
playwright install chromium
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your keys
```

Required variables:
```
ANTHROPIC_API_KEY=sk-ant-...
```

Optional:
```
JSEARCH_API_KEY=...           # RapidAPI JSearch (200 free req/month)
OUTLOOK_EMAIL=you@outlook.com
OUTLOOK_APP_PASSWORD=...      # Microsoft App Password (not your login password)
DEV_MODE=true                 # Skip real browser + scrapers; use mock data
```

### 4. Initialize the database

```bash
python setup/init_db.py
```

### 5. (Optional) Seed demo data

Populates the DB with a fictional but realistic profile, 8 job listings with fit scores, and 2 shadow-review applications so the UI is ready to explore immediately:

```bash
python setup/seed.py
```

Use `--force` to re-seed a database that already has data.

### 6. Install dashboard dependencies

```bash
cd dashboard
npm install
```

---

## Running

### Backend

```bash
# From project root
uvicorn api.main:app --reload --port 8000
```

The server performs a startup validation pass:
- Checks `ANTHROPIC_API_KEY` is set
- Creates all data directories (`data/`, `data/resumes/`, `data/generated/`, etc.)
- Runs `init_db` if the database doesn't exist
- Logs a summary: profile complete? jobs in queue? pending applications?

Interactive API docs: **http://localhost:8000/docs**

### Dashboard

```bash
cd dashboard
npm run dev
# Opens http://localhost:5173
```

Vite proxies `/api` to `localhost:8000` automatically.

---

## Usage Guide

### Step 1: Upload your resume

On first launch you'll see the onboarding flow. Drop your PDF or DOCX resume onto the upload zone. Claude extracts your name, contact info, education, experience, and skills automatically. Answer 3-4 quick questions to fill remaining gaps (target role, salary, remote preference).

### Step 2: Discover jobs

In the **Discover** tab, type a role + location and click **Find**. The JobScout fans out to JSearch, Greenhouse, and Lever simultaneously, deduplicates results, and scores each against your profile. Fit scores appear as they compute — green (70+), amber (40-69), red (below 40).

### Step 3: Shadow-apply to top matches

Click **Shadow Apply** on any job. The pipeline:
1. Tailors your resume to the job's language (Claude)
2. Writes a cover letter (Claude)
3. Opens the application form in a headless browser
4. Fills every field using Playwright + Claude vision
5. Captures screenshots at each step
6. **Stops before submitting** — puts the application in "Review"

### Step 4: Review and approve

In the **Apply** tab, find the application in the "Review" column. The review panel has three tabs:
- **Screenshots** — step-by-step form fill images
- **Resume Diff** — your original vs. the tailored version (green = added, red = removed)
- **Cover Letter & Q&A** — editable cover letter + any free-text question answers

Click **Approve & Submit** (with inline confirmation) to submit the real application. Or **Edit in Chat** to have Claude refine anything first.

### Step 5: Track replies

Enable Outlook IMAP in `.env` and the server will check your inbox every 30 minutes for recruiter replies. Emails are classified (interview request / rejection / offer / follow-up) and the application status updates automatically. A pill appears on each application card in the Apply view.

### Chat

The **Chat** tab gives you a full-screen conversation with Claude. It always knows:
- Your complete profile
- All job listings with fit scores
- All application statuses
- Any pending email alerts

Try: *"Find Python backend jobs in Boston under $130k and shadow apply to the top 2"* — the Orchestrator breaks this into sub-tasks, runs them with tools, and reports back.

---

## Running Tests

```bash
# All tests
pytest

# Unit tests only
pytest tests/unit/ -v

# Integration tests
pytest tests/integration/ -v

# E2E (requires full pipeline + DB)
pytest tests/e2e/ -v

# With coverage
pytest --cov=. --cov-report=term-missing
```

Tests use an in-memory SQLite DB per test (via `tmp_path`), mock LLM (no real API calls), and mock Stagehand (no real browser). The full suite runs in under 30 seconds.

---

## Architecture Deep-Dive

### MCP Servers

Three MCP servers expose structured tool access to agents without custom API wiring:

| Server | Tools | Purpose |
|--------|-------|---------|
| `profile_server` | get_profile, update_profile, add_qa_note, get_resume_text, get_profile_completeness | Profile read/write |
| `jobs_server` | list_jobs, get_job, update_job_status, list_applications, get_application, get_application_memory | Jobs + applications DB |
| `files_server` | read/write_tailored_resume, read/write_cover_letter, list_screenshots, get_fill_log | Generated document access |

When the `mcp` package is not installed, each server falls back to a `_StubServer` whose tools are still callable programmatically — tests work without installing the full SDK.

### Multi-Agent System (Manager/Worker)

The **Orchestrator** is the Manager agent. It receives user goals via chat and delegates to Worker agents using Claude's native tool-use API:

```
User: "Find Python jobs in Boston, shadow apply to the top one"
  ↓
Orchestrator: calls search_jobs("Python", "Boston", 15)
  → JobScout: scrapes JSearch + Greenhouse + Lever, scores each, returns sorted list
Orchestrator: calls run_shadow_application(job_id="best-match")
  → Pipeline: tailor → fill → persist
Orchestrator: "Done. Shadow applied to Stripe (fit: 84). Review in the Apply tab."
```

The loop runs up to 8 iterations with a safety cap. Plain text from Claude = done.

### Universal Form Filler (Playwright + Claude Vision)

Instead of per-ATS selectors that break on every redesign, `filler/universal.py` uses a screenshot → instruction → fill loop:

1. Navigate to the apply URL
2. Take full-page screenshot
3. Ask Claude: "What fields are visible? What should I fill next?"
4. Execute the action (fill text, select dropdown, upload file)
5. Repeat until "form complete" or 3 failed retries
6. Shadow mode: stop before the submit button. Live mode: click it.

This handles Greenhouse, Lever, LinkedIn Easy Apply, Workday, and custom ATS without any site-specific code.

### Memory System

**Short-term**: Last 20 messages passed to Claude on every chat turn.

**Rolling summary**: Every 30 messages, Claude summarizes the conversation so far. The summary + last 20 messages = the context window, keeping token cost bounded regardless of conversation length.

**Per-company form notes**: After each fill, we record what worked, what failed, and any custom question labels for that company. Next time the user applies to the same company, these notes are injected into the fill prompt.

### Human-in-the-Loop Pause Points

1. **Fit threshold** — Jobs below ~40 are de-emphasized in the UI. User decides to proceed.
2. **Shadow review** — Every application pauses at `shadow_review` status. User sees screenshots, diffs, and cover letter before anything is submitted.
3. **Approval confirmation** — "Approve & Submit" requires an inline confirmation click. There is no way to accidentally submit.

### PII Encryption

`email`, `phone`, and `address` fields are encrypted at rest using Fernet symmetric encryption. The key is stored in `DB_ENCRYPTION_KEY` (auto-generated on first run if not set). No plaintext PII is written to SQLite.

---

## API Reference

Full interactive docs available at **http://localhost:8000/docs** (Swagger UI) and **http://localhost:8000/redoc**.

Key endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/profile | Full profile (all tables merged) |
| POST | /api/profile/resume | Upload PDF/DOCX, extract profile |
| GET | /api/jobs | Filtered job list |
| POST | /api/jobs/search | Start background job search |
| POST | /api/apply/{job_id}/shadow | Start shadow application |
| POST | /api/apply/{app_id}/approve | Approve → live submit |
| POST | /api/chat | SSE streaming chat |
| GET | /api/chat/history | Last 50 messages |
| POST | /api/email/sync | Scan Outlook inbox |
| GET | /api/tasks/{task_id} | Background task status |
| GET | /api/health | Liveness probe |

---

## Course Concepts Demonstrated

| Concept | Implementation |
|---------|----------------|
| **MCP as "USB for AI"** | 3 MCP servers expose profile, jobs, and files via standard tool interface. Agents never touch the DB directly — they call tools. |
| **Custom MCP Server** | `mcp_servers/jobs_server.py` bridges Claude to SQLite without custom API code in agents |
| **Manager/Worker Agents** | `orchestrator.py` delegates via Claude tool use to `job_scout`, `resume_writer`, `form_filler`, `profile_builder`, `email_tracker` |
| **Collaborative multi-agent** | Workers share data via MCP tools — decoupled, swappable, independently testable |
| **Human-in-the-Loop** | 3 pause points: fit review, shadow review with diffs, approve confirmation |
| **Long-term Memory** | SQLite persistence + rolling conversation summary + per-company form notes survive server restart |
| **State persistence** | DB survives restarts; checkpoint-style application states (pending → shadow_running → shadow_review → submitted) |
| **Prompt tunability** | Every agent behavior in `prompts/*.md` — edit the file, restart, behavior changes. No code changes. |
| **Email integration** | Outlook IMAP + Claude classification → auto-updates application status |

---

## DEV_MODE

Set `DEV_MODE=true` in `.env` to run the full pipeline without a real browser or external APIs:

- **JSearch** returns 5 hardcoded listings
- **Stagehand/Playwright** returns a mock `FillResult` with placeholder screenshots  
- **LLM** still hits the real Anthropic API (prompts are still tested)

This lets the entire UI flow work locally on day one.

```bash
DEV_MODE=true uvicorn api.main:app --reload
```

---

## Logs

The app writes structured JSON logs to `data/logs/app.log` (rotating, 10 MB × 5 files):

```bash
# See all errors
grep '"level": "ERROR"' data/logs/app.log | python -m json.tool

# Follow all LLM calls
grep '"input_tokens"' data/logs/app.log | tail -f | python -m json.tool

# Trace one application
grep '"app_id": "abc123"' data/logs/app.log | python -m json.tool
```

Human-readable output also streams to stdout during development.
