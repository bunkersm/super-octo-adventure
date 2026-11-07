"""Intake -> extract -> match fund -> validate -> review -> approve (book) workflow."""
from __future__ import annotations

import difflib
import hashlib
import io
import json
import os
import re
import sqlite3

import pdfplumber

from . import extract_llm, extract_rules, ledger, validate
from .db import DATA_DIR, audit, now_iso, row_to_dict
from .models import Extraction
from .posting import build_entries, save_entries

FILES_DIR = DATA_DIR / "files"


def pdf_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _norm(s: str) -> str:
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\b(l\.?\s?p\.?|llc|ltd|inc|the)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def match_fund(conn: sqlite3.Connection, name: str | None, text: str = "") -> dict | None:
    funds = [row_to_dict(r) for r in conn.execute("SELECT * FROM funds")]
    best, best_score = None, 0.0
    if name:
        n = _norm(name)
        for f in funds:
            for candidate in [f["name"], *f["aliases"]]:
                score = difflib.SequenceMatcher(None, n, _norm(candidate)).ratio()
                if score > best_score:
                    best, best_score = f, score
        if best_score >= 0.86:
            return best
    # Fall back to an exact name/alias anywhere in the text (e.g. the name is in a header line).
    low = _norm(text)
    hits = [f for f in funds if any(_norm(c) and f" {_norm(c)} " in f" {low} " for c in [f["name"], *f["aliases"]])]
    return hits[0] if len(hits) == 1 else None


def extractor_mode() -> str:
    mode = os.environ.get("ALTINV_EXTRACTOR", "auto")
    if mode == "auto":
        return "claude" if extract_llm.available() else "rules"
    return mode


def extract(conn: sqlite3.Connection, pdf_bytes: bytes) -> tuple[Extraction, str, str]:
    text = pdf_text(pdf_bytes)
    if extractor_mode() == "claude":
        names = [r["name"] for r in conn.execute("SELECT name FROM funds ORDER BY name")]
        return extract_llm.extract_pdf(pdf_bytes, names), "claude", text
    return extract_rules.extract(text), "rules", text


def _evaluate(conn, doc_id, doc_type, ex: dict, fund, sha):
    checks = validate.run_checks(conn, doc_type, ex, fund, document_id=doc_id, sha256=sha)
    return checks


def ingest(conn: sqlite3.Connection, pdf_bytes: bytes, file_name: str, actor: str | None = None) -> dict:
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    stored = FILES_DIR / f"{sha}.pdf"
    if not stored.exists():
        stored.write_bytes(pdf_bytes)

    dup = conn.execute("SELECT id FROM documents WHERE sha256 = ? AND status != 'rejected'", (sha,)).fetchone()
    try:
        ex, extractor, text = extract(conn, pdf_bytes)
        exd = ex.model_dump()
        fund = match_fund(conn, ex.fund_name, text)
        if fund and not exd.get("fund_name"):
            exd["fund_name"] = fund["name"]  # found by scanning the text rather than a labelled field
        status, error = "needs_review", None
    except Exception as e:  # keep the document; a person can fill the fields in by hand
        exd, extractor, fund = {"doc_type": "other", "notes": f"Extraction failed: {e}"}, "error", None
        status, error = "needs_review", str(e)

    cur = conn.execute(
        "INSERT INTO documents (sha256, file_name, stored_path, uploaded_at, uploaded_by, doc_type, fund_code, "
        "extraction, extractor, confidence, status, duplicate_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (sha, file_name, str(stored), now_iso(), actor, exd.get("doc_type"), fund["code"] if fund else None,
         json.dumps(exd), extractor, exd.get("confidence"), "duplicate" if dup else status, dup["id"] if dup else None),
    )
    doc_id = cur.lastrowid
    checks = _evaluate(conn, doc_id, exd.get("doc_type"), exd, fund, sha)
    conn.execute("UPDATE documents SET validation = ? WHERE id = ?", (json.dumps(checks), doc_id))
    audit(conn, actor, "uploaded", doc_id, {"file": file_name, "extractor": extractor, "error": error})
    conn.commit()
    return get_document(conn, doc_id)


def get_document(conn: sqlite3.Connection, doc_id: int) -> dict | None:
    doc = row_to_dict(conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone())
    if not doc:
        return None
    fund = row_to_dict(conn.execute("SELECT * FROM funds WHERE code = ?", (doc["fund_code"],)).fetchone()) if doc["fund_code"] else None
    doc["fund"] = fund
    doc["overall"] = validate.summarize(doc.get("validation") or [])
    if doc["status"] in ("needs_review", "duplicate") and fund:
        doc["proposed_entries"] = proposed_entries(conn, doc["doc_type"], doc["extraction"], fund)
    else:
        doc["proposed_entries"] = []
    if doc["status"] == "approved":
        from .posting import entries_with_lines
        doc["posted_entries"] = entries_with_lines(conn, "j.document_id = ?", (doc_id,))
    return doc


def proposed_entries(conn, doc_type: str, ex: dict, fund: dict) -> list[dict]:
    before = ledger.book_nav(conn, fund["code"], as_of=ex.get("period_end")) if doc_type == "capital_account_statement" else None
    try:
        entries = build_entries(doc_type, ex, fund, before)
    except Exception:
        return []
    # A call/distribution dated inside a period whose GP statement is already booked was included in
    # that statement's NAV true-up, where the gap went to suspense. Reverse that piece now.
    date_ = ex.get("effective_date")
    if doc_type in ("capital_call", "distribution") and date_ and entries:
        covered = conn.execute("SELECT period_end FROM statements WHERE fund_code = ? AND document_id IS NOT NULL "
                               "AND period_end >= ? ORDER BY period_end LIMIT 1", (fund["code"], date_)).fetchone()
        if covered:
            inv = next(l for l in entries[0]["lines"] if l["account_key"] == "investment")
            amt = inv["debit"] or inv["credit"]
            is_call = doc_type == "capital_call"
            entries.append({
                "fund_code": fund["code"], "entry_type": "suspense_clear", "entry_date": date_,
                "memo": f"{fund['code']} clear suspense: {doc_type.replace('_', ' ')} already in GP NAV at {covered['period_end']}",
                "lines": [
                    {"account_key": "recon_suspense", "debit": amt if is_call else 0.0, "credit": 0.0 if is_call else amt, "memo": None},
                    {"account_key": "investment", "debit": 0.0 if is_call else amt, "credit": amt if is_call else 0.0, "memo": None},
                ],
                "balanced": True,
            })
    return entries


def update_document(conn: sqlite3.Connection, doc_id: int, actor: str | None, extraction: dict | None = None,
                    fund_code: str | None = None, doc_type: str | None = None) -> dict:
    doc = get_document(conn, doc_id)
    if doc["status"] not in ("needs_review", "duplicate"):
        raise ValueError("Only documents awaiting review can be edited")
    ex = doc["extraction"] or {}
    if extraction is not None:
        ex = Extraction.model_validate({**ex, **extraction}).model_dump()
    if doc_type:
        ex["doc_type"] = doc_type
    code = fund_code if fund_code is not None else doc["fund_code"]
    fund = row_to_dict(conn.execute("SELECT * FROM funds WHERE code = ?", (code,)).fetchone()) if code else None
    checks = _evaluate(conn, doc_id, ex["doc_type"], ex, fund, doc["sha256"])
    conn.execute("UPDATE documents SET extraction = ?, doc_type = ?, fund_code = ?, validation = ? WHERE id = ?",
                 (json.dumps(ex), ex["doc_type"], fund["code"] if fund else None, json.dumps(checks), doc_id))
    audit(conn, actor, "edited", doc_id, {"fields": sorted((extraction or {}).keys()), "fund": code, "doc_type": doc_type})
    conn.commit()
    return get_document(conn, doc_id)


def approve(conn: sqlite3.Connection, doc_id: int, actor: str, note: str | None = None, override: bool = False) -> dict:
    if not actor:
        raise ValueError("Reviewer name is required to approve")
    doc = get_document(conn, doc_id)
    if doc["status"] not in ("needs_review", "duplicate"):
        raise ValueError(f"Document is {doc['status']}, not awaiting review")
    fund = doc["fund"]
    ex, doc_type = doc["extraction"], doc["doc_type"]
    # Re-run checks against the current books, in case other documents were approved meanwhile.
    checks = _evaluate(conn, doc_id, doc_type, ex, fund, doc["sha256"])
    fails = [c for c in checks if c["status"] == "fail"]
    hard = [c for c in fails if c["code"] in ("fund_match", "doc_type", "required_fields")]
    if hard:
        raise ValueError("Cannot approve: " + "; ".join(c["label"] for c in hard))
    if fails and not (override and note):
        raise ValueError("Checks failed: " + "; ".join(c["label"] for c in fails)
                         + ". Fix the fields, or override with a note explaining why.")

    code = fund["code"]
    if doc_type == "capital_call":
        cc = ex["capital_call"]
        conn.execute("INSERT INTO transactions (document_id, fund_code, txn_type, effective_date, amount, detail) "
                     "VALUES (?,?,?,?,?,?)", (doc_id, code, "contribution", ex["effective_date"], cc["total"], json.dumps(cc)))
    elif doc_type == "distribution":
        d = ex["distribution"]
        gross = float(d.get("net_amount") or 0) + float(d.get("withholding") or 0)
        conn.execute("INSERT INTO transactions (document_id, fund_code, txn_type, effective_date, amount, recallable, detail) "
                     "VALUES (?,?,?,?,?,?,?)", (doc_id, code, "distribution", ex["effective_date"], gross,
                                                float(d.get("recallable") or 0), json.dumps(d)))
    elif doc_type == "capital_account_statement":
        ca = ex["capital_account"]
        conn.execute(
            "INSERT INTO statements (document_id, fund_code, period_start, period_end, beginning_balance, contributions, "
            "distributions, investment_income, expenses, realized_gain, unrealized_gain, carried_interest, ending_balance) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, code, ex.get("period_start"), ex["period_end"], ca.get("beginning_balance"), ca.get("contributions"),
             ca.get("distributions"), ca.get("investment_income"), ca.get("expenses"), ca.get("realized_gain"),
             ca.get("unrealized_gain"), ca.get("carried_interest"), ca["ending_balance"]))
    elif doc_type == "k1":
        k = ex.get("k1") or {}
        conn.execute("INSERT INTO k1s (document_id, fund_code, tax_year, is_final, boxes) VALUES (?,?,?,?,?)",
                     (doc_id, code, ex["tax_year"], 1 if k.get("is_final") else 0, json.dumps(k)))

    entries = proposed_entries(conn, doc_type, ex, fund)
    save_entries(conn, doc_id, entries)
    conn.execute("UPDATE documents SET status = 'approved', reviewed_by = ?, reviewed_at = ?, review_note = ?, validation = ? "
                 "WHERE id = ?", (actor, now_iso(), note, json.dumps(checks), doc_id))
    audit(conn, actor, "approved", doc_id, {"override": bool(fails), "note": note, "entries": len(entries)})
    conn.commit()
    return get_document(conn, doc_id)


def reject(conn: sqlite3.Connection, doc_id: int, actor: str, note: str | None = None) -> dict:
    doc = get_document(conn, doc_id)
    if doc["status"] not in ("needs_review", "duplicate"):
        raise ValueError(f"Document is {doc['status']}, not awaiting review")
    conn.execute("UPDATE documents SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, review_note = ? WHERE id = ?",
                 (actor, now_iso(), note, doc_id))
    audit(conn, actor, "rejected", doc_id, {"note": note})
    conn.commit()
    return get_document(conn, doc_id)


def record_opening_balance(conn, fund_code: str, as_of: str, nav: float, called: float, distributed: float, actor="setup"):
    """Conversion entry when a fund is first loaded: history before this date lives in the old system."""
    conn.execute("INSERT INTO transactions (document_id, fund_code, txn_type, effective_date, amount, detail) VALUES (?,?,?,?,?,?)",
                 (None, fund_code, "opening_balance", as_of, nav, json.dumps({"called": called, "distributed": distributed})))
    conn.execute("INSERT INTO statements (document_id, fund_code, period_end, ending_balance) VALUES (?,?,?,?)",
                 (None, fund_code, as_of, nav))
    save_entries(conn, None, [{
        "fund_code": fund_code, "entry_date": as_of, "entry_type": "opening_balance",
        "memo": f"{fund_code} opening balance (called {called:,.2f}, distributed {distributed:,.2f})",
        "lines": [{"account_key": "investment", "debit": nav, "credit": 0.0, "memo": None},
                  {"account_key": "opening_equity", "debit": 0.0, "credit": nav, "memo": None}],
    }])
    audit(conn, actor, "opening_balance", None, {"fund": fund_code, "as_of": as_of, "nav": nav})
