```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE sessions(
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at          TIMESTAMP DEFAULT NOW(),
    session_label       TEXT,

    -- User inputs
    original_prompt     TEXT NOT NULL,
    test_input          TEXT NOT NULL,
    input_mode          TEXT NOT NULL DEFAULT 'prompt',
                        -- 'prompt' | 'n8n_workflow'
    model_used          TEXT NOT NULL DEFAULT 'claude-sonnet-4-20250514',

    -- Claude outputs (top-level)
    score               INT,
    summary             TEXT,
    original_output     TEXT,
    suggested_prompt    TEXT,
    improved_output     TEXT,
    response_time_ms    INT
);

CREATE TABLE issues (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID REFERENCES sessions(id) ON DELETE CASCADE,
    created_at      TIMESTAMP DEFAULT NOW(),

    type            TEXT NOT NULL,
    severity        TEXT NOT NULL,
    explanation     TEXT NOT NULL,
    fix             TEXT NOT NULL
);
```