import os
import tempfile

os.environ["ALTINV_DATA_DIR"] = tempfile.mkdtemp(prefix="altinv-test-")
os.environ["ALTINV_EXTRACTOR"] = "rules"

import csv
import io
from types import SimpleNamespace

import pytest

from altinv import db, extract_llm, ledger, pipeline
from altinv.posting import export_csv
from altinv.samples import seed_demo


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    seed_demo(c, tmp_path / "samples")
    yield c
    c.close()


def queue(conn, file_part):
    return conn.execute("SELECT id FROM documents WHERE file_name LIKE ? AND status IN ('needs_review','duplicate') "
                        "ORDER BY id", (f"%{file_part}%",)).fetchall()


def all_funds(conn):
    return db.rows(conn, "SELECT * FROM funds")


def test_history_loads_cleanly_and_books_tie_to_gp(conn):
    # Every historical document passed every check (approve() raises otherwise) ...
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'approved'").fetchone()[0] == 33
    for f in all_funds(conn):
        s = ledger.fund_summary(conn, f)
        # ... and after the Q2 true-up our book NAV equals the GP's ending capital, with nothing in suspense.
        assert s["book_nav"] == pytest.approx(s["gp_nav"], abs=0.01), f["code"]
        assert s["suspense"] == 0


def test_every_journal_entry_balances(conn):
    for je_id, diff in conn.execute("SELECT je_id, ROUND(SUM(debit - credit), 2) FROM je_lines GROUP BY je_id"):
        assert diff == 0, je_id


def test_statement_before_its_calls_goes_to_suspense_then_clears(conn):
    stmt = queue(conn, "HRZ4_capital_account_2026-09-30")[0]["id"]
    doc = pipeline.get_document(conn, stmt)
    assert {c["code"] for c in doc["validation"] if c["status"] == "warn"} >= {"recon_contributions", "recon_distributions"}
    pipeline.approve(conn, stmt, "tester")
    hrz = next(f for f in all_funds(conn) if f["code"] == "HRZ4")
    assert ledger.suspense_balance(conn, "HRZ4") != 0
    # Now book the quarter's notices that the GP had already reflected.
    for part in ("HRZ4_capital_call_2026-07", "HRZ4_capital_call_2026-08", "HRZ4_distribution_2026-08"):
        for row in queue(conn, part):
            if pipeline.get_document(conn, row["id"])["status"] == "needs_review":
                pipeline.approve(conn, row["id"], "tester")
    s = ledger.fund_summary(conn, hrz)
    assert s["suspense"] == 0
    assert s["book_nav"] == pytest.approx(s["gp_nav"], abs=0.01)
    # GP-reported unfunded on that statement now agrees too.
    gp_unfunded = pipeline.get_document(conn, stmt)["extraction"]["capital_account"]["unfunded_commitment"]
    assert s["unfunded"] == pytest.approx(gp_unfunded, abs=0.01)


def test_duplicate_download_is_blocked(conn):
    dup = conn.execute("SELECT id FROM documents WHERE status = 'duplicate'").fetchone()["id"]
    original = conn.execute("SELECT duplicate_of FROM documents WHERE id = ?", (dup,)).fetchone()[0]
    pipeline.approve(conn, original, "tester")
    with pytest.raises(ValueError, match="duplicate"):
        pipeline.approve(conn, dup, "tester")
    pipeline.reject(conn, dup, "tester", "Re-download of the same notice")


def test_roll_forward_failure_blocks_until_fixed(conn):
    sid = queue(conn, "SRE3_capital_account_2026-09-30")[0]["id"]
    doc = pipeline.get_document(conn, sid)
    rf = next(c for c in doc["validation"] if c["code"] == "roll_forward")
    assert rf["status"] == "fail"
    with pytest.raises(ValueError, match="rolls forward"):
        pipeline.approve(conn, sid, "tester")
    ca = dict(doc["extraction"]["capital_account"])
    ca["unrealized_gain"] -= 10_000  # reviewer confirms the GP's typo with the administrator
    fixed = pipeline.update_document(conn, sid, "tester", {"capital_account": ca})
    assert next(c for c in fixed["validation"] if c["code"] == "roll_forward")["status"] == "pass"
    pipeline.approve(conn, sid, "tester")


def test_unknown_fund_cannot_be_overridden(conn):
    bid = queue(conn, "Bluewater")[0]["id"]
    with pytest.raises(ValueError, match="Matched to a fund"):
        pipeline.approve(conn, bid, "tester", note="just post it", override=True)


def test_approval_requires_a_named_reviewer(conn):
    cid = queue(conn, "MIF1_capital_call_2026-07")[0]["id"]
    with pytest.raises(ValueError, match="Reviewer"):
        pipeline.approve(conn, cid, "")


def test_export_once_then_nothing_new(conn):
    content, n = export_csv(conn, "B1")
    assert n > 0
    rows = list(csv.DictReader(io.StringIO(content)))
    assert sum(float(r["debit"]) for r in rows) == pytest.approx(sum(float(r["credit"]) for r in rows))
    assert export_csv(conn, "B2")[1] == 0


def test_rules_extraction_reads_each_house_style(conn):
    # One fund per GP layout; the extracted total must match what was booked from the notice.
    for code in ("HRZ4", "NCO2", "SRE3"):
        call = conn.execute("SELECT amount, d.extraction FROM transactions t JOIN documents d ON d.id = t.document_id "
                            "WHERE t.fund_code = ? AND txn_type = 'contribution' LIMIT 1", (code,)).fetchone()
        if call:
            import json
            ex = json.loads(call["extraction"])
            cc = ex["capital_call"]
            assert cc["investment"] + cc["management_fee"] + cc["fund_expenses"] == pytest.approx(call["amount"])


def test_claude_extractor_handles_refusal():
    resp = SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(explanation="declined"), parsed_output=None)
    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=lambda **kw: resp)))
    with pytest.raises(extract_llm.ExtractionRefused):
        extract_llm.extract_pdf(b"%PDF-1.4", ["Fund A"], client=fake)


def test_claude_extractor_sends_pdf_and_returns_parsed(conn):
    from altinv.models import Extraction
    seen = {}
    out = Extraction(doc_type="capital_call", fund_name="Fund A", confidence=0.9)

    def parse(**kw):
        seen.update(kw)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=out)

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    assert extract_llm.extract_pdf(b"%PDF-1.4", ["Fund A"], client=fake) is out
    block = seen["messages"][0]["content"][0]
    assert block["type"] == "document" and block["source"]["media_type"] == "application/pdf"
    assert seen["output_format"] is Extraction
