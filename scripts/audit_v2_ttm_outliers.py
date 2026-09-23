from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.pit_reconciliation import eastern_timestamp
from finance.research.ttm_outlier_audit import (
    current_missing_ttm_coverage,
    flag_annual_ttm_outliers,
    reconcile_q4_ttm_to_reported_annual,
    summarize_q4_reconciliation,
)
from finance.research.v2 import (
    resolve_v2_sec_artifact_paths,
    resolve_v2_ttm_diagnostic_paths,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit current V2 TTM numerator outliers, quarter lineage, "
            "Q4 annual reconciliation, and missing coverage. Read-only."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _decision_cutoff(current_snapshot: pd.DataFrame, as_of: date) -> pd.Timestamp:
    rows = current_snapshot.loc[
        pd.to_datetime(
            current_snapshot["decision_date"], errors="coerce"
        ).dt.date.eq(as_of),
        "as_of",
    ]
    if rows.empty:
        raise SystemExit(
            f"Current V2 snapshot has no rows for {as_of.isoformat()}"
        )
    cutoffs = rows.map(eastern_timestamp)
    if cutoffs.isna().any() or cutoffs.nunique() != 1:
        raise SystemExit("Current V2 snapshot has invalid/multiple cutoffs")
    return cutoffs.iloc[0]


def _outlier_lineage(
    outliers: pd.DataFrame,
    latest_ttm: pd.DataFrame,
) -> pd.DataFrame:
    lineage_columns = [
        "cik",
        "concept",
        "uom",
        "ttm_end_fy",
        "ttm_end_quarter",
        "ttm_end_date",
        "ttm_value",
        "available_at",
        "quarter_keys",
        "quarter_end_dates",
        "quarter_values",
        "quarter_derivations",
        "source_adshs",
        "source_tags",
        "source_forms",
        "source_accepted_ats",
        "source_ddate_dates",
    ]
    available = [c for c in lineage_columns if c in latest_ttm.columns]
    concept_map = {
        "revenue": ("revenue",),
        "net_income": ("net_income",),
        "operating_cash_flow": ("operating_cash_flow",),
        "capital_expenditures": ("capital_expenditures",),
        "free_cash_flow": ("operating_cash_flow", "capital_expenditures"),
    }

    rows: list[pd.DataFrame] = []
    for metric, concepts in concept_map.items():
        flagged = outliers.loc[
            outliers["metric"].eq(metric),
            ["ticker", "cik", "company_name", "metric", "flag_reasons"],
        ]
        if flagged.empty:
            continue
        for concept in concepts:
            lineage = latest_ttm.loc[
                latest_ttm["concept"].eq(concept),
                available,
            ].copy()
            merged = flagged.merge(
                lineage,
                on="cik",
                how="left",
                validate="many_to_one",
            )
            merged["lineage_component"] = concept
            rows.append(merged)

    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True).sort_values(
        ["metric", "ticker", "lineage_component"],
        kind="stable",
    ).reset_index(drop=True)


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    ttm = resolve_v2_ttm_diagnostic_paths(root, args.as_of)

    required = {
        "current snapshot": v2["current_snapshot"],
        "TTM validation summary": ttm["ttm_validation_summary"],
        "annual-vs-TTM comparison": ttm["ttm_annual_comparison"],
        "TTM values": ttm["ttm_values"],
        "latest TTM by concept": ttm["ttm_latest_by_concept"],
        "current TTM numerators": ttm["ttm_current_numerators"],
        "TTM construction audit": ttm["ttm_construction_audit"],
        "enriched quarters": ttm["enriched_quarters"],
        "TTM duration winners": ttm["duration_winners"],
        "research manifest": v2["manifest"],
    }
    missing = [
        f"{name}: {path}" for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing required TTM outlier-audit input(s):\n  "
            + "\n  ".join(missing)
        )

    validation_summary = json.loads(
        ttm["ttm_validation_summary"].read_text(encoding="utf-8")
    )
    if validation_summary.get("status") != (
        "CURRENT_TTM_NUMERATOR_VALIDATION_COMPLETE"
    ):
        raise SystemExit("Current TTM numerator validation is not complete")
    if int(validation_summary.get("pit_violations", 1)) != 0:
        raise SystemExit("Current TTM numerator validation has PIT violations")

    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    if any(
        bool(value)
        for value in manifest.get("execution_capabilities", {}).values()
    ):
        raise SystemExit("V2 manifest enables an execution capability")

    if ttm["ttm_outlier_dir"].exists():
        raise SystemExit(
            "TTM outlier audit output already exists; preserve or rename it "
            f"before another run: {ttm['ttm_outlier_dir']}"
        )

    print("V2 TTM OUTLIER / CONSISTENCY AUDIT", flush=True)
    comparison = pd.read_csv(ttm["ttm_annual_comparison"], low_memory=False)
    latest = pd.read_csv(ttm["ttm_latest_by_concept"], low_memory=False)
    values = pd.read_csv(ttm["ttm_values"], low_memory=False)
    current_numerators = pd.read_csv(
        ttm["ttm_current_numerators"], low_memory=False
    )
    current_snapshot = pd.read_csv(v2["current_snapshot"], low_memory=False)
    construction_audit = pd.read_csv(
        ttm["ttm_construction_audit"], low_memory=False
    )
    quarters = pd.read_csv(ttm["enriched_quarters"], low_memory=False)
    duration = pd.read_csv(ttm["duration_winners"], low_memory=False)

    cutoff = _decision_cutoff(current_snapshot, args.as_of)
    print(f"Decision cutoff (Eastern):  {cutoff.isoformat()}", flush=True)
    print("Flagging annual-vs-TTM review outliers...", flush=True)
    outliers = flag_annual_ttm_outliers(comparison)
    lineage = _outlier_lineage(outliers, latest)
    print(
        f"Flagged metric rows:        {len(outliers):,}",
        flush=True,
    )

    print("Reconciling Q4-ending TTM values to reported annual facts...", flush=True)
    q4_detail = reconcile_q4_ttm_to_reported_annual(
        values,
        duration,
        cutoff=cutoff,
    )
    q4_summary = summarize_q4_reconciliation(q4_detail)

    print("Explaining current missing TTM coverage...", flush=True)
    missing_detail = current_missing_ttm_coverage(
        current_snapshot,
        current_numerators,
        quarters,
        construction_audit,
    )
    missing_summary = (
        missing_detail.groupby(
            ["metric", "missing_reason"],
            as_index=False,
            dropna=False,
        )
        .size()
        .rename(columns={"size": "rows"})
        .sort_values(
            ["metric", "rows"],
            ascending=[True, False],
            kind="stable",
        )
    )

    material_q4 = int(
        q4_detail["material_difference_gt_1pct"].fillna(False).sum()
    )
    exact_q4 = int(
        q4_detail["exact_within_numeric_tolerance"].fillna(False).sum()
    )
    annual_present = int(
        q4_detail["reported_annual_present"].fillna(False).sum()
    )
    sign_changes = int(
        outliers["sign_change"].fillna(False).sum()
    ) if not outliers.empty else 0

    summary = {
        "schema_version": 1,
        "status": "TTM_OUTLIER_AUDIT_COMPLETE",
        "as_of": args.as_of.isoformat(),
        "decision_cutoff_eastern": cutoff.isoformat(),
        "outlier_policy": {
            "positive_ratio_low": 0.5,
            "positive_ratio_high": 2.0,
            "absolute_change_vs_annual_threshold": 1.0,
            "sign_change_flagged": True,
            "policy_use": "diagnostic_review_only",
        },
        "flagged_metric_rows": len(outliers),
        "flagged_unique_tickers": (
            int(outliers["ticker"].nunique()) if not outliers.empty else 0
        ),
        "flagged_sign_changes": sign_changes,
        "q4_ttm_rows": len(q4_detail),
        "q4_reported_annual_present": annual_present,
        "q4_exact_within_numeric_tolerance": exact_q4,
        "q4_material_difference_gt_1pct": material_q4,
        "current_missing_ttm_rows": len(missing_detail),
        "factor_values_changed": False,
        "scoring_changed": False,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    ttm["ttm_outlier_dir"].mkdir(parents=True, exist_ok=False)
    outliers.to_csv(ttm["ttm_outliers"], index=False)
    lineage.to_csv(ttm["ttm_outlier_lineage"], index=False)
    q4_detail.to_csv(ttm["ttm_q4_reconciliation"], index=False)
    q4_summary.to_csv(
        ttm["ttm_q4_reconciliation_summary"], index=False
    )
    missing_detail.to_csv(ttm["ttm_missing_coverage"], index=False)
    missing_summary.to_csv(
        ttm["ttm_missing_coverage_summary"], index=False
    )
    ttm["ttm_outlier_summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    provenance = {
        "schema_version": 1,
        "decision_date": args.as_of.isoformat(),
        "direct_inputs": fingerprint_files(
            root=root,
            paths=list(required.values()),
        ),
        "code": git_provenance(root),
    }
    ttm["ttm_outlier_fingerprints"].write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V2 TTM OUTLIER / CONSISTENCY AUDIT COMPLETE")
    print(f"Flagged metric rows:        {len(outliers):,}")
    print(
        "Flagged unique tickers:     "
        f"{outliers['ticker'].nunique() if not outliers.empty else 0:,}"
    )
    print(f"Sign-change flags:          {sign_changes:,}")
    print(f"Q4 TTM rows:                {len(q4_detail):,}")
    print(f"Q4 annual matches present:  {annual_present:,}")
    print(f"Q4 exact reconciliations:   {exact_q4:,}")
    print(f"Q4 >1% differences:         {material_q4:,}")
    print(f"Current missing rows:       {len(missing_detail):,}")
    print(f"Output directory:           {ttm['ttm_outlier_dir']}")
    print("NO FACTOR VALUES OR SCORES WERE CHANGED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
