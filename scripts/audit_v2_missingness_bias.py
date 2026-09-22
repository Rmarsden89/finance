from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.missingness_bias import (
    PROMOTION_THRESHOLDS,
    cohort_counts,
    compare_variants,
    coverage_table,
    current_cohorts_by_group,
    forward_return_analysis,
    prepare_missingness_panel,
    summarize_missingness_bias,
    summary_as_dict,
)
from finance.research.v2 import (
    resolve_v2_impact_artifact_paths,
    resolve_v2_missingness_audit_paths,
    resolve_v2_sec_artifact_paths,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Quantify V2 missing-data selection effects using completed, saved "
            "baseline/challenger artifacts. No data refresh or trading capability."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _conclusion(summary: dict[str, object], thresholds: dict[str, object]) -> str:
    overlap_ok = summary["current_top10_overlap"] >= thresholds["current_top10_overlap_min"]
    rank = summary["current_median_absolute_rank_displacement"]
    rank_ok = rank is not None and rank <= thresholds["median_absolute_rank_displacement_max"]
    turnover = summary["mean_replacement_rate_delta_percentage_points"]
    turnover_ok = turnover is not None and turnover <= thresholds[
        "mean_weekly_replacement_rate_increase_max_percentage_points"
    ]
    max_turnover = summary[
        "max_weekly_replacement_rate_delta_percentage_points"
    ]
    max_turnover_ok = max_turnover is not None and max_turnover <= thresholds[
        "unexplained_replacement_rate_increase_max_percentage_points"
    ]
    concentration = summary["max_market_cap_band_share_increase_percentage_points"]
    concentration_ok = concentration is not None and concentration <= thresholds[
        "top10_market_cap_band_share_increase_max_percentage_points"
    ]
    safety_ok = summary["point_in_time_violations"] == 0
    metadata = (
        "Classification metadata was available and is reported."
        if summary["classification_metadata_available"]
        else "Sector/industry metadata was absent, so no unsupported classification conclusion was made."
    )
    return f"""# V2 missing-data selection-bias audit

This audit compares the frozen V1 exact-only panel with the approved V2 data-policy challenger. It separates eligibility/availability changes from score-only cross-sectional effects and does not modify either model.

## Predeclared threshold results

| Check | Result | Pass |
|---|---:|:---:|
| Point-in-time violations | {summary['point_in_time_violations']} | {'yes' if safety_ok else 'no'} |
| Current shares coverage gain (percentage points) | {summary['current_shares_coverage_gain_percentage_points']} | {'yes' if summary['current_shares_coverage_gain_percentage_points'] >= thresholds['coverage_improvement_min_percentage_points'] else 'no'} |
| Current valuation coverage gain (percentage points) | {summary['current_valuation_coverage_gain_percentage_points']} | {'yes' if summary['current_valuation_coverage_gain_percentage_points'] >= thresholds['coverage_improvement_min_percentage_points'] else 'no'} |
| Current Top-10 overlap | {summary['current_top10_overlap']}/10 | {'yes' if overlap_ok else 'no'} |
| Current median absolute rank displacement | {rank if rank is not None else 'n/a'} | {'yes' if rank_ok else 'no'} |
| Mean weekly replacement-rate increase (percentage points) | {turnover if turnover is not None else 'n/a'} | {'yes' if turnover_ok else 'no'} |
| Maximum weekly replacement-rate increase (percentage points) | {max_turnover if max_turnover is not None else 'n/a'} | {'yes' if max_turnover_ok else 'no'} |
| Maximum Top-10 market-cap-band share increase (percentage points) | {concentration if concentration is not None else 'n/a'} | {'yes' if concentration_ok else 'no'} |

## Interpretation

- Current challenger coverage contains {summary['current_full_four_family']} full-four-family, {summary['current_three_family']} three-family, and {summary['current_fewer_than_three']} fewer-than-three-family rows.
- Across all aligned dates, {summary['availability_gain_rows']} rows gained Top-Conviction eligibility, {summary['availability_loss_rows']} lost it, and {summary['score_only_change_rows']} changed score without a family-availability change.
- One-week forward-return evidence spans {summary['one_week_forward_decision_dates']} decision dates. These returns are diagnostic, not a promotion decision; this is not a newly frozen out-of-sample test.
- No benchmark-return artifact is part of the completed impact package, so benchmark conclusions were not inferred by this audit.
- {metadata}
- Missing inputs were not imputed. A later observation becoming complete is used only as an outcome label and never to change the earlier cohort.

## Decision

The artifact is suitable for reviewing selection effects and for deciding what should enter a future frozen challenger. It is not, by itself, authorization to change V1 scoring or promote V2.
"""


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    impact = resolve_v2_impact_artifact_paths(root, args.as_of)
    output = resolve_v2_missingness_audit_paths(root, args.as_of)

    required = {
        "research manifest": v2["manifest"],
        "baseline scored panel": impact["baseline_scored"],
        "challenger scored panel": impact["challenger_scored"],
        "impact PIT audit": impact["pit_audit"],
        "impact summary": impact["summary_json"],
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.exists()]
    if missing:
        raise SystemExit("Missing completed audit input(s):\n  " + "\n  ".join(missing))
    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    if manifest.get("status") != "SEC_RESEARCH_COMPLETE":
        raise SystemExit("V2 SEC research must be complete")
    if any(bool(value) for value in manifest.get("execution_capabilities", {}).values()):
        raise SystemExit("V2 manifest enables an execution capability")

    baseline = pd.read_csv(impact["baseline_scored"], low_memory=False)
    challenger = pd.read_csv(impact["challenger_scored"], low_memory=False)
    if set(zip(baseline["decision_date"], baseline["ticker"])) != set(
        zip(challenger["decision_date"], challenger["ticker"])
    ):
        raise SystemExit("Baseline and challenger decision-date/ticker universes differ")

    prepared = prepare_missingness_panel(challenger)
    by_year = pd.concat(
        [
            coverage_table(baseline).assign(variant="baseline"),
            coverage_table(challenger).assign(variant="challenger"),
        ],
        ignore_index=True,
    )
    current_coverage = pd.concat(
        [
            coverage_table(baseline, current_only=True).assign(variant="baseline"),
            coverage_table(challenger, current_only=True).assign(variant="challenger"),
        ],
        ignore_index=True,
    )
    cohorts = pd.concat(
        [
            cohort_counts(baseline).assign(variant="baseline"),
            cohort_counts(challenger).assign(variant="challenger"),
        ],
        ignore_index=True,
    )
    by_market_cap = current_cohorts_by_group(
        challenger, candidates=("market_cap_band",)
    )
    by_classification = current_cohorts_by_group(
        challenger,
        candidates=(
            "gics_sector", "sector", "gics_sub_industry", "industry",
            "shares_outstanding_source_system", "source_system",
            "shares_outstanding_form", "form",
        ),
    )
    forward_detail, forward_summary = forward_return_analysis(challenger)
    (
        impact_detail,
        weekly_top10,
        turnover,
        rank_displacement,
        concentration,
    ) = compare_variants(baseline, challenger)
    pit = pd.read_csv(impact["pit_audit"], low_memory=False)
    summary = summarize_missingness_bias(
        baseline,
        challenger,
        impact_detail,
        weekly_top10,
        turnover,
        rank_displacement,
        concentration,
        forward_detail,
        point_in_time_violations=len(pit),
        classification_metadata_available=not by_classification.empty,
    )
    summary_dict = summary_as_dict(summary)
    summary_dict["thresholds"] = PROMOTION_THRESHOLDS
    summary_dict["as_of"] = args.as_of.isoformat()
    summary_dict["status"] = "MISSINGNESS_BIAS_AUDIT_COMPLETE"
    summary_dict["benchmark_evaluation"] = {
        "status": "not_available_in_completed_impact_artifacts",
        "conclusion_drawn": False,
    }
    rank_value = summary.current_median_absolute_rank_displacement
    turnover_value = summary.mean_replacement_rate_delta_percentage_points
    max_turnover_value = (
        summary.max_weekly_replacement_rate_delta_percentage_points
    )
    concentration_value = summary.max_market_cap_band_share_increase_percentage_points
    summary_dict["threshold_results"] = {
        "pit_safety": summary.point_in_time_violations == 0,
        "shares_coverage": (
            summary.current_shares_coverage_gain_percentage_points
            >= PROMOTION_THRESHOLDS["coverage_improvement_min_percentage_points"]
        ),
        "valuation_coverage": (
            summary.current_valuation_coverage_gain_percentage_points
            >= PROMOTION_THRESHOLDS["coverage_improvement_min_percentage_points"]
        ),
        "top10_overlap": (
            summary.current_top10_overlap
            >= PROMOTION_THRESHOLDS["current_top10_overlap_min"]
        ),
        "rank_stability": (
            rank_value is not None
            and rank_value
            <= PROMOTION_THRESHOLDS["median_absolute_rank_displacement_max"]
        ),
        "turnover": (
            turnover_value is not None
            and turnover_value
            <= PROMOTION_THRESHOLDS[
                "mean_weekly_replacement_rate_increase_max_percentage_points"
            ]
        ),
        "maximum_weekly_turnover": (
            max_turnover_value is not None
            and max_turnover_value
            <= PROMOTION_THRESHOLDS[
                "unexplained_replacement_rate_increase_max_percentage_points"
            ]
        ),
        "market_cap_concentration": (
            concentration_value is not None
            and concentration_value
            <= PROMOTION_THRESHOLDS[
                "top10_market_cap_band_share_increase_max_percentage_points"
            ]
        ),
        "performance_promotion": False,
    }
    summary_dict["execution_capabilities"] = {
        "broker_access": False,
        "order_intents": False,
        "order_placement": False,
        "order_review": False,
    }

    output["audit_dir"].mkdir(parents=True, exist_ok=True)
    by_year.to_csv(output["coverage_by_year"], index=False)
    current_coverage.to_csv(output["current_coverage"], index=False)
    cohorts.to_csv(output["cohorts"], index=False)
    by_market_cap.to_csv(output["cohorts_by_market_cap"], index=False)
    by_classification.to_csv(output["cohorts_by_classification"], index=False)
    forward_detail.to_csv(output["forward_detail"], index=False)
    forward_summary.to_csv(output["forward_summary"], index=False)
    impact_detail.to_csv(output["impact_detail"], index=False)
    weekly_top10.to_csv(output["weekly_top10"], index=False)
    turnover.to_csv(output["turnover"], index=False)
    rank_displacement.to_csv(output["rank_displacement"], index=False)
    concentration.to_csv(output["concentration"], index=False)
    output["thresholds"].write_text(
        json.dumps(PROMOTION_THRESHOLDS, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output["summary"].write_text(
        json.dumps(summary_dict, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output["conclusion"].write_text(
        _conclusion(summary_dict, PROMOTION_THRESHOLDS), encoding="utf-8"
    )
    provenance = {
        "schema_version": 1,
        "decision_date": args.as_of.isoformat(),
        "source_research_fingerprint_bundle": manifest.get(
            "input_fingerprints", {}
        ).get("bundle_sha256"),
        "direct_inputs": fingerprint_files(root=root, paths=required.values()),
        "audit_code": git_provenance(root),
    }
    output["input_fingerprints"].write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V2 MISSING-DATA SELECTION-BIAS AUDIT")
    print(f"Scored rows:                 {len(prepared)}")
    print(f"Decision dates:              {summary.decision_dates}")
    print(
        "Current family cohorts 4/3/<3: "
        f"{summary.current_full_four_family}/"
        f"{summary.current_three_family}/"
        f"{summary.current_fewer_than_three}"
    )
    print(f"Current Top-10 overlap:      {summary.current_top10_overlap}/10")
    print(
        "Median current rank move:  "
        f"{summary.current_median_absolute_rank_displacement}"
    )
    print(
        "Mean turnover delta (pp):  "
        f"{summary.mean_replacement_rate_delta_percentage_points}"
    )
    print(f"PIT violations:              {summary.point_in_time_violations}")
    print(f"Summary:                     {output['summary']}")
    print(f"Conclusion:                  {output['conclusion']}")
    print("V1 reports, V2 inputs, and scoring rules were NOT modified.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
