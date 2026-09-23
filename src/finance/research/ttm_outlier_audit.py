from __future__ import annotations

import numpy as np
import pandas as pd


ANNUAL_TTM_PAIRS = {
    "revenue": ("annual_revenue", "ttm_revenue"),
    "net_income": ("annual_net_income", "ttm_net_income"),
    "operating_cash_flow": (
        "annual_operating_cash_flow",
        "ttm_operating_cash_flow",
    ),
    "capital_expenditures": (
        "annual_capital_expenditures",
        "ttm_capital_expenditures",
    ),
    "free_cash_flow": ("annual_free_cash_flow", "ttm_free_cash_flow"),
}


def flag_annual_ttm_outliers(
    comparison: pd.DataFrame,
    *,
    ratio_low: float = 0.5,
    ratio_high: float = 2.0,
    relative_change_threshold: float = 1.0,
) -> pd.DataFrame:
    """Return review-only annual-vs-TTM outliers without modifying values."""

    rows: list[dict[str, object]] = []
    for metric, (annual_col, ttm_col) in ANNUAL_TTM_PAIRS.items():
        annual = pd.to_numeric(comparison[annual_col], errors="coerce")
        ttm = pd.to_numeric(comparison[ttm_col], errors="coerce")
        both = annual.notna() & ttm.notna()
        sign_change = both & annual.ne(0) & ttm.ne(0) & (
            np.sign(annual) != np.sign(ttm)
        )
        positive_ratio = pd.Series(np.nan, index=comparison.index)
        positive_base = both & annual.gt(0)
        positive_ratio.loc[positive_base] = (
            ttm.loc[positive_base] / annual.loc[positive_base]
        )
        ratio_outlier = positive_base & (
            positive_ratio.lt(ratio_low) | positive_ratio.gt(ratio_high)
        )
        relative_change = pd.Series(np.nan, index=comparison.index)
        nonzero = both & annual.ne(0)
        relative_change.loc[nonzero] = (
            (ttm.loc[nonzero] - annual.loc[nonzero]).abs()
            / annual.loc[nonzero].abs()
        )
        change_outlier = nonzero & relative_change.gt(
            relative_change_threshold
        )
        flagged = sign_change | ratio_outlier | change_outlier

        for idx in comparison.index[flagged]:
            reasons: list[str] = []
            if sign_change.at[idx]:
                reasons.append("sign_change")
            if ratio_outlier.at[idx]:
                reasons.append("positive_ratio_outside_0.5x_2.0x")
            if change_outlier.at[idx]:
                reasons.append("absolute_change_gt_100pct_of_annual")
            rows.append(
                {
                    "ticker": comparison.at[idx, "ticker"],
                    "cik": comparison.at[idx, "cik"],
                    "company_name": comparison.at[idx, "company_name"],
                    "metric": metric,
                    "annual_value": annual.at[idx],
                    "ttm_value": ttm.at[idx],
                    "ttm_minus_annual": ttm.at[idx] - annual.at[idx],
                    "ttm_to_annual": positive_ratio.at[idx],
                    "relative_change_abs": relative_change.at[idx],
                    "sign_change": bool(sign_change.at[idx]),
                    "flag_reasons": "|".join(reasons),
                    "review_status": "needs_lineage_review",
                }
            )

    columns = [
        "ticker", "cik", "company_name", "metric", "annual_value",
        "ttm_value", "ttm_minus_annual", "ttm_to_annual",
        "relative_change_abs", "sign_change", "flag_reasons",
        "review_status",
    ]
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["metric", "relative_change_abs", "ticker"],
        ascending=[True, False, True],
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)


def reconcile_q4_ttm_to_reported_annual(
    ttm_values: pd.DataFrame,
    duration_winners: pd.DataFrame,
    *,
    cutoff: pd.Timestamp,
) -> pd.DataFrame:
    """Compare Q4-ending TTM totals with PIT-visible reported annual facts."""

    annual = duration_winners.copy()
    annual["accepted_at"] = pd.to_datetime(
        annual["accepted_at"], errors="coerce"
    )
    naive_cutoff = pd.Timestamp(cutoff)
    if naive_cutoff.tzinfo is not None:
        naive_cutoff = naive_cutoff.tz_localize(None)
    annual = annual.loc[
        annual["accepted_at"].notna()
        & annual["accepted_at"].le(naive_cutoff)
        & pd.to_numeric(annual["qtrs"], errors="coerce").eq(4)
        & annual["fp"].astype(str).str.upper().eq("FY")
    ].copy()
    annual["fy"] = pd.to_numeric(annual["fy"], errors="coerce")
    annual["value"] = pd.to_numeric(annual["value"], errors="coerce")
    annual["uom"] = annual["uom"].astype(str).str.upper().str.strip()
    keys = ["cik", "concept", "fy", "uom"]
    annual = (
        annual.sort_values([*keys, "accepted_at"], kind="stable")
        .drop_duplicates(keys, keep="last")
    )

    q4 = ttm_values.loc[
        ttm_values["ttm_end_quarter"].astype(str).eq("Q4")
    ].copy()
    q4["ttm_end_fy"] = pd.to_numeric(q4["ttm_end_fy"], errors="coerce")
    q4["uom"] = q4["uom"].astype(str).str.upper().str.strip()
    merged = q4.merge(
        annual[
            [
                "cik", "concept", "fy", "uom", "value",
                "accepted_at", "adsh", "source_tag",
            ]
        ],
        left_on=["cik", "concept", "ttm_end_fy", "uom"],
        right_on=keys,
        how="left",
        suffixes=("_ttm", "_annual"),
    )
    merged = merged.rename(
        columns={
            "value": "reported_annual_value",
            "accepted_at_annual": "reported_annual_accepted_at",
            "adsh": "reported_annual_adsh",
            "source_tag": "reported_annual_source_tag",
        }
    )
    reported = pd.to_numeric(
        merged["reported_annual_value"], errors="coerce"
    )
    ttm = pd.to_numeric(merged["ttm_value"], errors="coerce")
    merged["difference"] = ttm - reported
    denominator = reported.abs().where(reported.ne(0))
    merged["relative_difference_abs"] = (
        merged["difference"].abs() / denominator
    )
    merged["reported_annual_present"] = reported.notna()
    tolerance = np.maximum(1.0, reported.abs() * 1e-9)
    merged["exact_within_numeric_tolerance"] = (
        reported.notna() & merged["difference"].abs().le(tolerance)
    )
    merged["material_difference_gt_1pct"] = (
        reported.notna()
        & reported.ne(0)
        & merged["relative_difference_abs"].gt(0.01)
    )
    return merged


def summarize_q4_reconciliation(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame(
            columns=[
                "concept", "q4_ttm_rows", "reported_annual_present",
                "exact_within_numeric_tolerance",
                "material_difference_gt_1pct",
            ]
        )
    return (
        detail.groupby("concept", as_index=False)
        .agg(
            q4_ttm_rows=("cik", "size"),
            reported_annual_present=("reported_annual_present", "sum"),
            exact_within_numeric_tolerance=(
                "exact_within_numeric_tolerance", "sum"
            ),
            material_difference_gt_1pct=(
                "material_difference_gt_1pct", "sum"
            ),
        )
        .sort_values("concept", kind="stable")
        .reset_index(drop=True)
    )


def current_missing_ttm_coverage(
    current_snapshot: pd.DataFrame,
    current_numerators: pd.DataFrame,
    quarters: pd.DataFrame,
    construction_audit: pd.DataFrame,
) -> pd.DataFrame:
    """Explain current missing TTM inputs without imputing any value."""

    universe = current_snapshot[
        ["ticker", "cik", "company_name"]
    ].drop_duplicates("ticker")
    current = universe.merge(
        current_numerators,
        on=["ticker", "cik", "company_name"],
        how="left",
        validate="one_to_one",
    )

    metric_map = {
        "revenue": "ttm_revenue",
        "net_income": "ttm_net_income",
        "operating_cash_flow": "ttm_operating_cash_flow",
        "capital_expenditures": "ttm_capital_expenditures",
    }
    quarter_counts = (
        quarters.groupby(["cik", "concept"], as_index=False)
        .size()
        .rename(columns={"size": "discrete_quarter_rows"})
    )
    latest_audit = construction_audit.copy()
    if not latest_audit.empty:
        latest_audit["ttm_end_date"] = pd.to_datetime(
            latest_audit["ttm_end_date"], errors="coerce"
        )
        latest_audit = (
            latest_audit.sort_values(
                ["cik", "concept", "ttm_end_date"], kind="stable"
            )
            .groupby(["cik", "concept"], as_index=False, sort=False)
            .tail(1)
            [["cik", "concept", "reason"]]
            .rename(columns={"reason": "latest_ttm_audit_reason"})
        )

    rows: list[pd.DataFrame] = []
    for concept, column in metric_map.items():
        missing = current.loc[
            pd.to_numeric(current[column], errors="coerce").isna(),
            ["ticker", "cik", "company_name"],
        ].copy()
        missing["metric"] = concept
        missing = missing.merge(
            quarter_counts.loc[
                quarter_counts["concept"].eq(concept),
                ["cik", "discrete_quarter_rows"],
            ],
            on="cik",
            how="left",
        )
        if not latest_audit.empty:
            missing = missing.merge(
                latest_audit.loc[
                    latest_audit["concept"].eq(concept),
                    ["cik", "latest_ttm_audit_reason"],
                ],
                on="cik",
                how="left",
            )
        else:
            missing["latest_ttm_audit_reason"] = pd.NA
        missing["discrete_quarter_rows"] = (
            missing["discrete_quarter_rows"].fillna(0).astype(int)
        )
        missing["missing_reason"] = np.where(
            missing["discrete_quarter_rows"].eq(0),
            "no_discrete_quarter_history",
            np.where(
                missing["discrete_quarter_rows"].lt(4),
                "fewer_than_four_discrete_quarters",
                missing["latest_ttm_audit_reason"].fillna(
                    "no_valid_consecutive_ttm_window"
                ),
            ),
        )
        rows.append(missing)

    cash_missing = current.loc[
        pd.to_numeric(
            current["ttm_free_cash_flow"], errors="coerce"
        ).isna(),
        ["ticker", "cik", "company_name",
         "ttm_operating_cash_flow", "ttm_capital_expenditures"],
    ].copy()
    cash_missing["metric"] = "free_cash_flow"
    cash_missing["discrete_quarter_rows"] = pd.NA
    cash_missing["latest_ttm_audit_reason"] = pd.NA
    ocf_present = pd.to_numeric(
        cash_missing["ttm_operating_cash_flow"], errors="coerce"
    ).notna()
    capex_present = pd.to_numeric(
        cash_missing["ttm_capital_expenditures"], errors="coerce"
    ).notna()
    cash_missing["missing_reason"] = np.select(
        [
            ~ocf_present & ~capex_present,
            ~ocf_present & capex_present,
            ocf_present & ~capex_present,
        ],
        [
            "missing_ttm_ocf_and_capex",
            "missing_ttm_ocf",
            "missing_ttm_capex",
        ],
        default="cash_flow_ttm_endpoint_mismatch",
    )
    rows.append(
        cash_missing[
            [
                "ticker", "cik", "company_name", "metric",
                "discrete_quarter_rows", "latest_ttm_audit_reason",
                "missing_reason",
            ]
        ]
    )

    return pd.concat(rows, ignore_index=True).sort_values(
        ["metric", "missing_reason", "ticker"], kind="stable"
    ).reset_index(drop=True)
