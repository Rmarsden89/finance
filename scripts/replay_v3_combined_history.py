from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import numpy as np
import pandas as pd

from finance.research.missingness_bias import (
    compare_variants,
    forward_return_analysis,
)
from finance.research.v2 import resolve_v2_sec_artifact_paths
from finance.research.v2_impact import score_long_growth_panel
from finance.research.v3 import (
    V3_LIABILITIES_RULE,
    V3_SHARES_RULE,
    validate_v3_shares_freeze,
    v3_liabilities_approved_ciks,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay the frozen V3 liabilities and shares rules together across "
            "the frozen historical weekly panel. V1 and V2 remain read-only."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.fillna("").astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes"}
    )


def _positive(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").gt(0)


def _top10_weekly_forward_returns(scored: pd.DataFrame) -> pd.DataFrame:
    detail, _ = forward_return_analysis(scored, horizons=(1,))
    if detail.empty:
        return pd.DataFrame(
            columns=["decision_date", "top10_count", "mean_forward_return"]
        )
    selected = detail.loc[_bool(detail["top10"])].copy()
    return (
        selected.groupby("decision_date", as_index=False)
        .agg(
            top10_count=("ticker", "size"),
            mean_forward_return=("forward_return", "mean"),
        )
        .sort_values("decision_date", kind="stable")
    )


def _outcome_summary(
    baseline: pd.DataFrame,
    challenger: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    left = _top10_weekly_forward_returns(baseline).rename(
        columns={
            "top10_count": "baseline_top10_count",
            "mean_forward_return": "baseline_return",
        }
    )
    right = _top10_weekly_forward_returns(challenger).rename(
        columns={
            "top10_count": "v3_top10_count",
            "mean_forward_return": "v3_return",
        }
    )
    weekly = left.merge(
        right, on="decision_date", how="inner", validate="one_to_one"
    )
    weekly = weekly.loc[
        weekly["baseline_top10_count"].eq(10)
        & weekly["v3_top10_count"].eq(10)
    ].copy()
    weekly["return_delta"] = weekly["v3_return"] - weekly["baseline_return"]

    if weekly.empty:
        return weekly, {
            "comparable_weeks": 0,
            "baseline_mean_weekly_top10_return": None,
            "v3_mean_weekly_top10_return": None,
            "mean_weekly_return_delta": None,
            "baseline_compounded_return": None,
            "v3_compounded_return": None,
        }

    return weekly, {
        "comparable_weeks": int(len(weekly)),
        "baseline_mean_weekly_top10_return": float(
            weekly["baseline_return"].mean()
        ),
        "v3_mean_weekly_top10_return": float(weekly["v3_return"].mean()),
        "mean_weekly_return_delta": float(weekly["return_delta"].mean()),
        "baseline_compounded_return": float(
            (1.0 + weekly["baseline_return"]).prod() - 1.0
        ),
        "v3_compounded_return": float(
            (1.0 + weekly["v3_return"]).prod() - 1.0
        ),
    }


def _coverage_by_year(
    baseline: pd.DataFrame,
    challenger: pd.DataFrame,
) -> pd.DataFrame:
    left = baseline.copy()
    right = challenger.copy()
    left["decision_date"] = pd.to_datetime(
        left["decision_date"], errors="coerce"
    )
    right["decision_date"] = pd.to_datetime(
        right["decision_date"], errors="coerce"
    )
    left["year"] = left["decision_date"].dt.year
    right["year"] = right["decision_date"].dt.year

    left["_shares"] = _positive(left["shares_outstanding"])
    right["_shares"] = _positive(right["shares_outstanding"])
    left["_liabilities"] = _positive(left["total_liabilities"])
    right["_liabilities"] = _positive(right["total_liabilities"])

    base = left.groupby("year", as_index=False).agg(
        rows=("ticker", "size"),
        baseline_shares_present=("_shares", "sum"),
        baseline_liabilities_present=("_liabilities", "sum"),
    )
    chal = right.groupby("year", as_index=False).agg(
        v3_shares_present=("_shares", "sum"),
        v3_liabilities_present=("_liabilities", "sum"),
    )
    out = base.merge(chal, on="year", how="outer", validate="one_to_one")
    out["shares_gain"] = (
        out["v3_shares_present"] - out["baseline_shares_present"]
    )
    out["liabilities_gain"] = (
        out["v3_liabilities_present"] - out["baseline_liabilities_present"]
    )
    return out.sort_values("year", kind="stable")


def _eligibility_by_year(
    baseline: pd.DataFrame,
    challenger: pd.DataFrame,
) -> pd.DataFrame:
    def summarize(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
        temp = frame.copy()
        temp["decision_date"] = pd.to_datetime(
            temp["decision_date"], errors="coerce"
        )
        temp["year"] = temp["decision_date"].dt.year
        temp["_health"] = pd.to_numeric(
            temp["financial_health_score"], errors="coerce"
        ).notna()
        temp["_valuation"] = pd.to_numeric(
            temp["valuation_score"], errors="coerce"
        ).notna()
        temp["_top"] = _bool(temp["top_conviction_eligible"])
        return temp.groupby("year", as_index=False).agg(
            **{
                f"{prefix}_health_eligible": ("_health", "sum"),
                f"{prefix}_valuation_eligible": ("_valuation", "sum"),
                f"{prefix}_top_conviction_eligible": ("_top", "sum"),
            }
        )

    return summarize(baseline, "baseline").merge(
        summarize(challenger, "v3"),
        on="year",
        how="outer",
        validate="one_to_one",
    ).sort_values("year", kind="stable")


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    evidence_date = args.as_of.isoformat()

    V3_LIABILITIES_RULE.validate()
    validate_v3_shares_freeze()
    if evidence_date != V3_LIABILITIES_RULE.evidence_as_of:
        raise SystemExit(
            "Frozen V3 liabilities evidence date is "
            f"{V3_LIABILITIES_RULE.evidence_as_of}; got {evidence_date}"
        )
    if evidence_date != V3_SHARES_RULE.evidence_as_of:
        raise SystemExit(
            "Frozen V3 shares evidence date is "
            f"{V3_SHARES_RULE.evidence_as_of}; got {evidence_date}"
        )

    base_dir = root / "reports" / "v3" / "data_sources" / evidence_date
    liabilities_path = (
        base_dir
        / "liabilities_historical_validation"
        / "pit_replay_detail.csv"
    )
    shares_path = (
        base_dir
        / "shares_historical_replay"
        / "pit_replay_detail.csv"
    )
    for path in (liabilities_path, shares_path):
        if not path.exists():
            raise SystemExit(f"Missing validated V3 replay artifact: {path}")

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    historical_value = manifest.get("input_artifacts", {}).get("historical panel")
    if not historical_value:
        raise SystemExit("V2 manifest lacks historical panel input")
    historical_path = Path(historical_value)
    if not historical_path.is_absolute():
        historical_path = root / historical_path
    if not historical_path.exists():
        raise SystemExit(f"Missing frozen historical panel: {historical_path}")

    baseline = pd.read_csv(historical_path, low_memory=False)
    baseline["decision_date"] = pd.to_datetime(
        baseline["decision_date"], errors="coerce"
    ).dt.normalize()
    baseline["cik"] = pd.to_numeric(
        baseline["cik"], errors="coerce"
    ).astype("Int64")
    if baseline[["decision_date", "ticker"]].duplicated().any():
        raise SystemExit("Historical panel has duplicate decision_date/ticker rows")

    liabilities = pd.read_csv(liabilities_path, low_memory=False)
    liabilities["decision_date"] = pd.to_datetime(
        liabilities["decision_date"], errors="coerce"
    ).dt.normalize()
    liabilities["cik"] = pd.to_numeric(
        liabilities["cik"], errors="coerce"
    ).astype("Int64")
    liabilities["constructed_liabilities"] = pd.to_numeric(
        liabilities["constructed_liabilities"], errors="coerce"
    )
    liabilities["selected_available_at"] = pd.to_datetime(
        liabilities["selected_available_at"], errors="coerce", utc=True
    )
    liabilities["decision_cutoff"] = pd.to_datetime(
        liabilities["decision_cutoff"], errors="coerce", utc=True
    )
    approved_ciks = set(v3_liabilities_approved_ciks())
    liabilities = liabilities.loc[
        liabilities["cik"].isin(approved_ciks)
        & liabilities["constructed_liabilities"].gt(0)
    ].copy()
    liabilities_future = liabilities.loc[
        liabilities["selected_available_at"].notna()
        & liabilities["decision_cutoff"].notna()
        & liabilities["selected_available_at"].gt(
            liabilities["decision_cutoff"]
        )
    ]
    if not liabilities_future.empty:
        raise SystemExit(
            "PIT violation in frozen liabilities replay input: "
            f"{len(liabilities_future)} rows"
        )
    liabilities = (
        liabilities.sort_values(
            ["decision_date", "cik", "selected_available_at"],
            kind="stable",
        )
        .drop_duplicates(["decision_date", "cik"], keep="last")
        .reset_index(drop=True)
    )

    shares = pd.read_csv(shares_path, low_memory=False)
    shares["decision_date"] = pd.to_datetime(
        shares["decision_date"], errors="coerce"
    ).dt.normalize()
    shares["cik"] = pd.to_numeric(
        shares["cik"], errors="coerce"
    ).astype("Int64")
    shares["candidate_shares"] = pd.to_numeric(
        shares["candidate_shares"], errors="coerce"
    )
    shares["selected_available_at"] = pd.to_datetime(
        shares["selected_available_at"], errors="coerce", utc=True
    )
    shares["decision_cutoff"] = pd.to_datetime(
        shares["decision_cutoff"], errors="coerce", utc=True
    )
    shares_future = shares.loc[
        shares["selected_available_at"].notna()
        & shares["decision_cutoff"].notna()
        & shares["selected_available_at"].gt(shares["decision_cutoff"])
    ]
    if not shares_future.empty:
        raise SystemExit(
            "PIT violation in frozen shares replay input: "
            f"{len(shares_future)} rows"
        )
    shares = (
        shares.loc[shares["candidate_shares"].gt(0)]
        .sort_values(
            ["decision_date", "ticker", "cik", "selected_available_at"],
            kind="stable",
        )
        .drop_duplicates(["decision_date", "ticker", "cik"], keep="last")
        .reset_index(drop=True)
    )

    challenger = baseline.merge(
        liabilities[
            [
                "decision_date",
                "cik",
                "constructed_liabilities",
                "selected_accession",
                "selected_period_date",
                "selected_available_at",
            ]
        ].rename(
            columns={
                "selected_accession": "v3_liabilities_accession",
                "selected_period_date": "v3_liabilities_period_date",
                "selected_available_at": "v3_liabilities_available_at",
            }
        ),
        on=["decision_date", "cik"],
        how="left",
        validate="many_to_one",
    )
    challenger = challenger.merge(
        shares[
            [
                "decision_date",
                "ticker",
                "cik",
                "candidate_shares",
                "selection_rule",
                "selected_accession",
                "selected_context_instant",
                "selected_available_at",
            ]
        ].rename(
            columns={
                "selection_rule": "v3_shares_selection_rule",
                "selected_accession": "v3_shares_accession",
                "selected_context_instant": "v3_shares_context_instant",
                "selected_available_at": "v3_shares_available_at",
            }
        ),
        on=["decision_date", "ticker", "cik"],
        how="left",
        validate="one_to_one",
    )

    original_liabilities = pd.to_numeric(
        challenger["total_liabilities"], errors="coerce"
    ).copy()
    original_shares = pd.to_numeric(
        challenger["shares_outstanding"], errors="coerce"
    ).copy()

    liabilities_apply = (
        challenger["cik"].isin(approved_ciks)
        & ~original_liabilities.gt(0)
        & pd.to_numeric(
            challenger["constructed_liabilities"], errors="coerce"
        ).gt(0)
    )
    shares_apply = (
        ~original_shares.gt(0)
        & pd.to_numeric(
            challenger["candidate_shares"], errors="coerce"
        ).gt(0)
    )

    challenger["v3_liabilities_rule_applied"] = liabilities_apply
    challenger["v3_shares_rule_applied"] = shares_apply
    challenger["baseline_total_liabilities"] = original_liabilities
    challenger["baseline_shares_outstanding"] = original_shares
    challenger.loc[liabilities_apply, "total_liabilities"] = challenger.loc[
        liabilities_apply, "constructed_liabilities"
    ]
    challenger.loc[shares_apply, "shares_outstanding"] = challenger.loc[
        shares_apply, "candidate_shares"
    ]

    baseline_scored = score_long_growth_panel(baseline)
    challenger_scored = score_long_growth_panel(challenger)

    (
        impact_detail,
        weekly_top10,
        turnover,
        rank_displacement,
        concentration,
    ) = compare_variants(baseline_scored, challenger_scored)
    weekly_outcomes, outcomes = _outcome_summary(
        baseline_scored, challenger_scored
    )

    base_health = pd.to_numeric(
        baseline_scored["financial_health_score"], errors="coerce"
    ).notna()
    v3_health = pd.to_numeric(
        challenger_scored["financial_health_score"], errors="coerce"
    ).notna()
    base_valuation = pd.to_numeric(
        baseline_scored["valuation_score"], errors="coerce"
    ).notna()
    v3_valuation = pd.to_numeric(
        challenger_scored["valuation_score"], errors="coerce"
    ).notna()
    base_top = _bool(baseline_scored["top_conviction_eligible"])
    v3_top = _bool(challenger_scored["top_conviction_eligible"])

    valid_turnover = turnover.loc[_bool(turnover["comparison_valid"])].copy()
    turnover_means = valid_turnover.groupby("variant")[
        "replacement_rate"
    ].mean()
    baseline_turnover = float(turnover_means.get("baseline", np.nan))
    v3_turnover = float(turnover_means.get("challenger", np.nan))

    populated = weekly_top10.loc[
        weekly_top10["baseline_count"].eq(10)
        & weekly_top10["challenger_count"].eq(10)
    ].copy()

    both_apply = liabilities_apply & shares_apply
    coverage = _coverage_by_year(baseline, challenger)
    eligibility = _eligibility_by_year(
        baseline_scored, challenger_scored
    )

    summary = {
        "as_of": evidence_date,
        "liabilities_rule_id": V3_LIABILITIES_RULE.rule_id,
        "liabilities_rule_hash": V3_LIABILITIES_RULE.configuration_hash,
        "shares_rule_id": V3_SHARES_RULE.rule_id,
        "shares_rule_hash": V3_SHARES_RULE.configuration_hash,
        "historical_rows": int(len(baseline)),
        "historical_decision_dates": int(
            baseline["decision_date"].nunique()
        ),
        "liabilities_recovery_rows": int(liabilities_apply.sum()),
        "shares_recovery_rows": int(shares_apply.sum()),
        "rows_receiving_both_recoveries": int(both_apply.sum()),
        "baseline_positive_liabilities": int(
            _positive(baseline["total_liabilities"]).sum()
        ),
        "v3_positive_liabilities": int(
            _positive(challenger["total_liabilities"]).sum()
        ),
        "baseline_positive_shares": int(
            _positive(baseline["shares_outstanding"]).sum()
        ),
        "v3_positive_shares": int(
            _positive(challenger["shares_outstanding"]).sum()
        ),
        "health_eligibility_gained_rows": int(
            (~base_health & v3_health).sum()
        ),
        "health_eligibility_lost_rows": int(
            (base_health & ~v3_health).sum()
        ),
        "valuation_eligibility_gained_rows": int(
            (~base_valuation & v3_valuation).sum()
        ),
        "valuation_eligibility_lost_rows": int(
            (base_valuation & ~v3_valuation).sum()
        ),
        "top_conviction_gained_rows": int(
            (~base_top & v3_top).sum()
        ),
        "top_conviction_lost_rows": int(
            (base_top & ~v3_top).sum()
        ),
        "populated_top10_dates": int(len(populated)),
        "mean_top10_overlap": (
            float(populated["overlap_count"].mean())
            if not populated.empty else None
        ),
        "latest_top10_overlap": (
            int(populated.iloc[-1]["overlap_count"])
            if not populated.empty else None
        ),
        "identical_top10_order_rate": (
            float(populated["identical_order"].mean())
            if not populated.empty else None
        ),
        "baseline_mean_weekly_replacement_rate": (
            None if np.isnan(baseline_turnover) else baseline_turnover
        ),
        "v3_mean_weekly_replacement_rate": (
            None if np.isnan(v3_turnover) else v3_turnover
        ),
        "replacement_rate_delta": (
            None
            if np.isnan(baseline_turnover) or np.isnan(v3_turnover)
            else v3_turnover - baseline_turnover
        ),
        "rank_displacement_rows": int(len(rank_displacement)),
        "median_absolute_rank_displacement": (
            float(
                rank_displacement[
                    "absolute_rank_displacement"
                ].median()
            )
            if not rank_displacement.empty else None
        ),
        "max_absolute_rank_displacement": (
            float(
                rank_displacement[
                    "absolute_rank_displacement"
                ].max()
            )
            if not rank_displacement.empty else None
        ),
        "pit_violations": 0,
        "outcomes": outcomes,
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
        "broker_capability": False,
        "order_capability": False,
    }

    output_dir = base_dir / "v3_combined_historical_replay"
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "summary": output_dir / "summary.json",
        "coverage": output_dir / "coverage_by_year.csv",
        "eligibility": output_dir / "eligibility_by_year.csv",
        "impact": output_dir / "impact_detail.csv",
        "top10": output_dir / "weekly_top10_comparison.csv",
        "turnover": output_dir / "weekly_top10_turnover.csv",
        "rank": output_dir / "rank_displacement.csv",
        "outcomes": output_dir / "weekly_top10_forward_returns.csv",
        "recovery": output_dir / "combined_recovery_detail.csv",
    }

    recovery_columns = [
        "decision_date",
        "ticker",
        "cik",
        "v3_liabilities_rule_applied",
        "baseline_total_liabilities",
        "total_liabilities",
        "constructed_liabilities",
        "v3_liabilities_accession",
        "v3_liabilities_period_date",
        "v3_liabilities_available_at",
        "v3_shares_rule_applied",
        "baseline_shares_outstanding",
        "shares_outstanding",
        "candidate_shares",
        "v3_shares_selection_rule",
        "v3_shares_accession",
        "v3_shares_context_instant",
        "v3_shares_available_at",
    ]
    challenger.loc[
        liabilities_apply | shares_apply,
        recovery_columns,
    ].to_csv(paths["recovery"], index=False)
    coverage.to_csv(paths["coverage"], index=False)
    eligibility.to_csv(paths["eligibility"], index=False)
    impact_detail.to_csv(paths["impact"], index=False)
    weekly_top10.to_csv(paths["top10"], index=False)
    turnover.to_csv(paths["turnover"], index=False)
    rank_displacement.to_csv(paths["rank"], index=False)
    weekly_outcomes.to_csv(paths["outcomes"], index=False)
    paths["summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V3 COMBINED HISTORICAL REPLAY COMPLETE")
    print(f"As of:                       {summary['as_of']}")
    print(
        f"Historical rows/dates:       "
        f"{summary['historical_rows']}/"
        f"{summary['historical_decision_dates']}"
    )
    print(
        f"Liabilities recovery rows:   "
        f"{summary['liabilities_recovery_rows']}"
    )
    print(
        f"Shares recovery rows:        "
        f"{summary['shares_recovery_rows']}"
    )
    print(
        f"Rows receiving both:         "
        f"{summary['rows_receiving_both_recoveries']}"
    )
    print(
        f"Positive liabilities:        "
        f"{summary['baseline_positive_liabilities']} -> "
        f"{summary['v3_positive_liabilities']}"
    )
    print(
        f"Positive shares:             "
        f"{summary['baseline_positive_shares']} -> "
        f"{summary['v3_positive_shares']}"
    )
    print(
        f"Health gained/lost:          "
        f"{summary['health_eligibility_gained_rows']}/"
        f"{summary['health_eligibility_lost_rows']}"
    )
    print(
        f"Valuation gained/lost:       "
        f"{summary['valuation_eligibility_gained_rows']}/"
        f"{summary['valuation_eligibility_lost_rows']}"
    )
    print(
        f"Top-conviction gained/lost:  "
        f"{summary['top_conviction_gained_rows']}/"
        f"{summary['top_conviction_lost_rows']}"
    )
    print(
        f"Top-10 mean/latest overlap:  "
        f"{summary['mean_top10_overlap']}/"
        f"{summary['latest_top10_overlap']}"
    )
    print(
        f"Mean replacement rate:       "
        f"{summary['baseline_mean_weekly_replacement_rate']} -> "
        f"{summary['v3_mean_weekly_replacement_rate']}"
    )
    print(
        f"Median/max rank displacement:"
        f" {summary['median_absolute_rank_displacement']}/"
        f"{summary['max_absolute_rank_displacement']}"
    )
    print(
        f"Comparable 1w outcome weeks: "
        f"{outcomes['comparable_weeks']}"
    )
    print(
        f"Mean 1w Top-10 return:        "
        f"{outcomes['baseline_mean_weekly_top10_return']} -> "
        f"{outcomes['v3_mean_weekly_top10_return']}"
    )
    print(f"PIT violations:              {summary['pit_violations']}")
    print(f"Summary:                     {paths['summary']}")
    print(f"Coverage by year:            {paths['coverage']}")
    print(f"Eligibility by year:         {paths['eligibility']}")
    print(f"Combined recovery detail:    {paths['recovery']}")
    print(f"Weekly Top-10:               {paths['top10']}")
    print(f"Turnover:                    {paths['turnover']}")
    print(f"Rank displacement:           {paths['rank']}")
    print(f"Weekly outcomes:             {paths['outcomes']}")
    print("V1 AND FROZEN V2 INPUTS/RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
