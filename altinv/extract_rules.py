"""Offline, rules-based extractor.

This is the fallback that needs no API key. It recognises common GP wording ("capital call",
"drawdown", "return of capital" ...) and label synonyms, which covers the sample documents and
many plain-text statements. With ~100 GPs, each with its own layout, the Claude extractor
(extract_llm.py) is the one meant for production; this one keeps the demo and tests self-contained.
"""
from __future__ import annotations

import re
from datetime import datetime

from .models import REQUIRED_FIELDS, Extraction, get_field

AMOUNT_RE = re.compile(
    r"(?P<neg>\(|-)?\s?(?:USD|\$)?\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+\.\d{2})(?P<close>\))?"
)
DATE_PATTERNS = [
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"), "%Y-%m-%d"),
    (re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b"), "%m/%d/%Y"),
    (re.compile(r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})\b"), "%B %d, %Y"),
    (re.compile(r"\b((?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{1,2},\s+\d{4})\b"), "%b %d, %Y"),
    (re.compile(r"\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b"), "%d %B %Y"),
]
BOX_PREFIX = re.compile(r"^(?:box\s*)?\d{1,2}[a-z]?[\s.:)-]+")

# Keyword scores for classifying the document type. Highest total wins.
TYPE_KEYWORDS = {
    "k1": ["schedule k-1", "partner's share of income", "form 1065", "k-1"],
    "capital_call": ["capital call", "drawdown notice", "call notice", "capital contribution notice", "amount due"],
    "distribution": ["distribution notice", "notice of distribution", "return of capital", "net distribution"],
    "capital_account_statement": [
        "capital account statement", "statement of partner's capital", "partner capital account",
        "investor capital statement", "beginning capital", "ending capital", "net asset value",
    ],
    "financial_statements": [
        "statement of assets and liabilities", "statements of operations", "financial statements",
        "independent auditor", "schedule of investments", "balance sheet",
    ],
}

LABELS = {
    "capital_call.investment": ["investment amount", "capital for investments", "investments", "portfolio investment"],
    "capital_call.management_fee": ["management fee"],
    "capital_call.fund_expenses": ["partnership expenses", "fund expenses", "organizational", "expenses"],
    "capital_call.other": ["interest", "equalization", "other"],
    "capital_call.total": ["total amount due", "total capital call", "total call amount", "total due", "amount due", "total"],
    "distribution.return_of_capital": ["return of capital", "capital returned"],
    "distribution.realized_gain": ["realized gain", "capital gain", "gain on sale"],
    "distribution.income": ["dividend", "interest and dividend", "income"],
    "distribution.withholding": ["tax withheld", "withholding", "less: withholding"],
    "distribution.net_amount": ["net distribution", "net amount payable", "net cash distribution", "net amount", "total distribution"],
    "distribution.recallable": ["recallable", "subject to recall"],
    "capital_account.beginning_balance": ["beginning capital", "beginning balance", "opening capital", "capital, beginning", "opening nav"],
    "capital_account.contributions": ["contributions", "capital contributions", "paid-in capital"],
    "capital_account.distributions": ["distributions"],
    "capital_account.investment_income": ["net investment income", "investment income", "income"],
    "capital_account.expenses": ["management fees and expenses", "fees and expenses", "management fees", "expenses"],
    "capital_account.realized_gain": ["net realized gain", "realized gain"],
    "capital_account.unrealized_gain": ["change in unrealized", "net change in unrealized", "unrealized gain"],
    "capital_account.carried_interest": ["carried interest", "incentive allocation", "carry"],
    "capital_account.ending_balance": ["ending capital", "ending balance", "closing capital", "capital, ending", "ending nav", "net asset value"],
    "capital_account.commitment": ["total commitment", "commitment"],
    "capital_account.unfunded_commitment": ["unfunded commitment", "remaining commitment"],
    "k1.ordinary_income": ["ordinary business income"],
    "k1.interest_income": ["interest income"],
    "k1.dividends": ["ordinary dividends"],
    "k1.st_capital_gain": ["net short-term capital gain"],
    "k1.lt_capital_gain": ["net long-term capital gain"],
    "k1.distributions": ["distributions"],
    "k1.ending_capital": ["ending capital account"],
    "financial_statements.fund_total_nav": ["total partners' capital", "total partners capital", "net assets"],
}
# Expenses / carry are shown as negatives on many statements; we store them positive.
POSITIVE_FIELDS = {"capital_account.expenses", "capital_account.carried_interest", "distribution.withholding",
                   "capital_account.distributions"}


def parse_amount(text: str) -> float | None:
    m = AMOUNT_RE.search(text)
    if not m:
        return None
    value = float(m.group("num").replace(",", ""))
    if m.group("neg") == "-" or (m.group("neg") == "(" and m.group("close")):
        value = -value
    return value


def _to_iso(raw: str, fmt: str) -> str:
    raw = re.sub(r"\bSept\b", "Sep", raw).replace(".", "")
    return datetime.strptime(raw, fmt).date().isoformat()


def parse_date(text: str) -> str | None:
    for pattern, fmt in DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                return _to_iso(m.group(1), fmt)
            except ValueError:
                continue
    return None


def all_dates(text: str) -> list[str]:
    found = []
    for pattern, fmt in DATE_PATTERNS:
        for m in pattern.finditer(text):
            try:
                found.append(_to_iso(m.group(1), fmt))
            except ValueError:
                pass
    return found


def classify(text: str) -> tuple[str, float]:
    low = text.lower()
    scores = {t: sum(low.count(k) for k in kws) for t, kws in TYPE_KEYWORDS.items()}
    # Titles near the top of the page are the strongest signal.
    head = low[:400]
    for t, kws in TYPE_KEYWORDS.items():
        scores[t] += sum(3 for k in kws if k in head)
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "other", 0.0
    total = sum(scores.values())
    return best, scores[best] / total


def _key(line: str) -> str:
    return BOX_PREFIX.sub("", line.strip().lower())


def find_amount(lines: list[str], labels: list[str]) -> float | None:
    for label in labels:
        for line in lines:
            key = _key(line)
            if key.startswith(label):
                amount = parse_amount(key[len(label):])
                if amount is not None:
                    return amount
    return None


def find_labeled_date(lines: list[str], labels: list[str]) -> str | None:
    for label in labels:
        for line in lines:
            low = line.lower()
            if label in low:
                d = parse_date(line[low.index(label):])
                if d:
                    return d
    return None


def find_labeled_text(lines: list[str], labels: list[str]) -> str | None:
    for label in labels:
        for line in lines:
            if line.strip().lower().startswith(label):
                value = line.split(":", 1)[1].strip() if ":" in line else line.strip()[len(label):].strip()
                if value:
                    return value
    return None


def _set(data: dict, dotted: str, value):
    section, field = dotted.split(".")
    data.setdefault(section, {})[field] = value


def extract(text: str) -> Extraction:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    doc_type, type_conf = classify(text)
    data: dict = {"doc_type": doc_type}

    data["fund_name"] = find_labeled_text(lines, ["fund:", "partnership:", "fund name:", "re:"])
    data["gp_name"] = find_labeled_text(lines, ["general partner:", "manager:", "from:"])
    data["investor_name"] = find_labeled_text(lines, ["limited partner:", "investor:", "partner:"])
    data["notice_date"] = find_labeled_date(lines, ["notice date", "date of notice", "statement date", "date:"])
    data["currency"] = "USD" if ("$" in text or "USD" in text) else None

    section = {
        "capital_call": "capital_call",
        "distribution": "distribution",
        "capital_account_statement": "capital_account",
        "k1": "k1",
        "financial_statements": "financial_statements",
    }.get(doc_type)
    if section:
        for dotted, labels in LABELS.items():
            if not dotted.startswith(section + "."):
                continue
            value = find_amount(lines, labels)
            if value is not None:
                if dotted in POSITIVE_FIELDS:
                    value = abs(value)
                _set(data, dotted, value)
        data.setdefault(section, {})

    if doc_type == "capital_call":
        data["effective_date"] = find_labeled_date(lines, ["due date", "payment due", "funding date", "due on or before", "wire by"])
    elif doc_type == "distribution":
        data["effective_date"] = find_labeled_date(lines, ["payment date", "distribution date", "value date", "paid on"])
    elif doc_type in ("capital_account_statement", "financial_statements"):
        period_line = next((ln for ln in lines if re.search(r"period|quarter ended|as of|ended", ln, re.I) and parse_date(ln)), None)
        if period_line:
            dates = all_dates(period_line)
            if len(dates) >= 2:
                data["period_start"], data["period_end"] = min(dates), max(dates)
            elif dates:
                data["period_end"] = dates[0]
        if doc_type == "financial_statements":
            data["financial_statements"]["audited"] = "independent auditor" in text.lower()
    elif doc_type == "k1":
        m = re.search(r"(?:tax year|calendar year|for year)\s*(\d{4})", text, re.I) or re.search(r"\b(20\d{2})\b", text)
        data["tax_year"] = int(m.group(1)) if m else None
        data["k1"]["is_final"] = not re.search(r"\bestimate", text, re.I)

    required = REQUIRED_FIELDS.get(doc_type, [])
    found = sum(1 for f in required if get_field(data, f) not in (None, ""))
    coverage = found / len(required) if required else 0.5
    data["confidence"] = round(0.4 * type_conf + 0.6 * coverage, 2)
    data["uncertain_fields"] = [f for f in required if get_field(data, f) in (None, "")]
    data["notes"] = "Extracted with the offline rules engine. Check every figure against the PDF."
    return Extraction.model_validate(data)
