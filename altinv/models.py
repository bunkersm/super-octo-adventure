"""The structured shape every extractor (rules or Claude) must produce for a document.

One flat-ish schema covers all five document types; sections that don't apply stay null.
Amounts are the investor's (our) share, in the fund currency, as positive numbers unless
the field says it is signed.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

DocType = Literal[
    "capital_call",
    "distribution",
    "capital_account_statement",
    "financial_statements",
    "k1",
    "other",
]

DOC_TYPE_LABELS = {
    "capital_call": "Capital call",
    "distribution": "Distribution",
    "capital_account_statement": "Capital account statement",
    "financial_statements": "Financial statements",
    "k1": "Schedule K-1",
    "other": "Other / unknown",
}


class CapitalCall(BaseModel):
    investment: Optional[float] = Field(None, description="Portion of the call for investments")
    management_fee: Optional[float] = Field(None, description="Portion for management fees")
    fund_expenses: Optional[float] = Field(None, description="Portion for partnership / organizational expenses")
    other: Optional[float] = Field(None, description="Any other portion (e.g. interest, equalization)")
    total: Optional[float] = Field(None, description="Total amount due from the investor")


class Distribution(BaseModel):
    return_of_capital: Optional[float] = Field(None, description="Return of capital portion")
    realized_gain: Optional[float] = Field(None, description="Realized gain / capital gain portion")
    income: Optional[float] = Field(None, description="Dividend / interest / other income portion")
    withholding: Optional[float] = Field(None, description="Tax withheld (positive number)")
    net_amount: Optional[float] = Field(None, description="Net cash paid to the investor")
    recallable: Optional[float] = Field(None, description="Amount that is recallable / may be called again")


class CapitalAccount(BaseModel):
    beginning_balance: Optional[float] = Field(None, description="Investor capital at start of period")
    contributions: Optional[float] = Field(None, description="Contributions during the period")
    distributions: Optional[float] = Field(None, description="Distributions during the period (positive)")
    investment_income: Optional[float] = Field(None, description="Net investment income allocated (signed)")
    expenses: Optional[float] = Field(None, description="Management fees and fund expenses allocated (positive number that reduces capital)")
    realized_gain: Optional[float] = Field(None, description="Realized gain (loss), signed")
    unrealized_gain: Optional[float] = Field(None, description="Change in unrealized gain (loss), signed")
    carried_interest: Optional[float] = Field(None, description="Carried interest allocation to GP (positive number that reduces capital)")
    ending_balance: Optional[float] = Field(None, description="Investor capital at end of period (NAV)")
    commitment: Optional[float] = Field(None, description="Total commitment, if shown")
    unfunded_commitment: Optional[float] = Field(None, description="Remaining unfunded commitment, if shown")


class K1(BaseModel):
    is_final: Optional[bool] = Field(None, description="False if marked estimate / draft")
    ordinary_income: Optional[float] = Field(None, description="Box 1 ordinary business income (loss)")
    interest_income: Optional[float] = Field(None, description="Box 5 interest income")
    dividends: Optional[float] = Field(None, description="Box 6a ordinary dividends")
    st_capital_gain: Optional[float] = Field(None, description="Box 8 net short-term capital gain (loss)")
    lt_capital_gain: Optional[float] = Field(None, description="Box 9a net long-term capital gain (loss)")
    distributions: Optional[float] = Field(None, description="Box 19 distributions")
    ending_capital: Optional[float] = Field(None, description="Item L ending capital account")


class FinancialStatements(BaseModel):
    audited: Optional[bool] = None
    fund_total_nav: Optional[float] = Field(None, description="Total partners' capital of the whole fund")


class Extraction(BaseModel):
    doc_type: DocType
    fund_name: Optional[str] = Field(None, description="Fund / partnership name exactly as printed")
    gp_name: Optional[str] = Field(None, description="General partner or manager name")
    investor_name: Optional[str] = Field(None, description="Limited partner / investor name")
    notice_date: Optional[str] = Field(None, description="Date of the notice or document, YYYY-MM-DD")
    effective_date: Optional[str] = Field(None, description="Due date (calls) or payment date (distributions), YYYY-MM-DD")
    period_start: Optional[str] = Field(None, description="Reporting period start, YYYY-MM-DD")
    period_end: Optional[str] = Field(None, description="Reporting period end, YYYY-MM-DD")
    tax_year: Optional[int] = None
    currency: Optional[str] = Field(None, description="ISO currency code, e.g. USD")
    capital_call: Optional[CapitalCall] = None
    distribution: Optional[Distribution] = None
    capital_account: Optional[CapitalAccount] = None
    k1: Optional[K1] = None
    financial_statements: Optional[FinancialStatements] = None
    confidence: float = Field(0.0, description="0-1 overall confidence in the extraction")
    uncertain_fields: list[str] = Field(default_factory=list, description="Dotted names of fields that were unclear")
    notes: Optional[str] = Field(None, description="Anything a reviewer should know")


# Fields that must be present for each type before the document can be approved.
REQUIRED_FIELDS: dict[str, list[str]] = {
    "capital_call": ["fund_name", "effective_date", "capital_call.total"],
    "distribution": ["fund_name", "effective_date", "distribution.net_amount"],
    "capital_account_statement": [
        "fund_name", "period_end", "capital_account.beginning_balance", "capital_account.ending_balance",
    ],
    "financial_statements": ["fund_name", "period_end"],
    "k1": ["fund_name", "tax_year"],
    "other": [],
}


def get_field(data: dict, dotted: str):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur
