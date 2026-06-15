## POJECT NAME:
AI Integration Diagnostic Tool
One-line description: A diagnnnostic tool for developers building LLM-powered features and n8n automations- tells you why your AI node or prompt is producing bad output and what to fix.
Problem it solves: When a developer's LLM integration is producing bad results, they don't know if the problem is the prompt, the model,
the context, or the data. They spend hours debugging blind.
Primary user (specific, not generic): Mid-level developers who have shipped LLM-powered features but are getting inconsistent, wrong, or low-quality outputs in production.
What success looks like (user perspective): Developer pastes a broken prompt and a test input, gets a clear diagnosis of what's wrong, sees a suggested rewrite, runs the fixed prompt, and can compare the before/after output side by side. Session is saved so they can come back of it.
What success looks like (technical perspective): The app sends the user's prompt + test input to the LLM, which returns structured output (JSON) identifying specific failure categories and suggesting fixes. The improved prompt is run automatically and the output is stored. Every session is persisted to a SQLite database. The user can retrieve and compare past sessions.

## INPUTS:
- User's original prompt (text, the system/user prompt they wrote)
- Test input (text, the sample user message or data they want to run through the prompt)
- Optional: model name (default to sensible default if not provided)
- Optional: session label (a name they give this diagnostic run)
- Optional: n8n workflow JSON (paste the exported workflow; the tool identifies which AI node is the problem and analyses its configuration - prompt, model, context window, output parsing - as a distinct diagnostic mode)

## OUTPUTS:
- Diagnosis: list of identified issues with the prompt (e.g. "no output format specified", "ambiguous instruction", "no persona defined")
- Suggested rewrite: an improved version of the original prompt
- Original output: what the original prompt produced on the test input
- Improved output: what the suggested rewrite produced on the same test input
- Side-by-side comparison in the UI
- Session ID for retrieval

## DATA STORED IN DATABASE:
- Table 1: sessions - stores one row per diagnostic run. Fields: session_id(UUID), session_label, created_at, original_prompt, test_input, model_used. Why: gives the user a list of past sessions to return to. `input_mode`(enum: `prompt` or `n8n_workflow`) so you can query which mode was used and so the results table knows which analysis template was applied.
- Table 2: issues - stores one row per identified issue within a session. Fields: id(UUID), session_id (FK), type, severity, explanation, fix. Suggested_prompt, original_output and improved_output live as columns on sessions - they are session-level outputs, not per-issue.

## DATABASE SCHEMA
Run this on Day 2 before writing any backend code.

See sql/schema.sql

### Why two tables instead of one

`issues` as separate rows (not a JSON array on `sessions`) means
analytical queries work without JSON parsing:

```sql
-- Most common issue types
SELECT type, COUNT(*) FROM issues GROUP BY type ORDER BY COUNT (*) DESC;

-- Average score by mode
SELECT input_mode, ROUND(AVG(score)) FROM sessions GROUP BY input_mode;

-- Worst prompts this week
SELECT s.id, s.score, COUNT(i.id) as issue_count
FROM sessions s
LEFT JOIN issues i ON i.session_id = s.id
WHERE s.created_at > NOW() - INTERVAL '7 days'
GROUP BY s.id, s.score
ORDER BY issue_count DESC;
```

### SQLite note

The spec targets local development this week. SQLite doesn't support
`gen_random_uuid()` natively. Use Python's `uuid.uuid4()` to generate
UUIDs in the application layer and pass them as strings.
For PostgreSQL in production the DEFAULT handles it automatically.

## EDGE CASES:
- Empty input: If either the original prompt or test input is blank, reject immediately with a clear validation error before any API call is made. Do not send empty fields to the LLM.
- AI call falls: Return a 503 with a user-readable error message. Do not write a partial result to the database. Log the error server-side.
- Database write fails: Log the error, return the diagnostic result to the user anyway (don't punish them with a failed session), and flag that the session was not saved.
- Malicious input: Treat all inputs as untrusted strings. No SQL string interpolation - use parametrerised queries only. Prompt injection is a risk; your diagnostic LLM call uses a hardcoded system prompt that you control. so user input only ever lands in the user-turn, never in the system turn.


## FAILURE MODES:
- Wrong answers: The diagnotstic LLM call might misclassify the problem - it might say the prompt is fine when it isn't, or suggest a fix that makes things worse. This is inherent to LLM-based diagnosis. Mitigation: Make the issue categories concrete and enumerable (not open-ended) so the model has less room to hallucinate a diagnosis.
- Input that break it: Very long prompts (>8k tokens combined) will hit context limits or produce degraded analysis. Extremely short inputs ("fix this:bad") give the diagnostic model nothing to work with - output will be low quality but technically valid.
- Silent failure: If the structured output from the diagnostic call doesn't parse correctly (model returns malformed JSON), the app could silently store garbage or crash without useful error. Mitigation: validate the diagnostic response schema with Pydantic before writing to the database.


## OUT OF SCOPE (for this week):
- User accounts and authentication - no login, no multi-user support, sessions are not user-scoped
- Streaming output - responses are returned in full, no token-by-token display
- Multiple model comparison - one model per session, no A/B across providers
- Automated prompt optimization loops - one round of diagnosis and suggestion, not iterative self-improvement
- Deployement - runs locally or on your own machine, no production hosting this week
- n8n workflow repair - the tool identifies what's wrong with the AI node configuration, it does not rewrite the worflow JSON for you. Diagnosis only, not automated fix.


## API ENDPOINTS
POST /diagnostics
Purpose: Run a full diagnostic on a prompt or n8n workflow JSON.
         Core endpoint - everything else is read-only.
Request: {
    original_prompt:    string (required),
    test_input:         string (required),
    input_mode:         "prompt" | "n8n_workflow" (default: "prompt"),
    model_used:         string (optional, default: "claude-sonnet-4-20250514"),
    session_label:      string (optional)
} 
Response: {
    session_id:         uuid,
    score:              int (0-100),
    summary:            string,
    issues:             [{type, severity, explanation, fix}],
    original_output:    string,
    suggested_prompt:   string,
    improved_output:    string
}
DB: INSERT into sessions (all input fields + all Claude outputs)
    INSERT into issues (one row per issue, FK to session_id)
AI: Two Claude calls in sequence -
    Call 1: diagnostic call - sends original_prompt + test_input,
            returns structured JSON (score, summary, issues[],
            suggested_prompt, original_output)
    Call 2: validation call - sends suggested_prompt + test_input,
            returns improved_output so user can compare before/after
    If Call 1 fails: raise 503. write nothing to DB
    If Call 2 fails: return Call 1 results anyway, improved_output: null


GET /diagnostics/{session_id}
Purpose: Retrieve a complete past session with all issues.
Request: none
Response: {
    session_id:         uuid,
    session_label:      string,
    created_At:         timestamp,
    input_mode:         string,
    original_prompt:    string,
    test_input:         string,
    model_used:         string,
    score:              int,
    summary:            string,
    original_output:    string,
    suggested_prompt:   string,
    improved_output:    string,
    issues:             [{type, severity, explanation, fix}]
}
DB: SELECT from sessions WHERE id = session_id
    SELECT from issues WHERE session_id = session_id
    Return 404 if session not found
AI: none


GET /diagnostics
Purpose: List past sessions for the history view.
         No issue detail - just enough to render the session list.
Request: query params -
    limit: int (optional, default 20, max 100)
    mode:  "prompt" | "n8n_workflow" (optional filter)
Response: {
    sessions: [{
        session_id:     uuid,
        session_label:  string,
        created_at:     timestamp,
        input_mode:     string,
        score:          int,
        summary:        string,
        issue_count:    int
    }]
}
DB: SELECT sessions.*, COUNT(issues.id) as issue_count
    FROM sessions LEFT JOIN issues ON issues.sessions_id = sessions.id
    GROUP BY sessions.id
    ORDER BY created_at DESC
    LIMIT {limit}
    WHERE input_mode = {mode} if filter provided
AI: none


GET /diagnostics/stats
Purpose: Aggregate analytics across all sessions.
         Powers the "most common issues" insight in the UI.
         This is also your Week 10 SQL analytical queries endpoint.
Request: none
Response: {
    total_sessions:         int,
    avg_score:              float,
    top_issues: [{
        type:               string,
        count:              int,
        pct_of_sessions:    float
    }],
    avg_score_by_mode: {
        prompt:             float,
        n8n_workflow:       float
    },
    avg_response_time_ms:   float
}
DB: SELECT COUNT(*) FROM sessions
    SELECT type, COUNT(*) FROM issues GROUP BY type ORDER BY COUNT DESC
    SELECT input_mode, AVG(score) FROM sessions GROUP BY input_mode
    SELECT AVG(response_time_ms) FROM sessions
AI: none


## FRONTEND PLAN

┌─────────────────────────────────────────────────────┐
│  AI Integration Diagnostic Tool                      │
│  [n8n workflow mode toggle]                          │
├──────────────────────┬──────────────────────────────┤
│  LEFT PANEL          │  RIGHT PANEL                  │
│  (input)             │  (results)                    │
│                      │                               │
│  Prompt textarea     │  Score badge (0-100)          │
│                      │  Summary sentence             │
│  Test input textarea │                               │
│                      │  Issues list                  │
│  Session label       │  (colored by severity)        │
│  (optional)          │                               │
│                      │  Before / After tabs          │
│  [Run Diagnostic]    │  (original vs improved)       │
│                      │                               │
│  ──────────────────  │  Suggested prompt             │
│  Past Sessions       │  (copyable)                   │
│  (scrollable list)   │                               │
└──────────────────────┴──────────────────────────────┘


## Architecture Review

### 1. Does the system match what you designed?
**main.py** - Matches exactly. Four endpoints defined in the spec: POST /diagnostics, GET /diagnostics/stats, GET /diagnostics/{session_id}, GET /diagnostics. Pydantic validation on request and response models. CORS middleware. Error hierarchy: 422 from Pydantic, 503 from AnalysisError, 200 with saved: false on DB failure.

**ai.py** - Matches the three-job structure designed on Day 1: build_messages(), call_claude(), parse_response(). The retry loop was planned. The second Claude call (run_improved_prompt()) was planned. The function signature drifted - see section 2.

**db.py** - Matches the four functions: save_session(), get_session(), list_sessions(), get_stats(). The SQL in list_sessions() uses LEFT JOIN + GROUP BY as designed. The CASE severity ordering in get_session() was added during implementation - not in the original spec but a natural extension of the design intent.

**sql/schema.sql** - Matches exactly. Two tables, UUID primary keys, ON DELETE CASCADE on the issues foreign key, pgcrypto extension for gen_random_uuid().

**Endpoints** - All four endpoints implemented with the request/response shapes defined in the spec. The /diagnostics/stats route ordering decision (must come before /{session_id}) was not explicit in the spec but is a direct consequence of the FastAPI route matching behaviour described in the Week 8 work.

**What does not match** - The spec said "runs locally, no production hosting this week." The database is on Railway PostgreSQL, which is production infrastructure. This was the right call - SQLite would have meant rewriting the connection layer later. But it is a deviation from the spec's stated scope.

---

### 2. What changed and why?
**analyse() return type changed from DiagnoticResult to tuple[DiagnosticResult, str | None]**
Original design had analyse() returning a DiagnosticResult. When run_improved_prompt() was added for the second Claude call, the function needed to return both the diagnostic result and the improved output together. Changed to a tuple. Every caller - test_ai.py, test_db.py, main.py - had to be updated to unpack it. This was a conscious change made when the second call was added, not a surprise. It would have been cleaner to design the return type correctly from the start.

**save_session() generates UUID in Python instead of letting PostgreSQL generate it**
The schema has DEFAULT gen_random_uuid() on the sessions.id column. Original assumption was the PostgreSQL would handle UUID generation automatically. During implementation it became clear that the session UUID was needed immediately after the sessions INSERT to use as the foreign key in the issues INSERTs. Two options: generate in Python and pass it in, or run a SELECT after the INSERT to retrieve the generated value. Generating in Python is one fewer database round-trip and keeps the logic simpler. Changed consciously.

**DiagnosticResponse needed improved_output: str | None = None, not str | None**
The spec correctly identified improved_output as optional - it is None when the second Claude call fails. The Pydantic model was written as improved_output: str | None, which declares the type as string-or-None but does not give the field a default. Pydantic treats it as required. This caused 500 eroors on every valid test case in Day 4 until the fix: improved_output: str | None = None. The distinction between "type allows None" and "field has a default of None" was not obvious at design time. In hindsight, any field that is conditionally populated should always have a default in the response model.

**CASE severity ordering added to get_session()**
The spec defined the issues list but did not specify ordering. During implementation it was obvious that high severity issues should appear before medium and low - that is the order the UI needs. Added ORDER BY CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END to the query. Small addition, not a design decision, but worth documenting as something the spec left underspecified.

**sql/run_schema.py added, not in original spec**
The spec said "run the schema SQL in Supabase SQL Editor." During the implementation, running schema SQL through a Python script was clearly better - repeatable, version controlled, executable in a single command. Added as a utility script. Zero impact on the system design.

---

### 3. Where did the spec protect you?

**The two-table design prevented a JSON aggregation trap**
Without the spec forcing the decision upfront, issues would almost certainly have been stored as a JSONB column on the sessions table - it is the path of least resistance when you are moving fast. The spec required documenting the analytical queries before writing any code. Writing SELECT type, COUNT(*) FROM issues GROUP BY type made it immediately obvious that this query is impossible on a JSON array without unpacking syntax. The two-table design was locked in before Day 2 began. The stats enpoint wrote itself.

**The DB failure case was pre-decided**
During Day 3, save_session() had multiple bugs that caused it to raise exceptions. In the moment of debugging, there was no temptation to return a 500 and call it done - the spec's edge cases section already said "return the diagnostic result to the user anyway, flag that the session was not saved." The saved: bool field was in the response model from the start. The behaviour under failure was a design decision, not an emergency response.

**The prompt injection defence was written before ai.py existed**
The spec's edge cases section said "user input only ever lands in the user-turn, never in the system turn." When build_messages() was written, this constraint was already documented. The system prompt was made a module-level constant before any user-facing code existed. Without the spec, this is the kind of defence that gets added retroactively after someone notices the gap.

**The ouf-of-scope list prevented feature creep**
During Day 4, while building the stats endpoint, it was tempting to add model comparison - show which model produces the highest scores. The spec's out-of-scope list explicitly named "multiple model comparison - one model per session, no A/B across providers." The decision was already made. No time was spent evaluating whether to build it.

### 4. What would you design differently with hindsight?

**Connection pooling from day one**
Every function in db.py opens a connection, uses it, and closes it. This is correct for a single-user local tool. Under any concurrent load - even 5 simultaneous users - this creates 5 connections that each stay open for the duration of a 15-30 second request. PostgreSQL has a default connection limit of 100. A production system would use psycopg2.pool.ThreadedConnectionPool or SQLAlchemy's connection pool. The fix is not complex but it requires changing the get_connection() helper and every function that calls it. Easier to design in from the start than to retrofit.

**Async endpoints to overlap the two Claude calls**
The two Claude calls are sequential: Call 1 completes, then Call 2 starts. Total request time is 15-30 seconds. Call 2 (running the improved prompt) does not depend on anything from Call 1 except suggested_prompt - but suggested_prompt is only available after Call 1 finishes, so true parallelism is not possible here. What async would enable is handling multiple concurrent requests without blocking threads. With synchronous def endpoints, each request occupies a thread for its entire 15-30 seconds. With async def endpoints and await on the Claude calls, the event loop can handle each other requests while waiting for the API response. This requires switching from the Anthropic sync client to the async client which is a straightforward change but needs to be planned from the start.

**improved_output: str | None = None in the response model from day one**
The rule going forward: any field in a response model that is conditionally populated must have = None as a default. The type annotation str | None is not enough - it describes what values the field accepts, not whether the field is required. A field without a default is required by Pydantic regardless of its type. This would have prevented the Day 4 bug that caused 500 errors on all 16 valid test cases. The fix took 30 seconds once diagnosed. The diagnosis took longer because the error message - "Field required" - does not obviously point to a missing default.

**Input length validation at the token level, not character level**
The DiagnosticRequest model validates original_prompt at max_length=8000 characters. Claude's context window is measured in tokens, not characters. A prompt consisting of 8000 Chinese characters is roughly 16000 tokens. A prompt of 8000 ASCII characters is roughly 2000 tokens. The character limit is a proxy that does not accurately reflect Claude's actual capacity constraints. A production implementation would count tokens using the Anthropic tokeniser before sending, and reject or truncate inputs that exceed the combined token budget for system prompt + user prompt + expected output.

**Request timeout handling**
The current implementation has no timeout on the Claude API calls. A slow response from Anthropic - which happens occasionally - means the request hangs indefinitely, occupying a thread and a database connection until the Anthropic client eventually times out on its own (which can take several minutes). The Anthropic client accepts a timeout parameter. Setting it to 60 seconds and handling the resulting exception explicitely would give the user a clear 503 rather than a silent hang.

---

### 5. What is missing for production readiness?

**API key authentication**
Currently any client with the URL can call POST /diagnostics and charge to the Anthropic account. The fix is the same pattern used in Week 8: an X-API-Key header validated by a FastAPI dependency. Without authentication, the depolyed URL cannot be shared with anyone - sharing it means sharing billing access. This is the single most important missing piece before any public exposure.

**Rate limiting**
Even with authentication, a single client with a valid key can send unlimited requests. Each request costs approximately 15-30 seconds of compute and two Anthropic API calls. A rate limit of 10 requests per minute per client would prevent accidental or deliberate abuse from draining credits. FastAPI does not include rate limiting natively - a library like slowapi (which wraps limits) or a middleware implementation with Redis for state would handle this. Without it, the API is one script loop away from an empty Anthropic account.

**Structured logging**
The current logging is print() statements. In production, print() output is unstructured, unsearchable, and does not carry context. A structured logging setup - using Python's logging module with JSON formatting - would emit log lines that include timestamp, request ID, endpoint, session ID, response time, error type, and HTTP status. This makes debugging production failures possible: instead of reading raw terminal output, you query logs for all requests where response_time_ms > 20000, or all sessions where saved = false, or all AnalysisErrors in the last hour. Without structured logging, production incidents are diagnosed by guessing.