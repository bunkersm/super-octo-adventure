"""Command line: `python -m altinv.cli demo` (reset + load sample data) and `python -m altinv.cli serve`."""
from __future__ import annotations

import argparse
import shutil

from . import db


def cmd_demo(args):
    if db.DATA_DIR.exists():
        if not args.yes:
            ans = input(f"This deletes everything in {db.DATA_DIR}. Continue? [y/N] ")
            if ans.strip().lower() != "y":
                return
        shutil.rmtree(db.DATA_DIR)
    from .samples import seed_demo
    conn = db.connect()
    counts = seed_demo(conn, db.DATA_DIR / "samples")
    conn.commit()
    print(f"Loaded demo data: {counts['history']} historical documents approved, "
          f"{counts['inbox']} waiting in the review queue.")
    print(f"Sample PDFs to try uploading yourself: {db.DATA_DIR / 'samples' / 'upload'}")


def cmd_serve(args):
    import uvicorn
    print(f"Open http://{args.host}:{args.port}")
    uvicorn.run("altinv.app:app", host=args.host, port=args.port, reload=False)


def main():
    p = argparse.ArgumentParser(prog="altinv")
    sub = p.add_subparsers(required=True)
    d = sub.add_parser("demo", help="Reset the local database and load synthetic sample data")
    d.add_argument("-y", "--yes", action="store_true", help="Don't ask before deleting existing data")
    d.set_defaults(func=cmd_demo)
    s = sub.add_parser("serve", help="Run the web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
