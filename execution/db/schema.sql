-- execution/db/schema.sql

CREATE TABLE IF NOT EXISTS system_state (
    id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    kill_switch INTEGER NOT NULL,
    startup_sync_ok INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    size REAL NOT NULL,
    entry_price REAL NOT NULL,
    status TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    pnl REAL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    size REAL NOT NULL,
    price REAL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_state (
    id INTEGER PRIMARY KEY,
    daily_loss REAL NOT NULL,
    daily_profit REAL NOT NULL,
    max_daily_loss REAL NOT NULL,
    current_drawdown REAL NOT NULL,
    max_drawdown REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Optional: speed up lookups
CREATE INDEX IF NOT EXISTS ix_audit_event_type ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS ix_positions_status ON positions(status);

-- Idempotency support (soft): ensure we can prevent duplicates by querying quickly
-- (We use has_executed_signal() in repository; unique constraint not required for v1.)
