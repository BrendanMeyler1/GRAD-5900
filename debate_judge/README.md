# Debate Judge — A Reasoning Evaluation Agent

Debate Judge is an AI system that analyzes a debate and determines which speaker presented the stronger argument using **structured reasoning**, **evidence verification**, and **deterministic scoring**.

> Unlike a chatbot, Debate Judge does not "pick a side."  
> It evaluates argument quality by extracting claims, verifying them against evidence, detecting logical fallacies, and applying a reproducible scoring function.

This project demonstrates **LLM orchestration and tool-augmented reasoning**, not conversation.

---

## Architecture Overview

Debate Judge is **not** a conversational agent.  
It is a reasoning pipeline where large language models perform analysis while Python code performs judgment.

The system deliberately separates responsibilities:

| Layer | Responsibility |
|-------|----------------|
| **LLM** | Language understanding and evidence comparison |
| **Python** | Scoring, routing, and final decision |

> The model never directly decides the winner.

### High-Level Flow

```
                 User Debate
                      │
                      ▼
          ┌────────────────────────┐
          │  Speaker Segmentation  │
          └────────────────────────┘
                      │
                      ▼
          ┌────────────────────────┐
          │    Claim Extraction    │
          │   (Structured JSON)    │
          └────────────────────────┘
                      │
                      ▼
          ┌────────────────────────┐
          │  Claim Type Classifier │
          └────────────────────────┘
                      │
                      ▼
              Complexity Router
         (Fast Model vs Reasoning Model)
          │                       │
    FACTUAL                CAUSAL / STATISTICAL
    gpt-4o-mini               gpt-4o
          │                       │
          └───────────┬───────────┘
                      ▼
          ┌────────────────────────┐
          │ Evidence Retrieval Tool│
          │     (Wikipedia API)    │
          └────────────────────────┘
                      │
                      ▼
          ┌────────────────────────┐
          │   Claim Verification   │
          │ (Evidence Comparison)  │
          └────────────────────────┘
                      │
                      ▼
          ┌────────────────────────┐
          │   Fallacy Detection    │
          └────────────────────────┘
                      │
                      ▼
          ┌────────────────────────┐
          │ Deterministic Scoring  │
          │      (Python Only)     │
          └────────────────────────┘
                      │
                      ▼
          ┌────────────────────────┐
          │   Winner Selection     │
          └────────────────────────┘
                      │
                      ▼
          ┌────────────────────────┐
          │ LLM Explanation Layer  │
          │ (Grounded in results)  │
          └────────────────────────┘
                      │
                      ▼
                Final Judgment
```

---

## How It Works

The debate is converted into structured reasoning units and evaluated step-by-step.

### 1. Speaker Segmentation
Splits dialogue into individual speaker messages.

### 2. Claim Extraction
Converts text into atomic claims, each tagged with a type:

| Type | Description |
|------|-------------|
| `FACTUAL` | Objective statement about a past/present event or study result |
| `STATISTICAL` | Claim involving specific numbers, rates, or data |
| `CAUSAL` | Asserts that one thing causes another |
| `VALUE` | Subjective judgment, moral statement, or future prediction |
| `RHETORICAL` | Personal attack or purely persuasive statement with no factual content |

### 3. Complexity Routing
`FACTUAL` claims use the cheaper `gpt-4o-mini` model for verification.  
`CAUSAL` and `STATISTICAL` claims use the stronger `gpt-4o` model.  
`VALUE` and `RHETORICAL` claims are skipped entirely.

### 4. Evidence Retrieval
Verifiable claims are checked against neutral reference material using the Wikipedia API.

### 5. Claim Verification
Each claim is classified as:
- `SUPPORTED` — Evidence broadly consistent with the claim
- `CONTRADICTED` — Evidence explicitly opposes the claim
- `INSUFFICIENT` — Evidence is off-topic or not found

### 6. Fallacy Detection
The system checks for:
- **Ad Hominem** — Attacking the person, not the argument
- **Strawman** — Misrepresenting the opponent's argument
- **False Cause** — Assuming correlation implies causation
- **Hasty Generalization** — Broad claim from insufficient evidence
- **Moving Goalposts** — Changing criteria mid-debate

### 7. Deterministic Scoring
Python code calculates argument strength — no LLM involved.

### 8. Explanation Generation
The LLM produces a grounded explanation using the structured results.

---

## Scoring System

| Event | Points |
|-------|--------|
| Supported claim | **+2** |
| Provided citation | **+1** |
| Insufficient evidence | **−1** |
| Contradicted claim | **−2** |
| Logical fallacy | **−3** |

The winner is selected purely by score. The model only explains the result afterward.

---

## Example Output

```
Speaker A: -7 points
  Supported:    0
  Contradicted: 3
  Insufficient: 0
  Citations:    0
  Fallacies:    1  (Hasty Generalization)

Speaker B: +4 points
  Supported:    2
  Contradicted: 0
  Insufficient: 0
  Citations:    0
  Fallacies:    0

Winner: Speaker B

Explanation:
Speaker A relied on unsupported statistical claims and personal attacks.
Speaker B supported their claims with verifiable evidence.
```

---

## Project Structure

```
debate_judge/
│
├── main.py              # Entry point — orchestrates the full pipeline
├── extractor.py         # Stage 1: Claim extraction
├── verifier.py          # Stage 2: Evidence-based claim verification
├── fallacy.py           # Stage 3: Logical fallacy detection
├── scoring.py           # Stage 4: Deterministic scoring (no LLM)
├── router.py            # Complexity routing and model selection
│
├── tools/
│   └── wikipedia_tool.py  # Wikipedia evidence retrieval
│
├── prompts/
│   ├── extract.txt      # System prompt for claim extraction
│   ├── verify.txt       # System prompt for claim verification
│   └── fallacy.txt      # System prompt for fallacy detection
│
├── mocks.py             # Offline mock classes for testing
├── test_debate_judge.py # Unit and integration tests
└── requirements.txt
```

---

## Installation

**1. Clone the repository:**
```bash
git clone https://github.com/yourname/debate-judge.git
cd debate-judge
```

**2. Create and activate a virtual environment:**
```bash
# Create
python -m venv .venv

# Activate — Windows
.venv\Scripts\activate

# Activate — Mac/Linux
source .venv/bin/activate
```

**3. Install dependencies:**
```bash
pip install -r requirements.txt
```

**4. Create a `.env` file:**
```
OPENAI_API_KEY=your_api_key_here
```

---

## Running

```bash
python main.py
```

Paste a debate transcript when prompted, then press **Enter twice** to submit.

**Example input:**
```
A: Crime has increased every year.
B: FBI statistics show crime decreased in 2022.
A: Those statistics are unreliable and you clearly don't understand economics.
```

**Running tests (no API key required):**
```bash
python -m pytest test_debate_judge.py -v
```

---

## Limitations

- Wikipedia summaries may be incomplete or outdated
- Philosophical and future-tense claims cannot be externally verified
- Fallacy detection is heuristic and may miss subtle cases
- Scoring weights are manually chosen and fixed
- Wikipedia is the only evidence source (no news, academic databases, etc.)

> This is a research prototype, not a truth engine.

---

## Why This Project Exists

Most LLM demos show conversation.  
Real AI systems require:

- **Structured outputs** — JSON from every LLM call, not free text
- **Tool usage** — External data sources grounded in facts
- **Verification** — Claims checked against neutral evidence
- **Deterministic decisions** — Python decides the winner, not the model

Debate Judge demonstrates how LLMs can function as **analytical components** inside reliable software systems rather than autonomous decision-makers.