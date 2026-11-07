"""Web API + static UI. Run with `python -m altinv.cli serve` (binds to localhost only)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, ledger, pipeline
from .models import DOC_TYPE_LABELS
from .posting import entries_with_lines, export_csv, load_accounts

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="LP Activity Tracker")
_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = db.connect()
    return _conn


def _fail(e: Exception):
    raise HTTPException(status_code=400, detail=str(e))


# ----------------------------------------------------------------------------- reference data

@app.get("/api/meta")
def meta():
    return {"doc_types": DOC_TYPE_LABELS, "extractor": pipeline.extractor_mode(),
            "accounts": load_accounts()["accounts"], "today": date.today().isoformat()}


@app.get("/api/funds")
def funds(conn=Depends(get_conn)):
    return [ledger.fund_summary(conn, f) for f in db.rows(conn, "SELECT * FROM funds ORDER BY name")]


class FundIn(BaseModel):
    code: str
    name: str
    gp_name: str | None = None
    aliases: list[str] = []
    strategy: str | None = None
    vintage: int | None = None
    commitment: float
    portal: str | None = None


@app.post("/api/funds")
def add_fund(f: FundIn, conn=Depends(get_conn)):
    try:
        conn.execute("INSERT INTO funds (code, name, gp_name, aliases, strategy, vintage, commitment, portal) "
                     "VALUES (?,?,?,?,?,?,?,?)", (f.code.upper(), f.name, f.gp_name, json.dumps(f.aliases),
                                                  f.strategy, f.vintage, f.commitment, f.portal))
        db.audit(conn, None, "fund_added", None, f.model_dump())
        conn.commit()
    except sqlite3.IntegrityError as e:
        _fail(e)
    return {"ok": True}


@app.get("/api/funds/{code}")
def fund_detail(code: str, conn=Depends(get_conn)):
    f = db.row_to_dict(conn.execute("SELECT * FROM funds WHERE code = ?", (code,)).fetchone())
    if not f:
        raise HTTPException(404, "Fund not found")
    return {
        "summary": ledger.fund_summary(conn, f),
        "transactions": db.rows(conn, "SELECT t.*, d.file_name FROM transactions t LEFT JOIN documents d ON d.id = t.document_id "
                                      "WHERE t.fund_code = ? ORDER BY effective_date", (code,)),
        "statements": db.rows(conn, "SELECT s.*, d.file_name FROM statements s LEFT JOIN documents d ON d.id = s.document_id "
                                    "WHERE s.fund_code = ? ORDER BY period_end", (code,)),
        "k1s": db.rows(conn, "SELECT * FROM k1s WHERE fund_code = ? ORDER BY tax_year", (code,)),
        "documents": db.rows(conn, "SELECT id, file_name, doc_type, status, uploaded_at FROM documents WHERE fund_code = ? "
                                   "ORDER BY id DESC", (code,)),
    }


# ----------------------------------------------------------------------------- documents

@app.get("/api/documents")
def documents(status: str | None = None, conn=Depends(get_conn)):
    sql = "SELECT id, file_name, doc_type, fund_code, status, extractor, confidence, validation, uploaded_at, reviewed_by FROM documents"
    params: tuple = ()
    if status == "queue":
        sql += " WHERE status IN ('needs_review', 'duplicate')"
    elif status:
        sql += " WHERE status = ?"
        params = (status,)
    out = []
    for d in db.rows(conn, sql + " ORDER BY id DESC", params):
        checks = d.pop("validation") or []
        d["fails"] = sum(c["status"] == "fail" for c in checks)
        d["warns"] = sum(c["status"] == "warn" for c in checks)
        out.append(d)
    return out


@app.post("/api/documents")
async def upload(files: list[UploadFile] = File(...), actor: str = Form(""), conn=Depends(get_conn)):
    results = []
    for f in files:
        data = await f.read()
        if not data.startswith(b"%PDF"):
            results.append({"file_name": f.filename, "error": "Not a PDF"})
            continue
        doc = pipeline.ingest(conn, data, f.filename or "upload.pdf", actor=actor or None)
        results.append({"id": doc["id"], "file_name": doc["file_name"], "status": doc["status"]})
    return results


@app.get("/api/documents/{doc_id}")
def document(doc_id: int, conn=Depends(get_conn)):
    doc = pipeline.get_document(conn, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    doc.pop("stored_path", None)
    doc["history"] = db.rows(conn, "SELECT * FROM audit_log WHERE document_id = ? ORDER BY id", (doc_id,))
    return doc


@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: int, conn=Depends(get_conn)):
    row = conn.execute("SELECT stored_path, file_name FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Document not found")
    return FileResponse(row["stored_path"], media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{row["file_name"]}"'})


class DocUpdate(BaseModel):
    actor: str | None = None
    extraction: dict | None = None
    fund_code: str | None = None
    doc_type: str | None = None


@app.patch("/api/documents/{doc_id}")
def edit_document(doc_id: int, body: DocUpdate, conn=Depends(get_conn)):
    try:
        return pipeline.update_document(conn, doc_id, body.actor, body.extraction, body.fund_code, body.doc_type)
    except ValueError as e:
        _fail(e)


class Decision(BaseModel):
    actor: str
    note: str | None = None
    override: bool = False


@app.post("/api/documents/{doc_id}/approve")
def approve(doc_id: int, body: Decision, conn=Depends(get_conn)):
    try:
        return pipeline.approve(conn, doc_id, body.actor.strip(), body.note, body.override)
    except ValueError as e:
        conn.rollback()
        _fail(e)


@app.post("/api/documents/{doc_id}/reject")
def reject(doc_id: int, body: Decision, conn=Depends(get_conn)):
    try:
        return pipeline.reject(conn, doc_id, body.actor.strip(), body.note)
    except ValueError as e:
        _fail(e)


# ----------------------------------------------------------------------------- tracking

@app.get("/api/outstanding")
def outstanding(conn=Depends(get_conn)):
    """What we expect but haven't booked: quarterly statements, final K-1s, and calls coming due."""
    today = date.today()
    q_end_month = ((today.month - 1) // 3) * 3
    last_q_end = date(today.year, q_end_month, 30 if q_end_month in (6, 9) else 31) if q_end_month else date(today.year - 1, 12, 31)
    tax_year = today.year - 1
    items = []
    for f in db.rows(conn, "SELECT * FROM funds ORDER BY name"):
        code = f["code"]
        has_stmt = conn.execute("SELECT 1 FROM statements WHERE fund_code = ? AND period_end >= ?",
                                (code, last_q_end.isoformat())).fetchone()
        if not has_stmt:
            pending = conn.execute("SELECT id FROM documents WHERE fund_code = ? AND doc_type = 'capital_account_statement' "
                                   "AND status IN ('needs_review','duplicate')", (code,)).fetchone()
            items.append({"fund_code": code, "fund": f["name"], "kind": "statement",
                          "item": f"Capital account statement for {last_q_end.isoformat()}",
                          "state": "in review queue" if pending else "not received", "document_id": pending["id"] if pending else None,
                          "portal": f["portal"]})
        k1 = conn.execute("SELECT is_final FROM k1s WHERE fund_code = ? AND tax_year = ? ORDER BY is_final DESC",
                          (code, tax_year)).fetchone()
        if not k1 or not k1["is_final"]:
            pending = conn.execute("SELECT id FROM documents WHERE fund_code = ? AND doc_type = 'k1' "
                                   "AND status IN ('needs_review','duplicate')", (code,)).fetchone()
            items.append({"fund_code": code, "fund": f["name"], "kind": "k1",
                          "item": f"{tax_year} Schedule K-1 (final)",
                          "state": "estimate only" if k1 else ("in review queue" if pending else "not received"),
                          "document_id": pending["id"] if pending else None, "portal": f["portal"]})
    for d in db.rows(conn, "SELECT id, fund_code, extraction FROM documents WHERE doc_type = 'capital_call' "
                           "AND status IN ('needs_review')"):
        due = (d["extraction"] or {}).get("effective_date")
        amt = ((d["extraction"] or {}).get("capital_call") or {}).get("total")
        if due and due >= today.isoformat():
            items.append({"fund_code": d["fund_code"], "fund": d["fund_code"] or "Unmatched", "kind": "call_due",
                          "item": f"Capital call {amt:,.2f} due {due}" if amt else f"Capital call due {due}",
                          "state": "awaiting approval", "document_id": d["id"], "portal": None})
    return {"as_of_quarter": last_q_end.isoformat(), "tax_year": tax_year, "items": items}


@app.get("/api/k1s")
def k1_tracker(conn=Depends(get_conn)):
    return db.rows(conn, "SELECT k.*, f.name AS fund FROM k1s k JOIN funds f ON f.code = k.fund_code ORDER BY tax_year DESC, fund")


@app.get("/api/journal")
def journal(unexported: bool = False, conn=Depends(get_conn)):
    where = "j.export_batch IS NULL" if unexported else "1=1"
    return entries_with_lines(conn, where)[::-1]


@app.post("/api/journal/export", response_class=PlainTextResponse)
def export(actor: str = "", include_exported: bool = False, conn=Depends(get_conn)):
    batch = "EXP-" + db.now_iso().replace(":", "").replace("-", "")[:15]
    content, n = export_csv(conn, batch, include_exported)
    db.audit(conn, actor or None, "exported", None, {"batch": batch, "entries": n, "re_export": include_exported})
    conn.commit()
    return PlainTextResponse(content, media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{batch}.csv"', "X-Entry-Count": str(n)})


@app.get("/api/audit")
def audit_log(conn=Depends(get_conn)):
    return db.rows(conn, "SELECT a.*, d.file_name FROM audit_log a LEFT JOIN documents d ON d.id = a.document_id "
                         "ORDER BY a.id DESC LIMIT 300")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
