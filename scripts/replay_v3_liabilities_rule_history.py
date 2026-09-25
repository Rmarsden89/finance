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
    v3_liabilities_approved_ciks,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay the V3-only historically clean liabilities rule across the "
            "frozen historical weekly panel. V1 and V2 inputs remain read-only."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _positive(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").gt(0)


def _bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.fillna("").astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes"}
    )


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
    left["_liabilities"] = _positive(left["total_liabilities"])
    right["_liabilities"] = _positive(right["total_liabilities"])

    base = (
        left.groupby("year", as_index=False)
        .agg(
            rows=("ticker", "size"),
            baseline_positive_liabilities=("_liabilities", "sum"),
        )
    )
    chal = (
        right.groupby("year", as_index=False)
        .agg(
            v3_positive_liabilities=("_liabilities", "sum"),
        )
    )
    result = base.merge(chal, on="year", how="outer", validate="one_to_one")
    result["baseline_liabilities_coverage"] = (
        result["baseline_positive_liabilities"] / result["rows"]
    )
    result["v3_liabilities_coverage"] = (
        result["v3_positive_liabilities"] / result["rows"]
    )
    result["coverage_gain_percentage_points"] = (
        result["v3_liabilities_coverage"]
        - result["baseline_liabilities_coverage"]
    ) * 100
    return result.sort_values("year", kind="stable")


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
        temp["_health"] = (
            pd.to_numeric(
                temp["financial_health_score"], errors="coerce"
            ).notna()
        )
        temp["_top"] = _bool(temp["top_conviction_eligible"])
        return (
            temp.groupby("year", as_index=False)
            .agg(
                **{
                    f"{prefix}_health_eligible": ("_health", "sum"),
                    f"{prefix}_top_conviction_eligible": ("_top", "sum"),
                }
            )
        )

    return summarize(baseline, "baseline").merge(
        summarize(challenger, "v3"),
        on="year",
        how="outer",
        validate="one_to_one",
    ).sort_values("year", kind="stable")


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


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    base = root / "reports" / "v3" / "data_sources" / args.as_of.isoformat()
    hist_dir = base / "liabilities_historical_validation"

    replay_path = hist_dir / "pit_replay_detail.csv"
    if not replay_path.exists():
        raise SystemExit(f"Missing historical PIT replay detail: {replay_path}")

    V3_LIABILITIES_RULE.validate()
    if args.as_of.isoformat() != V3_LIABILITIES_RULE.evidence_as_of:
        raise SystemExit(
            "Frozen V3 liabilities v1 evidence date is "
            f"{V3_LIABILITIES_RULE.evidence_as_of}; got {args.as_of.isoformat()}"
        )
    clean_ciks = set(v3_liabilities_approved_ciks())

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    historical_value = manifest.get("input_artifacts", {}).get("historical panel")
    if not historical_value:
        raise SystemExit("V2 manifest lacks historical panel input")
    panel_path = Path(historical_value)
    if not panel_path.is_absolute():
        panel_path = root / panel_path
    if not panel_path.exists():
        raise SystemExit(f"Missing frozen historical panel: {panel_path}")

    baseline = pd.read_csv(panel_path, low_memory=False)
    baseline["decision_date"] = pd.to_datetime(
        baseline["decision_date"], errors="coerce"
    ).dt.normalize()
    baseline["cik"] = pd.to_numeric(
        baseline["cik"], errors="coerce"
    ).astype("Int64")
    if baseline[["decision_date", "ticker"]].duplicated().any():
        raise SystemExit("Historical panel has duplicate decision_date/ticker rows")

    replay = pd.read_csv(replay_path, low_memory=False)
    replay["decision_date"] = pd.to_datetime(
        replay["decision_date"], errors="coerce"
    ).dt.normalize()
    replay["cik"] = pd.to_numeric(replay["cik"], errors="coerce").astype("Int64")
    replay["constructed_liabilities"] = pd.to_numeric(
        replay["constructed_liabilities"], errors="coerce"
    )
    replay["selected_available_at"] = pd.to_datetime(
        replay["selected_available_at"], errors="coerce", utc=True
    )
    replay["decision_cutoff"] = pd.to_datetime(
        replay["decision_cutoff"], errors="coerce", utc=True
    )

    replay = replay.loc[
        replay["cik"].isin(clean_ciks)
        & replay["constructed_liabilities"].gt(0)
    ].copy()

    future = replay.loc[
        replay["selected_available_at"].notna()
        & replay["decision_cutoff"].notna()
        & replay["selected_available_at"].gt(replay["decision_cutoff"])
    ]
    if not future.empty:
        raise SystemExit(
            f"PIT violation in replay input: {len(future)} rows are future-dated"
        )

    replay = (
        replay.sort_values(
            ["decision_date", "cik", "selected_available_at"],
            kind="stable",
        )
        .drop_duplicates(["decision_date", "cik"], keep="last")
        .reset_index(drop=True)
    )

    challenger = baseline.copy()
    challenger = challenger.merge(
        replay[
            [
                "decision_date",
                "cik",
                "constructed_liabilities",
                "selected_accession",
                "selected_period_date",
                "selected_available_at",
                "source_zip",
            ]
        ],
        on=["decision_date", "cik"],
        how="left",
        validate="many_to_one",
    )

    original = pd.to_numeric(
        challenger["total_liabilities"], errors="coerce"
    )
    apply = (
        challenger["cik"].isin(clean_ciks)
        & ~original.gt(0)
        & challenger["constructed_liabilities"].gt(0)
    )
    challenger["v3_liabilities_rule_applied"] = apply
    challenger["baseline_total_liabilities"] = original
    challenger.loc[apply, "total_liabilities"] = challenger.loc[
        apply, "constructed_liabilities"
    ]

    baseline_scored = score_long_growth_panel(baseline)
    challenger_scored = score_long_growth_panel(challenger)

    coverage = _coverage_by_year(baseline, challenger)
    eligibility = _eligibility_by_year(baseline_scored, challenger_scored)

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

    valid_turnover = turnover.loc[_bool(turnover["comparison_valid"])].copy()
    turnover_means = valid_turnover.groupby("variant")[
        "replacement_rate"
    ].mean()
    baseline_turnover = float(
        turnover_means.get("baseline", np.nan)
    )
    v3_turnover = float(turnover_means.get("challenger", np.nan))

    populated = weekly_top10.loc[
        weekly_top10["baseline_count"].eq(10)
        & weekly_top10["challenger_count"].eq(10)
    ].copy()
    current_overlap = (
        int(populated.iloc[-1]["overlap_count"]) if not populated.empty else None
    )
    mean_overlap = (
        float(populated["overlap_count"].mean())
        if not populated.empty
        else None
    )
    identical_order_rate = (
        float(populated["identical_order"].mean())
        if not populated.empty
        else None
    )

    applied = challenger.loc[apply].copy()
    applied["year"] = applied["decision_date"].dt.year
    recovery_by_year = (
        applied.groupby("year", as_index=False)
        .agg(
            recovery_rows=("ticker", "size"),
            recovered_issuers=("cik", "nunique"),
            recovered_tickers=("ticker", "nunique"),
            decision_dates=("decision_date", "nunique"),
        )
        .sort_values("year", kind="stable")
    )

    gained_health = (
        pd.to_numeric(
            baseline_scored["financial_health_score"], errors="coerce"
        ).isna()
        & pd.to_numeric(
            challenger_scored["financial_health_score"], errors="coerce"
        ).notna()
    )
    base_top = _bool(baseline_scored["top_conviction_eligible"])
    v3_top = _bool(challenger_scored["top_conviction_eligible"])

    summary = {
        "as_of": args.as_of.isoformat(),
        "rule_id": V3_LIABILITIES_RULE.rule_id,
        "rule_configuration_hash": V3_LIABILITIES_RULE.configuration_hash,
        "rule_issuer_count": len(clean_ciks),
        "historical_rows": int(len(baseline)),
        "historical_decision_dates": int(
            baseline["decision_date"].nunique()
        ),
        "recovery_rows_applied": int(apply.sum()),
        "recovery_issuers_applied": int(
            challenger.loc[apply, "cik"].nunique()
        ),
        "recovery_decision_dates": int(
            challenger.loc[apply, "decision_date"].nunique()
        ),
        "baseline_positive_liabilities": int(
            _positive(baseline["total_liabilities"]).sum()
        ),
        "v3_positive_liabilities": int(
            _positive(challenger["total_liabilities"]).sum()
        ),
        "health_eligibility_gained_rows": int(gained_health.sum()),
        "top_conviction_gained_rows": int((~base_top & v3_top).sum()),
        "top_conviction_lost_rows": int((base_top & ~v3_top).sum()),
        "populated_top10_dates": int(len(populated)),
        "mean_top10_overlap": mean_overlap,
        "latest_top10_overlap": current_overlap,
        "identical_top10_order_rate": identical_order_rate,
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
            float(rank_displacement["absolute_rank_displacement"].median())
            if not rank_displacement.empty
            else None
        ),
        "max_absolute_rank_displacement": (
            float(rank_displacement["absolute_rank_displacement"].max())
            if not rank_displacement.empty
            else None
        ),
        "pit_violations": 0,
        "outcomes": outcomes,
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
        "broker_capability": False,
        "order_capability": False,
    }

    output_dir = base / "v3_liabilities_historical_replay"
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    recovery_detail_path = output_dir / "recovery_detail.csv"
    recovery_by_year_path = output_dir / "recovery_by_year.csv"
    coverage_path = output_dir / "coverage_by_year.csv"
    eligibility_path = output_dir / "eligibility_by_year.csv"
    top10_path = output_dir / "weekly_top10_comparison.csv"
    turnover_path = output_dir / "weekly_top10_turnover.csv"
    rank_path = output_dir / "rank_displacement.csv"
    outcome_path = output_dir / "weekly_top10_forward_returns.csv"

    recovery_columns = [
        "decision_date",
        "ticker",
        "cik",
        "baseline_total_liabilities",
        "total_liabilities",
        "constructed_liabilities",
        "selected_accession",
        "selected_period_date",
        "selected_available_at",
        "source_zip",
    ]
    challenger.loc[apply, recovery_columns].to_csv(
        recovery_detail_path, index=False
    )
    recovery_by_year.to_csv(recovery_by_year_path, index=False)
    coverage.to_csv(coverage_path, index=False)
    eligibility.to_csv(eligibility_path, index=False)
    weekly_top10.to_csv(top10_path, index=False)
    turnover.to_csv(turnover_path, index=False)
    rank_displacement.to_csv(rank_path, index=False)
    weekly_outcomes.to_csv(outcome_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V3 LIABILITIES HISTORICAL RULE REPLAY COMPLETE")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Rule issuers:                {summary['rule_issuer_count']}")
    print(f"Historical rows/dates:       {summary['historical_rows']}/"
          f"{summary['historical_decision_dates']}")
    print(f"Recovery rows applied:       {summary['recovery_rows_applied']}")
    print(f"Recovery issuers/dates:      {summary['recovery_issuers_applied']}/"
          f"{summary['recovery_decision_dates']}")
    print(
        f"Positive liabilities rows:   "
        f"{summary['baseline_positive_liabilities']} -> "
        f"{summary['v3_positive_liabilities']}"
    )
    print(
        f"Health eligibility gained:   "
        f"{summary['health_eligibility_gained_rows']}"
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
    print(f"Summary:                     {summary_path}")
    print(f"Coverage by year:            {coverage_path}")
    print(f"Eligibility by year:         {eligibility_path}")
    print(f"Weekly Top-10:               {top10_path}")
    print(f"Turnover:                    {turnover_path}")
    print(f"Rank displacement:           {rank_path}")
    print(f"Weekly outcomes:             {outcome_path}")
    print("V1 AND FROZEN V2 INPUTS/RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
