from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from finance.factors import (
    add_financial_health_factors,
    add_growth_factors,
    add_momentum_factors,
    add_quality_factors,
    add_stability_factors,
    add_valuation_factors,
    validate_raw_factors,
)
from finance.models import add_long_growth_v1_scores
from finance.scoring import add_family_scores, normalize_validated_factors


@dataclass(frozen=True)
class V1V2ImpactSummary:
    universe_rows: int
    baseline_shares_present: int
    challenger_shares_present: int
    shares_gained: int
    shares_lost: int
    baseline_valuation_available: int
    challenger_valuation_available: int
    valuation_gained: int
    valuation_lost: int
    baseline_top_conviction_eligible: int
    challenger_top_conviction_eligible: int
    top_conviction_gained: int
    top_conviction_lost: int
    baseline_top10_count: int
    challenger_top10_count: int
    top10_overlap: int
    top10_entered: int
    top10_exited: int
    pit_violations: int


def score_long_growth_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """Apply the frozen V1 factor/scoring stack to a research panel."""

    scored = add_quality_factors(panel)
    scored = add_financial_health_factors(scored)
    scored = add_growth_factors(scored)
    scored = add_valuation_factors(scored)
    scored = add_stability_factors(scored)
    scored = add_momentum_factors(scored)
    scored = validate_raw_factors(scored)
    scored = normalize_validated_factors(scored)
    scored = add_family_scores(scored)
    return add_long_growth_v1_scores(scored)


def _snapshot(frame: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    result = frame.copy()
    result["decision_date"] = pd.to_datetime(
        result["decision_date"], errors="coerce"
    ).dt.normalize()
    result = result.loc[result["decision_date"].eq(as_of.normalize())].copy()
    if result["ticker"].duplicated().any():
        duplicates = sorted(result.loc[result["ticker"].duplicated(), "ticker"].unique())
        raise ValueError("Duplicate current tickers: " + ", ".join(duplicates))
    return result


def _rank_snapshot(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["selection_rank"] = pd.NA
    result["top10"] = False
    eligible = result.loc[
        result["top_conviction_eligible"].fillna(False).astype(bool)
        & pd.to_numeric(result["long_growth_v1_score"], errors="coerce").notna()
    ].sort_values(
        ["long_growth_v1_score", "ticker"],
        ascending=[False, True],
        kind="mergesort",
    )
    ranks = pd.Series(range(1, len(eligible) + 1), index=eligible.index)
    result.loc[eligible.index, "selection_rank"] = ranks
    result.loc[eligible.head(10).index, "top10"] = True
    return result


def _present(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").gt(0)


def _state_change(before: pd.Series, after: pd.Series) -> pd.Series:
    return pd.Series(
        [
            "gained" if not left and right else
            "lost" if left and not right else
            "unchanged_present" if left and right else
            "unchanged_missing"
            for left, right in zip(before, after)
        ],
        index=before.index,
        dtype="object",
    )


def compare_v1_v2_impact(
    *,
    baseline_snapshot: pd.DataFrame,
    challenger_snapshot: pd.DataFrame,
    baseline_scored: pd.DataFrame,
    challenger_scored: pd.DataFrame,
    as_of: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, V1V2ImpactSummary]:
    """Compare same-input exact-only and V2 DEI-fallback current states."""

    base_scores = _rank_snapshot(_snapshot(baseline_scored, as_of))
    chal_scores = _rank_snapshot(_snapshot(challenger_scored, as_of))

    provenance = [
        "shares_outstanding",
        "shares_outstanding_period_date",
        "shares_outstanding_filing_period_date",
        "shares_outstanding_filed_date",
        "shares_outstanding_accepted_at",
        "shares_outstanding_form",
        "shares_outstanding_source_tag",
        "shares_outstanding_adsh",
    ]
    metrics = [
        "market_cap",
        "valuation_score",
        "top_conviction_eligible",
        "long_growth_v1_score",
        "selection_rank",
        "top10",
    ]

    def select_snapshot(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
        available = [column for column in provenance if column in frame.columns]
        selected = frame[["ticker", "cik", "company_name", *available]].copy()
        return selected.rename(
            columns={
                column: f"{prefix}_{column}"
                for column in ["cik", "company_name", *available]
            }
        )

    def select_scores(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
        available = [column for column in metrics if column in frame.columns]
        selected = frame[["ticker", *available]].copy()
        return selected.rename(
            columns={column: f"{prefix}_{column}" for column in available}
        )

    detail = select_snapshot(baseline_snapshot, "baseline").merge(
        select_snapshot(challenger_snapshot, "challenger"),
        on="ticker",
        how="outer",
        validate="one_to_one",
    )
    detail["cik"] = detail["baseline_cik"].combine_first(detail["challenger_cik"])
    detail["company_name"] = detail["baseline_company_name"].combine_first(
        detail["challenger_company_name"]
    )
    detail = detail.merge(
        select_scores(base_scores, "baseline"),
        on="ticker",
        how="left",
        validate="one_to_one",
    ).merge(
        select_scores(chal_scores, "challenger"),
        on="ticker",
        how="left",
        validate="one_to_one",
    )

    base_shares = _present(detail["baseline_shares_outstanding"])
    chal_shares = _present(detail["challenger_shares_outstanding"])
    detail["shares_coverage_change"] = _state_change(base_shares, chal_shares)
    detail["shares_value_changed"] = (
        base_shares
        & chal_shares
        & pd.to_numeric(detail["baseline_shares_outstanding"], errors="coerce").ne(
            pd.to_numeric(detail["challenger_shares_outstanding"], errors="coerce")
        )
    )

    base_valuation = pd.to_numeric(
        detail["baseline_valuation_score"], errors="coerce"
    ).notna()
    chal_valuation = pd.to_numeric(
        detail["challenger_valuation_score"], errors="coerce"
    ).notna()
    detail["valuation_change"] = _state_change(base_valuation, chal_valuation)

    base_top = detail["baseline_top_conviction_eligible"].fillna(False).astype(bool)
    chal_top = detail["challenger_top_conviction_eligible"].fillna(False).astype(bool)
    detail["top_conviction_change"] = _state_change(base_top, chal_top)
    detail["score_change"] = (
        pd.to_numeric(detail["challenger_long_growth_v1_score"], errors="coerce")
        - pd.to_numeric(detail["baseline_long_growth_v1_score"], errors="coerce")
    )
    detail["rank_improvement"] = (
        pd.to_numeric(detail["baseline_selection_rank"], errors="coerce")
        - pd.to_numeric(detail["challenger_selection_rank"], errors="coerce")
    )
    detail = detail.sort_values("ticker", kind="stable").reset_index(drop=True)

    base_top10 = detail["baseline_top10"].fillna(False).astype(bool)
    chal_top10 = detail["challenger_top10"].fillna(False).astype(bool)
    top10 = detail.loc[base_top10 | chal_top10, [
        "ticker",
        "baseline_long_growth_v1_score",
        "challenger_long_growth_v1_score",
        "baseline_selection_rank",
        "challenger_selection_rank",
        "baseline_top10",
        "challenger_top10",
        "score_change",
        "rank_improvement",
    ]].copy()
    top10["top10_change"] = _state_change(base_top10.loc[top10.index], chal_top10.loc[top10.index])
    top10 = top10.sort_values(
        ["challenger_selection_rank", "baseline_selection_rank", "ticker"],
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)

    pit_rows: list[dict[str, object]] = []
    cutoff = as_of.normalize()
    for side in ("baseline", "challenger"):
        for field in (
            "shares_outstanding_accepted_at",
            "shares_outstanding_filed_date",
        ):
            column = f"{side}_{field}"
            values = pd.to_datetime(detail[column], errors="coerce")
            violations = values.dt.normalize().gt(cutoff)
            for index in detail.index[violations]:
                pit_rows.append(
                    {
                        "ticker": detail.at[index, "ticker"],
                        "side": side,
                        "field": field,
                        "value": detail.at[index, column],
                        "decision_date": cutoff.date().isoformat(),
                        "status": "future_dated",
                    }
                )
    pit_audit = pd.DataFrame(
        pit_rows,
        columns=["ticker", "side", "field", "value", "decision_date", "status"],
    )

    summary = V1V2ImpactSummary(
        universe_rows=len(detail),
        baseline_shares_present=int(base_shares.sum()),
        challenger_shares_present=int(chal_shares.sum()),
        shares_gained=int((~base_shares & chal_shares).sum()),
        shares_lost=int((base_shares & ~chal_shares).sum()),
        baseline_valuation_available=int(base_valuation.sum()),
        challenger_valuation_available=int(chal_valuation.sum()),
        valuation_gained=int((~base_valuation & chal_valuation).sum()),
        valuation_lost=int((base_valuation & ~chal_valuation).sum()),
        baseline_top_conviction_eligible=int(base_top.sum()),
        challenger_top_conviction_eligible=int(chal_top.sum()),
        top_conviction_gained=int((~base_top & chal_top).sum()),
        top_conviction_lost=int((base_top & ~chal_top).sum()),
        baseline_top10_count=int(base_top10.sum()),
        challenger_top10_count=int(chal_top10.sum()),
        top10_overlap=int((base_top10 & chal_top10).sum()),
        top10_entered=int((~base_top10 & chal_top10).sum()),
        top10_exited=int((base_top10 & ~chal_top10).sum()),
        pit_violations=len(pit_audit),
    )
    return detail, top10, pit_audit, summary


def summary_dict(summary: V1V2ImpactSummary) -> dict[str, int]:
    return asdict(summary)
