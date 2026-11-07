"""SQLite storage. One file, no server: easy to run locally and easy to swap for Postgres later."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ALTINV_DATA_DIR", ROOT / "data"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS funds (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    gp_name TEXT,
    aliases TEXT NOT NULL DEFAULT '[]',
    strategy TEXT,
    vintage INTEGER,
    commitment REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    portal TEXT,
    statement_frequency TEXT NOT NULL DEFAULT 'quarterly'
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL,
    file_name TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    uploaded_by TEXT,
    doc_type TEXT,
    fund_code TEXT REFERENCES funds(code),
    extraction TEXT,
    extractor TEXT,
    confidence REAL,
    validation TEXT,
    status TEXT NOT NULL,
    duplicate_of INTEGER,
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER REFERENCES documents(id),
    fund_code TEXT NOT NULL REFERENCES funds(code),
    txn_type TEXT NOT NULL,          -- contribution | distribution | opening_balance
    effective_date TEXT NOT NULL,
    amount REAL NOT NULL,            -- contribution total / gross distribution
    recallable REAL NOT NULL DEFAULT 0,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER REFERENCES documents(id),
    fund_code TEXT NOT NULL REFERENCES funds(code),
    period_start TEXT,
    period_end TEXT NOT NULL,
    beginning_balance REAL,
    contributions REAL,
    distributions REAL,
    investment_income REAL,
    expenses REAL,
    realized_gain REAL,
    unrealized_gain REAL,
    carried_interest REAL,
    ending_balance REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS k1s (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER REFERENCES documents(id),
    fund_code TEXT NOT NULL REFERENCES funds(code),
    tax_year INTEGER NOT NULL,
    is_final INTEGER,
    boxes TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER REFERENCES documents(id),
    fund_code TEXT NOT NULL REFERENCES funds(code),
    entry_date TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    memo TEXT,
    created_at TEXT NOT NULL,
    export_batch TEXT
);

CREATE TABLE IF NOT EXISTS je_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    je_id INTEGER NOT NULL REFERENCES journal_entries(id),
    account_key TEXT NOT NULL,
    debit REAL NOT NULL DEFAULT 0,
    credit REAL NOT NULL DEFAULT 0,
    memo TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT,
    action TEXT NOT NULL,
    document_id INTEGER,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_documents_sha ON documents(sha256);
CREATE INDEX IF NOT EXISTS idx_txn_fund ON transactions(fund_code, effective_date);
CREATE INDEX IF NOT EXISTS idx_je_fund ON journal_entries(fund_code, entry_date);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def db_path() -> Path:
    return DATA_DIR / "altinv.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def audit(conn: sqlite3.Connection, actor: str | None, action: str, document_id: int | None = None, detail=None):
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, document_id, detail) VALUES (?,?,?,?,?)",
        (now_iso(), actor, action, document_id, json.dumps(detail) if detail is not None else None),
    )


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for key in ("aliases", "extraction", "validation", "detail", "boxes"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except json.JSONDecodeError:
                pass
    return d


def rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
