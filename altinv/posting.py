"""Turns approved document data into balanced journal entries, and exports them.

Accounting approach (NAV / fair-value basis, the common one for LP interests):
- Capital call:   Dr Investment            Cr Capital Calls Payable   (on the due date)
- Distribution:   Dr Distributions Receivable (net) + Dr Tax Withheld   Cr Investment (gross)
- Capital account statement: true up the investment account to the GP's ending NAV, splitting the
  change into income / expenses / realized / unrealized / carry using the statement's own figures.
  If our book activity for the period doesn't match the GP's (e.g. a call we haven't recorded yet),
  the unexplained remainder goes to a suspense account so the book NAV still equals the GP NAV and
  the difference is visible until someone clears it.
Account numbers live in config/accounts.json.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from functools import lru_cache
from pathlib import Path

from .db import now_iso

CONFIG = Path(__file__).resolve().parent.parent / "config" / "accounts.json"


@lru_cache(maxsize=1)
def load_accounts() -> dict:
    return json.loads(CONFIG.read_text())


def _line(account_key: str, debit: float = 0.0, credit: float = 0.0, memo: str | None = None) -> dict:
    # A negative debit is a credit and vice versa, so callers can pass signed values.
    if debit < 0:
        debit, credit = 0.0, credit - debit
    if credit < 0:
        debit, credit = debit - credit, 0.0
    return {"account_key": account_key, "debit": round(debit, 2), "credit": round(credit, 2), "memo": memo}


def _n(v) -> float:
    return float(v or 0.0)


def build_entries(doc_type: str, ex: dict, fund: dict, book_nav_before: float | None = None) -> list[dict]:
    """Proposed journal entries for one document. Pure function: nothing is saved."""
    code = fund["code"]
    entries: list[dict] = []
    if doc_type == "capital_call":
        cc = ex.get("capital_call") or {}
        total = _n(cc.get("total"))
        parts = ", ".join(f"{k.replace('_', ' ')} {_n(cc.get(k)):,.2f}" for k in
                          ("investment", "management_fee", "fund_expenses", "other") if cc.get(k))
        entries.append({
            "entry_type": "capital_call",
            "entry_date": ex.get("effective_date") or ex.get("notice_date"),
            "memo": f"{code} capital call due {ex.get('effective_date')}" + (f" ({parts})" if parts else ""),
            "lines": [_line("investment", debit=total), _line("capital_call_payable", credit=total)],
        })
    elif doc_type == "distribution":
        d = ex.get("distribution") or {}
        net = _n(d.get("net_amount"))
        wh = _n(d.get("withholding"))
        gross = net + wh
        lines = [_line("distribution_receivable", debit=net)]
        if wh:
            lines.append(_line("tax_withheld", debit=wh))
        lines.append(_line("investment", credit=gross,
                           memo=f"ROC {_n(d.get('return_of_capital')):,.2f} / gain {_n(d.get('realized_gain')):,.2f} / income {_n(d.get('income')):,.2f}"))
        entries.append({
            "entry_type": "distribution",
            "entry_date": ex.get("effective_date") or ex.get("notice_date"),
            "memo": f"{code} distribution paid {ex.get('effective_date')}",
            "lines": lines,
        })
    elif doc_type == "capital_account_statement":
        ca = ex.get("capital_account") or {}
        ending = _n(ca.get("ending_balance"))
        before = _n(book_nav_before)
        needed = round(ending - before, 2)  # total change to investment account to land on GP NAV
        components = {
            "investment_income": _n(ca.get("investment_income")),
            "expenses": -_n(ca.get("expenses")),
            "realized_gain": _n(ca.get("realized_gain")),
            "unrealized_gain": _n(ca.get("unrealized_gain")),
            "carried_interest": -_n(ca.get("carried_interest")),
        }
        pnl = round(sum(components.values()), 2)
        account_for = {
            "investment_income": "investment_income", "expenses": "partnership_expenses",
            "realized_gain": "realized_gain", "unrealized_gain": "unrealized_gain",
            "carried_interest": "carried_interest",
        }
        lines = []
        for key, amount in components.items():
            if amount:
                # Income/gain: credit the P&L account. Expense/carry: debit it (amount is negative).
                lines.append(_line(account_for[key], credit=amount))
        residual = round(needed - pnl, 2)
        if residual:
            lines.append(_line("recon_suspense", credit=residual,
                               memo="Book vs GP difference - investigate and clear"))
        if needed:
            lines.append(_line("investment", debit=needed))
        if lines:
            entries.append({
                "entry_type": "nav_true_up",
                "entry_date": ex.get("period_end"),
                "memo": f"{code} record share of earnings to GP NAV {ending:,.2f} for period ended {ex.get('period_end')}",
                "lines": lines,
                "residual": residual,
            })
    # K-1s and fund financial statements are tracked, not posted.
    for e in entries:
        e["fund_code"] = code
        e["balanced"] = round(sum(l["debit"] for l in e["lines"]) - sum(l["credit"] for l in e["lines"]), 2) == 0
    return entries


def save_entries(conn: sqlite3.Connection, document_id: int | None, entries: list[dict]) -> list[int]:
    ids = []
    for e in entries:
        cur = conn.execute(
            "INSERT INTO journal_entries (document_id, fund_code, entry_date, entry_type, memo, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (document_id, e["fund_code"], e["entry_date"], e["entry_type"], e["memo"], now_iso()),
        )
        for l in e["lines"]:
            conn.execute("INSERT INTO je_lines (je_id, account_key, debit, credit, memo) VALUES (?,?,?,?,?)",
                         (cur.lastrowid, l["account_key"], l["debit"], l["credit"], l.get("memo")))
        ids.append(cur.lastrowid)
    return ids


def entries_with_lines(conn: sqlite3.Connection, where: str = "1=1", params=()) -> list[dict]:
    accounts = load_accounts()["accounts"]
    jes = [dict(r) for r in conn.execute(
        f"SELECT j.*, d.file_name FROM journal_entries j LEFT JOIN documents d ON d.id = j.document_id "
        f"WHERE {where} ORDER BY j.entry_date, j.id", params)]
    for je in jes:
        je["lines"] = []
        for l in conn.execute("SELECT * FROM je_lines WHERE je_id = ? ORDER BY id", (je["id"],)):
            l = dict(l)
            acct = accounts[l["account_key"]]
            l["account_number"], l["account_name"] = acct["number"], acct["name"]
            je["lines"].append(l)
    return jes


EXPORT_COLUMNS = ["je_id", "entry_date", "entity", "account_number", "account_name", "fund_code",
                  "debit", "credit", "description", "entry_type", "source_document"]


def export_csv(conn: sqlite3.Connection, batch_id: str, include_exported: bool = False) -> tuple[str, int]:
    """CSV in a generic GL-import layout. Map the columns to your accounting system's import template."""
    where = "1=1" if include_exported else "j.export_batch IS NULL"
    jes = entries_with_lines(conn, where)
    entity = load_accounts().get("entity", "")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLUMNS)
    for je in jes:
        for l in je["lines"]:
            w.writerow([je["id"], je["entry_date"], entity, l["account_number"], l["account_name"], je["fund_code"],
                        f"{l['debit']:.2f}", f"{l['credit']:.2f}", l["memo"] or je["memo"], je["entry_type"],
                        je.get("file_name") or ""])
    if not include_exported and jes:
        conn.executemany("UPDATE journal_entries SET export_batch = ? WHERE id = ?", [(batch_id, je["id"]) for je in jes])
    return buf.getvalue(), len(jes)
