"""Book-side queries: what our records say about each fund (called, distributed, NAV, unfunded)."""
from __future__ import annotations

import sqlite3

from .posting import load_accounts


def _scalar(conn: sqlite3.Connection, sql: str, params=()) -> float:
    v = conn.execute(sql, params).fetchone()[0]
    return float(v or 0.0)


def book_nav(conn: sqlite3.Connection, fund_code: str, as_of: str | None = None) -> float:
    """Carrying value of the investment account, from posted journal lines."""
    sql = ("SELECT SUM(l.debit - l.credit) FROM je_lines l JOIN journal_entries j ON j.id = l.je_id "
           "WHERE j.fund_code = ? AND l.account_key = 'investment'")
    params: list = [fund_code]
    if as_of:
        sql += " AND j.entry_date <= ?"
        params.append(as_of)
    return round(_scalar(conn, sql, params), 2)


def activity(conn: sqlite3.Connection, fund_code: str, txn_type: str, start: str | None = None,
             end: str | None = None, include_opening: bool = True) -> float:
    sql = "SELECT SUM(amount) FROM transactions WHERE fund_code = ? AND txn_type = ?"
    params: list = [fund_code, txn_type]
    if start:
        sql += " AND effective_date > ?"
        params.append(start)
    if end:
        sql += " AND effective_date <= ?"
        params.append(end)
    total = _scalar(conn, sql, params)
    if include_opening and not start:
        opening = conn.execute(
            "SELECT detail FROM transactions WHERE fund_code = ? AND txn_type = 'opening_balance'", (fund_code,)
        ).fetchone()
        if opening:
            import json
            d = json.loads(opening["detail"])
            total += d.get("called" if txn_type == "contribution" else "distributed", 0.0)
    return round(total, 2)


def recallable(conn: sqlite3.Connection, fund_code: str) -> float:
    return round(_scalar(conn, "SELECT SUM(recallable) FROM transactions WHERE fund_code = ?", (fund_code,)), 2)


def unfunded(conn: sqlite3.Connection, fund: dict) -> float:
    called = activity(conn, fund["code"], "contribution")
    return round(fund["commitment"] - called + recallable(conn, fund["code"]), 2)


def last_statement(conn: sqlite3.Connection, fund_code: str, before: str | None = None) -> dict | None:
    sql = "SELECT * FROM statements WHERE fund_code = ?"
    params: list = [fund_code]
    if before:
        sql += " AND period_end < ?"
        params.append(before)
    row = conn.execute(sql + " ORDER BY period_end DESC LIMIT 1", params).fetchone()
    return dict(row) if row else None


def suspense_balance(conn: sqlite3.Connection, fund_code: str) -> float:
    return round(_scalar(
        conn,
        "SELECT SUM(l.debit - l.credit) FROM je_lines l JOIN journal_entries j ON j.id = l.je_id "
        "WHERE j.fund_code = ? AND l.account_key = 'recon_suspense'", (fund_code,)), 2)


def fund_summary(conn: sqlite3.Connection, fund: dict) -> dict:
    code = fund["code"]
    called = activity(conn, code, "contribution")
    distributed = activity(conn, code, "distribution")
    nav = book_nav(conn, code)
    stmt = last_statement(conn, code)
    return {
        **fund,
        "called": called,
        "distributed": distributed,
        "recallable": recallable(conn, code),
        "unfunded": unfunded(conn, fund),
        "book_nav": nav,
        "last_statement_period": stmt["period_end"] if stmt else None,
        "gp_nav": stmt["ending_balance"] if stmt else None,
        "suspense": suspense_balance(conn, code),
        "dpi": round(distributed / called, 2) if called else None,
        "tvpi": round((distributed + nav) / called, 2) if called else None,
        "pct_called": round(called / fund["commitment"], 3) if fund["commitment"] else None,
    }


def account_label(key: str) -> str:
    acct = load_accounts()["accounts"][key]
    return f"{acct['number']} {acct['name']}"
