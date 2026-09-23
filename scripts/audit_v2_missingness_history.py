from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.models import LONG_GROWTH_V1
from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.missingness_bias import (
    PROMOTION_THRESHOLDS,
    cohort_counts,
    coverage_table,
    current_rank_shift_diagnostic,
    forward_return_analysis,
    historical_cohorts_by_group,
    historical_top10_analysis,
)
from finance.research.v2 import (
    resolve_v2_impact_artifact_paths,
    resolve_v2_missingness_audit_paths,
    resolve_v2_missingness_history_paths,
    resolve_v2_sec_artifact_paths,
)
from finance.research.v2_impact import score_long_growth_panel
from finance.research.pit_reconciliation import (
    POLICY, audit_panel_availability, reconcile_panel,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build PIT-safe historical missingness evidence from the frozen "
            "V1 panel and explain current V1/V2 rank shifts. No data refresh "
            "or execution capability exists."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--reconcile-pit", action="store_true",
        help="Replay the fingerprinted baseline SEC cache and rebuild isolated history with filing availability constraints.",
    )
    return parser.parse_args()


def _compact_scored(scored: pd.DataFrame) -> pd.DataFrame:
    keep = [
        "decision_date", "ticker", "cik", "company_name", "return_price",
        "return_price_basis", "price_source", "close", "shares_outstanding",
        "market_cap", "total_assets", "total_liabilities", "cash",
        "operating_cash_flow", "quality_score", "financial_health_score",
        "growth_score", "valuation_score", "long_growth_v1_score",
        "long_growth_v1_eligible", "top_conviction_eligible",
    ]
    classifications = [
        "gics_sector", "sector", "gics_sub_industry", "industry", "form",
    ]
    available = [column for column in [*keep, *classifications] if column in scored]
    return scored[available].copy()


def _conclusion(summary: dict[str, object]) -> str:
    history_ok = summary["historical_evidence_status"] == "evaluated"
    rank_value = summary["current_median_absolute_rank_displacement"]
    rank_ok = (
        rank_value is not None
        and rank_value
        <= PROMOTION_THRESHOLDS["median_absolute_rank_displacement_max"]
    )
    return f"""# Issue #5 history-qualified missingness evidence

The historical section uses the frozen V1 scoring rules. Historical input scope: {summary["historical_evidence_scope"]}. It does not apply current V2 facts to earlier dates. The current section compares the saved V1-exact and V2-challenger panels only on their actual shared decision date.

## Historical evidence gate

| Check | Result | Pass |
|---|---:|:---:|
| Historical decision dates | {summary['historical_decision_dates']} | informational |
| Populated historical Top-10 dates | {summary['populated_historical_top10_dates']} | {'yes' if summary['populated_historical_top10_dates'] >= 52 else 'no'} |
| Valid historical turnover transitions | {summary['valid_historical_turnover_transitions']} | {'yes' if summary['valid_historical_turnover_transitions'] >= 51 else 'no'} |
| One-week forward-return decision dates | {summary['one_week_forward_decision_dates']} | {'yes' if summary['one_week_forward_decision_dates'] >= 52 else 'no'} |
| Full-four-family forward observations | {summary['full_four_family_forward_observations']} | {'yes' if summary['full_four_family_forward_observations'] > 0 else 'no'} |
| Historical PIT violations | {summary['historical_pit_violations']} | {'yes' if summary['historical_pit_violations'] == 0 else 'no'} |
| Historical evidence status | {summary['historical_evidence_status']} | {'yes' if history_ok else 'no'} |

## Current V1/V2 rank evidence

| Check | Result | Pass |
|---|---:|:---:|
| Continuously eligible names | {summary['current_continuously_eligible_rows']} | informational |
| Median absolute rank displacement | {rank_value if rank_value is not None else 'n/a'} | {'not evaluated' if rank_value is None else ('yes' if rank_ok else 'no')} |
| Names moving more than 10 ranks | {summary['current_moved_more_than_10']} | informational |
| Names moving more than 25 ranks | {summary['current_moved_more_than_25']} | informational |
| Names moving more than 50 ranks | {summary['current_moved_more_than_50']} | informational |

## Interpretation

- Historical cohort returns and rank behavior are diagnostic evidence about V1 missingness. They are not a retrospective V2 performance claim.
- Current rank-shift files identify whether Valuation, Financial Health, or another family accounts for most score changes after the approved data improvements.
- Current V2 performance remains prospective. It must be accumulated through future frozen weekly runs.
- No missing inputs were imputed, and no broker or order capability was used.
"""


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    impact = resolve_v2_impact_artifact_paths(root, args.as_of)
    missingness = resolve_v2_missingness_audit_paths(root, args.as_of)
    output = resolve_v2_missingness_history_paths(root, args.as_of)
    if args.reconcile_pit:
        isolated = output["history_dir"] / "pit_reconciled"
        output = {key: isolated if key == "history_dir" else isolated / path.name
                  for key, path in output.items()}
        if isolated.exists():
            raise SystemExit(f"Output already exists; preserve or rename it before another run: {isolated}")

    if not v2["manifest"].exists():
        raise SystemExit(f"Missing completed V2 manifest: {v2['manifest']}")
    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    if manifest.get("status") != "SEC_RESEARCH_COMPLETE":
        raise SystemExit("V2 SEC research must be complete")
    if any(bool(value) for value in manifest.get("execution_capabilities", {}).values()):
        raise SystemExit("V2 manifest enables an execution capability")

    historical_value = manifest.get("input_artifacts", {}).get("historical panel")
    if not historical_value:
        raise SystemExit("Completed V2 manifest lacks historical panel input")
    historical_path = Path(historical_value)
    required = {
        "historical panel": historical_path,
        "baseline scored panel": impact["baseline_scored"],
        "challenger scored panel": impact["challenger_scored"],
        "current missingness summary": missingness["summary"],
        "current PIT audit": impact["pit_audit"],
        "research manifest": v2["manifest"],
    }
    absent = [f"{name}: {path}" for name, path in required.items() if not path.exists()]
    if absent:
        raise SystemExit("Missing history-audit input(s):\n  " + "\n  ".join(absent))

    current_missingness = json.loads(
        missingness["summary"].read_text(encoding="utf-8")
    )
    if current_missingness.get("status") != "MISSINGNESS_BIAS_AUDIT_COMPLETE":
        raise SystemExit("Corrected current missingness audit must be complete")
    if any(
        bool(value)
        for value in current_missingness.get("execution_capabilities", {}).values()
    ):
        raise SystemExit("Current missingness summary enables an execution capability")

    expected_history_hash = (
        manifest.get("input_fingerprints", {})
        .get("groups", {})
        .get("historical_panel", {})
        .get("sha256")
    )
    actual_history_fingerprint = fingerprint_files(
        root=root, paths=[historical_path]
    )
    if (
        expected_history_hash
        and actual_history_fingerprint["sha256"] != expected_history_hash
    ):
        raise SystemExit(
            "Historical panel differs from the completed V2 research input"
        )

    print("V2 HISTORY-QUALIFIED MISSINGNESS AUDIT", flush=True)
    print(f"Historical panel:            {historical_path}", flush=True)
    print("Loading historical panel for frozen-V1 scoring...", flush=True)
    historical_raw = pd.read_csv(historical_path, low_memory=False)
    reconciliation_summary = None
    if args.reconcile_pit:
        if not expected_history_hash:
            raise SystemExit("PIT reconciliation requires the original historical panel fingerprint")
        sec_group = manifest.get("input_fingerprints", {}).get("groups", {}).get("historical_sec", {})
        sec_value = manifest.get("input_artifacts", {}).get("historical SEC winners")
        if not sec_value or sec_group.get("file_count") != 1 or not sec_group.get("sha256"):
            raise SystemExit("Manifest must fingerprint exactly one baseline historical SEC winner cache")
        sec_path = Path(sec_value)
        if not sec_path.is_absolute():
            sec_path = root / sec_path
        if fingerprint_files(root=root, paths=[sec_path])["sha256"] != sec_group["sha256"]:
            raise SystemExit("Baseline historical SEC cache differs from the completed V2 input")
        required["baseline historical SEC winners"] = sec_path
        print("Replaying original facts and reconciling SEC availability (Eastern time)...", flush=True)
        historical_raw, changes, pit_audit = reconcile_panel(
            historical_raw, pd.read_csv(sec_path, low_memory=False),
            progress=lambda message: print(message, flush=True),
        )
        output["history_dir"].mkdir(parents=True, exist_ok=False)
        panel_output = output["history_dir"] / "reconciled_historical_panel.csv"
        historical_raw.to_csv(panel_output, index=False)
        changes.to_csv(output["history_dir"] / "pit_reconciliation_changes.csv", index=False)
        reselections = changes.loc[changes["change_type"].eq("availability_reselection")]
        reconciliation_summary = {
            "policy": POLICY,
            "original_replay_matched": True,
            "changed_existing_cells": len(reselections),
            "changed_observations": len(reselections[["decision_date", "ticker"]].drop_duplicates()),
            "added_provenance_cells": int(changes["change_type"].eq("provenance_added").sum()),
            "reconciled_panel_fingerprint": fingerprint_files(root=root, paths=[panel_output]),
        }
        print(f"Reconciled observations: {reconciliation_summary['changed_observations']}", flush=True)
    else:
        # Retain the old filed-date findings and strengthen same-day acceptance
        # and missing-provenance checks. Original annual snapshots lack filed
        # dates, so a fully qualified result requires provenance reconciliation.
        pit_audit = audit_panel_availability(historical_raw)
    historical_scored = _compact_scored(score_long_growth_panel(historical_raw))
    historical_scored["decision_date"] = pd.to_datetime(
        historical_scored["decision_date"], errors="coerce"
    ).dt.normalize()
    evaluation_start = pd.Timestamp(LONG_GROWTH_V1.evaluation_start)
    historical_scored = historical_scored.loc[
        historical_scored["decision_date"].ge(evaluation_start)
    ].copy()
    del historical_raw

    coverage = coverage_table(historical_scored).assign(
        evidence_scope="frozen_v1_historical"
    )
    cohorts = cohort_counts(historical_scored).assign(
        evidence_scope="frozen_v1_historical"
    )
    cohorts_by_market_cap = historical_cohorts_by_group(
        historical_scored, candidates=("market_cap_band",)
    )
    cohorts_by_classification = historical_cohorts_by_group(
        historical_scored,
        candidates=(
            "gics_sector", "sector", "gics_sub_industry", "industry",
            "price_source", "return_price_basis", "form",
        ),
    )
    forward_detail, forward_summary = forward_return_analysis(historical_scored)
    weekly_top10, turnover, concentration = historical_top10_analysis(
        historical_scored
    )

    baseline = pd.read_csv(impact["baseline_scored"], low_memory=False)
    challenger = pd.read_csv(impact["challenger_scored"], low_memory=False)
    rank_detail, rank_by_band, rank_by_family = current_rank_shift_diagnostic(
        baseline, challenger
    )
    continuously_eligible = rank_detail.loc[rank_detail["continuously_eligible"]]
    rank_median = (
        float(continuously_eligible["absolute_rank_change"].median())
        if not continuously_eligible.empty
        else None
    )

    populated_top10 = int(weekly_top10["top10_populated"].sum())
    valid_turnover = int(turnover["comparison_valid"].sum())
    one_week = forward_detail.loc[forward_detail["horizon_weeks"].eq(1)]
    full_forward = forward_detail.loc[
        forward_detail["family_cohort"].eq("full_four_family")
    ]
    three_forward = forward_detail.loc[
        forward_detail["family_cohort"].eq("three_family")
    ]
    historical_ok = (
        populated_top10 >= 52
        and valid_turnover >= 51
        and one_week["decision_date"].nunique() >= 52
        and not full_forward.empty
        and pit_audit.empty
    )
    current_pit = pd.read_csv(impact["pit_audit"], low_memory=False)
    summary = {
        "schema_version": 1,
        "status": "HISTORY_QUALIFIED_AUDIT_COMPLETE",
        "as_of": args.as_of.isoformat(),
        "historical_evidence_scope": (
            "availability_reconciled_frozen_v1_rules" if args.reconcile_pit
            else "frozen_v1_historical_context"
        ),
        "pit_reconciliation": reconciliation_summary,
        "current_comparison_scope": "same_date_v1_exact_vs_v2_challenger",
        "retrospective_v2_performance_claim": False,
        "historical_rows": len(historical_scored),
        "historical_decision_dates": int(
            historical_scored["decision_date"].nunique()
        ),
        "populated_historical_top10_dates": populated_top10,
        "valid_historical_turnover_transitions": valid_turnover,
        "mean_historical_replacement_rate": (
            float(
                turnover.loc[
                    turnover["comparison_valid"], "replacement_rate"
                ].mean()
            )
            if valid_turnover else None
        ),
        "one_week_forward_decision_dates": int(
            one_week["decision_date"].nunique()
        ),
        "full_four_family_forward_observations": len(full_forward),
        "three_family_forward_observations": len(three_forward),
        "historical_pit_violations": len(pit_audit),
        "current_pit_violations": len(current_pit),
        "historical_evidence_status": (
            "evaluated" if historical_ok else "not_evaluated"
        ),
        "current_continuously_eligible_rows": len(continuously_eligible),
        "current_median_absolute_rank_displacement": rank_median,
        "current_moved_more_than_10": int(
            continuously_eligible["absolute_rank_change"].gt(10).sum()
        ),
        "current_moved_more_than_25": int(
            continuously_eligible["absolute_rank_change"].gt(25).sum()
        ),
        "current_moved_more_than_50": int(
            continuously_eligible["absolute_rank_change"].gt(50).sum()
        ),
        "promotion_thresholds": PROMOTION_THRESHOLDS,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_placement": False,
            "order_review": False,
        },
    }

    output["history_dir"].mkdir(parents=True, exist_ok=True)
    coverage.to_csv(output["coverage_by_year"], index=False)
    cohorts.to_csv(output["cohorts_by_year"], index=False)
    cohorts_by_market_cap.to_csv(output["cohorts_by_market_cap"], index=False)
    cohorts_by_classification.to_csv(
        output["cohorts_by_classification"], index=False
    )
    forward_summary.to_csv(output["forward_summary"], index=False)
    weekly_top10.to_csv(output["weekly_top10"], index=False)
    turnover.to_csv(output["turnover"], index=False)
    concentration.to_csv(output["concentration"], index=False)
    rank_detail.to_csv(output["rank_detail"], index=False)
    rank_by_band.to_csv(output["rank_by_band"], index=False)
    rank_by_family.to_csv(output["rank_by_family"], index=False)
    pit_audit.to_csv(output["pit_audit"], index=False)
    output["summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output["conclusion"].write_text(_conclusion(summary), encoding="utf-8")
    provenance = {
        "schema_version": 1,
        "decision_date": args.as_of.isoformat(),
        "source_research_fingerprint_bundle": manifest.get(
            "input_fingerprints", {}
        ).get("bundle_sha256"),
        "historical_panel": actual_history_fingerprint,
        "direct_inputs": fingerprint_files(root=root, paths=required.values()),
        "audit_code": git_provenance(root),
    }
    output["input_fingerprints"].write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V2 HISTORY-QUALIFIED MISSINGNESS AUDIT COMPLETE")
    print(f"Historical rows:             {summary['historical_rows']:,}")
    print(f"Historical decision dates:   {summary['historical_decision_dates']}")
    print(f"Populated Top-10 dates:      {populated_top10}")
    print(f"Valid turnover transitions:  {valid_turnover}")
    print(
        "One-week forward dates:    "
        f"{summary['one_week_forward_decision_dates']}"
    )
    print(
        "Full/three-family returns: "
        f"{len(full_forward):,}/{len(three_forward):,}"
    )
    print(f"Historical PIT violations:  {len(pit_audit)}")
    print(
        "Current median rank shift: "
        f"{summary['current_median_absolute_rank_displacement']}"
    )
    print(f"Historical evidence status: {summary['historical_evidence_status']}")
    print(f"Summary:                     {output['summary']}")
    print(f"Conclusion:                  {output['conclusion']}")
    print("V1 reports, V2 inputs, and scoring rules were NOT modified.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
