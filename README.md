# Zycus AI Engineer Assessment

## Video Walkthrough

[![Zycus AI Assessment Demo](https://cdn.loom.com/sessions/thumbnails/9a897833986d480bb72ecaa33dbe5d5d-with-play.gif)](https://www.loom.com/share/9a897833986d480bb72ecaa33dbe5d5d)

🎥 **[Watch the Complete System & Code Walkthrough on Loom](https://www.loom.com/share/9a897833986d480bb72ecaa33dbe5d5d)**

---

## Overview

This project provides an automated, production-oriented AI pipeline for customer support operations and Technical Account Management (TAM). It processes customer support tickets and account history to automate ticket triage, generate QBR-ready account summaries, and systematically evaluate AI quality.

The solution consists of four integrated components:
1. **Intelligent Ticket Triage (`POST /triage`)**: Categorizes support tickets, assesses urgency, matches knowledge-base documentation, routes to responder teams, and drafts safe first responses.
2. **TAM Account Health Summariser (`POST /account-health`)**: Gathers customer account details and 90-day support history to produce concise executive summaries, risk signals with verbatim ticket quotes, and QBR talking points.
3. **Evaluation Harness (`evals/`)**: An objective testing framework with 12 rule-based test cases (including adversarial inputs) to verify schema adherence, classification accuracy, and safety constraints.
4. **Production Design Note**: A comprehensive architecture analysis covering failure modes, latency trade-offs, PII/data security, and scaling considerations.

**Overall System Flow:**
- **Support ticket** &rarr; **Task 1 Triage** &rarr; Structured triage JSON (category, urgency, KB reference, responder, draft response)
- **Account ID** &rarr; **Task 2 Account Health** &rarr; Grounded TAM brief (summary, verified risks, talking points)
- **Task 3 Evaluation Harness** &rarr; Validates both pipelines using deterministic acceptance criteria and precision scoring.

---

## Task 1 — Intelligent Ticket Triage

### Problem & Approach
Customer support teams receive high volumes of tickets with varying urgency and technical complexity. Manually routing each ticket delays response times and causes misrouting. Task 1 automates this process using keyword retrieval over the product knowledge base combined with structured LLM generation.

- **Input:** Raw ticket `subject` and `body`.
- **Output (`TriageResponse`):**
  - `product_area`: Module within the product (or `null` for cross-product/billing tickets).
  - `issue_category`: Classification (Bug, Feature Request, How-To, Performance, Billing, Integration, Onboarding, Data Loss).
  - `urgency`: Priority tier (P1 critical outage to P4 cosmetic/minor).
  - `reasoning`: Grounded explanation based only on ticket facts and retrieved KB text.
  - `known_issue`: Boolean flag (`true` only when retrieved KB documentation matches the reported symptom).
  - `knowledge_base_document` & `knowledge_base_reference`: Source file and section heading (set to `null` if `known_issue` is false).
  - `recommended_responder_team`: Deterministically routed responder team (e.g., Billing Support, Data Recovery Team).
  - `first_response`: Cautious, empathetic draft response acknowledging customer issues without promising unauthorized refunds or unconfirmed timelines.

### Knowledge-Base Grounding
A local BM25 keyword retrieval engine indexes markdown documentation across product features, common errors, troubleshooting guides, billing rules, and onboarding checklists. Top matching chunks are injected into the prompt so classifications and references remain strictly grounded.

- **REST Endpoint:** `POST /triage`
- **Interactive Testing:** Start the API server and navigate to [http://localhost:8000/docs](http://localhost:8000/docs) to test `/triage` directly in the Swagger UI.

---

## Task 2 — TAM Account Health Summariser

### Problem & Approach
Technical Account Managers (TAMs) spend hours reviewing customer usage metrics and past support tickets before Quarterly Business Reviews (QBRs). Task 2 generates an instant, executive-level health brief from `accounts.json` and `tickets.json`.

- **Input:** `account_id` (e.g., `ACC-2944`).
- **Data Retrieved:**
  - Full account profile (ARR, plan tier, licensed vs. active seats, NPS score, last login recency, escalation notes).
  - Ticket history filtered to the last 90 days (sorted deterministically by timestamp).
- **Output (`AccountHealthResponse`):**
  - `executive_summary`: 3–5 sentence synthesis of account health, usage trends, seat utilization, and recent ticket activity.
  - `open_risks`: Detected churn and escalation signals with calibrated severity (`High`, `Medium`, `Low`), explanation, and supporting evidence.
  - `evidence`: Direct `ticket_id` and exact **verbatim quotes** copied word-for-word from ticket bodies. Account-level signals (e.g., NPS or escalation notes) set quote fields to `null`.
  - `talking_points`: Actionable agenda items for the TAM's upcoming customer sync.

### Grounding & Edge-Case Handling
- **Literal metric handling:** `last_login_days_ago` is treated strictly as an organization-level metric, never confused with individual user login activity.
- **Support volume phrasing:** Accounts with zero or few tickets are described as having "limited recent support activity", rather than inferring unverified "operational stability".
- **Lapsed renewals:** Renewal dates in the past are framed as items for the TAM to confirm agreement status, avoiding inflated High/Medium risk ratings without supporting negative evidence.
- **Missing accounts:** Invalid or non-existent account IDs return a clear HTTP 404 response rather than fabricating account details.
- **REST Endpoint:** `POST /account-health`

---

## Task 3 — Evaluation Harness

Task 3 provides an automated testing harness in `evals/` that evaluates the quality, consistency, and safety of Task 1 and Task 2 outputs without relying on vague manual inspection.

### Test Suite Structure
- **6 Task 1 Test Cases (`evals/cases/t1_cases.json`):**
  - `T1-01`: P1 Data Loss (production database deletion &rarr; Data Recovery Team routing).
  - `T1-02`: P4 Feature Request (dashboard dark mode &rarr; Product Engineering routing).
  - `T1-03`: Billing Error (duplicate charge &rarr; null product area, Billing Support routing).
  - `T1-04`: P3 Performance Degradation (pipeline slowdown with workaround).
  - `T1-05`: P4 How-To (CSV export question).
  - `T1-06 (Adversarial)`: Prompt injection attempt trying to force P1 urgency, data loss category, and fabricated KB links.
- **6 Task 2 Test Cases (`evals/cases/t2_cases.json`):**
  - `T2-01`: Healthy Account (clean metrics &rarr; no High/Medium risks).
  - `T2-02`: Churning Account (multiple active escalation notes &rarr; identified open risks).
  - `T2-03`: Positive New Account (NPS=10 &rarr; positive summary).
  - `T2-04`: Healthy Account with Low NPS (calibrated Low severity talking point).
  - `T2-05 (Adversarial)`: Lapsed renewal date (verified that renewal is not called "upcoming" and risk is not inflated).
  - `T2-06 (Adversarial)`: Non-existent account ID (verified HTTP 404 response).

### Scoring Methodology
Each test case executes multiple objective rule-based checks:
- Exact enum matching (`issue_category`, `urgency`, `recommended_responder_team`).
- Schema validity and string length thresholds.
- Maximum risk severity ceilings (`max_risk_severity`).
- Substring presence/absence (`text_contains`, `full_text_not_contains`).
- Verbatim quote integrity validation.

Individual cases receive a quality score from `0.0` to `1.0` (`passed_checks / total_checks`). A case passes if its score meets or exceeds its defined threshold (default `0.70`).

### Latest Evaluation Run Results
From [`evals/report.json`](evals/report.json):
- **Total Test Cases:** 12 / 12 Passed
- **Overall Score:** `0.9833`
- **Task 1 Average Score:** `0.97`
- **Task 2 Average Score:** `1.00`

---

## Task 4 — Design Note

### Failure Modes

1. **Hallucinated Citations or Inaccurate Classification:**
   - *Failure:* The LLM might misclassify an issue or invent a non-existent document reference when customer wording is ambiguous.
   - *Detection:* Automated Pydantic validation rejects any category or product area outside controlled enums. Quote sanitization logic checks cited ticket quotes against the raw ticket body and strips hallucinated quotes.
   - *Mitigation:* Constrain model outputs with strict JSON schemas, set `temperature=0.0`, enforce controlled vocabularies, and use deterministic keyword retrieval before generation.

2. **Knowledge Base Retrieval Misses (Vocabulary Mismatch):**
   - *Failure:* A customer describes an issue with colloquial phrasing (e.g., "login screen is spinning") that lacks keyword overlap with official docs ("SSO SAML Handshake Timeout"), returning irrelevant chunks.
   - *Detection:* Monitored via low chunk similarity scores, empty retrieval results, or `known_issue=false` on recurring support issues.
   - *Mitigation:* Augment keyword search with BM25 synonym expansion and semantic dense embeddings in production to match conceptual intent.

3. **External LLM API Latency, Rate Limits, or Outages:**
   - *Failure:* Upstream model providers experience traffic spikes, 503 unavailability, or request timeouts during business hours.
   - *Detection:* Catching connection errors, HTTP 429/503 status codes, and tracking request latency timers.
   - *Mitigation:* The system includes automated exponential backoff retries and model fallback (`gemini-2.5-flash` to `gemini-3.5-flash-lite`), alongside structured HTTP 502/503 status responses.

### Latency vs Quality

The current architecture prioritizes **output quality and grounding accuracy** over raw execution speed. For Task 1, the pipeline executes a multi-step workflow (BM25 retrieval over 40+ KB chunks &rarr; full prompt construction &rarr; LLM inference &rarr; Pydantic validation &rarr; deterministic routing table override), taking ~1.5–3 seconds per ticket. Task 2 processes an account profile and up to 90 days of tickets in a single prompt with verbatim quote checking, taking ~2–4 seconds.

**If latency became the hard constraint (<300ms SLA), future optimizations would include:**
- Running zero-shot deterministic regex/keyword heuristics for obvious categories (e.g., billing keywords &rarr; Billing Support) before invoking an LLM.
- Replacing large general-purpose models with a smaller, fine-tuned classification model (or distilled model) specifically trained on support taxonomy.
- Caching account metrics and 90-day ticket summaries in Redis so QBR briefs only recompute when new tickets are filed.

### Data Sensitivity

Support tickets and customer account records frequently contain Personally Identifiable Information (PII) such as customer names, email addresses, phone numbers, internal server hostnames, and billing information.

**Current Security Practices:**
- Zero hardcoded secrets: API credentials are read exclusively from environment variables (`.env`), documented safely in `.env.example`, and excluded via `.gitignore`.
- Minimum context passing: Only necessary ticket text and account fields are sent to the model; internal database IDs and unused metadata are filtered out.

**Recommended Production Enhancements:**
- Implement automated client-side PII scrubbing (e.g., using Microsoft Presidio) to mask emails, credit card numbers, and employee names before external API transit.
- Deploy models within a private VPC / enterprise agreement where vendor data-retention policies guarantee zero model training on customer inputs.

### Scaling

If support ticket volume scales **10&times; (from 500 to 5,000+ tickets/day)**:

- **Immediate Bottlenecks:** The current implementation reads `tickets.json` and `accounts.json` from disk on demand. While cached in memory, linear filtering (`O(N)`) over thousands of JSON records inside request handlers will consume CPU and memory. Furthermore, sequential synchronous LLM calls will quickly trigger API provider rate limits.
- **Production Architecture Path:**
  1. **Relational Database Storage:** Migrate JSON files to PostgreSQL with B-tree indexes on `account_id` and composite indexes on `(account_id, created_at)`.
  2. **Asynchronous Background Processing:** Decouple incoming ticket ingestion from triage using an asynchronous message queue (e.g., Celery + Redis or AWS SQS). Tickets are ingested immediately (`202 Accepted`), triaged by worker pools, and pushed back via webhooks.
  3. **Read Replicas & Summary Caching:** Cache pre-calculated account health metrics and 90-day ticket aggregations in Redis, invalidating only when a new ticket is logged.

---

## How to Run

### 1. Install Dependencies
Ensure Python 3.10+ is installed:
```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Create your local `.env` file from the provided example and add your Gemini API key:
```bash
cp .env.example .env
```
Edit `.env`:
```ini
GEMINI_API_KEY=your_actual_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
```

### 3. Start the API Server
```bash
uvicorn src.main:app --reload
```
The server will start at `http://localhost:8000`.

### 4. Interactive API Documentation
Open your browser to explore and test endpoints via Swagger UI:
- **Swagger Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check:** `GET http://localhost:8000/health`

### 5. Run the Test Suite & Evaluation Harness
```bash
# Run 31 unit & integration tests
pytest

# Run the complete Task 3 Evaluation Harness (12 test cases)
python -m evals.run_evals

# Run evaluation harness by specific task
python -m evals.run_evals --task t1
python -m evals.run_evals --task t2
```

---

## Project Structure

```
zycus-ai-engineer-assessment/
├── data/
│   ├── accounts.json          # 50 synthetic enterprise account profiles
│   └── tickets.json           # 500 synthetic customer support tickets
├── knowledge-base/            # Product reference and troubleshooting documentation
│   ├── billing/
│   ├── onboarding/
│   ├── products/
│   └── troubleshooting/
├── src/
│   ├── account_health.py      # Task 2: Account health pipeline & quote verification
│   ├── config.py              # Settings & environment variable configuration
│   ├── main.py                # FastAPI app with lifespan manager & error handlers
│   ├── retrieval.py           # BM25 knowledge-base chunk retrieval engine
│   ├── schemas.py             # Pydantic request/response validation schemas & enums
│   └── triage.py              # Task 1: Ticket triage LLM logic & responder routing
├── evals/
│   ├── cases/
│   │   ├── t1_cases.json      # 6 Task 1 evaluation test cases (incl. adversarial)
│   │   └── t2_cases.json      # 6 Task 2 evaluation test cases (incl. adversarial)
│   ├── run_evals.py           # Evaluation runner CLI and report formatter
│   ├── scorer.py              # 14 rule-based check evaluators & scoring functions
│   └── report.json            # Generated evaluation report with precision scores
├── tests/
│   ├── test_account_health.py # Pytest test suite for Task 2 pipeline
│   └── test_triage.py         # Pytest test suite for Task 1 triage & retrieval
├── streamlit_app.py           # Streamlit demo UI (Ticket Triage + Account Health)
├── .env.example               # Template for required environment variables
├── DATA_SCHEMA.md             # Field-level schema reference for data files
├── requirements.txt           # Python package dependencies
└── README.md                  # Comprehensive system documentation
```

---

## Streamlit Demo

A lightweight Streamlit interface is included to make the backend features accessible without any API or JSON knowledge.

**Step 1 — Start the backend API**
```bash
uvicorn src.main:app --reload
```

**Step 2 — Open a new terminal and launch the Streamlit UI**
```bash
python -m streamlit run streamlit_app.py
```

**Step 3 — Open the browser URL shown (typically [http://localhost:8501](http://localhost:8501))**

The UI provides two tabs:
- **Ticket Triage**: Paste a customer message, click *Analyze Ticket*, and receive a structured triage result (issue type, urgency, KB reference, recommended team, and a draft response).
- **Account Health**: Enter a customer account ID, click *Generate Account Brief*, and receive an executive summary, flagged risks with ticket evidence, and QBR talking points.

Sample tickets and account IDs are pre-loaded for quick demonstration.

---

## Design Principles

- **Ground AI Outputs in Verifiable Data:** The system never invents facts or targets. Ticket quotes and KB references must match source documents verbatim.
- **Enforce Structured, Validated Schemas:** All inputs and outputs are validated through strict Pydantic schemas and controlled enums to prevent malformed data.
- **Prioritize Deterministic Overrides:** Critical operational decisions (such as team routing and invalid quote stripping) are handled by deterministic Python logic rather than relying solely on probabilistic LLM output.
- **Systematic Objective Evaluation:** System quality is measured using deterministic rule-based checks across standard and adversarial scenarios.
- **Lightweight, Appropriate Architecture:** Uses fast, modular code without introducing heavy, unnecessary infrastructure for the current workload.
