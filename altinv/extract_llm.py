"""Claude-based extractor: sends the PDF itself (so scanned / image PDFs work) and gets back a
validated `Extraction` via structured outputs.

Credentials are resolved by the Anthropic SDK (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an
`ant auth login` profile). Before sending real LP documents, confirm your firm has approved this
use and the data-retention terms.
"""
from __future__ import annotations

import base64
import os

import anthropic

from .models import Extraction

MODEL = os.environ.get("ALTINV_MODEL", "claude-opus-5-5")

SYSTEM_PROMPT = """You extract data from private-fund (LP) investor documents for an investment \
accounting team. The documents come from many different general partners, each with its own layout.

Rules:
- Report amounts for this investor's share only, never fund-level totals (except \
financial_statements.fund_total_nav).
- Use plain numbers without currency symbols. Expenses, fees, carried interest, withholding and \
distributions are positive numbers; gains and income are signed.
- Dates must be YYYY-MM-DD. For a capital call, effective_date is the due date; for a distribution, \
the payment date.
- Fill only the section matching doc_type; leave the others null.
- Leave a field null when the document doesn't show it. Never guess or compute a value the \
document doesn't state, and list any field you were unsure about in uncertain_fields.
- If a capital account statement shows several columns (quarter, year-to-date, inception), use the \
current-period column.
- Set confidence (0-1) to reflect how cleanly the figures could be read."""


class ExtractionRefused(RuntimeError):
    pass


def extract_pdf(pdf_bytes: bytes, fund_names: list[str], client: anthropic.Anthropic | None = None) -> Extraction:
    client = client or anthropic.Anthropic()
    known = "\n".join(f"- {n}" for n in fund_names)
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
                    },
                },
                {
                    "type": "text",
                    "text": "Extract this document. Funds we hold (match fund_name to one of these "
                            f"when it is clearly the same fund, else copy the name as printed):\n{known}",
                },
            ],
        }],
        output_format=Extraction,
    )
    if response.stop_reason == "refusal":
        raise ExtractionRefused(getattr(response.stop_details, "explanation", None) or "Request declined")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise RuntimeError(f"Extraction incomplete (stop_reason={response.stop_reason})")
    return response.parsed_output


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_PROFILE"))
