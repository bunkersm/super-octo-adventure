"""Synthetic demo data: a fund master list and realistic-looking LP notices rendered as PDFs.

Every name and number here is made up. Three different GP "house styles" are used so the
extraction has to cope with different wording, date formats and table layouts.
"""
from __future__ import annotations

import io
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

INVESTOR = "Pinecrest Holdings LLC"

FUNDS = [
    dict(code="HRZ4", name="Horizon Growth Partners IV, L.P.", gp_name="Horizon Capital Management",
         aliases=["Horizon Growth IV", "HGP IV"], strategy="Buyout", vintage=2021, commitment=25_000_000,
         portal="Intralinks", style="A"),
    dict(code="NCO2", name="Northgate Credit Opportunities Fund II, L.P.", gp_name="Northgate Asset Management",
         aliases=["Northgate Credit II", "NCOF II"], strategy="Private credit", vintage=2022, commitment=15_000_000,
         portal="Juniper Square", style="B"),
    dict(code="SRE3", name="Silverline Real Estate Partners III, L.P.", gp_name="Silverline Property Advisors",
         aliases=["Silverline RE III"], strategy="Real estate", vintage=2020, commitment=20_000_000,
         portal="DFIN Venue", style="C"),
    dict(code="CRV6", name="Cedar Ridge Ventures VI, L.P.", gp_name="Cedar Ridge Partners",
         aliases=["Cedar Ridge VI"], strategy="Venture", vintage=2023, commitment=10_000_000,
         portal="Carta", style="A"),
    dict(code="MIF1", name="Meridian Infrastructure Fund I, L.P.", gp_name="Meridian Infrastructure Advisors",
         aliases=["Meridian Infra I"], strategy="Infrastructure", vintage=2019, commitment=30_000_000,
         portal="Allvue investor portal", style="B"),
    dict(code="ATS5", name="Atlas Secondaries Fund V, L.P.", gp_name="Atlas Secondary Partners",
         aliases=["Atlas Secondaries V"], strategy="Secondaries", vintage=2024, commitment=12_000_000,
         portal="GP website", style="C"),
]
UNKNOWN_FUND = dict(code="BWE1", name="Bluewater Energy Transition Fund I, L.P.", gp_name="Bluewater Capital",
                    commitment=8_000_000, style="A")

QUARTERS = [(date(2026, 1, 1), date(2026, 3, 31)), (date(2026, 4, 1), date(2026, 6, 30)),
            (date(2026, 7, 1), date(2026, 9, 30))]
OPENING_DATE = date(2025, 12, 31)


# ----------------------------------------------------------------------------- formatting

def money(v: float, style: str, dollar: bool = True) -> str:
    s = f"{abs(v):,.2f}"
    if dollar and style != "C":
        s = "$" + s
    if v < 0:
        return f"-{s}" if style == "A" else f"({s})"
    return s


def fmt_date(d: date, style: str) -> str:
    if style == "A":
        return d.strftime("%B %-d, %Y")
    if style == "B":
        return d.strftime("%m/%d/%Y")
    return d.strftime("%-d %B %Y")


class Page:
    """Minimal report-style PDF writer."""

    def __init__(self, fund: dict):
        self.buf = io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=letter)
        self.c.setTitle(fund["name"])
        self.y = 740
        self.c.setFont("Helvetica-Bold", 13)
        self.c.drawString(72, self.y, fund["gp_name"])
        self.c.setFont("Helvetica", 9)
        self.y -= 14
        self.c.drawString(72, self.y, "Confidential - for the named limited partner only")
        self.y -= 30

    def title(self, text: str):
        self.c.setFont("Helvetica-Bold", 15)
        self.c.drawString(72, self.y, text)
        self.y -= 26
        self.c.setFont("Helvetica", 10)

    def text(self, text: str, bold: bool = False, gap: int = 15):
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        self.c.drawString(72, self.y, text)
        self.y -= gap

    def row(self, label: str, *values: str, bold: bool = False):
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        self.c.drawString(72, self.y, label)
        x = 470 if len(values) == 1 else 380
        for v in values:
            self.c.drawRightString(x, self.y, v)
            x += 110
        self.y -= 16

    def rule(self):
        self.c.line(72, self.y + 10, 540, self.y + 10)
        self.y -= 6

    def bytes(self) -> bytes:
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


# ----------------------------------------------------------------------------- renderers

def render_call(fund: dict, notice: date, due: date, parts: dict) -> bytes:
    s = fund["style"]
    p = Page(fund)
    total = round(sum(parts.values()), 2)
    if s == "A":
        p.title("Capital Call Notice")
        p.text(f"Fund: {fund['name']}")
        p.text(f"Limited Partner: {INVESTOR}")
        p.text(f"Notice Date: {fmt_date(notice, s)}")
        p.text(f"Due Date: {fmt_date(due, s)}", gap=26)
        p.row("Investment Amount", money(parts["investment"], s))
        p.row("Management Fee", money(parts["management_fee"], s))
        p.row("Partnership Expenses", money(parts["fund_expenses"], s))
        p.rule()
        p.row("Total Amount Due", money(total, s), bold=True)
    elif s == "B":
        p.title("Capital Call Notice")
        p.text(f"Re: {fund['name']}")
        p.text(f"Investor: {INVESTOR}")
        p.text(f"Date of notice: {fmt_date(notice, s)}")
        p.text(f"Payment due: {fmt_date(due, s)}", gap=26)
        p.text("In accordance with the Partnership Agreement, your capital contribution is called as follows:", gap=22)
        p.row("Capital for Investments", money(parts["investment"], s))
        p.row("Management Fees", money(parts["management_fee"], s))
        p.row("Organizational & Other Expenses", money(parts["fund_expenses"], s))
        p.rule()
        p.row("Total Capital Call", money(total, s), bold=True)
    else:
        p.title("Drawdown Notice")
        p.text(f"Partnership: {fund['name']}")
        p.text(f"Investor: {INVESTOR}")
        p.text(f"Statement date: {fmt_date(notice, s)}")
        p.text(f"Funding date: {fmt_date(due, s)}", gap=26)
        p.row("Purpose", "Your share (USD)", bold=True)
        p.row("Portfolio investment", money(parts["investment"], s))
        p.row("Management fee", money(parts["management_fee"], s))
        p.row("Fund expenses", money(parts["fund_expenses"], s))
        p.rule()
        p.row("Total due", money(total, s), bold=True)
    p.y -= 20
    p.text("Wire instructions are available on the investor portal. Please verify by phone before sending funds.")
    return p.bytes()


def render_distribution(fund: dict, notice: date, paid: date, d: dict) -> bytes:
    s = fund["style"]
    p = Page(fund)
    gross = d["return_of_capital"] + d["realized_gain"] + d["income"]
    net = round(gross - d["withholding"], 2)
    if s == "A":
        p.title("Distribution Notice")
        p.text(f"Fund: {fund['name']}")
        p.text(f"Limited Partner: {INVESTOR}")
        p.text(f"Notice Date: {fmt_date(notice, s)}")
        p.text(f"Payment Date: {fmt_date(paid, s)}", gap=26)
        p.row("Return of Capital", money(d["return_of_capital"], s))
        p.row("Realized Gain", money(d["realized_gain"], s))
        p.row("Dividend Income", money(d["income"], s))
        p.row("Tax Withheld", money(-d["withholding"], s))
        p.rule()
        p.row("Net Distribution", money(net, s), bold=True)
        p.row("Recallable Amount", money(d["recallable"], s))
    elif s == "B":
        p.title("Notice of Distribution")
        p.text(f"Re: {fund['name']}")
        p.text(f"Investor: {INVESTOR}")
        p.text(f"Date of notice: {fmt_date(notice, s)}")
        p.text(f"Distribution date: {fmt_date(paid, s)}", gap=26)
        p.row("Capital Returned", money(d["return_of_capital"], s))
        p.row("Capital Gain", money(d["realized_gain"], s))
        p.row("Interest and Dividend Income", money(d["income"], s))
        p.row("Less: Withholding", money(-d["withholding"], s))
        p.rule()
        p.row("Net Amount Payable", money(net, s), bold=True)
        p.text(f"Subject to recall: {money(d['recallable'], s)}")
    else:
        p.title("Distribution Notice")
        p.text(f"Partnership: {fund['name']}")
        p.text(f"Investor: {INVESTOR}")
        p.text(f"Statement date: {fmt_date(notice, s)}")
        p.text(f"Value date: {fmt_date(paid, s)}", gap=26)
        p.row("Component", "Your share (USD)", bold=True)
        p.row("Return of capital", money(d["return_of_capital"], s))
        p.row("Gain on sale", money(d["realized_gain"], s))
        p.row("Income", money(d["income"], s))
        p.row("Withholding", money(-d["withholding"], s))
        p.rule()
        p.row("Net cash distribution", money(net, s), bold=True)
        p.row("Recallable", money(d["recallable"], s))
    return p.bytes()


def render_cas(fund: dict, start: date, end: date, ca: dict, ytd: dict, issued: date) -> bytes:
    s = fund["style"]
    p = Page(fund)
    if s == "A":
        p.title("Capital Account Statement")
        p.text(f"Fund: {fund['name']}")
        p.text(f"Limited Partner: {INVESTOR}")
        p.text(f"For the period {fmt_date(start, s)} to {fmt_date(end, s)}")
        p.text(f"Statement Date: {fmt_date(issued, s)}", gap=26)
        p.row("Beginning Capital", money(ca["beginning_balance"], s))
        p.row("Contributions", money(ca["contributions"], s))
        p.row("Distributions", money(-ca["distributions"], s))
        p.row("Net Investment Income", money(ca["investment_income"], s))
        p.row("Management Fees and Expenses", money(-ca["expenses"], s))
        p.row("Net Realized Gain (Loss)", money(ca["realized_gain"], s))
        p.row("Net Change in Unrealized Gain (Loss)", money(ca["unrealized_gain"], s))
        p.row("Carried Interest Allocation", money(-ca["carried_interest"], s))
        p.rule()
        p.row("Ending Capital", money(ca["ending_balance"], s), bold=True)
        p.y -= 10
        p.row("Total Commitment", money(fund["commitment"], s))
        p.row("Unfunded Commitment", money(ca["unfunded_commitment"], s))
    elif s == "B":
        p.title("Statement of Partner's Capital")
        p.text(f"Re: {fund['name']}")
        p.text(f"Investor: {INVESTOR}")
        p.text(f"Quarter ended {fmt_date(end, s)}", gap=26)
        p.row("Opening Capital", money(ca["beginning_balance"], s))
        p.row("Capital Contributions", money(ca["contributions"], s))
        p.row("Distributions", money(-ca["distributions"], s))
        p.row("Investment Income", money(ca["investment_income"], s))
        p.row("Fees and Expenses", money(-ca["expenses"], s))
        p.row("Realized Gain (Loss)", money(ca["realized_gain"], s))
        p.row("Unrealized Gain (Loss)", money(ca["unrealized_gain"], s))
        p.row("Incentive Allocation", money(-ca["carried_interest"], s))
        p.rule()
        p.row("Closing Capital", money(ca["ending_balance"], s), bold=True)
        p.y -= 10
        p.row("Remaining Commitment", money(ca["unfunded_commitment"], s))
    else:
        p.title("Investor Capital Statement")
        p.text(f"Partnership: {fund['name']}")
        p.text(f"Investor: {INVESTOR}")
        p.text(f"Period: {fmt_date(start, s)} - {fmt_date(end, s)}", gap=26)
        p.row("", "Quarter", "Year to date", bold=True)
        labels = [("Opening NAV", "beginning_balance", 1), ("Paid-in capital", "contributions", 1),
                  ("Distributions", "distributions", -1), ("Income", "investment_income", 1),
                  ("Expenses", "expenses", -1), ("Realized gain", "realized_gain", 1),
                  ("Unrealized gain", "unrealized_gain", 1), ("Carry", "carried_interest", -1)]
        for label, key, sign in labels:
            p.row(label, money(sign * ca[key], s), money(sign * ytd[key], s))
        p.rule()
        p.row("Ending NAV", money(ca["ending_balance"], s), money(ytd["ending_balance"], s), bold=True)
    return p.bytes()


def render_k1(fund: dict, year: int, k: dict, final: bool) -> bytes:
    p = Page(fund)
    if not final:
        p.text("ESTIMATE - SUBJECT TO CHANGE. A final Schedule K-1 will follow.", bold=True, gap=20)
    p.title("Schedule K-1 (Form 1065)")
    p.text("Partner's Share of Income, Deductions, Credits, etc.")
    p.text(f"For calendar year {year}")
    p.text(f"Partnership: {fund['name']}")
    p.text(f"Partner: {INVESTOR}", gap=26)
    p.row("1 Ordinary business income (loss)", money(k["ordinary_income"], "B"))
    p.row("5 Interest income", money(k["interest_income"], "B"))
    p.row("6a Ordinary dividends", money(k["dividends"], "B"))
    p.row("8 Net short-term capital gain (loss)", money(k["st_capital_gain"], "B"))
    p.row("9a Net long-term capital gain (loss)", money(k["lt_capital_gain"], "B"))
    p.row("19 Distributions   Code A", money(k["distributions"], "B"))
    p.row("Ending capital account (Item L)", money(k["ending_capital"], "B"))
    return p.bytes()


def render_financials(fund: dict, as_of: date, total_nav: float) -> bytes:
    p = Page(fund)
    p.title("Financial Statements")
    p.text(fund["name"])
    p.text(f"As of and for the year ended {fmt_date(as_of, 'A')}", gap=26)
    p.text("Report of Independent Auditors", bold=True)
    p.text("In our opinion, the financial statements present fairly, in all material respects, the financial", gap=12)
    p.text("position of the Partnership. (Synthetic sample - not a real audit report.)", gap=26)
    p.text("Statement of Assets and Liabilities", bold=True)
    p.row("Investments, at fair value", money(total_nav * 1.04, "A"))
    p.row("Cash and other assets", money(total_nav * 0.01, "A"))
    p.row("Liabilities", money(-total_nav * 0.05, "A"))
    p.rule()
    p.row("Total partners' capital", money(total_nav, "A"), bold=True)
    return p.bytes()


# ----------------------------------------------------------------------------- simulation

@dataclass
class SampleDoc:
    file_name: str
    pdf: bytes
    fund_code: str
    kind: str
    sort_date: date
    phase: str  # history (pre-approved) | inbox (left for review) | upload (for the user to try)


@dataclass
class FundSim:
    fund: dict
    opening_nav: float
    opening_called: float
    opening_distributed: float
    docs: list[SampleDoc] = field(default_factory=list)


def _r(x: float) -> float:
    return round(x, 2)


def simulate(seed: int = 7) -> tuple[list[FundSim], list[SampleDoc]]:
    rng = random.Random(seed)
    sims = []
    extra: list[SampleDoc] = []
    for i, f in enumerate(FUNDS):
        commit = f["commitment"]
        maturity = (2026 - f["vintage"]) / 7
        called = _r(commit * min(0.92, 0.15 + 0.6 * maturity))
        distributed = _r(called * max(0.0, maturity - 0.3) * 0.8)
        nav = _r(called * (1.05 + 0.25 * maturity) - distributed)
        sim = FundSim(f, nav, called, distributed)
        sims.append(sim)
        beginning, unfunded_left = nav, commit - called
        ytd = dict(beginning_balance=nav, contributions=0, distributions=0, investment_income=0, expenses=0,
                   realized_gain=0, unrealized_gain=0, carried_interest=0)
        for qi, (qs, qe) in enumerate(QUARTERS):
            phase = "history" if qi < 2 else "inbox"
            contribs = dists = 0.0
            realized = 0.0
            # Capital calls: younger funds call more often.
            for n in range(rng.choice([0, 1, 1, 2]) if maturity < 0.8 else rng.choice([0, 0, 1])):
                due = qs + timedelta(days=20 + 35 * n + rng.randint(0, 10))
                inv = _r(commit * rng.uniform(0.01, 0.04))
                fee = _r(commit * 0.004)
                exp = _r(commit * rng.uniform(0.0002, 0.0008))
                parts = dict(investment=inv, management_fee=fee, fund_expenses=exp)
                amt = _r(inv + fee + exp)
                if amt > unfunded_left:
                    continue
                unfunded_left -= amt
                contribs += amt
                sim.docs.append(SampleDoc(f"{f['code']}_capital_call_{due.isoformat()}.pdf",
                                          render_call(f, due - timedelta(days=12), due, parts), f["code"], "capital_call",
                                          due, phase))
            # Distributions: older funds distribute.
            if maturity > 0.35 and rng.random() < 0.7:
                paid = qs + timedelta(days=rng.randint(40, 80))
                roc = _r(beginning * rng.uniform(0.02, 0.06))
                gain = _r(roc * rng.uniform(0.2, 0.8))
                inc = _r(beginning * rng.uniform(0.001, 0.004))
                wh = _r(inc * 0.1) if f["strategy"] in ("Infrastructure", "Real estate") else 0.0
                rec = _r(roc * 0.25) if rng.random() < 0.3 else 0.0
                d = dict(return_of_capital=roc, realized_gain=gain, income=inc, withholding=wh, recallable=rec)
                dists += _r(roc + gain + inc)
                realized += gain
                unfunded_left += rec
                sim.docs.append(SampleDoc(f"{f['code']}_distribution_{paid.isoformat()}.pdf",
                                          render_distribution(f, paid - timedelta(days=7), paid, d), f["code"],
                                          "distribution", paid, phase))
            income = _r(beginning * rng.uniform(0.002, 0.012))
            expenses = _r(commit * 0.004 + beginning * 0.0005)
            unreal = _r(beginning * rng.uniform(-0.02, 0.05))
            carry = _r(max(0.0, (income + realized + unreal - expenses) * 0.2)) if maturity > 0.5 else 0.0
            ending = _r(beginning + contribs - dists + income - expenses + realized + unreal - carry)
            ca = dict(beginning_balance=beginning, contributions=_r(contribs), distributions=_r(dists),
                      investment_income=income, expenses=expenses, realized_gain=_r(realized),
                      unrealized_gain=unreal, carried_interest=carry, ending_balance=ending,
                      unfunded_commitment=_r(unfunded_left))
            for k in ("contributions", "distributions", "investment_income", "expenses", "realized_gain",
                      "unrealized_gain", "carried_interest"):
                ytd[k] = _r(ytd[k] + ca[k])
            ytd["ending_balance"] = ending
            issued = qe + timedelta(days=40 + rng.randint(0, 15))
            render = dict(ca)
            if qi == 2 and f["code"] == "SRE3":
                render["unrealized_gain"] = _r(unreal + 10_000)  # typo on the GP statement: won't roll forward
            available = qi < 2 or f["code"] in ("HRZ4", "NCO2", "SRE3", "MIF1")  # others' Q3 not issued yet
            if available:
                sim.docs.append(SampleDoc(f"{f['code']}_capital_account_{qe.isoformat()}.pdf",
                                          render_cas(f, qs, qe, render, ytd, issued), f["code"],
                                          "capital_account_statement", qe + timedelta(days=1), phase))
            beginning = ending

        # 2025 K-1s (issued during 2026). Box 19 is 2025 activity, which predates the opening balance.
        k = dict(ordinary_income=_r(nav * rng.uniform(-0.002, 0.01)), interest_income=_r(nav * 0.003),
                 dividends=_r(nav * 0.002), st_capital_gain=_r(nav * rng.uniform(0, 0.004)),
                 lt_capital_gain=_r(nav * rng.uniform(0, 0.03)), distributions=_r(distributed * 0.3),
                 ending_capital=nav)
        if f["code"] in ("HRZ4", "NCO2", "SRE3", "MIF1"):
            sim.docs.append(SampleDoc(f"{f['code']}_K-1_2025.pdf", render_k1(f, 2025, k, True), f["code"], "k1",
                                      date(2026, 6, 15), "history"))
        elif f["code"] == "CRV6":
            sim.docs.append(SampleDoc(f"{f['code']}_K-1_2025_ESTIMATE.pdf", render_k1(f, 2025, k, False), f["code"],
                                      "k1", date(2026, 9, 1), "inbox"))
        if f["code"] == "HRZ4":
            sim.docs.append(SampleDoc(f"{f['code']}_audited_financials_2025.pdf",
                                      render_financials(f, OPENING_DATE, nav * 38.5), f["code"],
                                      "financial_statements", date(2026, 4, 10), "inbox"))
        # A fresh Q4 call (upcoming due date), left for the user to upload themselves.
        if maturity < 0.8:
            due = date(2026, 11, 20)
            parts = dict(investment=_r(commit * 0.02), management_fee=_r(commit * 0.004), fund_expenses=_r(commit * 0.0004))
            extra.append(SampleDoc(f"{f['code']}_capital_call_{due.isoformat()}.pdf",
                                   render_call(f, date(2026, 11, 5), due, parts), f["code"], "capital_call", due, "upload"))

    # A document for a fund we don't have in the master list, and an exact re-download of an inbox notice.
    bw = UNKNOWN_FUND
    extra.append(SampleDoc("Bluewater_capital_call_2026-11-18.pdf",
                           render_call(bw, date(2026, 11, 3), date(2026, 11, 18),
                                       dict(investment=160_000.0, management_fee=30_000.0, fund_expenses=4_000.0)),
                           bw["code"], "capital_call", date(2026, 11, 18), "inbox"))
    first_inbox = next(d for s in sims for d in s.docs if d.phase == "inbox" and d.kind == "capital_call")
    extra.append(SampleDoc(first_inbox.file_name.replace(".pdf", " (1).pdf"), first_inbox.pdf, first_inbox.fund_code,
                           first_inbox.kind, first_inbox.sort_date, "inbox"))
    return sims, extra


def seed_demo(conn, out_dir: Path, seed: int = 7) -> dict:
    """Load the fund master, opening balances and approved history, then fill the review queue."""
    from . import pipeline

    sims, extra = simulate(seed)
    for f in FUNDS:
        conn.execute("INSERT INTO funds (code, name, gp_name, aliases, strategy, vintage, commitment, portal) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     (f["code"], f["name"], f["gp_name"], json.dumps(f["aliases"]), f["strategy"], f["vintage"],
                      f["commitment"], f["portal"]))
    for s in sims:
        pipeline.record_opening_balance(conn, s.fund["code"], OPENING_DATE.isoformat(), s.opening_nav,
                                        s.opening_called, s.opening_distributed)
    conn.commit()

    all_docs = sorted([d for s in sims for d in s.docs] + extra, key=lambda d: (d.sort_date, d.kind != "capital_call"))
    counts = {"history": 0, "inbox": 0, "upload": 0}
    for sub in ("history", "inbox", "upload"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)
    for d in all_docs:
        (out_dir / d.phase / d.file_name).write_bytes(d.pdf)
        counts[d.phase] += 1
        if d.phase == "upload":
            continue
        doc = pipeline.ingest(conn, d.pdf, d.file_name, actor="demo loader")
        if d.phase == "history":
            pipeline.approve(conn, doc["id"], "demo loader (historical)", note="Historical load")
    return counts
