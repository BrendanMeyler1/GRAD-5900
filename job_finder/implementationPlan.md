# job_finder
## AI-Powered Job Application Automation System — Implementation Plan v4 (Final)
### IDE: Antigravity (agent-enabled IDE)

---

## 1. Architecture Overview

job_finder is a **multi-agent orchestration system** built on **LangGraph** (state machine with checkpointing), fronted by a **Decision Queue UI**, with a **local-first privacy architecture** ensuring PII never reaches remote LLMs.

### Design Principles
- **Decision Queue, not Dashboard** — The UI presents actionable approval prompts, not passive status lists
- **Shadow-first** — Every automated action starts in "draft/preview" mode; graduate to autonomous as trust builds
- **Reliability over intelligence** — A system that submits one application flawlessly beats one that orchestrates hundreds unreliably
- **Semi-automated, not fully autonomous** — Target 80% automation + 20% human input; aim toward confidence-aware auto-submit over time
- **Prompts as Code** — All agent behaviors live in versioned `/prompts/` files for transparency and tuning
- **PII Never Leaves Local** — Remote models see a tokenized "Experience Persona"; a local LLM handles final PII injection
- **One primary model early** — Single remote LLM + local LLM initially; model routing via abstraction layer later
- **Learn from failures** — Every failure is logged structurally so patterns surface automatically
- **Assistive, not bot-like** — Human-in-the-loop, aggressive throttling, and humanized behavior to respect platform ToS

---

## 2. System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        DECISION QUEUE UI                         │
│              (React + Tailwind — Approval Workflow)               │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │ Role Cards  │  │ Tailored Docs│  │ Application Tracker     │ │
│  │ (Approve/   │  │ (Preview +   │  │ (Sent → Interview →     │ │
│  │  Skip/Flag) │  │  Edit)       │  │  Offer/Reject)          │ │
│  └─────────────┘  └──────────────┘  └─────────────────────────┘ │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │ Human Assist Panel (prioritized: 🔴 BLOCKING → 🟡 → 🟢)    ││
│  │ + Confidence Visualization per field                         ││
│  │ + Batch Mode (approve N similar apps at once)                ││
│  └──────────────────────────────────────────────────────────────┘│
└──────────────────────────┬───────────────────────────────────────┘
                           │ FastAPI
┌──────────────────────────┴───────────────────────────────────────┐
│                     LANGGRAPH ORCHESTRATOR                        │
│           (State Machine + Checkpoints + State Recovery)          │
│                                                                  │
│  ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌────────────────┐  │
│  │Profile  │──▶│Job Scout│──▶│Fit      │──▶│Resume/Cover    │  │
│  │Analyst  │   │+ Ghost  │   │Scorer   │   │Tailor          │  │
│  │         │   │Ranker   │   │         │   │                │  │
│  └─────────┘   └─────────┘   └─────────┘   └───────┬────────┘  │
│       │                                             │            │
│       ▼                                  ┌──────────▼─────────┐ │
│  ┌──────────┐  ┌──────────────┐          │ PII Injector       │ │
│  │Normalizer│  │Account Mgr   │          │ (Local LLM:        │ │
│  │(canonical│  │+ Verification │◀─────────│  Ollama/Phi-3)     │ │
│  │ names)   │  │  Session      │          └──────────┬─────────┘ │
│  └──────────┘  │  Binder       │                     │           │
│                └──────┬───────┘                      │           │
│                       │      ┌───────────────────────▼─────────┐│
│                       └─────▶│ Form Interpreter (hybrid conf.) ││
│                              │ + Multi-Strategy Selectors      ││
│                              │ + Question Responder             ││
│                              │ → Playwright Filler              ││
│                              │ → Post-Upload Validator → PAUSE  ││
│                              └──────────────┬──────────────────┘│
│                                             │                    │
│  ┌───────────┐   ┌──────────────┐   ┌──────▼──────────────┐    │
│  │Learning   │◀──│Status Tracker │   │Replay Generalizer   │    │
│  │Loop (P5+) │   │(IMAP)        │   │(abstract traces)    │    │
│  └───────────┘   └──────────────┘   └─────────────────────┘    │
│                                                                  │
│  ┌──────────────┐  ┌───────────────────────────────────────┐    │
│  │ llm_router   │  │ failures.db + company_memory.db       │    │
│  └──────────────┘  └───────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────┴───────────────────────────────────────┐
│                        DATA & STORAGE                            │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │PII Vault     │  │ChromaDB      │  │Account Vault           │ │
│  │(Encrypted    │  │(Job + Company│  │(Encrypted credentials  │ │
│  │ SQLite)      │  │ Embeddings)  │  │ + session cookies)     │ │
│  └──────────────┘  └──────────────┘  └────────────────────────┘ │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │ ATS Templates (per-company Workday overrides)               ││
│  │ + Generalized Replay Traces + Company Memory                ││
│  └──────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Agent Specifications

### 3.1 Profile Analyst Agent
- **Model:** Primary (Claude initially, swappable via `llm_router`)
- **Prompt file:** `/prompts/profile_analyst.md`
- **Input:** Uploaded resume (PDF/DOCX)
- **Output:** Structured candidate profile JSON — skills, years of experience per domain, education, quantified achievements, career trajectory
- **Technique:** Chain-of-Thought extraction → structured output
- **Privacy:** Outputs tokenized "Experience Persona" with `{{PII_TOKENS}}`
- **Post-processing:** Runs through **Normalizer** to canonicalize school names, company names, job titles

### 3.2 Job Scout Agent
- **Model:** Primary (via `llm_router`) with web search tool access
- **Prompt file:** `/prompts/job_scout.md`
- **Pattern:** ReAct loop — Search → Evaluate → Refine → Search again
- **Sources:** Job board APIs (LinkedIn, Indeed, Greenhouse board feeds, Lever API, company career pages)
- **Output:** Candidate listings with metadata (posting date, source, company info, recruiter signals, **Alive Score**)
- **Ghost Job Handling:** Rank + label (see §4), NOT hard filter
- **Smart Skip:** Auto-deprioritize broken links, low alive + low fit, excessive HIGH-sensitivity fields

### 3.3 Fit Scorer Agent
- **Model:** Primary (via `llm_router`)
- **Prompt file:** `/prompts/fit_scorer.md`
- **Technique:** Analogical Prompting
- **Output:** Fit score (0–100), breakdown, gap analysis, talking points
- **Threshold:** ≥75 auto-proceed; 50–74 with warning; <50 deprioritized

### 3.4 Resume Tailor Agent
- **Model:** Primary (via `llm_router`)
- **Prompt file:** `/prompts/resume_tailor.md`
- **Technique:** CoT — top 5 requirements → map to experience → rewrite with quantified impact
- **Access via MCP:** Read-only `/data/raw/master_bullets.md`

### 3.5 Cover Letter Agent
- **Model:** Primary (via `llm_router`)
- **Prompt file:** `/prompts/cover_letter.md`

### 3.6 PII Injector (Local Only)
- **Model:** Ollama (Phi-3 or Llama 3)
- **Uses Normalizer** for context-aware substitution (e.g., "University of Connecticut" when form expects full name, "UConn" when it doesn't)
- **Respects PII Access Levels** (§5.5)

### 3.7 Form Interpreter Agent
- **Model:** Primary (via `llm_router`)
- **Prompt file:** `/prompts/form_interpreter.md`
- **Purpose:** Reads ATS form DOM → generates structured fill plan with **hybrid confidence scores** (rule-based, see §8.1)
- **Multi-strategy selector resolution** (see §8.2) — if primary selector fails, cascades through fallbacks
- **Checks generalized replay traces** for matching ATS patterns before invoking LLM
- **Output:** `fill_plan.json` with per-field confidence + LLM explanation

### 3.8 Question Responder Agent *(NEW — high value)*
- **Model:** Primary (via `llm_router`)
- **Prompt file:** `/prompts/question_responder.md`
- **Purpose:** Generates short, ATS-friendly answers to free-text questions ("Why do you want to work here?", "Describe a challenge", "Expected salary")
- **Grounded in:** Resume + job description + company research + fit score talking points
- **Cached:** Answers cached per company/role in Company Memory; similar questions across jobs reuse cached answers with minor tailoring
- **Reduces human input significantly** — most "weird questions" get handled automatically

### 3.9 Account Manager + Verification Session Binder
- **Prompt file:** `/prompts/account_manager.md`
- **Storage:** Encrypted account vault (separate from PII vault)
- **Verification Session Binder:** Stores browser session cookies + context ID at account creation; reopens SAME session for email verification links
- **Human escalation:** 2FA, unusual CAPTCHAs, verification requiring logged-in state

### 3.10 Post-Upload Validator
- **Prompt file:** `/prompts/post_upload_validator.md`
- **Uses Normalizer** to reconcile format differences

### 3.11 Status Tracker Agent
- **Model:** Primary via `llm_router`
- **Prompt file:** `/prompts/status_classifier.md`
- **Output:** `RECEIVED` | `REJECTED` | `INTERVIEW_SCHEDULED` | `FOLLOW_UP_NEEDED` | `OFFER` | `NO_RESPONSE_30D`

### 3.12 Learning Loop Agent *(Phase 5+)*
- Consumes `outcomes.db` + `failures.db` + Company Memory
- Interview Conversion Feedback Loop: correlates resume wording + job type + response rates

---

## 4. Ghost Job Detection — Rank + Label (Not Filter)

| Signal | Method | Weight |
|---|---|---|
| **Posting Freshness** | Compare dates across boards; flag if >30 days or refreshed without changes | High |
| **Recruiter Activity** | Hiring manager has recent LinkedIn activity? | High |
| **Company Headcount** | Growing or shrinking? | Medium |
| **Financial Health** | News API — layoff keywords, hiring freezes | Medium |
| **URL Provenance** | Redirects through 3+ aggregator domains? | Medium |
| **Duplicate Detection** | Embedding similarity — reposted monthly? | High |
| **Portal Check** | "Apply" link leads to functional page? | High |

### Smart Skip Logic
Auto-deprioritize (never hide) when:
- Apply link broken
- Alive < 0.4 AND Fit < 60
- 3+ HIGH-sensitivity fields required
- User can always override

---

## 5. Data Privacy Architecture

### 5.1 PII Vault
- Encrypted SQLite (sqlcipher or Fernet)
- Only local PII Injector reads from vault
- Encryption key in `.env`, never committed

### 5.2 Account Vault (Separate)
- Encrypted SQLite — usernames, passwords, **session cookies, browser context IDs**
- Only Account Manager reads/writes

### 5.3 Token Flow
```
[Resume] → Profile Analyst strips PII → Normalizer canonicalizes
    → Experience Persona with {{TOKENS}}
    → All remote agents work with {{TOKENS}} only
    → PII Injector (LOCAL) substitutes → Final document
    → Submitter uploads
```

### 5.4 PII_MANIFEST.json
```json
{
  "tokens": {
    "{{FULL_NAME}}": "Legal name",
    "{{EMAIL}}": "Contact email",
    "{{PHONE}}": "Phone number",
    "{{ADDRESS}}": "Mailing address",
    "{{LINKEDIN}}": "LinkedIn URL",
    "{{GITHUB}}": "GitHub URL",
    "{{SCHOOL}}": "Education institution (canonical + variants)",
    "{{EMPLOYER_N}}": "Employer names (canonical + variants)"
  },
  "storage": "Local encrypted SQLite (sqlcipher)",
  "remote_exposure": "NONE",
  "local_model": "Ollama (Phi-3/Llama3) handles PII injection",
  "normalization": "School/company names stored with canonical + variant forms"
}
```

### 5.5 PII Access Levels

| Level | Fields | Behavior |
|---|---|---|
| **LOW** | Name, email, LinkedIn, GitHub | Auto-fill |
| **MEDIUM** | Address, phone, work auth | Auto-fill + notify |
| **HIGH** | SSN, DOB, gov ID, salary history | **Manual approval required** |

### 5.6 Normalizer
**File:** `pii/normalizer.py`

```python
{
    "{{SCHOOL}}": {
        "canonical": "University of Connecticut",
        "variants": ["UConn", "UCONN", "U of Connecticut"]
    },
    "{{EMPLOYER_1}}": {
        "canonical": "International Business Machines Corporation",
        "variants": ["IBM", "I.B.M."]
    }
}
```

Used by: Profile Analyst, PII Injector, Post-Upload Validator.

### 5.7 .gitignore
```
.env
data/raw/
pii_vault.db
account_vault.db
company_memory.db
feedback/*.json
feedback/failures.db
replay_traces/
*.log
```

---

## 6. LLM Router

Single model early, specialize later.

| Task | Phase 1–3 | Phase 5+ |
|---|---|---|
| Profile analysis | Primary | Claude |
| Job scouting | Primary | GPT-4o |
| Fit scoring | Primary | Claude |
| Resume/cover letter | Primary | Claude |
| Form interpretation | Primary | GPT-4o |
| Question responding | Primary | Claude |
| Status classification | Primary | GPT-4o-mini |
| PII injection | Local (Ollama) | Local (Ollama) |

---

## 7. Knowledge & Retrieval Layer

### 7.1 Vector Store (ChromaDB — Local)
- Collections: `job_descriptions`, `company_profiles`, `resume_versions`, `master_bullets`

### 7.2 Hybrid Search (RAG 2.0)
- Vector similarity + BM25; self-correcting retrieval

### 7.3 ATS Templates (Per-Company Workday Overrides)
```
templates/
  greenhouse.json
  lever.json
  workday/
    _base.json
    stripe.json
    amazon.json
    meta.json
```

Workday treated as **multiple ATS platforms**. Each company instance gets its own override file.

### 7.4 Generalized Replay Traces *(Refined)*

**Raw traces** are saved per application. A **Replay Generalizer** (`replay/generalizer.py`) abstracts them into reusable patterns:

Instead of storing brittle selectors:
```json
"#first_name"
```

Store semantic descriptors:
```json
{
  "label": "First Name",
  "type": "text_input",
  "relative_position": "top_form",
  "aria_label": "First name",
  "selector_that_worked": "#first_name",
  "strategy_used": "exact_css"
}
```

When reusing a trace, the system **remaps dynamically** — matching by semantic descriptor, not brittle CSS. This turns replay into **pattern learning**, not copy-paste.

### 7.5 Company Memory *(NEW)*

**File:** `feedback/company_memory.db`

Stores per-company knowledge that accumulates across applications:

```json
{
  "company": "Stripe",
  "ats": "greenhouse",
  "preferred_answers": {
    "why_work_here": "cached response from last application",
    "salary_expectation": "180000"
  },
  "field_patterns": {
    "education": "expects full university name",
    "work_auth": "dropdown, not text"
  },
  "replay_trace_ids": ["stripe_2026-04-09", "stripe_2026-04-22"],
  "last_applied": "2026-04-22",
  "outcome": "INTERVIEW_SCHEDULED"
}
```

Next application to the same company (or same ATS) becomes **dramatically faster** — Question Responder reuses cached answers, Form Interpreter reuses field patterns.

### 7.6 GraphRAG (Neo4j — Phase 5+, deferred)

---

## 8. Reliability & Recovery Systems

### 8.1 Hybrid Confidence Scoring

**Rule-based, NOT LLM-generated:**

```python
confidence = (
    selector_match_score * 0.4 +
    label_similarity_score * 0.3 +
    template_match_score * 0.3
)
```

| Component | Computation |
|---|---|
| `selector_match_score` | 1.0 exact match to template, 0.5 partial, 0.0 none |
| `label_similarity_score` | Cosine similarity between DOM label and expected field name |
| `template_match_score` | 1.0 if in ATS template, 0.0 if dynamic/unknown |

LLM provides **explanation**, not score. Thresholds:
- ≥ 0.8 → auto-fill
- 0.5–0.79 → fill + flag (🟡)
- < 0.5 → escalate (🔴)

### 8.2 Multi-Strategy Selector Resolution *(NEW — critical for ATS resilience)*

**File:** `browser/selector_resolver.py`

CSS selectors WILL break. Dynamic IDs, DOM reshuffles, A/B tested layouts. The system uses a **fallback chain**:

```python
selector_strategies = [
    exact_css_selector,       # From template or replay trace
    label_based_xpath,        # Find input associated with label text
    aria_label_match,         # Match via aria-label attribute
    placeholder_text_match,   # Match via placeholder text
    spatial_proximity_match,  # Find input nearest to label text in DOM
]
```

**Behavior:**
1. Try strategies in order
2. First match wins
3. Log which strategy succeeded → feeds back into replay traces
4. If ALL fail → escalate to Human Assist Panel (🔴 BLOCKING)

Over time, replay traces accumulate which strategies work for which ATS, making the system more resilient without code changes.

### 8.3 State Recovery

Checkpoint per application: last step, browser URL, filled fields, generated docs, login status.
Failure → checkpoint → notify → [Resume] restores and continues.

### 8.4 Three Operating Modes

| Mode | Behavior | Use |
|---|---|---|
| **Dry Run** | Full pipeline, no browser | Debugging, prompt testing |
| **Shadow** | Browser fills fields, STOPS before submit | ATS validation |
| **Live** | Submission with approval | Production |

### 8.5 Confidence-Aware Auto-Submit *(Phase 5+ — path to scalability)*

When ALL of the following are true:
- Every field confidence ≥ 0.9
- No HIGH-sensitivity fields
- ATS is a known template (Greenhouse/Lever)
- Replay trace exists for this ATS
- User has opted in to auto-submit for this tier

→ Allow submission **without manual approval**.

This is the graduation path from "assistive tool" to "autonomous agent" — earned through demonstrated reliability, not assumed.

### 8.6 Batch Mode *(NEW — major UX improvement)*

When multiple similar jobs are queued (same ATS, same resume, similar role):

```
┌─────────────────────────────────────────────┐
│ Batch Apply: 4 Greenhouse Backend roles     │
│                                             │
│ ✅ Acme Corp       Fit: 91  Alive: 0.88    │
│ ✅ Widget Inc      Fit: 87  Alive: 0.92    │
│ ✅ FooBar Labs     Fit: 85  Alive: 0.79    │
│ ⚠️ Baz Systems    Fit: 78  Alive: 0.65    │
│                                             │
│ Shared: Resume v3, Cover Letter template A  │
│ Per-job: Cover letter intro customized      │
│                                             │
│ [Approve All 4] [Review Individually]       │
└─────────────────────────────────────────────┘
```

**One approval → multiple submissions.** Each still gets its own cover letter intro and company-specific answers, but the human review overhead drops dramatically.

### 8.7 Rate Limiting + Humanization

- Randomized delays (2–8s)
- Character-by-character typing
- Daily cap (default 10)
- Per-ATS cooldown (3/hour)
- Randomized mouse, realistic scroll

### 8.8 Failure Logging

**`feedback/failures.db`** — every failure logged structurally:

| Column | Example |
|---|---|
| `ats_type` | workday |
| `company` | Amazon |
| `failure_step` | form_fill |
| `error_type` | dropdown_mismatch |
| `field_name` | years_of_experience |
| `selector_strategy_tried` | [css, xpath, aria] |
| `fix_applied` | manual_override |
| `timestamp` | 2026-04-09T14:32:00Z |

Surfaces patterns: "80% of Workday failures = dropdown mismatch."

---

## 9. Human Assist Panel — Prioritized

| Priority | Example | Behavior |
|---|---|---|
| 🔴 **BLOCKING** | CAPTCHA, SSN, email verify, 2FA, all selectors failed | Paused until resolved |
| 🟡 **IMPORTANT** | Dropdown mismatch, low-confidence field | Continues with flag |
| 🟢 **OPTIONAL** | "How did you hear about us", cover letter tweak | Auto-skip after timeout |

### Confidence Visualization
```
First Name     → 98% ✅
Education      → 72% ⚠️ (normalized from "UConn")
Salary         → 40% 🔴 (needs input)
```

### Batch Handling
🟢 items can be approved/skipped in bulk. 🔴 presented individually.

---

## 10. Legal / ToS Awareness

Automating account creation and form submission on platforms like Workday and Greenhouse may conflict with their Terms of Service.

**Mitigations already built in:**
- Human-in-the-loop on every submission (or confidence-gated auto-submit)
- Aggressive throttling + humanized behavior
- System is "assistive" — fills forms for you, doesn't impersonate
- No scraping behind auth walls
- Daily caps prevent spam-like behavior

**Additional recommendations:**
- Keep the public README honest about what the tool does
- Don't advertise as "bypassing" or "botting" — frame as "assistive automation"
- If a platform sends a cease-and-desist or blocks the account, respect it
- Consider offering a "manual mode" where the system prepares everything but you literally click through the form yourself

---

## 11. MCP Integration

### Core MCP Servers (Built in Phases 1–3)

### 11.1 Local File MCP Server — read-only master bullets access
### 11.2 Browser-MCP Bridge — DOM as structured tree for Form Interpreter
### 11.3 Database MCP Server — bridges to SQLite, ChromaDB, Company Memory

### MCP Extensions (Tag-On — Add When Ready)

These are natural extensions that unify the entire system under MCP. Each can be added independently without refactoring the core pipeline — the agents already work via direct API calls, and migrating them to MCP is a drop-in swap.

### 11.4 Email MCP Server *(replaces raw IMAP/Gmail API)*

The Status Tracker currently talks to IMAP directly. Wrapping it in MCP standardizes the interface and makes the email layer swappable (Gmail today, Outlook tomorrow, self-hosted later).

**Tools exposed:**
```
search_inbox(query, since, folder) → list of email summaries
get_email(id) → full email body + metadata
classify_email(id) → status label (REJECTED, INTERVIEW, etc.)
mark_processed(id) → flag email as handled
get_verification_links(since) → extract verification URLs from recent emails
```

**Why it matters:**
- Account Manager's Verification Session Binder currently needs to poll for verification emails separately. With this MCP server, it calls `get_verification_links(since=account_creation_time)` through the same protocol as everything else.
- The Status Tracker becomes a pure LLM agent — it calls `search_inbox` and `classify_email` via MCP tools, with no IMAP knowledge baked into the agent code.
- If you later want to add Slack notifications for interview invites or rejection alerts, you add a Slack MCP server alongside — agents don't change.

**Implementation:**
```
mcp_servers/
  email_server.py         # IMAP/Gmail API wrapper exposing MCP tools
```

**Agent changes:** Status Tracker and Account Manager swap from direct API calls to MCP tool calls. Prompt files stay the same. Logic stays the same. Only the transport changes.

### 11.5 PII Vault MCP Server *(enforces access boundary architecturally)*

Currently, the PII Injector imports `vault.py` directly. This works, but the security boundary is enforced by convention ("only the Injector should call this"). An MCP server enforces it architecturally.

**Tools exposed:**
```
get_token_value(token, context?) → resolved PII value
    # context allows Normalizer-aware resolution:
    # get_token_value("{{SCHOOL}}", context="full_name") → "University of Connecticut"
    # get_token_value("{{SCHOOL}}", context="abbreviation") → "UConn"

get_field_sensitivity(field_name) → LOW | MEDIUM | HIGH

list_tokens() → available token names (no values — for Form Interpreter planning)
```

**Tools NOT exposed (by design):**
```
# No write access — vault is populated manually or by Profile Analyst only
# No bulk export — cannot dump all PII at once
# No access to encryption keys
```

**Why it matters:**
- The PII Vault MCP server runs **locally only** — it's never exposed to remote agents. The Ollama-based PII Injector calls it on localhost. Remote LLMs (Claude, GPT-4o) never have a tool that resolves PII tokens.
- If a remote agent somehow tried to call `get_token_value`, the MCP routing layer rejects it — the server isn't registered for remote agents. This is defense-in-depth: even a prompt injection attack on a remote model can't exfiltrate PII because the tool literally doesn't exist in their tool list.
- `list_tokens()` (names only, no values) IS available to the Form Interpreter so it can plan field mappings without seeing actual data.

**Implementation:**
```
mcp_servers/
  pii_vault_server.py    # Local-only MCP server, restricted tool set
```

**Agent changes:** PII Injector calls MCP tools instead of importing vault.py. The Normalizer context parameter replaces the current logic where the Injector decides which name variant to use.

### 11.6 MCP Architecture Summary (Core + Extensions)

```
┌─────────────────────────────────────────────────────┐
│                  MCP Protocol Layer                  │
│          ("USB for AI" — one protocol, any tool)     │
├──────────────┬──────────────┬───────────────────────┤
│   CORE (P1-3)│  EXTENSIONS  │  FUTURE (P5+)         │
├──────────────┼──────────────┼───────────────────────┤
│ File Server  │ Email Server │ Slack/Calendar Server │
│ Browser      │ PII Vault    │ Jira/Notion Server    │
│ Bridge       │ Server       │ (if tracking in       │
│ Database     │ (local-only) │  external tools)      │
│ Server       │              │                       │
└──────────────┴──────────────┴───────────────────────┘

Agent access rules:
  Remote LLMs  → File, Browser, Database, Email
  Local LLM    → File, Browser, Database, Email, PII Vault
  No agent     → raw filesystem, raw IMAP, raw SQLite
```

Every data source behind MCP means: agents are portable, access is controllable, and swapping implementations (Gmail → Outlook, SQLite → Postgres) never touches agent code.

### 11.7 When to Add These

| Extension | Trigger | Effort |
|---|---|---|
| Email MCP Server | When Status Tracker is working and you want cleaner verification flow | ~1 day (wrapping existing IMAP code) |
| PII Vault MCP Server | When you want defense-in-depth on PII access, or before open-sourcing | ~0.5 day (thin wrapper over vault.py) |

Neither is blocking for Phase 1–4. Both are recommended before Phase 5+ (autonomy/auto-submit), where the security boundary becomes more critical because humans are reviewing less.

---

## 12. Evaluation Framework

### 12.1 RAGAS — retrieval quality
### 12.2 LLM-as-Judge — resume/cover letter quality (`/prompts/judges/`)
### 12.3 Submission Reliability Metrics
- Fill accuracy, success rate per ATS, recovery rate, human intervention rate, **time-to-apply**
### 12.4 Outcome Tracking
- Applications → responses → interviews → offers (segmented by role, company, fit, ATS)

---

## 13. Project Structure

```
job_finder/
├── README.md
├── .env.example
├── .gitignore
├── PII_MANIFEST.json
│
├── prompts/
│   ├── profile_analyst.md
│   ├── job_scout.md
│   ├── fit_scorer.md
│   ├── resume_tailor.md
│   ├── cover_letter.md
│   ├── form_interpreter.md
│   ├── question_responder.md     # NEW
│   ├── post_upload_validator.md
│   ├── account_manager.md
│   ├── status_classifier.md
│   ├── learning_loop.md          # Phase 5+
│   ├── ats/
│   │   ├── greenhouse.md
│   │   ├── lever.md
│   │   └── workday.md
│   └── judges/
│       ├── resume_judge.md
│       └── cover_letter_judge.md
│
├── agents/
│   ├── profile_analyst.py
│   ├── job_scout.py
│   ├── fit_scorer.py
│   ├── resume_tailor.py
│   ├── cover_letter.py
│   ├── form_interpreter.py
│   ├── question_responder.py     # NEW
│   ├── post_upload_validator.py
│   ├── account_manager.py        # Includes Verification Session Binder
│   ├── pii_injector.py
│   ├── submitter.py
│   ├── status_tracker.py
│   └── learning_loop.py          # Phase 5+
│
├── llm_router/
│   ├── router.py
│   └── config.yaml
│
├── graph/
│   ├── state.py
│   ├── workflow.py
│   └── checkpoints.py
│
├── pii/
│   ├── vault.py
│   ├── account_vault.py          # Session cookies included
│   ├── tokenizer.py
│   ├── normalizer.py
│   ├── field_classifier.py
│   └── sanitizer.py
│
├── retrieval/
│   ├── embeddings.py
│   ├── vector_store.py
│   └── hybrid_search.py
│
├── templates/
│   ├── greenhouse.json
│   ├── lever.json
│   └── workday/
│       ├── _base.json
│       ├── stripe.json
│       ├── amazon.json
│       └── meta.json
│
├── replay/
│   ├── traces/                   # Raw per-application traces
│   │   └── .gitkeep
│   └── generalizer.py            # NEW — abstracts to semantic descriptors
│
├── mcp_servers/
│   ├── filesystem_server.py
│   ├── browser_bridge.py
│   ├── database_server.py
│   ├── email_server.py           # Extension — wraps IMAP/Gmail as MCP
│   └── pii_vault_server.py       # Extension — local-only, restricted tools
│
├── browser/
│   ├── playwright_driver.py
│   ├── humanizer.py
│   ├── selector_resolver.py      # NEW — multi-strategy fallback chain
│   ├── confidence_scorer.py
│   └── ats_strategies/
│       ├── greenhouse.py
│       ├── lever.py
│       └── workday.py
│
├── data/
│   ├── raw/                      # NEVER pushed
│   │   └── master_bullets.md
│   └── processed/
│
├── feedback/
│   ├── outcomes.db
│   ├── failures.db
│   ├── company_memory.db         # NEW
│   └── analysis.py
│
├── evals/
│   ├── ragas_config.yaml
│   ├── judge_runner.py
│   └── test_sets/
│
├── dashboard/
│   ├── src/
│   │   ├── components/
│   │   │   ├── DecisionQueue.jsx
│   │   │   ├── BatchApproval.jsx     # NEW
│   │   │   ├── ApplicationTracker.jsx
│   │   │   ├── ResumePreview.jsx
│   │   │   ├── HumanAssistPanel.jsx
│   │   │   ├── ConfidenceView.jsx
│   │   │   └── InsightsPanel.jsx
│   │   └── App.jsx
│   └── package.json
│
├── api/
│   ├── main.py
│   ├── routes/
│   └── middleware/
│       └── pii_guard.py
│
├── tests/
│   ├── test_agents/
│   ├── test_pii/
│   ├── test_normalizer/
│   ├── test_confidence_scorer/
│   ├── test_selector_resolver/   # NEW
│   ├── test_form_interpreter/
│   ├── test_account_manager/
│   └── test_ghost_detection/
│
└── docker-compose.yml
```

---

## 14. Phased Implementation Plan

### Phase 1 — Foundation (Weeks 1–2)
**Goal:** Core infrastructure, profile analysis, privacy layer

- [ ] Project scaffolding
- [ ] LLM Router — single-model config
- [ ] PII Vault + Tokenizer + Normalizer + Field Classifier
- [ ] Profile Analyst Agent → normalized Experience Persona
- [ ] LangGraph skeleton
- [ ] FastAPI + PII guard middleware
- [ ] Ollama setup
- [ ] `/prompts/` initial set
- [ ] Tests: PII tokenization, normalization

**Deliverable:** Upload resume → tokenized, normalized Experience Persona

### Phase 2 — Core Intelligence + Submission Foundation (Weeks 3–5)
**Goal:** Discovery, matching, docs, AND form-filling infrastructure

- [ ] Job Scout + Ghost Ranker + Smart Skip
- [ ] Fit Scorer
- [ ] Resume Tailor + Cover Letter
- [ ] **Question Responder Agent** ← reduces human input dramatically
- [ ] ChromaDB + hybrid search
- [ ] Local File MCP Server
- [ ] Account Manager + Verification Session Binder
- [ ] Form Interpreter + hybrid confidence scorer
- [ ] **Multi-strategy selector resolver**
- [ ] ATS Template: Greenhouse
- [ ] Post-Upload Validator
- [ ] Failure logging DB
- [ ] **Company Memory DB** (start populating)
- [ ] LLM-as-Judge evals

**Deliverable:** Job URL → fit score + tailored docs + fill plan + question answers + confidence scores

### Phase 2.5 — Dry Run + Shadow Mode (Week 6)

**Dry Run:** 10 real listings, validate everything, fix issues.

**Shadow Mode:**
- [ ] Playwright + Browser-MCP for Greenhouse
- [ ] Humanizer
- [ ] Shadow fill + confidence visualization + prioritized Human Assist
- [ ] **Save replay traces → run through Generalizer**
- [ ] Log all failures

**Deliverable:** Greenhouse app filled + previewed. Generalized replay traces saved.

### Phase 3 — Live Submission + Tracking (Weeks 7–8)
**Goal:** First real submissions, Decision Queue UI

- [ ] Live submission with approval
- [ ] State Recovery
- [ ] Status Tracker + email integration
- [ ] Application Tracker DB
- [ ] **Decision Queue UI** (full: role cards, docs, confidence, Human Assist, tracker)
- [ ] **Batch Mode** for similar jobs
- [ ] Replay trace reuse for repeat Greenhouse apps
- [ ] Daily cap + cooldowns
- [ ] Time-to-apply tracking
- [ ] Analyze `failures.db` → fix top failure

**Deliverable:** First real applications. Greenhouse reliable. Batch mode working.

### Phase 4 — ATS Expansion (Weeks 9–11)
**Goal:** Lever + Workday support

- [ ] Lever templates + strategies
- [ ] Workday `_base.json` + 2–3 company overrides
- [ ] Ghost Ranker enhancements
- [ ] Form Interpreter improvements from failure data
- [ ] Company Memory accumulation
- [ ] Reliability metrics dashboard

**Deliverable:** Greenhouse ✅ Lever ✅ Workday ⚠️ (partially, per-company)

### Phase 5+ — Intelligence & Autonomy (Weeks 12+)
**Goal:** Self-improvement, earned autonomy

- [ ] Learning Loop Agent (outcomes + failures + company memory)
- [ ] **Interview Conversion Feedback Loop** — correlate wording → results
- [ ] **Confidence-aware auto-submit** (opt-in, high-confidence only)
- [ ] GraphRAG (Neo4j)
- [ ] Multi-model routing
- [ ] Insights Panel — funnels, trends, bottlenecks
- [ ] A/B prompt testing
- [ ] Docker Compose

---

## 15. Application Submission Flow (Final)

```
1. Job Scout finds listing → Ghost Ranker: Alive 0.85 ✅
   └→ Smart Skip: passes

2. Fit Scorer: 87/100 → tailoring

3. Resume Tailor + Cover Letter (tokenized, normalized)
   └→ LLM-as-Judge quality check

4. Question Responder generates answers for free-text fields
   └→ Check Company Memory for cached answers
   └→ Cache new answers

5. Decision Queue:
   ┌────────────────────────────────────┐
   │ Senior Backend Eng — Acme Corp    │
   │ Fit: 87  Alive: 0.85 ✅           │
   │ [View Resume] [View Cover Letter] │
   │ [View Answers]                    │
   │ [Approve] [Edit] [Skip]           │
   └────────────────────────────────────┘
   (or Batch Mode for similar jobs)

6. Account Manager:
   └→ Existing? → Restore session → Login
   └→ New? → Create (Session Binder stores cookies for verification)

7. Form Interpreter:
   └→ Match to Greenhouse template
   └→ Check generalized replay traces
   └→ Multi-strategy selector resolution
   └→ Hybrid confidence scoring
   └→ Fill plan generated

8. PII Injector (LOCAL):
   └→ Normalizer picks correct name form
   └→ LOW: auto | MEDIUM: notify | HIGH: block

9. Playwright fills (humanized)

10. Resume uploaded → ATS autofills

11. Post-Upload Validator → fix mismatches

12. Shadow preview + confidence viz:
    First Name     → 98% ✅ (css selector)
    Education      → 72% ⚠️ (aria_label fallback, normalized)
    Why Work Here  → 90% ✅ (Question Responder, cached)
    Salary         → 40% 🔴 (needs input)

    🔴 BLOCKING: Salary (1 item)
    🟡 IMPORTANT: Education verify (1 item)
    🟢 OPTIONAL: "Referral source" — auto-skip in 30s

13. Resolve blockers → [Submit]

14. Trace saved → Generalizer abstracts → Company Memory updated
    Application Tracker: SUBMITTED
    Status Tracker monitors inbox
    Failures (if any) → failures.db
```

---

## 16. Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph |
| Backend | Python / FastAPI |
| Frontend | React + Tailwind |
| LLM Abstraction | `llm_router` |
| Remote LLM | Claude OR GPT-4o (one to start) |
| Local LLM | Ollama (Phi-3/Llama 3) |
| PII | SQLite + sqlcipher |
| Accounts | SQLite + sqlcipher (separate) |
| Vectors | ChromaDB |
| Browser | Playwright + MCP bridge + humanizer + selector resolver |
| Email | IMAP / Gmail API |
| Eval | RAGAS + LLM-as-Judge |
| Protocol | MCP |
| Containers | Docker Compose (Phase 5+) |

---

## 17. Security Checklist

- [ ] `.env` in `.gitignore`
- [ ] `.env.example` with dummy values
- [ ] `data/raw/` in `.gitignore`
- [ ] `pii_vault.db`, `account_vault.db`, `company_memory.db` in `.gitignore`
- [ ] `replay/traces/` in `.gitignore`
- [ ] `PII_MANIFEST.json` documents protections
- [ ] `feedback/` DBs and logs in `.gitignore`
- [ ] README: "PII Vault is local-only. No personal data transmitted to remote LLMs."
- [ ] PII sanitizer middleware
- [ ] All test fixtures use synthetic data
- [ ] Pre-commit hook scans for PII patterns

---

## 18. Risk Mitigations

| Risk | Mitigation |
|---|---|
| ATS form failures | Form Interpreter + templates + hybrid confidence + multi-strategy selectors + Post-Upload Validator + Shadow Mode |
| Selector fragility | 5-strategy fallback chain; log which works; feed into replay traces |
| Workday complexity | Per-company overrides; treat as multiple ATS |
| Free-text questions | Question Responder + Company Memory caching |
| Mid-application crashes | State Recovery checkpoint/resume |
| Account creation | Account Manager + Session Binder + human escalation |
| Verification emails | Session Binder reopens same context |
| Ghost job false negatives | Rank + label; user decides |
| PII leakage | Tokenization; local injection; field levels; Normalizer; sanitizer |
| Name mismatches | Normalizer: canonical + variants |
| ATS detection/blocking | Humanizer; caps; cooldowns; assistive framing |
| ToS concerns | Human-in-the-loop; throttling; honest README |
| Approval fatigue | Batch Mode; confidence-aware auto-submit (Phase 5+) |
| Repeated failures | `failures.db` surfaces patterns automatically |
| Over-engineering | GraphRAG, learning loop, multi-model all Phase 5+ |

---

## 19. Build Order (Recommended)

**Step 1:** Greenhouse end-to-end ONLY. Ignore everything else until you can reliably submit 3–5 applications.

**Step 2:** Add replay traces + failure logging. Start accumulating data.

**Step 3:** Expand to Lever.

**Step 4:** Carefully attempt Workday (per-company).

Everything else follows from reliability on the core path.

---

# APPENDICES — Agent Implementation Reference

> **For the coding agent:** These appendices contain the concrete schemas, contracts, setup instructions, and build sequences you need to implement this plan without guessing. When the main plan says "structured JSON" or "state schema," the definition is here.

---

## Appendix A: Environment Setup

### A.1 Prerequisites
```
Python >= 3.11
Node.js >= 20 LTS
Ollama (latest)
```

### A.2 Initial Setup Sequence
```bash
# 1. Clone and enter project
git clone <repo_url>
cd job_finder

# 2. Python environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

# 3. Ollama (local LLM for PII injection)
ollama pull phi3          # or: ollama pull llama3
ollama serve              # runs on localhost:11434

# 4. Environment variables
cp .env.example .env
# Edit .env with real values (see A.3)

# 5. Initialize databases
python -m job_finder.setup.init_db  # creates pii_vault.db, account_vault.db, outcomes.db, failures.db, company_memory.db

# 6. Dashboard (separate terminal)
cd dashboard
npm install
npm run dev               # runs on localhost:5173

# 7. API server
cd ..
uvicorn api.main:app --reload --port 8000
```

### A.3 .env.example
```env
# LLM Provider (pick one as primary for Phases 1-3)
ANTHROPIC_API_KEY=sk-ant-xxxxx
# OPENAI_API_KEY=sk-xxxxx

# Primary model (used by llm_router in single-model mode)
PRIMARY_MODEL=claude-sonnet-4-20250514
# PRIMARY_MODEL=gpt-4o

# Local LLM
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=phi3

# PII Vault encryption
PII_VAULT_KEY=generate-a-fernet-key-here

# Account Vault encryption (separate key)
ACCOUNT_VAULT_KEY=generate-a-different-fernet-key-here

# Email (for Status Tracker)
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=your-email@gmail.com
IMAP_PASSWORD=app-specific-password

# ChromaDB
CHROMA_PERSIST_DIR=./data/chroma

# API
API_HOST=localhost
API_PORT=8000
DASHBOARD_URL=http://localhost:5173

# Rate limits
DAILY_APPLICATION_CAP=10
PER_ATS_HOURLY_CAP=3
```

### A.4 requirements.txt (Core)
```
# Orchestration
langgraph>=0.2.0
langchain-core>=0.3.0

# LLM Providers
anthropic>=0.39.0
openai>=1.50.0

# Local LLM
ollama>=0.3.0

# Web framework
fastapi>=0.115.0
uvicorn>=0.32.0
pydantic>=2.9.0

# Database
sqlcipher3>=0.5.0
chromadb>=0.5.0

# Browser automation
playwright>=1.48.0

# Embeddings & search
sentence-transformers>=3.0.0
rank-bm25>=0.2.2

# Document generation
python-docx>=1.1.0
reportlab>=4.2.0

# Email
imapclient>=3.0.0

# MCP
mcp>=1.0.0

# Utilities
python-dotenv>=1.0.0
cryptography>=43.0.0
httpx>=0.27.0

# Testing
pytest>=8.3.0
pytest-asyncio>=0.24.0
```

---

## Appendix B: Data Schemas & Interface Contracts

> **Critical:** These are the JSON shapes agents pass to each other. Every agent's input/output MUST conform to these schemas. When building an agent, check its input schema (what it receives) and output schema (what it returns).

### B.1 Experience Persona (Profile Analyst → everything downstream)
```json
{
  "persona_id": "uuid",
  "created_at": "2026-04-09T14:00:00Z",
  "contact": {
    "full_name": "{{FULL_NAME}}",
    "email": "{{EMAIL}}",
    "phone": "{{PHONE}}",
    "address": "{{ADDRESS}}",
    "linkedin": "{{LINKEDIN}}",
    "github": "{{GITHUB}}"
  },
  "summary": "8+ years backend engineering experience...",
  "skills": {
    "languages": ["Python", "Go", "TypeScript"],
    "frameworks": ["FastAPI", "Django", "React"],
    "infrastructure": ["AWS", "Kubernetes", "Terraform"],
    "domains": ["distributed systems", "API design", "data pipelines"]
  },
  "experience": [
    {
      "employer": "{{EMPLOYER_1}}",
      "employer_normalized": {
        "canonical": "International Business Machines Corporation",
        "variants": ["IBM", "I.B.M."]
      },
      "title": "Senior Software Engineer",
      "start_date": "2022-01",
      "end_date": "present",
      "bullets": [
        "Designed event-driven microservices processing 2M+ events/day",
        "Reduced API latency by 40% through caching layer redesign"
      ]
    }
  ],
  "education": [
    {
      "institution": "{{SCHOOL}}",
      "institution_normalized": {
        "canonical": "University of Connecticut",
        "variants": ["UConn", "UCONN", "U of Connecticut"]
      },
      "degree": "B.S. Computer Science",
      "graduation_date": "2018-05",
      "gpa": "3.7"
    }
  ],
  "years_of_experience": 8,
  "work_authorization": "US Citizen"
}
```

### B.2 Job Listing (Job Scout → Fit Scorer)
```json
{
  "listing_id": "uuid",
  "source": "greenhouse",
  "source_url": "https://boards.greenhouse.io/acme/jobs/12345",
  "company": {
    "name": "Acme Corp",
    "size": "500-1000",
    "industry": "fintech",
    "careers_url": "https://acme.com/careers"
  },
  "role": {
    "title": "Senior Backend Engineer",
    "department": "Platform",
    "location": "Remote US",
    "salary_range": {"min": 160000, "max": 200000, "currency": "USD"},
    "posted_date": "2026-04-06",
    "description_text": "Full job description text...",
    "requirements": [
      "5+ years Python or Go",
      "Experience with distributed systems",
      "AWS/GCP proficiency"
    ]
  },
  "alive_score": {
    "composite": 0.85,
    "signals": {
      "posting_freshness": 0.95,
      "recruiter_activity": 0.80,
      "headcount_trend": 0.70,
      "financial_health": 0.90,
      "url_provenance": 1.0,
      "duplicate_check": 1.0,
      "portal_check": 1.0
    },
    "flags": []
  },
  "ats_type": "greenhouse",
  "apply_url": "https://boards.greenhouse.io/acme/jobs/12345#app",
  "scraped_at": "2026-04-09T10:00:00Z"
}
```

### B.3 Fit Score (Fit Scorer → Resume Tailor, Cover Letter, Decision Queue)
```json
{
  "fit_id": "uuid",
  "listing_id": "ref:listing_id",
  "persona_id": "ref:persona_id",
  "overall_score": 87,
  "breakdown": {
    "skills_match": 92,
    "experience_level": 85,
    "domain_relevance": 88,
    "culture_signals": 78,
    "location_match": 100
  },
  "gaps": [
    {"requirement": "Kafka experience", "severity": "minor", "mitigation": "Has RabbitMQ and event-driven architecture experience"}
  ],
  "strengths": [
    {"requirement": "Distributed systems", "evidence": "Built microservices processing 2M+ events/day at {{EMPLOYER_1}}"}
  ],
  "talking_points": [
    "Event-driven architecture experience directly maps to their platform needs",
    "API latency optimization work aligns with their performance focus"
  ],
  "recommendation": "APPLY",
  "scored_at": "2026-04-09T10:05:00Z"
}
```

### B.4 Fill Plan (Form Interpreter → Playwright Filler)
```json
{
  "fill_plan_id": "uuid",
  "listing_id": "ref:listing_id",
  "ats_type": "greenhouse",
  "url": "https://boards.greenhouse.io/acme/jobs/12345#app",
  "fields": [
    {
      "field_id": "first_name",
      "label": "First Name",
      "type": "text_input",
      "selector": "#first_name",
      "selector_strategy": "exact_css",
      "value": "{{FIRST_NAME}}",
      "pii_level": "LOW",
      "confidence": 0.98,
      "confidence_breakdown": {
        "selector_match": 1.0,
        "label_similarity": 0.95,
        "template_match": 1.0
      },
      "source": "template",
      "explanation": "Exact match to Greenhouse template field"
    },
    {
      "field_id": "education_school",
      "label": "School / University",
      "type": "text_input",
      "selector": null,
      "selector_strategy": "aria_label_match",
      "selector_fallback_chain": ["label_based_xpath", "placeholder_text_match"],
      "value": "{{SCHOOL}}",
      "normalization_context": "full_name",
      "pii_level": "LOW",
      "confidence": 0.72,
      "confidence_breakdown": {
        "selector_match": 0.5,
        "label_similarity": 0.85,
        "template_match": 0.0
      },
      "source": "llm_interpreted",
      "explanation": "Label says 'School / University' — likely education institution field. Not in template. Using aria-label strategy."
    },
    {
      "field_id": "resume_upload",
      "label": "Resume",
      "type": "file_upload",
      "selector": "input[type=file]",
      "selector_strategy": "exact_css",
      "value": "generated_resume.pdf",
      "pii_level": "LOW",
      "confidence": 0.95,
      "source": "template"
    },
    {
      "field_id": "why_work_here",
      "label": "Why do you want to work at Acme Corp?",
      "type": "textarea",
      "selector": null,
      "selector_strategy": "label_based_xpath",
      "value": "QUESTION_RESPONDER:why_work_here",
      "pii_level": "NONE",
      "confidence": 0.65,
      "source": "llm_interpreted",
      "explanation": "Free-text question. Delegated to Question Responder agent.",
      "requires_question_responder": true
    }
  ],
  "escalations": [
    {
      "field_id": "salary_expectation",
      "reason": "HIGH sensitivity + low confidence",
      "priority": "BLOCKING",
      "label": "Expected Salary (USD)"
    }
  ],
  "replay_trace_used": "greenhouse_general_v3",
  "generated_at": "2026-04-09T10:10:00Z"
}
```

### B.5 Question Response (Question Responder → Fill Plan / Decision Queue)
```json
{
  "question_id": "uuid",
  "listing_id": "ref:listing_id",
  "field_id": "why_work_here",
  "question_text": "Why do you want to work at Acme Corp?",
  "response_text": "Your fintech platform's focus on real-time processing aligns directly with my experience building event-driven systems. I'm particularly drawn to the challenge of scaling your API layer...",
  "grounded_in": ["fit_score.talking_points[0]", "persona.experience[0].bullets[0]"],
  "cached_from_company_memory": false,
  "generated_at": "2026-04-09T10:11:00Z"
}
```

### B.6 Application Record (stored in outcomes.db)
```json
{
  "application_id": "uuid",
  "listing_id": "ref:listing_id",
  "persona_id": "ref:persona_id",
  "company": "Acme Corp",
  "role_title": "Senior Backend Engineer",
  "ats_type": "greenhouse",
  "fit_score": 87,
  "alive_score": 0.85,
  "status": "SUBMITTED",
  "status_history": [
    {"status": "QUEUED", "timestamp": "2026-04-09T10:05:00Z"},
    {"status": "APPROVED", "timestamp": "2026-04-09T10:12:00Z"},
    {"status": "SUBMITTED", "timestamp": "2026-04-09T10:15:00Z"}
  ],
  "resume_version": "acme_backend_v1.pdf",
  "cover_letter_version": "acme_backend_cl_v1.pdf",
  "time_to_apply_seconds": 180,
  "human_interventions": 1,
  "submitted_at": "2026-04-09T10:15:00Z"
}
```

### B.7 Failure Record (stored in failures.db)
```json
{
  "failure_id": "uuid",
  "application_id": "ref:application_id",
  "ats_type": "workday",
  "company": "Amazon",
  "failure_step": "form_fill",
  "error_type": "dropdown_mismatch",
  "field_name": "years_of_experience",
  "field_label": "Years of Professional Experience",
  "selector_strategies_tried": ["exact_css", "label_based_xpath", "aria_label_match"],
  "selector_strategy_that_worked": null,
  "fix_applied": "manual_override",
  "error_message": "Dropdown options [0-2, 3-5, 6-10, 10+] — could not map value '8' to option",
  "timestamp": "2026-04-09T14:32:00Z"
}
```

---

## Appendix C: LangGraph State Schema

> **This is the central data structure.** Every agent reads from and writes to this state. The LangGraph orchestrator passes it between nodes.

### C.1 State Definition (`graph/state.py`)
```python
from typing import Optional, Literal
from pydantic import BaseModel, Field
from datetime import datetime

class ApplicationState(BaseModel):
    """Central state passed through the LangGraph workflow.
    Every agent reads what it needs and writes its output."""

    # --- Identity ---
    application_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # --- Phase: Profile (set once at startup) ---
    persona: Optional[dict] = None                # B.1 schema
    resume_raw_path: Optional[str] = None         # path to uploaded file

    # --- Phase: Discovery ---
    listing: Optional[dict] = None                # B.2 schema
    alive_score: Optional[dict] = None            # subset of B.2

    # --- Phase: Evaluation ---
    fit_score: Optional[dict] = None              # B.3 schema

    # --- Phase: Document Generation ---
    tailored_resume_tokenized: Optional[str] = None    # file path
    tailored_resume_final: Optional[str] = None        # after PII injection
    cover_letter_tokenized: Optional[str] = None
    cover_letter_final: Optional[str] = None
    question_responses: list[dict] = Field(default_factory=list)  # B.5 schema

    # --- Phase: Form Filling ---
    fill_plan: Optional[dict] = None              # B.4 schema
    account_status: Optional[Literal["existing", "created", "failed"]] = None
    session_context_id: Optional[str] = None      # browser session for verification

    # --- Phase: Submission ---
    submission_mode: Literal["dry_run", "shadow", "live"] = "shadow"
    fields_filled: list[dict] = Field(default_factory=list)   # snapshot of filled values
    post_upload_corrections: list[dict] = Field(default_factory=list)
    human_escalations: list[dict] = Field(default_factory=list)

    # --- Phase: Outcome ---
    status: Literal[
        "QUEUED", "APPROVED", "FILLING", "SHADOW_REVIEW",
        "SUBMITTED", "RECEIVED", "REJECTED",
        "INTERVIEW_SCHEDULED", "OFFER", "NO_RESPONSE_30D",
        "FAILED", "ABORTED"
    ] = "QUEUED"
    status_history: list[dict] = Field(default_factory=list)
    failure_record: Optional[dict] = None         # B.7 schema if failed

    # --- Metadata ---
    replay_trace_id: Optional[str] = None
    time_to_apply_seconds: Optional[int] = None
    human_interventions: int = 0
```

### C.2 Workflow Definition (`graph/workflow.py`) — Skeleton
```python
from langgraph.graph import StateGraph, END
from graph.state import ApplicationState

workflow = StateGraph(ApplicationState)

# --- Node registration (each calls its agent) ---
workflow.add_node("evaluate_fit", fit_scorer_node)
workflow.add_node("generate_documents", document_generation_node)
workflow.add_node("interpret_form", form_interpreter_node)
workflow.add_node("inject_pii", pii_injection_node)
workflow.add_node("fill_form", form_filler_node)
workflow.add_node("validate_upload", post_upload_validator_node)
workflow.add_node("human_review", human_review_node)       # PAUSE POINT
workflow.add_node("submit", submission_node)
workflow.add_node("record_outcome", outcome_recorder_node)

# --- Edges (conditional routing) ---
workflow.set_entry_point("evaluate_fit")

workflow.add_conditional_edges("evaluate_fit", route_by_fit_score, {
    "apply": "generate_documents",
    "skip": "record_outcome",           # fit too low → record as skipped
})

workflow.add_edge("generate_documents", "interpret_form")
workflow.add_edge("interpret_form", "inject_pii")
workflow.add_edge("inject_pii", "fill_form")
workflow.add_edge("fill_form", "validate_upload")
workflow.add_edge("validate_upload", "human_review")        # ALWAYS pause here

workflow.add_conditional_edges("human_review", route_by_approval, {
    "approved": "submit",
    "edit": "generate_documents",       # back to doc gen with edits
    "aborted": "record_outcome",
})

workflow.add_conditional_edges("submit", route_by_mode, {
    "shadow": "human_review",           # shadow loops back for final approval
    "live": "record_outcome",
    "failed": "record_outcome",
})

workflow.add_edge("record_outcome", END)

# --- Compile with checkpointing ---
from langgraph.checkpoint.sqlite import SqliteSaver
checkpointer = SqliteSaver.from_conn_string("./data/checkpoints.db")
app = workflow.compile(checkpointer=checkpointer)
```

### C.3 Routing Functions
```python
def route_by_fit_score(state: ApplicationState) -> str:
    if state.fit_score and state.fit_score["overall_score"] >= 50:
        return "apply"
    return "skip"

def route_by_approval(state: ApplicationState) -> str:
    # Set by the human_review node based on UI action
    if state.status == "APPROVED":
        return "approved"
    elif state.status == "ABORTED":
        return "aborted"
    return "edit"

def route_by_mode(state: ApplicationState) -> str:
    if state.status == "FAILED":
        return "failed"
    if state.submission_mode == "shadow":
        return "shadow"
    return "live"
```

---

## Appendix D: Prompt Template Format

> **Every prompt file follows this format.** Agents load prompts from `/prompts/`, interpolate variables, and send to the LLM via `llm_router`.

### D.1 Prompt File Structure
Each `.md` file in `/prompts/` uses this format:

```markdown
# Agent: [Agent Name]
# Version: 1.0
# Model: primary (or: local, specific model name)
# Last tested: 2026-04-09

## System Prompt

You are the [Agent Name] for job_finder, an AI-powered job application system.

[Role description and behavioral instructions]

## Input Format

You will receive:
- `persona`: The candidate's experience persona (JSON)
- `job_description`: The full job listing text
[etc.]

## Output Format

Respond ONLY with valid JSON matching this schema:
```json
{ ... }
```

Do not include any text outside the JSON block.

## Rules

1. [Specific behavioral rules]
2. [Quality constraints]
3. NEVER include real PII — all personal data uses {{TOKEN}} placeholders

## Examples

### Example Input:
[Concrete input example]

### Example Output:
[Concrete output example matching the schema]
```

### D.2 Variable Interpolation

Prompts use Python f-string style `{variable_name}` for runtime values. The agent loader does:

```python
def load_prompt(prompt_path: str, **variables) -> str:
    """Load a prompt file from /prompts/, extract system prompt,
    and interpolate variables."""
    raw = Path(prompt_path).read_text()
    # Extract section after "## System Prompt" until next "##"
    system_prompt = extract_section(raw, "System Prompt")
    return system_prompt.format(**variables)
```

**PII tokens** (`{{FULL_NAME}}`) use double braces and are NOT interpolated by the prompt loader — they pass through to the LLM as literal strings. Only the PII Injector (local) resolves them.

### D.3 Example: Fit Scorer Prompt (`/prompts/fit_scorer.md`)
```markdown
# Agent: Fit Scorer
# Version: 1.0
# Model: primary
# Last tested: 2026-04-09

## System Prompt

You are the Fit Scorer for job_finder. Your job is to evaluate how well a candidate matches a job listing.

You use analogical reasoning: compare this role to archetypal roles where a candidate with this profile would thrive or struggle.

## Input Format

You will receive:
- `persona`: Candidate experience persona (JSON, PII tokenized)
- `listing`: Job listing with requirements (JSON)

## Output Format

Respond ONLY with valid JSON:
```json
{
  "overall_score": 0-100,
  "breakdown": {
    "skills_match": 0-100,
    "experience_level": 0-100,
    "domain_relevance": 0-100,
    "culture_signals": 0-100,
    "location_match": 0-100
  },
  "gaps": [
    {"requirement": "...", "severity": "minor|moderate|major", "mitigation": "..."}
  ],
  "strengths": [
    {"requirement": "...", "evidence": "..."}
  ],
  "talking_points": ["...", "..."],
  "recommendation": "APPLY|MAYBE|SKIP"
}
```

## Rules

1. Be calibrated: 90+ means near-perfect match. 70-89 is strong. 50-69 is borderline. Below 50 is poor fit.
2. Every gap must include a mitigation — how the candidate could address it.
3. Talking points should be usable directly by the Cover Letter agent.
4. NEVER hallucinate skills the candidate doesn't have.
5. If the listing is vague, score conservatively and note the ambiguity.

## Examples

### Example Input:
persona: {persona}
listing: {listing}

### Example Output:
{example_output}
```

---

## Appendix E: API Endpoints (FastAPI)

> **The Dashboard communicates with the backend exclusively through these endpoints.**

### E.1 Route Map (`api/routes/`)

```
POST   /api/persona/upload          Upload resume → trigger Profile Analyst
GET    /api/persona/current          Get current Experience Persona

POST   /api/jobs/scan                Trigger Job Scout scan
GET    /api/jobs/queue               Get job listings pending review
POST   /api/jobs/{listing_id}/skip   Skip a listing
POST   /api/jobs/{listing_id}/flag   Flag a listing

POST   /api/apply/{listing_id}       Start application workflow for a listing
GET    /api/apply/{app_id}/status    Get current workflow state
POST   /api/apply/{app_id}/approve   Approve submission (from Decision Queue)
POST   /api/apply/{app_id}/edit      Send edits back (resume, cover letter, answers)
POST   /api/apply/{app_id}/abort     Abort application
POST   /api/apply/{app_id}/resume    Resume from checkpoint after failure

POST   /api/apply/{app_id}/escalation/{field_id}/resolve
                                     Resolve a human escalation (provide value)

GET    /api/applications             List all applications with status
GET    /api/applications/{app_id}    Full application detail (docs, fill plan, trace)

POST   /api/batch/approve            Batch approve multiple listings
GET    /api/batch/candidates         Get batch-eligible listing groups

GET    /api/insights/overview        Funnel metrics, success rates
GET    /api/insights/failures        Top failure patterns from failures.db

GET    /api/settings                 Current config (daily cap, mode, etc.)
PUT    /api/settings                 Update config
```

### E.2 WebSocket (real-time updates)
```
WS     /ws/application/{app_id}     Stream workflow progress + escalation prompts
WS     /ws/queue                    Stream new job discoveries to Decision Queue
```

---

## Appendix F: Database Schemas (SQLite)

### F.1 pii_vault.db
```sql
CREATE TABLE tokens (
    token_key    TEXT PRIMARY KEY,     -- e.g. "{{FULL_NAME}}"
    value        TEXT NOT NULL,         -- encrypted actual value
    category     TEXT NOT NULL,         -- LOW | MEDIUM | HIGH
    created_at   TEXT NOT NULL
);

CREATE TABLE normalized_names (
    token_key    TEXT NOT NULL,         -- e.g. "{{SCHOOL}}"
    form         TEXT NOT NULL,         -- "canonical" | "variant"
    value        TEXT NOT NULL,         -- encrypted
    FOREIGN KEY (token_key) REFERENCES tokens(token_key)
);
```

### F.2 account_vault.db
```sql
CREATE TABLE accounts (
    account_id        TEXT PRIMARY KEY,
    company           TEXT NOT NULL,
    ats_type          TEXT NOT NULL,
    username          TEXT NOT NULL,      -- encrypted
    password          TEXT NOT NULL,      -- encrypted
    session_cookies   TEXT,               -- encrypted JSON blob
    browser_context   TEXT,               -- context ID for Session Binder
    status            TEXT DEFAULT 'active',  -- active | locked | needs_verify
    created_at        TEXT NOT NULL,
    last_used_at      TEXT
);
```

### F.3 outcomes.db
```sql
CREATE TABLE applications (
    application_id    TEXT PRIMARY KEY,
    listing_id        TEXT NOT NULL,
    company           TEXT NOT NULL,
    role_title        TEXT NOT NULL,
    ats_type          TEXT NOT NULL,
    fit_score         INTEGER,
    alive_score       REAL,
    status            TEXT NOT NULL,
    resume_version    TEXT,
    cover_letter_ver  TEXT,
    time_to_apply_s   INTEGER,
    human_interventions INTEGER DEFAULT 0,
    submitted_at      TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE status_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  TEXT NOT NULL,
    status          TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    FOREIGN KEY (application_id) REFERENCES applications(application_id)
);
```

### F.4 failures.db
```sql
CREATE TABLE failures (
    failure_id              TEXT PRIMARY KEY,
    application_id          TEXT,
    ats_type                TEXT NOT NULL,
    company                 TEXT NOT NULL,
    failure_step            TEXT NOT NULL,
    error_type              TEXT NOT NULL,
    field_name              TEXT,
    field_label             TEXT,
    selector_strategies     TEXT,           -- JSON array
    strategy_that_worked    TEXT,
    fix_applied             TEXT,
    error_message           TEXT,
    timestamp               TEXT NOT NULL
);

-- Index for pattern analysis
CREATE INDEX idx_failures_ats_error ON failures(ats_type, error_type);
CREATE INDEX idx_failures_step ON failures(failure_step);
```

### F.5 company_memory.db
```sql
CREATE TABLE companies (
    company_id       TEXT PRIMARY KEY,
    company_name     TEXT NOT NULL,
    ats_type         TEXT,
    field_patterns   TEXT,                 -- JSON
    last_applied     TEXT,
    last_outcome     TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE cached_answers (
    answer_id       TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    question_key    TEXT NOT NULL,          -- normalized key: "why_work_here", "salary", etc.
    question_text   TEXT NOT NULL,
    answer_text     TEXT NOT NULL,
    used_count      INTEGER DEFAULT 1,
    last_used       TEXT NOT NULL,
    FOREIGN KEY (company_id) REFERENCES companies(company_id)
);

CREATE TABLE replay_refs (
    company_id      TEXT NOT NULL,
    trace_id        TEXT NOT NULL,
    FOREIGN KEY (company_id) REFERENCES companies(company_id)
);
```

---

## Appendix G: LLM Router Config

### G.1 config.yaml
```yaml
# LLM Router Configuration
# Phase 1-3: all tasks use primary model
# Phase 5+: uncomment per-task overrides

default_model: ${PRIMARY_MODEL}    # from .env
default_temperature: 0.3
default_max_tokens: 4096

local:
  base_url: ${OLLAMA_BASE_URL}
  model: ${OLLAMA_MODEL}
  temperature: 0.1                  # low creativity for PII injection

# Per-task overrides (uncomment when ready to specialize)
# task_routing:
#   profile_analysis:
#     model: claude-sonnet-4-20250514
#     temperature: 0.2
#   job_scouting:
#     model: gpt-4o
#     temperature: 0.3
#   fit_scoring:
#     model: claude-sonnet-4-20250514
#     temperature: 0.2
#   resume_tailoring:
#     model: claude-sonnet-4-20250514
#     temperature: 0.4
#   cover_letter:
#     model: claude-sonnet-4-20250514
#     temperature: 0.5
#   form_interpretation:
#     model: gpt-4o
#     temperature: 0.1
#   question_responding:
#     model: claude-sonnet-4-20250514
#     temperature: 0.4
#   status_classification:
#     model: gpt-4o-mini
#     temperature: 0.1

response_format: json               # enforce JSON output for all agents
retry_on_parse_failure: 3           # retry if JSON parse fails
```

### G.2 Router Implementation Pattern
```python
# llm_router/router.py
import yaml
from anthropic import Anthropic
from openai import OpenAI
from ollama import Client as OllamaClient

class LLMRouter:
    def __init__(self, config_path="llm_router/config.yaml"):
        self.config = yaml.safe_load(open(config_path))
        self.anthropic = Anthropic()
        self.openai = OpenAI()
        self.ollama = OllamaClient(host=self.config["local"]["base_url"])

    def route(self, task_type: str, system_prompt: str, user_prompt: str) -> str:
        """Route to appropriate model. Returns parsed response text."""
        model_config = self._get_model_config(task_type)

        if task_type == "pii_injection":
            return self._call_ollama(system_prompt, user_prompt, model_config)
        elif model_config["model"].startswith("claude"):
            return self._call_anthropic(system_prompt, user_prompt, model_config)
        else:
            return self._call_openai(system_prompt, user_prompt, model_config)

    def _get_model_config(self, task_type: str) -> dict:
        """Check task-specific override, fall back to default."""
        overrides = self.config.get("task_routing", {})
        if task_type in overrides:
            return overrides[task_type]
        return {
            "model": self.config["default_model"],
            "temperature": self.config["default_temperature"],
            "max_tokens": self.config["default_max_tokens"],
        }

    # _call_anthropic, _call_openai, _call_ollama implementations
    # Each: send prompt → get response → parse JSON → retry on failure
```

---

## Appendix H: File-by-File Build Order

> **For the coding agent:** Build files in this order within each phase. Dependencies flow downward — each file may import from files listed above it.

### Phase 1 Build Order

```
# --- Step 1: Project scaffolding ---
.env.example
.gitignore
PII_MANIFEST.json
README.md
requirements.txt

# --- Step 2: Core utilities (no dependencies on each other) ---
llm_router/config.yaml
llm_router/router.py

# --- Step 3: PII layer (build bottom-up) ---
pii/vault.py                  # encrypted SQLite CRUD
pii/normalizer.py             # canonical + variant lookups
pii/tokenizer.py              # imports vault.py, normalizer.py
pii/field_classifier.py       # LOW/MEDIUM/HIGH classification
pii/sanitizer.py              # middleware, imports tokenizer

# --- Step 4: Database initialization ---
setup/init_db.py              # creates all .db files with schemas from Appendix F

# --- Step 5: LangGraph foundation ---
graph/state.py                # ApplicationState (Appendix C.1)
graph/checkpoints.py          # SqliteSaver config
graph/workflow.py             # skeleton with nodes as stubs (Appendix C.2)

# --- Step 6: First agent ---
prompts/profile_analyst.md    # following format from Appendix D
agents/profile_analyst.py     # imports llm_router, tokenizer, normalizer

# --- Step 7: API boilerplate ---
api/main.py                   # FastAPI app, CORS, middleware
api/middleware/pii_guard.py   # imports sanitizer
api/routes/persona.py         # POST /upload, GET /current

# --- Step 8: Tests ---
tests/test_pii/test_vault.py
tests/test_pii/test_tokenizer.py
tests/test_pii/test_normalizer.py
tests/test_agents/test_profile_analyst.py
```

### Phase 2 Build Order

```
# --- Step 1: Retrieval layer ---
retrieval/embeddings.py
retrieval/vector_store.py          # ChromaDB wrapper
retrieval/hybrid_search.py         # vector + BM25

# --- Step 2: Discovery agents ---
prompts/job_scout.md
agents/job_scout.py                # imports hybrid_search, llm_router
prompts/fit_scorer.md
agents/fit_scorer.py

# --- Step 3: Document generation agents ---
prompts/resume_tailor.md
agents/resume_tailor.py
prompts/cover_letter.md
agents/cover_letter.py
prompts/question_responder.md
agents/question_responder.py       # imports company_memory

# --- Step 4: Form infrastructure ---
templates/greenhouse.json          # ATS template (Appendix B.4 structure)
browser/confidence_scorer.py       # hybrid scoring (§8.1)
browser/selector_resolver.py       # multi-strategy fallback (§8.2)
prompts/form_interpreter.md
agents/form_interpreter.py         # imports templates, confidence_scorer, selector_resolver

# --- Step 5: Account & validation ---
pii/account_vault.py
prompts/account_manager.md
agents/account_manager.py
prompts/post_upload_validator.md
agents/post_upload_validator.py

# --- Step 6: Feedback infrastructure ---
feedback/failures.db               # via init_db.py update
feedback/company_memory.db         # via init_db.py update

# --- Step 7: MCP servers ---
mcp_servers/filesystem_server.py
mcp_servers/database_server.py

# --- Step 8: Wire into LangGraph ---
# Update graph/workflow.py: replace stubs with real agent calls
# Add routing functions (Appendix C.3)

# --- Step 9: Evaluation ---
prompts/judges/resume_judge.md
prompts/judges/cover_letter_judge.md
evals/judge_runner.py

# --- Step 10: API routes ---
api/routes/jobs.py
api/routes/applications.py
```

### Phase 2.5 Build Order

```
# --- Browser automation ---
browser/humanizer.py
browser/playwright_driver.py
mcp_servers/browser_bridge.py
prompts/ats/greenhouse.md
browser/ats_strategies/greenhouse.py

# --- PII injection ---
agents/pii_injector.py             # imports ollama, vault, normalizer

# --- Submission pipeline ---
agents/submitter.py                # imports playwright_driver, humanizer

# --- Replay ---
replay/generalizer.py
replay/traces/.gitkeep

# --- Wire shadow mode into workflow ---
# Update graph/workflow.py: add shadow review loop
```

### Phase 3 Build Order

```
# --- Status tracking ---
prompts/status_classifier.md
agents/status_tracker.py

# --- Dashboard ---
dashboard/package.json
dashboard/src/App.jsx
dashboard/src/components/DecisionQueue.jsx
dashboard/src/components/ApplicationTracker.jsx
dashboard/src/components/ResumePreview.jsx
dashboard/src/components/HumanAssistPanel.jsx
dashboard/src/components/ConfidenceView.jsx
dashboard/src/components/BatchApproval.jsx
dashboard/src/components/InsightsPanel.jsx

# --- Remaining API routes ---
api/routes/batch.py
api/routes/insights.py
api/routes/settings.py
api/routes/websocket.py
```

---

## Appendix I: Testing Patterns

### I.1 Framework
```
pytest + pytest-asyncio
```

### I.2 LLM Mocking Strategy
**Never call real LLMs in unit tests.** Mock the `llm_router.route()` method to return predetermined JSON responses.

```python
# tests/conftest.py
import pytest
from unittest.mock import AsyncMock

@pytest.fixture
def mock_router(monkeypatch):
    """Mock LLM router that returns predetermined responses."""
    mock = AsyncMock()
    monkeypatch.setattr("agents.fit_scorer.router.route", mock)
    return mock

@pytest.fixture
def sample_persona():
    """Load a synthetic test persona (no real PII)."""
    return json.load(open("tests/fixtures/synthetic_persona.json"))

@pytest.fixture
def sample_listing():
    return json.load(open("tests/fixtures/synthetic_listing.json"))
```

### I.3 Test Fixtures
All fixtures in `tests/fixtures/` use **synthetic data only**:
```
tests/fixtures/
  synthetic_persona.json       # fake person, fake companies
  synthetic_listing.json       # fake job at fake company
  synthetic_fill_plan.json     # fake form fields
  greenhouse_page.html         # saved DOM for Form Interpreter tests
```

### I.4 What to Test Per Agent
```
Profile Analyst:  PII correctly tokenized, all fields extracted, normalizer populated
Job Scout:        Ghost score computation, smart skip logic, deduplication
Fit Scorer:       Score calibration (known good/bad matches), JSON schema valid
Resume Tailor:    Output still has {{TOKENS}}, bullets map to requirements
Cover Letter:     Company-specific content, no hallucinated skills
Form Interpreter: Confidence scoring math, template matching, escalation triggers
Question Resp:    Grounded in persona (no hallucinated experience)
PII Injector:     All tokens resolved, correct normalization form chosen
Selector Resolver: Fallback chain works, logs which strategy succeeded
Post-Upload Val:  Catches common ATS autofill errors (name split, date format)
Account Manager:  Login vs signup decision, session cookies stored
```

### I.5 Integration Test (Dry Run)
```python
async def test_full_dry_run_pipeline():
    """Run the complete workflow in dry_run mode with mocked LLM
    and assert every state transition happens correctly."""
    state = ApplicationState(
        submission_mode="dry_run",
        persona=load_fixture("synthetic_persona.json"),
        listing=load_fixture("synthetic_listing.json"),
    )
    # Run workflow with mocked router
    final_state = await app.ainvoke(state)
    assert final_state.status in ["SUBMITTED", "SHADOW_REVIEW"]
    assert final_state.fill_plan is not None
    assert "{{" not in final_state.tailored_resume_final  # PII resolved
```

---

## Appendix J: Error Handling Conventions

### J.1 Exception Hierarchy
```python
# errors.py
class JobFinderError(Exception):
    """Base exception for all job_finder errors."""
    pass

class PIILeakError(JobFinderError):
    """PII detected in output destined for remote LLM or log. CRITICAL."""
    pass

class LLMParseError(JobFinderError):
    """LLM returned non-JSON or invalid schema. Retryable."""
    pass

class SelectorResolutionError(JobFinderError):
    """All selector strategies failed for a field. Needs human."""
    pass

class ATSFormError(JobFinderError):
    """ATS form structure unexpected. Log to failures.db."""
    pass

class AccountError(JobFinderError):
    """Account creation/login failed. May need human."""
    pass

class CheckpointRecoveryError(JobFinderError):
    """Could not restore from checkpoint. Restart required."""
    pass
```

### J.2 Error Handling Pattern
Every agent follows this pattern:

```python
async def run_agent(state: ApplicationState) -> ApplicationState:
    try:
        result = await do_agent_work(state)
        return state.copy(update=result)
    except PIILeakError as e:
        # CRITICAL: halt everything, never continue
        logger.critical(f"PII LEAK DETECTED: {e}")
        raise  # propagates to orchestrator, stops workflow
    except LLMParseError as e:
        # Retryable: log and retry up to 3 times
        logger.warning(f"LLM parse failed: {e}, retrying...")
        return await retry_with_backoff(do_agent_work, state, max_retries=3)
    except SelectorResolutionError as e:
        # Needs human: escalate, don't retry
        state.human_escalations.append({
            "type": "selector_failure",
            "field": e.field_name,
            "priority": "BLOCKING",
            "message": str(e)
        })
        return state
    except Exception as e:
        # Unknown: log to failures.db, checkpoint, surface to user
        log_failure(state, step=AGENT_NAME, error=e)
        state.status = "FAILED"
        state.failure_record = build_failure_record(state, e)
        return state
```

### J.3 Logging Convention
```python
import logging
logger = logging.getLogger("job_finder.agents.fit_scorer")

# Levels:
# DEBUG   → LLM prompts/responses (ONLY in dev, sanitized of PII)
# INFO    → agent started, agent completed, key decisions
# WARNING → retryable failures, low confidence fields
# ERROR   → non-retryable failures, escalations
# CRITICAL → PII leaks, security boundary violations
```

---

*Last updated: April 2026 — v4 Final + Agent Implementation Appendices*