from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import numpy as np
import pandas as pd

from finance.research.missingness_bias import compare_variants, forward_return_analysis
from finance.research.v2 import resolve_v2_sec_artifact_paths
from finance.research.v2_impact import score_long_growth_panel


TARGET_TICKER = "DECK"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate a DECK-only V4 liabilities regime after the issuer's last "
            "material historical identity mismatch. The boundary is derived from "
            "historical validation evidence and applied PIT-safely."
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


def _outcomes(
    baseline: pd.DataFrame,
    challenger: pd.DataFrame,
) -> dict[str, object]:
    left = _top10_weekly_forward_returns(baseline).rename(
        columns={
            "top10_count": "baseline_count",
            "mean_forward_return": "baseline_return",
        }
    )
    right = _top10_weekly_forward_returns(challenger).rename(
        columns={
            "top10_count": "challenger_count",
            "mean_forward_return": "challenger_return",
        }
    )
    weekly = left.merge(right, on="decision_date", how="inner")
    weekly = weekly.loc[
        weekly["baseline_count"].eq(10)
        & weekly["challenger_count"].eq(10)
    ].copy()
    if weekly.empty:
        return {
            "comparable_weeks": 0,
            "baseline_mean_weekly_top10_return": None,
            "challenger_mean_weekly_top10_return": None,
        }
    return {
        "comparable_weeks": int(len(weekly)),
        "baseline_mean_weekly_top10_return": float(
            weekly["baseline_return"].mean()
        ),
        "challenger_mean_weekly_top10_return": float(
            weekly["challenger_return"].mean()
        ),
    }


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    collapsed_path = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "liabilities_exact_collapsed_history_review"
        / "collapsed_historical_detail.csv"
    )
    if not collapsed_path.exists():
        raise SystemExit(f"Missing collapsed V4 history detail: {collapsed_path}")

    collapsed = pd.read_csv(collapsed_path, low_memory=False)
    collapsed["ticker"] = collapsed["ticker"].astype(str).str.upper().str.strip()
    collapsed["ddate_date"] = pd.to_datetime(
        collapsed["ddate_date"], errors="coerce"
    ).dt.normalize()
    deck = collapsed.loc[collapsed["ticker"].eq(TARGET_TICKER)].copy()
    if deck.empty:
        raise SystemExit("No DECK collapsed historical evidence found")

    material = deck.loc[
        deck["collapsed_validation_band"].astype(str).eq("material_difference")
    ].copy()
    if material.empty:
        raise SystemExit("DECK has no material row; regime boundary is unnecessary")

    last_material_period = material["ddate_date"].max()
    post = deck.loc[deck["ddate_date"].gt(last_material_period)].copy()
    post_material_rows = int(
        post["collapsed_validation_band"].astype(str)
        .eq("material_difference")
        .sum()
    )
    post_ambiguous_rows = int(
        post["collapsed_validation_band"].astype(str).eq("ambiguous").sum()
    )
    post_clean = bool(
        len(post) > 0
        and post_material_rows == 0
        and post_ambiguous_rows == 0
    )

    first_clean_period = post["ddate_date"].min() if post_clean else pd.NaT

    replay_path = (
        root
        / "reports"
        / "v3"
        / "data_sources"
        / as_of
        / "liabilities_historical_validation"
        / "pit_replay_detail.csv"
    )
    if not replay_path.exists():
        raise SystemExit(f"Missing V3 PIT replay evidence: {replay_path}")

    replay = pd.read_csv(replay_path, low_memory=False)
    replay["ticker"] = replay["ticker"].astype(str).str.upper().str.strip()
    replay["decision_date"] = pd.to_datetime(
        replay["decision_date"], errors="coerce"
    ).dt.normalize()
    replay["selected_period_date"] = pd.to_datetime(
        replay["selected_period_date"], errors="coerce"
    ).dt.normalize()
    replay["selected_available_at"] = pd.to_datetime(
        replay["selected_available_at"], errors="coerce", utc=True
    )
    replay["decision_cutoff"] = pd.to_datetime(
        replay["decision_cutoff"], errors="coerce", utc=True
    )
    replay["constructed_liabilities"] = pd.to_numeric(
        replay["constructed_liabilities"], errors="coerce"
    )

    if not post_clean:
        eligible = replay.iloc[0:0].copy()
    else:
        eligible = replay.loc[
            replay["ticker"].eq(TARGET_TICKER)
            & replay["selected_period_date"].ge(first_clean_period)
            & replay["constructed_liabilities"].gt(0)
        ].copy()

    future = eligible.loc[
        eligible["selected_available_at"].notna()
        & eligible["decision_cutoff"].notna()
        & eligible["selected_available_at"].gt(eligible["decision_cutoff"])
    ]
    if not future.empty:
        raise SystemExit(f"PIT violation in DECK regime replay: {len(future)}")

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    historical_value = manifest.get("input_artifacts", {}).get("historical panel")
    if not historical_value:
        raise SystemExit("V2 manifest lacks historical panel input")
    panel_path = Path(historical_value)
    if not panel_path.is_absolute():
        panel_path = root / panel_path
    baseline = pd.read_csv(panel_path, low_memory=False)
    baseline["decision_date"] = pd.to_datetime(
        baseline["decision_date"], errors="coerce"
    ).dt.normalize()

    challenger = baseline.merge(
        eligible[
            [
                "decision_date",
                "ticker",
                "constructed_liabilities",
                "selected_period_date",
                "selected_available_at",
            ]
        ],
        on=["decision_date", "ticker"],
        how="left",
        validate="one_to_one",
    )

    original = pd.to_numeric(challenger["total_liabilities"], errors="coerce")
    apply = (
        challenger["ticker"].astype(str).str.upper().eq(TARGET_TICKER)
        & ~original.gt(0)
        & challenger["constructed_liabilities"].gt(0)
    )
    challenger["baseline_total_liabilities"] = original
    challenger["v4_deck_regime_rule_applied"] = apply
    challenger.loc[apply, "total_liabilities"] = challenger.loc[
        apply, "constructed_liabilities"
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

    base_top = _bool(baseline_scored["top_conviction_eligible"])
    chal_top = _bool(challenger_scored["top_conviction_eligible"])
    base_health = pd.to_numeric(
        baseline_scored["financial_health_score"], errors="coerce"
    ).notna()
    chal_health = pd.to_numeric(
        challenger_scored["financial_health_score"], errors="coerce"
    ).notna()

    populated = weekly_top10.loc[
        weekly_top10["baseline_count"].eq(10)
        & weekly_top10["challenger_count"].eq(10)
    ].copy()
    valid_turnover = turnover.loc[_bool(turnover["comparison_valid"])].copy()
    turnover_means = valid_turnover.groupby("variant")[
        "replacement_rate"
    ].mean()

    outcomes = _outcomes(baseline_scored, challenger_scored)

    summary = {
        "schema_version": 1,
        "status": "V4_DECK_REGIME_EVALUATION_COMPLETE",
        "as_of": as_of,
        "candidate_id": "v4_deck_current_plus_noncurrent_post_material_regime_v0",
        "ticker": TARGET_TICKER,
        "historical_filing_rows": int(len(deck)),
        "material_filing_rows": int(len(material)),
        "last_material_period": (
            last_material_period.date().isoformat()
            if pd.notna(last_material_period)
            else None
        ),
        "post_material_filing_rows": int(len(post)),
        "post_material_material_rows": post_material_rows,
        "post_material_ambiguous_rows": post_ambiguous_rows,
        "post_material_regime_clean": post_clean,
        "first_clean_period_after_material": (
            first_clean_period.date().isoformat()
            if pd.notna(first_clean_period)
            else None
        ),
        "recovery_rows_applied": int(apply.sum()),
        "recovery_decision_dates": int(
            challenger.loc[apply, "decision_date"].nunique()
        ),
        "health_eligibility_gained_rows": int((~base_health & chal_health).sum()),
        "top_conviction_gained_rows": int((~base_top & chal_top).sum()),
        "top_conviction_lost_rows": int((base_top & ~chal_top).sum()),
        "mean_top10_overlap": (
            float(populated["overlap_count"].mean())
            if len(populated)
            else None
        ),
        "latest_top10_overlap": (
            int(populated.iloc[-1]["overlap_count"])
            if len(populated)
            else None
        ),
        "baseline_mean_weekly_replacement_rate": (
            float(turnover_means.get("baseline"))
            if "baseline" in turnover_means
            else None
        ),
        "challenger_mean_weekly_replacement_rate": (
            float(turnover_means.get("challenger"))
            if "challenger" in turnover_means
            else None
        ),
        "median_absolute_rank_displacement": (
            float(rank_displacement["absolute_rank_displacement"].median())
            if len(rank_displacement)
            else None
        ),
        "max_absolute_rank_displacement": (
            float(rank_displacement["absolute_rank_displacement"].max())
            if len(rank_displacement)
            else None
        ),
        "pit_violations": 0,
        "outcomes": outcomes,
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
        "v3_modified": False,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    output_dir = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "deck_post_material_regime"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    recovery_path = output_dir / "recovery_detail.csv"
    filing_path = output_dir / "deck_historical_filing_evidence.csv"
    top10_path = output_dir / "weekly_top10_comparison.csv"
    rank_path = output_dir / "rank_displacement.csv"

    challenger.loc[apply].to_csv(recovery_path, index=False)
    deck.to_csv(filing_path, index=False)
    weekly_top10.to_csv(top10_path, index=False)
    rank_displacement.to_csv(rank_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 DECK POST-MATERIAL LIABILITIES REGIME EVALUATION")
    print(f"As of:                       {as_of}")
    print(f"Historical filing rows:      {len(deck)}")
    print(f"Material filing rows:        {len(material)}")
    print(
        f"Last material period:        "
        f"{summary['last_material_period']}"
    )
    print(f"Post-material filing rows:   {len(post)}")
    print(
        f"Post-material material:      "
        f"{post_material_rows}"
    )
    print(
        f"Post-material ambiguous:     "
        f"{post_ambiguous_rows}"
    )
    print(
        f"Post-material regime clean:  "
        f"{post_clean}"
    )
    print(
        f"First clean period:          "
        f"{summary['first_clean_period_after_material']}"
    )
    print(f"Recovery rows applied:       {summary['recovery_rows_applied']}")
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
        f"{summary['challenger_mean_weekly_replacement_rate']}"
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
        f"{outcomes['challenger_mean_weekly_top10_return']}"
    )
    print(f"PIT violations:              {summary['pit_violations']}")
    print(f"Summary:                     {summary_path}")
    print("V1/V2/V3 INPUTS AND RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
