"""Checks run on every extracted document before a reviewer can approve it.

Each check returns pass / warn / fail. A `fail` blocks approval unless the reviewer overrides
with a note; `warn` items are shown for judgement.
"""
from __future__ import annotations

import sqlite3

from . import ledger
from .models import REQUIRED_FIELDS, get_field

TOL = 1.00  # rounding tolerance in currency units


def _check(code: str, label: str, status: str, detail: str = "") -> dict:
    return {"code": code, "label": label, "status": status, "detail": detail}


def _n(v) -> float:
    return float(v or 0.0)


def _fmt(v: float) -> str:
    return f"{v:,.2f}"


def run_checks(conn: sqlite3.Connection, doc_type: str, ex: dict, fund: dict | None,
               document_id: int | None = None, sha256: str | None = None) -> list[dict]:
    checks: list[dict] = []

    if sha256:
        dup = conn.execute("SELECT id, file_name FROM documents WHERE sha256 = ? AND id < ? AND status != 'rejected'",
                           (sha256, document_id or 1 << 62)).fetchone()
        checks.append(_check("duplicate_file", "Not a duplicate file",
                             "fail" if dup else "pass",
                             f"Identical file already loaded as #{dup['id']} ({dup['file_name']})" if dup else ""))

    checks.append(_check("doc_type", "Document type identified",
                         "pass" if doc_type and doc_type != "other" else "fail",
                         "" if doc_type != "other" else "Pick the document type manually"))
    checks.append(_check("fund_match", "Matched to a fund in the master list",
                         "pass" if fund else "fail",
                         f"{fund['code']} - {fund['name']}" if fund else
                         f"No fund matches '{ex.get('fund_name') or '?'}'. Select the fund or add it to the master list."))

    missing = [f for f in REQUIRED_FIELDS.get(doc_type, []) if get_field(ex, f) in (None, "")]
    checks.append(_check("required_fields", "Required fields present",
                         "fail" if missing else "pass", ", ".join(missing)))

    if not fund:
        return checks
    code = fund["code"]

    if doc_type == "capital_call":
        cc = ex.get("capital_call") or {}
        parts = [cc.get(k) for k in ("investment", "management_fee", "fund_expenses", "other")]
        if any(p is not None for p in parts) and cc.get("total") is not None:
            s = sum(_n(p) for p in parts)
            diff = round(s - _n(cc["total"]), 2)
            checks.append(_check("call_components", "Call components add up to total",
                                 "pass" if abs(diff) <= TOL else "fail",
                                 f"Components {_fmt(s)} vs total {_fmt(_n(cc['total']))}"))
        unf = ledger.unfunded(conn, fund)
        total = _n(cc.get("total"))
        checks.append(_check("unfunded", "Call within unfunded commitment",
                             "pass" if total <= unf + TOL else "fail",
                             f"Call {_fmt(total)} vs unfunded {_fmt(unf)} (commitment {_fmt(fund['commitment'])})"))
        _same_txn_check(conn, checks, code, "contribution", ex.get("effective_date"), total)

    elif doc_type == "distribution":
        d = ex.get("distribution") or {}
        gross_parts = [d.get(k) for k in ("return_of_capital", "realized_gain", "income")]
        if any(p is not None for p in gross_parts) and d.get("net_amount") is not None:
            gross = sum(_n(p) for p in gross_parts)
            expected_net = round(gross - _n(d.get("withholding")), 2)
            diff = round(expected_net - _n(d["net_amount"]), 2)
            checks.append(_check("dist_components", "Distribution components less withholding = net",
                                 "pass" if abs(diff) <= TOL else "fail",
                                 f"Gross {_fmt(gross)} - withholding {_fmt(_n(d.get('withholding')))} = {_fmt(expected_net)} vs net {_fmt(_n(d['net_amount']))}"))
        if _n(d.get("recallable")) > _n(d.get("return_of_capital")) + TOL:
            checks.append(_check("recallable", "Recallable amount within return of capital", "warn",
                                 f"Recallable {_fmt(_n(d.get('recallable')))} exceeds ROC {_fmt(_n(d.get('return_of_capital')))}"))
        _same_txn_check(conn, checks, code, "distribution", ex.get("effective_date"),
                        _n(d.get("net_amount")) + _n(d.get("withholding")))

    elif doc_type == "capital_account_statement":
        ca = ex.get("capital_account") or {}
        computed = (_n(ca.get("beginning_balance")) + _n(ca.get("contributions")) - _n(ca.get("distributions"))
                    + _n(ca.get("investment_income")) - _n(ca.get("expenses")) + _n(ca.get("realized_gain"))
                    + _n(ca.get("unrealized_gain")) - _n(ca.get("carried_interest")))
        diff = round(computed - _n(ca.get("ending_balance")), 2)
        checks.append(_check("roll_forward", "Capital account rolls forward",
                             "pass" if abs(diff) <= TOL else "fail",
                             f"Beginning + activity = {_fmt(computed)} vs ending {_fmt(_n(ca.get('ending_balance')))} (diff {_fmt(diff)})"))

        period_end = ex.get("period_end")
        prior = ledger.last_statement(conn, code, before=period_end) if period_end else None
        if prior:
            d = round(_n(ca.get("beginning_balance")) - prior["ending_balance"], 2)
            checks.append(_check("continuity", "Beginning balance = prior statement's ending balance",
                                 "pass" if abs(d) <= TOL else "fail",
                                 f"Prior ({prior['period_end']}) ending {_fmt(prior['ending_balance'])} vs beginning {_fmt(_n(ca.get('beginning_balance')))}"))
        if period_end and conn.execute("SELECT 1 FROM statements WHERE fund_code = ? AND period_end = ?",
                                       (code, period_end)).fetchone():
            checks.append(_check("duplicate_period", "No statement already booked for this period", "fail",
                                 f"A {period_end} statement for {code} is already approved"))

        # activity() treats start as exclusive, so step back a day from a stated period start.
        start = _day_before(ex["period_start"]) if ex.get("period_start") else (prior["period_end"] if prior else None)
        if period_end and start:
            for kind, field in (("contribution", "contributions"), ("distribution", "distributions")):
                book = ledger.activity(conn, code, kind, start=start, end=period_end)
                gp = _n(ca.get(field))
                d = round(book - gp, 2)
                checks.append(_check(f"recon_{field}", f"GP {field} agree to our books",
                                     "pass" if abs(d) <= TOL else "warn",
                                     f"Books {_fmt(book)} vs GP {_fmt(gp)} for {start} to {period_end}"
                                     + ("" if abs(d) <= TOL else ". Look for unrecorded or pending notices; the gap will post to suspense.")))
        if ca.get("unfunded_commitment") is not None:
            book_unf = ledger.unfunded(conn, fund)
            d = round(book_unf - _n(ca["unfunded_commitment"]), 2)
            checks.append(_check("recon_unfunded", "GP unfunded commitment agrees to our books",
                                 "pass" if abs(d) <= TOL else "warn",
                                 f"Books {_fmt(book_unf)} vs GP {_fmt(_n(ca['unfunded_commitment']))}"))

    elif doc_type == "k1":
        k = ex.get("k1") or {}
        checks.append(_check("k1_final", "K-1 is final (not an estimate)",
                             "pass" if k.get("is_final") else "warn",
                             "" if k.get("is_final") else "Estimate - expect a final K-1 later"))
        year = ex.get("tax_year")
        if year and k.get("distributions") is not None:
            book = ledger.activity(conn, code, "distribution", start=f"{year - 1}-12-31", end=f"{year}-12-31")
            has_book = conn.execute(
                "SELECT 1 FROM transactions WHERE fund_code = ? AND txn_type = 'distribution' AND effective_date LIKE ?",
                (code, f"{year}-%")).fetchone()
            if has_book:
                d = round(book - _n(k["distributions"]), 2)
                checks.append(_check("k1_distributions", "Box 19 distributions agree to our books",
                                     "pass" if abs(d) <= TOL else "warn",
                                     f"Books {_fmt(book)} vs K-1 {_fmt(_n(k['distributions']))} for {year}"))
            else:
                checks.append(_check("k1_distributions", "Box 19 distributions agree to our books", "warn",
                                     f"No {year} distributions in the books to compare (year predates this system?)"))
    return checks


def _day_before(iso: str) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()


def _same_txn_check(conn, checks, code, txn_type, date, amount):
    if not date:
        return
    hit = conn.execute(
        "SELECT id FROM transactions WHERE fund_code = ? AND txn_type = ? AND effective_date = ? AND ABS(amount - ?) <= ?",
        (code, txn_type, date, amount, TOL)).fetchone()
    checks.append(_check("duplicate_txn", f"No matching {txn_type} already booked",
                         "fail" if hit else "pass",
                         f"Same fund, date and amount already booked (transaction #{hit['id']})" if hit else ""))


def summarize(checks: list[dict]) -> str:
    if any(c["status"] == "fail" for c in checks):
        return "fail"
    if any(c["status"] == "warn" for c in checks):
        return "warn"
    return "pass"
