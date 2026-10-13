CREATE TABLE events (
    idempotency_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
