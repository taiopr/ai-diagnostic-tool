# AI Integration Diagnostic Tool — Backend

A REST API for diagnosing LLM prompts and n8n workflow configurations. Paste a prompt and a test input — get a structured diagnosis, a score derived from specific failure patterns, a complete rewrite, and a before/after output comparison. Every session is persisted to PostgreSQL and retrievable via REST API.

**API base URL:** `http://localhost:8000`
**Interactive docs:** `http://localhost:8000/docs`
**Frontend:** [ai-diagnostic-tool-frontend](https://github.com/taiopar/ai-diagnostic-tool-frontend)
**Live demo:** [ai-diagnostic-tool-frontend.vercel.app](https://ai-diagnostic-tool-frontend.vercel.app)

---

## What it does

A developer pastes a broken LLM prompt and a test input. The system makes two Claude API calls: the first analyses the prompt against a fixed taxonomy of 10 failure patterns and produces a structured diagnosis with a score, issue list, and complete rewrite. The second runs the rewritten prompt against the same input and returns the improved output. The user sees the before/after comparison side by side. Every session is saved to PostgreSQL and retrievable by session ID.

---

## Architecture

```
POST /diagnostics (HTTP request)
    ↓
FastAPI — Pydantic validates request shape and field constraints
    ↓
ai.py — analyse()
    ├── build_messages() — constructs user turn from inputs
    ├── call_claude() — Call 1: diagnostic analysis
    │   Returns: score, summary, issues[], suggested_prompt, test_output
    └── run_improved_prompt() — Call 2: runs suggested prompt
            Returns: improved_output (None if this call fails)
    ↓
db.py — save_session()
    ├── INSERT into sessions (one row, all top-level fields)
    └── INSERT into issues (one row per issue, FK to session_id)
    ↓
DiagnosticResponse returned to client (200)
```

### Components

**`main.py` — FastAPI application**
The HTTP layer. Defines four endpoints, request/response Pydantic models, and the error handling hierarchy. Knows nothing about how Claude works internally or how SQL is structured — it receives a validated request, calls the right functions from `ai.py` and `db.py`, and returns a serialised response. Also handles the non-punishing DB failure pattern: if `save_session()` raises, the AI result is returned anyway with `saved: false`.

**`ai.py` — AI logic module**
The Claude integration layer. Four functions with clean separation:

- **`build_messages()`** — constructs the messages list sent to Claude. User input always lands in the user turn, never in the system prompt. This is the prompt injection defence: the system prompt is a module-level constant that never changes at runtime, regardless of what the user submits.
- **`call_claude()`** — sends messages to Anthropic API and returns the raw text response. `temperature=0` is pinned explicitly to maximise determinism across runs. Raises `AnalysisError` if the call fails. Single responsibility: no parsing, no validation, just the network call.
- **`parse_response()`** — strips markdown fences if Claude adds them despite instructions, runs `json.loads()`, and validates the result against `DiagnosticResult`. Raises on failure so the retry loop in `analyse()` can catch it.
- **`analyse()`** — the main pipeline function. Calls the three functions above in sequence, implements the retry loop on parse failure, calls `run_improved_prompt()` for the second Claude call, and returns a `(DiagnosticResult, str | None)` tuple. This is the only function `main.py` calls directly.

**`db.py` — Database layer**
Direct `psycopg2` calls — no ORM. The choice is deliberate: an ORM like SQLAlchemy abstracts away the SQL, which makes it harder to understand what queries are actually running, where the connection lifecycle is, and why a transaction is or isn't being committed. Using raw SQL forces every query to be explicit and every connection to be consciously opened, committed, and closed. Four functions:

- **`save_session()`** — generates a UUID in Python, inserts one row into sessions, then loops inserting one row per issue. Single `commit()` at the end covers both INSERTs. `rollback()` in the except block ensures partial writes never persist.
- **`get_session()`** — retrieves a session and its issues in two queries, combines them into one dict. Uses `RealDictCursor` so rows come back as named dicts rather than positional tuples.
- **`list_sessions()`** — returns session summaries with `issue_count` via `LEFT JOIN`. No issue detail — keeps the list endpoint lightweight.
- **`get_stats()`** — runs three aggregate queries and combines the results into the stats response dict.

**`sql/schema.sql` — PostgreSQL schema**
Two tables: `sessions` and `issues`. Issues are stored as separate rows with foreign key to sessions rather than as a JSON array column on sessions. The reason is queryability: `SELECT type, COUNT(*) FROM issues GROUP BY type` works directly. `ON DELETE CASCADE` on the foreign key means deleting a session automatically deletes its issues, preventing orphaned rows.

---

## How the diagnostic works

The system analyses prompts against a fixed taxonomy of 10 failure patterns:

| Pattern | Description |
|---------|-------------|
| `MISSING_SYSTEM_PROMPT` | No system prompt defined, or system prompt is empty |
| `AMBIGUOUS_INSTRUCTION` | Task instruction is vague or open to multiple interpretations |
| `NO_OUTPUT_FORMAT` | No output format specified — model will return unpredictable structures |
| `TASK_TOO_BROAD` | Prompt asks for too much in a single call |
| `NO_EXAMPLES` | Few-shot examples would significantly improve reliability but none are provided |
| `CONFLICTING_INSTRUCTIONS` | Prompt contains instructions that contradict each other |
| `NO_ERROR_HANDLING` | No instruction for what to do when the model is uncertain |
| `CONTEXT_OVERLOAD` | Excessive context buries the actual instruction |
| `PROMPT_INJECTION_RISK` | User input is interpolated without sanitisation or instruction barriers |
| `TEMPERATURE_MISMATCH` | Task requires deterministic output but no format constraints enforce it |

Using a fixed taxonomy rather than open-ended analysis serves two purposes: it enables analytical queries across sessions (`GROUP BY type`), and bounded categories reduce hallucination — Claude can't invent failure patterns that aren't in the schema.

**Score derivation:** Scores are calculated by deducting points per issue found: high severity issues deduct 15–25 points each, medium 5–10, low 1–3. The system prompt requires Claude to derive the score from this formula rather than assign a holistic judgment. This produces scores that are consistent with the issue list and distributed across the full 0–100 range.

**Summary constraint:** The summary field is constrained to a maximum of 120 characters in plain English. This matches the display space available in the frontend's verdict card without truncation, and forces Claude toward concise, non-technical language.

**Call 2 failure handling:** If the second Claude call fails, the first call's results are returned with `improved_output: null`. The user still gets the score, issues, and suggested prompt — only the output comparison is missing. The frontend handles this gracefully.

---

## API Reference

### POST /diagnostics
Run a full diagnostic on a prompt or n8n workflow. Makes two Claude API calls sequentially.

**Request body:**
```json
{
    "original_prompt": "You are a helpful assistant. Help the user.",
    "test_input": "My order hasn't arrived and it's been 3 weeks.",
    "input_mode": "prompt",
    "session_label": "Customer support bot"
}
```

`input_mode`: `"prompt"` or `"n8n_workflow"`. `session_label` is optional.

**Response:**
```json
{
    "session_id": "uuid",
    "score": 8,
    "summary": "Bare-minimum prompt with no domain, task, format, or safeguards.",
    "issues": [
        {
            "type": "MISSING_SYSTEM_PROMPT",
            "severity": "high",
            "explanation": "The system prompt provides no domain context...",
            "fix": "Define a specific role, task scope, and tone..."
        }
    ],
    "original_output": "What the broken prompt returns for the test input",
    "suggested_prompt": "Complete rewritten prompt with all issues fixed",
    "improved_output": "What the rewritten prompt returns for the same test input",
    "response_time_ms": 18400,
    "saved": true
}
```

### GET /diagnostics/stats
Aggregate analytics across all sessions.

```json
{
    "total_sessions": 140,
    "avg_score": 41.2,
    "avg_response_time_ms": 22100,
    "top_issues": [
        {"type": "AMBIGUOUS_INSTRUCTION", "count": 98, "pct_of_sessions": 70.0},
        {"type": "NO_OUTPUT_FORMAT", "count": 87, "pct_of_sessions": 62.1}
    ],
    "avg_score_by_mode": {
        "prompt": 43.1,
        "n8n_workflow": 31.4
    }
}
```

### GET /diagnostics/{session_id}
Retrieve a complete past session with all issues. Returns 404 if session not found. Issues ordered high → medium → low severity.

### GET /diagnostics
List past sessions. Query params: `limit` (default 20, max 100), `mode` (`prompt` or `n8n_workflow`). Returns session summaries with `issue_count` from LEFT JOIN — no issue detail.

### GET /health
Service health check. No authentication required.

---

## Database

Two tables: `sessions` and `issues`. Issues stored as rows with FK to sessions, not as a JSON array. This allows direct analytical queries without JSON unpacking:

```sql
-- Most common failure patterns
SELECT type, COUNT(*) AS occurrences
FROM issues
GROUP BY type
ORDER BY occurrences DESC;

-- Average score by input mode
SELECT input_mode, ROUND(AVG(score)::numeric, 1) AS avg_score, COUNT(*) AS sessions
FROM sessions
GROUP BY input_mode;

-- Worst prompts this week
SELECT s.session_label, s.score, COUNT(i.id) AS issue_count
FROM sessions s
LEFT JOIN issues i ON i.session_id = s.id
WHERE s.created_at > NOW() - INTERVAL '7 days'
GROUP BY s.id, s.session_label, s.score
ORDER BY issue_count DESC
LIMIT 10;

-- High severity issues in the last 7 days
SELECT s.original_prompt, i.type, i.fix
FROM issues i
JOIN sessions s ON i.session_id = s.id
WHERE i.severity = 'high'
AND s.created_at > NOW() - INTERVAL '7 days';

-- Failure rate by hour of day (timezone-corrected)
SELECT EXTRACT(HOUR FROM created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Madrid') AS hour,
       COUNT(*) AS sessions,
       ROUND(AVG(score)::numeric, 1) AS avg_score
FROM sessions
GROUP BY hour
ORDER BY hour;
```

---

## Setup

### Prerequisites
- Python 3.11+
- PostgreSQL (Railway Hobby plan works)
- Anthropic API key

### Install

```bash
git clone https://github.com/taiopar/ai-diagnostic-tool
cd ai-diagnostic-tool
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

### Environment variables
Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=your-key-here
DATABASE_URL=postgresql://user:password@host:port/dbname
API_KEY=your-api-key-here
```

### Database setup

```bash
python sql/run_schema.py
```

### Run

```bash
uvicorn main:app --reload
```

API available at `http://localhost:8000`  
Interactive docs at `http://localhost:8000/docs`

### Run tests

```bash
python test_ai.py       # AI layer — 10 cases (no server needed)
python test_db.py       # database layer — 5 cases (no server needed)
python test_suite.py    # full API suite — 20 cases (requires uvicorn running)
```

---

## Known Limitations

**Response time.** Each request makes two sequential Claude API calls. Total response time is typically 15–30 seconds. In production this would require async endpoints and overlapping calls.

**No connection pooling.** One connection opened and closed per function call. Under concurrent load this risks exhausting PostgreSQL's connection limit.

**Score variance on identical inputs.** After pinning `temperature=0`, run-to-run variance on identical inputs was reduced but not eliminated. The remaining variance is caused by reasoning-level non-determinism in the model, not temperature. The scoring rubric in the system prompt now requires Claude to derive scores mathematically from issue severity (high: −15 to −25 pts, medium: −5 to −10, low: −1 to −3) rather than assigning a holistic judgment, which substantially reduces clustering around round numbers.

**Score anchoring.** LLMs trained on feedback tend to anchor scores to a small set of psychologically "round" values (72, 62, 8) rather than using the full range. Fixed by the mechanical scoring rubric described above, with an explicit instruction to avoid round numbers. Residual clustering may still appear for prompts with very similar failure profiles — this is expected behaviour, not a bug.

**n8n mode is structural.** Analyses AI node configuration for missing prompts, absent output parsers, and missing error branches. Does not parse or validate the full workflow JSON schema.

**LLM misclassification.** The diagnostic call can misidentify or miss problems. The fixed taxonomy reduces but does not eliminate this. Inherent to LLM-based diagnosis.

**Scope: reusable system prompts, not one-off requests.** This tool is designed to diagnose prompts meant to handle variable future input (a customer support agent, an n8n AI node processing live data) — not complete, self-contained one-shot requests. Checks like `NO_ERROR_HANDLING` and `PROMPT_INJECTION_RISK` evaluate robustness against unpredictable future input, which is meaningless for a fully self-contained request with no variable input. A standalone request can be diagnosed by reframing it as a reusable system prompt — see the "Educational explainer prompt" demo example.

---

## Lessons Learned

**Pinned model versions can break without warning.** During Week 10 frontend integration, the app started returning 404s from the Anthropic API: `model: claude-sonnet-4-20250514` not found. The model had been retired the day before with no changes on my end. Fix was a one-line model string update. The real takeaway: hardcoded snapshot IDs are a maintenance liability. A more resilient setup uses a model alias, or at minimum logs and alerts on 404s from the model name specifically rather than surfacing them as a generic service error. The request schema also has a per-request `model_used` override field (a Pydantic default), which meant the fix required two separate edits — `ai.py` and the Pydantic model default — rather than one.

**Score anchoring is a prompt engineering problem, not a temperature problem.** The initial assumption was that score variance and round-number clustering were caused by LLM non-determinism, fixable by setting `temperature=0`. After pinning temperature, clustering persisted. The real cause is that asking for a holistic score judgment ("rate this prompt 0–100") invites anchoring to psychologically salient numbers from training data. The fix was changing the scoring instruction from a judgment task to a calculation task — giving the model a specific deduction formula to follow. This is a useful general pattern: wherever consistency matters, replace open-ended judgment prompts with constrained calculation prompts.

**Pydantic field defaults can silently override runtime values.** The `model_used` field on the response model had a hardcoded default string that was being returned in every response, masking the actual model being called. Caught during debugging when logs showed the correct model being called but the response claimed otherwise. Always validate that response field values reflect actual runtime state, not defaults.

**A system prompt written for one input shape silently degrades on another.** The n8n workflow diagnostic mode reused the exact same system prompt as plain-prompt mode — a prompt written entirely in terms of "the prompt," with no mention of JSON, nodes, or workflow structure, despite build_messages() handing Claude a full n8n workflow object for that mode. The result wasn't an error, which would have been easy to catch — it was thin, degenerate output (a near-empty suggested fix, issue counts inconsistent with the score) because the model had to guess what "the prompt" referred to inside a JSON blob the system prompt never acknowledged existed. Caught via live testing with the tool's own demo examples, not via the test suite, since test_ai.py's existing n8n case happened to still produce parseable (if weak) output. Fix was splitting the system prompt into a shared base plus a mode-specific suffix (build_system_prompt(mode)), explicitly telling the model where the real prompt lives inside the JSON and what the output schema should mean in that context. The general pattern: a schema or prompt that's technically mode-agnostic in code can still be semantically mode-blind — supporting a second input shape isn't done until the instructions actually describe that shape, not just parse it.

**Persistence without scoping is a leak waiting for traffic.** The sessions table and its "past sessions" sidebar were built in Week 9 with no visitor-identifying column at all — no user_id, no cookie, nothing — because the spec's idea of "user" at the time was singular: me, testing my own tool. That assumption broke silently the moment the tool got real outside traffic during application week: every visitor's session list was the same global feed, meaning a recruiter opening the tool saw my own dev-testing debris ("Cooking assistant," "Tone rewriter") alongside anyone else's test runs, with zero separation. Nothing crashed, nothing errored — it just quietly stopped meaning what it was supposed to mean once the number of visitors went from one to many. Fix was moving session history to localStorage on the frontend, scoped per-browser, with no backend schema changes needed — the full session data was already available client-side the moment a diagnostic completed, so persistence didn't require a round trip at all. The general takeaway: an MVP built for a single implicit user needs its ownership assumptions re-examined the moment it's exposed to actual multi-visitor traffic, not just its features — a working feature and a correctly-scoped feature are different claims, and a portfolio piece that goes public needs the second one.
---

## Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.13 |
| Web framework | FastAPI + Uvicorn |
| AI provider | Anthropic API — claude-sonnet-4-6 |
| Database | PostgreSQL (Railway) + psycopg2-binary |
| Validation | Pydantic v2 |
| HTTP testing | requests library |
| Environment | python-dotenv |
| Deployment | Railway (Hobby plan, Procfile included) |