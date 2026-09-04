from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .sec_concepts import map_canonical_facts


_ALLOWED_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}

_EXPECTED_STATEMENTS: dict[str, set[str]] = {
    "revenue": {"IS"},
    "net_income": {"IS", "CI"},
    "operating_income": {"IS"},
    "total_assets": {"BS"},
    "total_liabilities": {"BS"},
    "shareholders_equity": {"BS", "EQ"},
    "cash": {"BS"},
    "operating_cash_flow": {"CF"},
    "capital_expenditures": {"CF"},
    "shares_outstanding": {"BS", "CP"},
}

_INSTANT_CONCEPTS = {
    "total_assets",
    "total_liabilities",
    "shareholders_equity",
    "cash",
    "shares_outstanding",
}

_DURATION_CONCEPTS = {
    "revenue",
    "net_income",
    "operating_income",
    "operating_cash_flow",
    "capital_expenditures",
}


@dataclass(frozen=True)
class CanonicalFactAudit:
    rows_input: int
    rows_mapped: int
    rows_supported_forms: int
    rows_consolidated: int
    rows_statement_matched: int
    rows_period_matched: int
    duplicate_groups: int


def build_canonical_facts(
    submissions: pd.DataFrame,
    numeric_facts: pd.DataFrame,
    presentation: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, CanonicalFactAudit]:
    """Build conservative PIT-ready canonical SEC facts.

    The builder keeps only curated concepts, supported filing forms, consolidated
    entity facts, appropriate financial-statement placements, and concept-
    appropriate instant/duration contexts. It preserves remaining duplicate
    candidates for audit rather than silently choosing a winner.
    """

    mapped = map_canonical_facts(numeric_facts)

    sub_columns = [
        column
        for column in (
            "adsh", "cik", "name", "form", "fy", "fp",
            "period_date", "filed_date", "accepted_at",
        )
        if column in submissions.columns
    ]

    joined = mapped.merge(
        submissions[sub_columns],
        on="adsh",
        how="inner",
        validate="many_to_one",
    )

    supported = joined.loc[joined["form"].isin(_ALLOWED_FORMS)].copy()

    if "coreg" in supported.columns:
        coreg = supported["coreg"].fillna("").astype(str).str.strip()
        consolidated = supported.loc[coreg.eq("")].copy()
    else:
        consolidated = supported.copy()

    statement_matched = consolidated.copy()
    if presentation is not None:
        pre = presentation[["adsh", "tag", "version", "stmt"]].drop_duplicates()
        statement_matched = consolidated.merge(
            pre,
            on=["adsh", "tag", "version"],
            how="inner",
        )
        expected = statement_matched.apply(
            lambda row: row["stmt"] in _EXPECTED_STATEMENTS.get(row["concept"], set()),
            axis=1,
        )
        statement_matched = statement_matched.loc[expected].copy()

    qtrs = pd.to_numeric(statement_matched["qtrs"], errors="coerce")
    instant_mask = statement_matched["concept"].isin(_INSTANT_CONCEPTS) & qtrs.eq(0)
    duration_mask = statement_matched["concept"].isin(_DURATION_CONCEPTS) & qtrs.gt(0)
    period_matched = statement_matched.loc[instant_mask | duration_mask].copy()

    key_columns = [
        column
        for column in (
            "cik", "concept", "ddate_date", "qtrs", "uom", "accepted_at",
        )
        if column in period_matched.columns
    ]

    duplicate_groups = 0
    if key_columns:
        sizes = period_matched.groupby(key_columns, dropna=False).size()
        duplicate_groups = int((sizes > 1).sum())

    sort_columns = [
        column
        for column in ("cik", "accepted_at", "concept", "ddate_date", "qtrs")
        if column in period_matched.columns
    ]
    if sort_columns:
        period_matched = period_matched.sort_values(sort_columns).reset_index(drop=True)

    audit = CanonicalFactAudit(
        rows_input=len(numeric_facts),
        rows_mapped=len(mapped),
        rows_supported_forms=len(supported),
        rows_consolidated=len(consolidated),
        rows_statement_matched=len(statement_matched),
        rows_period_matched=len(period_matched),
        duplicate_groups=duplicate_groups,
    )

    return period_matched, audit


def facts_available_by(
    canonical_facts: pd.DataFrame,
    as_of: datetime,
) -> pd.DataFrame:
    if "accepted_at" not in canonical_facts.columns:
        raise ValueError("canonical facts missing accepted_at")

    return canonical_facts.loc[
        canonical_facts["accepted_at"].notna()
        & (canonical_facts["accepted_at"] <= as_of)
    ].copy()
