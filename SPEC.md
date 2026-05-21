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