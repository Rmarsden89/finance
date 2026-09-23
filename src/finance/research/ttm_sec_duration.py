from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from finance.data.sec_concepts import map_canonical_facts
from finance.data.sec_winners import select_canonical_winners


_ALLOWED_FORMS = {
    "10-K", "10-K/A", "10-Q", "10-Q/A",
    "20-F", "20-F/A", "40-F", "40-F/A",
}
_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
_QUARTERLY_FORMS = {"10-Q", "10-Q/A"}

_EXPECTED_STATEMENTS: dict[str, set[str]] = {
    "revenue": {"IS"},
    "net_income": {"IS", "CI"},
    "operating_cash_flow": {"CF"},
    "capital_expenditures": {"CF"},
}

_TTM_DURATION_CONCEPTS = set(_EXPECTED_STATEMENTS)
_INCOME_CONCEPTS = {"revenue", "net_income"}
_YTD_CASH_FLOW_CONCEPTS = {"operating_cash_flow", "capital_expenditures"}
_FP_TO_YTD_QTRS = {"Q1": 1, "Q2": 2, "Q3": 3}


@dataclass(frozen=True)
class TtmDurationAudit:
    rows_input: int
    rows_mapped: int
    rows_supported_forms: int
    rows_consolidated: int
    rows_statement_matched: int
    rows_period_matched: int
    rows_current_period: int
    rows_numeric_value: int
    rows_output: int
    winner_duplicate_groups: int
    winner_unresolved_groups: int


def build_ttm_duration_candidates(
    submissions: pd.DataFrame,
    numeric_facts: pd.DataFrame,
    presentation: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Build V2-only duration candidates required for discrete-quarter/TTM research.

    This intentionally preserves more duration representations than the frozen
    V1 canonical pipeline. Revenue and net income retain both direct qtrs=1
    quarter facts and compatible Q2/Q3 YTD facts. OCF and capex retain their
    expected YTD representations. Annual qtrs=4 facts are retained for all
    supported TTM concepts.

    No V1 canonical code or artifacts are modified.
    """

    mapped = map_canonical_facts(numeric_facts)
    mapped = mapped.loc[mapped["concept"].isin(_TTM_DURATION_CONCEPTS)].copy()

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
    if "segments" in consolidated.columns:
        segments = consolidated["segments"].fillna("").astype(str).str.strip()
        consolidated = consolidated.loc[segments.eq("")].copy()

    statement_matched = consolidated.copy()
    if presentation is not None:
        pre = presentation[["adsh", "tag", "version", "stmt"]].drop_duplicates()
        allowed_keys: set[tuple[str, str, str]] = set()
        for concept, statements in _EXPECTED_STATEMENTS.items():
            tags = set(
                consolidated.loc[
                    consolidated["concept"].eq(concept), "tag"
                ].dropna()
            )
            if not tags:
                continue
            subset = pre.loc[
                pre["tag"].isin(tags)
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
    fp = statement_matched.get(
        "fp", pd.Series("", index=statement_matched.index)
    ).fillna("").astype(str).str.upper()

    annual_mask = (
        form.isin(_ANNUAL_FORMS)
        & concept.isin(_TTM_DURATION_CONCEPTS)
        & qtrs.eq(4)
    )

    quarterly_income_direct = (
        form.isin(_QUARTERLY_FORMS)
        & concept.isin(_INCOME_CONCEPTS)
        & qtrs.eq(1)
    )

    expected_ytd_qtrs = fp.map(_FP_TO_YTD_QTRS)
    quarterly_income_ytd = (
        form.isin(_QUARTERLY_FORMS)
        & concept.isin(_INCOME_CONCEPTS)
        & fp.isin({"Q2", "Q3"})
        & qtrs.eq(expected_ytd_qtrs)
    )

    quarterly_cash_flow_ytd = (
        form.isin(_QUARTERLY_FORMS)
        & concept.isin(_YTD_CASH_FLOW_CONCEPTS)
        & qtrs.eq(expected_ytd_qtrs)
    )

    period_matched = statement_matched.loc[
        annual_mask
        | quarterly_income_direct
        | quarterly_income_ytd
        | quarterly_cash_flow_ytd
    ].copy()

    current_period = period_matched.loc[
        period_matched["ddate_date"].eq(period_matched["period_date"])
    ].copy()
    numeric_value = current_period.loc[
        current_period["value"].notna()
    ].copy()

    sort_columns = [
        column
        for column in (
            "cik", "accepted_at", "concept", "ddate_date", "qtrs", "source_tag"
        )
        if column in numeric_value.columns
    ]
    if sort_columns:
        numeric_value = numeric_value.sort_values(
            sort_columns, kind="stable"
        ).reset_index(drop=True)

    stage_counts = {
        "rows_input": len(numeric_facts),
        "rows_mapped": len(mapped),
        "rows_supported_forms": len(supported),
        "rows_consolidated": len(consolidated),
        "rows_statement_matched": len(statement_matched),
        "rows_period_matched": len(period_matched),
        "rows_current_period": len(current_period),
        "rows_numeric_value": len(numeric_value),
    }
    return numeric_value, stage_counts


def build_ttm_duration_winners(
    submissions: pd.DataFrame,
    numeric_facts: pd.DataFrame,
    presentation: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, TtmDurationAudit]:
    """Build deterministic V2-only TTM duration winners with full provenance."""

    candidates, counts = build_ttm_duration_candidates(
        submissions,
        numeric_facts,
        presentation,
    )
    winners, winner_audit, winner_summary = select_canonical_winners(
        candidates
    )

    audit = TtmDurationAudit(
        **counts,
        rows_output=len(winners),
        winner_duplicate_groups=winner_summary.duplicate_groups_input,
        winner_unresolved_groups=winner_summary.unresolved_groups,
    )
    return winners, winner_audit, audit
