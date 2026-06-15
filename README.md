# AI Integration Diagnostic Tool

A tool for developers building LLM-powered features and n8n automations. Paste a broken prompt and a test input - get a structured diagnosis of what's wrong, a suggested rewrite, and a before/after comparison. Every session is persisted to PostgreSQL and retrievable via REST API.

**API base URL:** `http://localhost:8000`
**Interactive docs:** `http://localhost:8000/docs`

---

## What it does

A developer pastes a broken LLM prompt and a simple input into the tool. The system makes two Claude API calls: the first analyses the prompt against a fixed taxonomy of 10 failure patterns and produces a structured diagnosis with a score, issue list, and complete rewrite. The second runs the rewritten prompt against the same input and returns the improved output. The user sees the before/after comparison side by side. Every session is saved to PostgreSQL and retrievable by session ID.

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
**`main.py` - FastAPI application**
The HTTP layer. Defines four endpoints, request/response Pydantic models, and the error handling hierarchy. Knows nothing about how Claude works internally or how SQL is structured - it receives a validated request, calls the right functions from `ai.py` and `db.py`, and returns a serialised response. Also handles the non-punishing DB failure pattern: if `save_session()` raises, the AI result is returned anyway with `saved: false`.

**`ai.py` - AI logic module**
The Claude integration layer. Four functions with clean separation:

- **`build_messages()`** - constructs the messages list sent to Claude. User input always lands in the user turn, never in the system prompt. This is the prompt injection defence: the system prompt is a module-level constant that never changes at runtime, regardless of what the user submits.
- **`call_claude()`** - sends messages to Anthropic API and returns the raw text response. Raises `AnalysisError` if the call fails. Single responsability: no parsing, no validation, just the network call.
- **`parse_response()`** - strips markdown fences if Claude adds them despite instructions, runs `json.loads()`, and validates the result against `DiagnosticResult`. Raises on failure so the retry loop in `analyse()` can catch it.
- **`analyse()`** - the main pipeline function. Calls the three functions above in sequence, implements the retry loop on parse failure, calls `run_improved_prompt()` for the second Claude call, and returns a `(DiagnosticResult, str | None)` tuple. This is the only function `main.py` calls directly.

**`db.py` - Database layer**
Direct `psycopg2` calls - no ORM. The choice is deliberate: an ORM like SQLAlchemy abstracts away the SQL, which makes it harder to understand what queries are actually running, where the connection lifecycle is, and why a transaction is or isn't being committed. Using raw SQL forces every query to be explicit and every connection to be consciously opened, committed, and closed. This matters for debugging - when `save_session()` failed on Day 3, the exact line and exact SQL were immediately visible. Four functions:

- **`save_session()`** - generates a UUID in Python, inserts one row into sessions, then loops inserting one row per issue. Single `commit()` at the end covers both INSERTs. `rollback()` in the except block ensures partial writes never persist.
- **`get_session()`** - retrieves a session and its issues in two queries, combines them into one dict. Uses `RealDictCursor` so rows come back as named dicts rather than positional tuples.
- **`list_sessions()`** - returns session summaries with `issue_count` via `LEFT JOIN`. No issue detail - keeps the list endpoint lightweight.
- **`get_stats()`** - runs three aggregate queries and combines the results into the stats response dict.

**`sql/schema.sql` - PostgreSQL schema**
Two tables: `sessions` and `issues`. Issues are stored as separate rows with foreign key to sessions rather than as a JSON array column on sessions. The reason is queryability: `SELECT type, COUNT(*) FROM issues GROUP BY type` works directly. With a JSON array, that same query requires JSON unpacking syntax / slower, harder to read, and non-portable. `ON DELETE CASCADE` on the foreign key means deleting a session automatically deletes its issues, preventing orphaned rows. The `pgcrypto` extension enables `gen_random_uuid()` for UUID primary keys generated by the database.

---

## How the diagnostic works

The system analyses prompts against a fixed taxonomy of 10 failure patterns:

- MISSING_SYSTEM_PROMPT
- AMBIGUOUS_INSTRUCTION
- NO_OUTPUT_FORMAT
- TASK_TOO_BROAD
- NO_EXAMPLES
- CONFLICTING_INSTRUCTIONS
- NO_ERROR_HANDLING
- CONTEXT_OVERLOAD
- PROMPT_INJECTION_RISK
- TEMPERATURE_MISMATCH

Using a fixed taxonomy rather than open-ended analysis serves two purposes...
(explain: consistency across sessions enables GROUP BY queries; bounded categories reduce hallucination)

Each issue includes a specific fix - not generic advice. The system prompt explicitly requires Claude to rewrite the relevant section rather than describe what should change...
(explain why this matters for the user)

The second Clause call runs the suggested prompt against the same test input. This produces the improved_output field - the before/after comparison the user sees in the UI...
(explain the failure handling: if Call 2 fails, Call 1 results still return)

---

## API Reference

### POST /diagnostics
Run a full diagnostic on a prompt or n8n workflow. Two Claude calls: analysis and validation of the rewrite.

**Request body:**
```json
{
    "original_prompt": "You are a helpful assistant. Help the user.",
    "test_input": "Summarise this document for me.",
    "input_mode": "prompt",
    "session_label": "optional label"
}
```

**Response:**
```json
{
    "session_id": "uuid",
    "score": 25,
    "summary": "This prompt is too vague for production to use",
    "issues": [
        {
            "type": "AMBIGUOUS_INSTRUCTION",
            "severity": "high",
            "explanation": "The instruction 'Help the user' provides no guidance...",
            "fix": "Replace with a specific role definition and task description..."
        }
    ],
    "original_output": "What the broken prompt returns for the test input",
    "suggested_prompt": "complete rewritten prompt with all issues fixed",
    "improved_output": "what the rewritten prompt returns for the same test input",
    "response_time_ms": 18400,
    "saved": true
}
```

### GET /diagnostics/stats

Aggregate analytics across all sessions.

```json
{
    "total_sessions": 42,
    "avg_score": 28.5,
    "avg_response_time_ms": 22100,
    "top_issues": [
        {"type": "NO_OUTPUT_FORMAT", "count": 34, "pct_of_sessions": 80.9},
        {"type": "AMBIGUOUS_INSTRUCTION", "count": 31, "pct_of_sessions": 73.8}
    ],
    "avg_score_by_mode": {
        "prompt": 29.1,
        "n8n_workflow": 18.3
    }
}
```


### GET /diagnostics/{session_id}

Retrieve a complete pasts session with all issues. Returns 404 if session not found. Issues ordered high → medium → low severity.

### GET /diagnostics

List past sessions. Query params: `limit` (default 20, mas 100), `mode` (`prompt` or `n8n_workflow`). Returns session summaries with `issue_count` from LEFT JOIN - no issue detail. 

### GET /health

Service health check. No authentication required.

---


## Database

Two tables: `sessions` and `issues`.

Issues are stored as separate rows rather than a JSON array on sessions.
This allows analytical queries without JSON parsing:

```sql
-- Most common failure patterns across all sessions
SELECT type, COUNT(*) as occurrences
FROM issues
GROUP BY type
ORDER BY occurences DESC;

-- Average score by input mode
SELECT input_mode, ROUND(AVG(score)::numeric, 1) as avg_score, COUNT(*) as sessions
FROM sessions
GROUP BY input_mode;

-- Worst prompts this week
SELECT s.id, s.score, COUNT(i.id) as issue_count
FROM sessions s
LEFT JOIN issues i ON i.session_id = s.id
WHERE s.created_At > NOW() - INTERVAL '7 days'
GROUP BY s.id, s.score
ORDER BY issue_count DESC
LIMIT 10;

-- High severity issues in the last 7 days
SELECT s.original_prompt, i.type, i.fix
FROM issues i
JOIN sessions s ON i.session_id = s.id
WHERE i.severity = 'high'
AND s.created_At > NOW() - INTERVAL '7 days';

-- Average response time by day
SELECT DAY(created_at) as day, ROUND(AVG(response_time_ms)::numeric, 0) as avg_ms
FROM sessions
GROUP BY day
ORDER BY day;
```

---

## Setup

### Prerequisites
- Python 3.11+
- PostgreSQL (Railway free tier works - add a PostgreSQL service to your existing project)
- Anthropic API key

### Install

```bash
git clone https://github.com/taiopr/ai-diagnostic-tool
cd ai-diagnostic-tool
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

### Environment variables
Create a `env.` file in the project root:

```
ANTHROPIC_API_KEY=your-key-here
DATABASE_URL=postgresql://user:password@host:port/dbname
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
python test_ai.py       # AI layer - 10 cases (no server needed)
python test_db.py       # database layer - 5 cases (no server needed)
python test_suite.py    # full API suite - 20 cases (requires uvicorn running)
```

---

## Known limitations
- **Response time:** Each request makes two sequential Claude API calls. Total response time is typically 15-30 seconds. In production this would require async endpoints and overlapping calls.
- **No connection pooling:** One connection opened and closed per function call. Under concurrent load this risks exhausting PostgreSQL's connection limit.
- **No authentication:** Any client with the URL can run diagnostics and charge to the Anthropic accound. API key auth (Week 8 pattern) required before any public exposure.
- **No rate limiting:** A single client can flood the API. Simple middleware would cap requests per IP.
- **n8n mode is structural:** Analyses AI node configuration for missing prompts, no output parser, no error branch. Does not parse or validate the full workflow JSON schema.
- **LLM misclassification:** The diagnostic call can misidentify problems or miss them. The fixed taxonomy reduces but does not eliminate this. Inherent to LLM-based diagnosis.


---
 
## Stack
 
| Layer | Technology |
|-------|-----------|
| Language | Python 3.11 |
| Web framework | FastAPI + Uvicorn |
| AI provider | Anthropic API — claude-sonnet-4-20250514 |
| Database | PostgreSQL (Railway) + psycopg2-binary |
| Validation | Pydantic v2 |
| HTTP testing | requests library |
| Environment | python-dotenv |
| Deployment target | Railway (Procfile included) |
 
---
