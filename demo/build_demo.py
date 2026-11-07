"""Build the browser-only demo: one self-contained HTML page with the sample data baked in.

    python demo/build_demo.py            # writes data/demo/index.html

It loads the synthetic sample data into a throwaway database, then embeds that state plus an
image of every PDF into demo/template.html. The page re-implements the checks and posting
rules in JavaScript so it can run with no server.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["ALTINV_DATA_DIR"] = tempfile.mkdtemp(prefix="altinv-demo-")
os.environ["ALTINV_EXTRACTOR"] = "rules"
sys.path.insert(0, str(ROOT))

import pypdfium2  # noqa: E402
from PIL import ImageOps  # noqa: E402

from altinv import db  # noqa: E402
from altinv.models import DOC_TYPE_LABELS  # noqa: E402
from altinv.posting import load_accounts  # noqa: E402
from altinv.samples import seed_demo  # noqa: E402


def page_image(pdf_path: str) -> str:
    img = pypdfium2.PdfDocument(pdf_path)[0].render(scale=1.5).to_pil().convert("L")
    box = ImageOps.invert(img).getbbox()
    if box:
        img = img.crop((0, 0, img.width, min(img.height, box[3] + 60)))
    buf = io.BytesIO()
    img.quantize(16).save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def main(out: Path):
    conn = db.connect()
    seed_demo(conn, Path(os.environ["ALTINV_DATA_DIR"]) / "samples")
    docs = db.rows(conn, "SELECT * FROM documents ORDER BY id")
    images = {}
    for d in docs:
        images.setdefault(d["sha256"], page_image(d.pop("stored_path")))
    jes = db.rows(conn, "SELECT * FROM journal_entries ORDER BY id")
    lines = db.rows(conn, "SELECT * FROM je_lines ORDER BY id")
    for je in jes:
        je["lines"] = [{k: l[k] for k in ("account_key", "debit", "credit", "memo")} for l in lines if l["je_id"] == je["id"]]
    state = {
        "funds": db.rows(conn, "SELECT * FROM funds ORDER BY code"),
        "documents": docs,
        "transactions": db.rows(conn, "SELECT * FROM transactions ORDER BY id"),
        "statements": db.rows(conn, "SELECT * FROM statements ORDER BY id"),
        "k1s": db.rows(conn, "SELECT * FROM k1s ORDER BY id"),
        "journal_entries": jes,
        "audit_log": db.rows(conn, "SELECT * FROM audit_log ORDER BY id"),
    }
    meta = {"doc_types": DOC_TYPE_LABELS, "accounts": load_accounts()["accounts"], "entity": load_accounts()["entity"]}
    html = (ROOT / "demo" / "template.html").read_text()
    html = html.replace("/*__STATE__*/null", json.dumps(state, separators=(",", ":")))
    html = html.replace("/*__META__*/null", json.dumps(meta, separators=(",", ":")))
    html = html.replace("/*__IMAGES__*/null", json.dumps(images, separators=(",", ":")))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    print(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {len(docs)} documents)")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "demo" / "index.html")
