from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .sec_concepts import map_canonical_facts


_ALLOWED_FORMS = {
    "10-K", "10-K/A", "10-Q", "10-Q/A",
    "20-F", "20-F/A", "40-F", "40-F/A",
}

_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
_QUARTERLY_FORMS = {"10-Q", "10-Q/A"}

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

_QUARTERLY_INCOME_CONCEPTS = {
    "revenue",
    "net_income",
    "operating_income",
}

_QUARTERLY_YTD_CONCEPTS = {
    "operating_cash_flow",
    "capital_expenditures",
}

_DURATION_CONCEPTS = _QUARTERLY_INCOME_CONCEPTS | _QUARTERLY_YTD_CONCEPTS

_FP_TO_YTD_QTRS = {
    "Q1": 1,
    "Q2": 2,
    "Q3": 3,
}


@dataclass(frozen=True)
class CanonicalFactAudit:
    rows_input: int
    rows_mapped: int
    rows_supported_forms: int
    rows_consolidated: int
    rows_statement_matched: int
    rows_period_matched: int
    rows_current_period: int
    rows_numeric_value: int
    duplicate_groups: int


def build_canonical_facts(
    submissions: pd.DataFrame,
    numeric_facts: pd.DataFrame,
    presentation: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, CanonicalFactAudit]:
    """Build conservative PIT-ready canonical SEC facts.

    Filters:
      - curated canonical concepts
      - supported primary financial-statement forms
      - consolidated entity facts
      - concept-appropriate statement placement
      - concept/form-appropriate instant or duration context
      - fact end date aligned to the filing's reported period

    Remaining duplicates are retained for the later tag/amendment winner stage.
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

    consolidated = supported.copy()

    if "coreg" in consolidated.columns:
        coreg = consolidated["coreg"].fillna("").astype(str).str.strip()
        consolidated = consolidated.loc[coreg.eq("")].copy()

    # The SEC's reprocessed Financial Statement Data Sets include dimensional
    # primary-statement facts in NUM via the segments field.  For canonical
    # whole-company totals, retain only non-dimensional facts.  Segment/member
    # facts remain valuable for later analysis but must not compete with the
    # consolidated company-level value here.
    if "segments" in consolidated.columns:
        segments = consolidated["segments"].fillna("").astype(str).str.strip()
        consolidated = consolidated.loc[segments.eq("")].copy()

    statement_matched = consolidated.copy()
    if presentation is not None:
        pre = presentation[["adsh", "tag", "version", "stmt"]].drop_duplicates()

        allowed_keys: set[tuple[str, str, str]] = set()
        for concept, statements in _EXPECTED_STATEMENTS.items():
            concept_tags = set(
                consolidated.loc[
                    consolidated["concept"].eq(concept),
                    "tag",
                ].dropna()
            )
            if not concept_tags:
                continue

            subset = pre.loc[
                pre["tag"].isin(concept_tags)
                & pre["stmt"].isin(statements),
                ["adsh", "tag", "version"],
            ].drop_duplicates()

            allowed_keys.update(
                tuple(row)
                for row in subset.itertuples(index=False, name=None)
            )

        keep = [
            (adsh, tag, version) in allowed_keys
            for adsh, tag, version in consolidated[
                ["adsh", "tag", "version"]
            ].itertuples(index=False, name=None)
        ]
        statement_matched = consolidated.loc[keep].copy()

    qtrs = pd.to_numeric(statement_matched["qtrs"], errors="coerce")
    form = statement_matched["form"].astype(str)
    concept = statement_matched["concept"].astype(str)
    fp = statement_matched.get("fp", pd.Series("", index=statement_matched.index))
    fp = fp.fillna("").astype(str).str.upper()

    instant_mask = concept.isin(_INSTANT_CONCEPTS) & qtrs.eq(0)

    annual_duration_mask = (
        form.isin(_ANNUAL_FORMS)
        & concept.isin(_DURATION_CONCEPTS)
        & qtrs.eq(4)
    )

    quarterly_income_mask = (
        form.isin(_QUARTERLY_FORMS)
        & concept.isin(_QUARTERLY_INCOME_CONCEPTS)
        & qtrs.eq(1)
    )

    expected_ytd_qtrs = fp.map(_FP_TO_YTD_QTRS)
    quarterly_ytd_mask = (
        form.isin(_QUARTERLY_FORMS)
        & concept.isin(_QUARTERLY_YTD_CONCEPTS)
        & qtrs.eq(expected_ytd_qtrs)
    )

    period_matched = statement_matched.loc[
        instant_mask
        | annual_duration_mask
        | quarterly_income_mask
        | quarterly_ytd_mask
    ].copy()

    current_period = period_matched.loc[
        period_matched["ddate_date"].eq(period_matched["period_date"])
    ].copy()

    numeric_value = current_period.loc[
        current_period["value"].notna()
    ].copy()

    key_columns = [
        column
        for column in (
            "cik", "concept", "ddate_date", "qtrs", "uom", "accepted_at",
        )
        if column in numeric_value.columns
    ]

    duplicate_groups = 0
    if key_columns:
        sizes = numeric_value.groupby(key_columns, dropna=False).size()
        duplicate_groups = int((sizes > 1).sum())

    sort_columns = [
        column
        for column in ("cik", "accepted_at", "concept", "ddate_date", "qtrs")
        if column in numeric_value.columns
    ]
    if sort_columns:
        numeric_value = numeric_value.sort_values(
            sort_columns
        ).reset_index(drop=True)

    audit = CanonicalFactAudit(
        rows_input=len(numeric_facts),
        rows_mapped=len(mapped),
        rows_supported_forms=len(supported),
        rows_consolidated=len(consolidated),
        rows_statement_matched=len(statement_matched),
        rows_period_matched=len(period_matched),
        rows_current_period=len(current_period),
        rows_numeric_value=len(numeric_value),
        duplicate_groups=duplicate_groups,
    )

    return numeric_value, audit


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
