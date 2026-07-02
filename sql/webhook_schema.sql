CREATE TABLE IF NOT EXISTS webhook_events (
    id SERIAL PRIMARY KEY,
    received_at TIMESTAMPTZ DEFAULT NOW(),
    source TEXT,
    event_type TEXT,
    payload JSONB NOT NULL,
    status TEXT DEFAULT 'received'
);