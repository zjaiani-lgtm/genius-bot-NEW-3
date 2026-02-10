import sqlite3
from execution.config import DB_PATH


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # positions
    cur.execute("""
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
    )
    """)

    # audit log
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)

    # system state
    cur.execute("""
    CREATE TABLE IF NOT EXISTS system_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        status TEXT NOT NULL DEFAULT 'RUNNING',
        startup_sync_ok INTEGER NOT NULL DEFAULT 0,
        kill_switch INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    INSERT OR IGNORE INTO system_state (id, status, startup_sync_ok, kill_switch, updated_at)
    VALUES (1, 'RUNNING', 0, 0, datetime('now'))
    """)

    # oco links
    cur.execute("""
    CREATE TABLE IF NOT EXISTS oco_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        base_asset TEXT NOT NULL,
        tp_order_id TEXT NOT NULL,
        sl_order_id TEXT NOT NULL,
        tp_price REAL NOT NULL,
        sl_stop_price REAL NOT NULL,
        sl_limit_price REAL NOT NULL,
        amount REAL NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)

    # ✅ executed signals (idempotency) — DEDUPE BY signal_id
    cur.execute("""
    CREATE TABLE IF NOT EXISTS executed_signals (
        signal_id TEXT PRIMARY KEY,
        signal_hash TEXT,
        action TEXT,
        symbol TEXT,
        executed_at TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()
