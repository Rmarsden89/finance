from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.ttm_q4_difference import (
    characterize_q4_material_differences,
    repeated_q4_difference_ciks,
    summarize_q4_difference_dimension,
)
from finance.research.v2 import resolve_v2_ttm_diagnostic_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Characterize material Q4 TTM-vs-annual reconciliation "
            "differences for revenue/net income. Read-only research."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    ttm = resolve_v2_ttm_diagnostic_paths(root, args.as_of)

    required = {
        "Q4 reconciliation detail": ttm["ttm_q4_reconciliation"],
        "Q4 reconciliation summary": ttm["ttm_q4_reconciliation_summary"],
        "outlier audit summary": ttm["ttm_outlier_summary"],
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing required Q4-difference input(s):\n  "
            + "\n  ".join(missing)
        )

    outlier_summary = json.loads(
        ttm["ttm_outlier_summary"].read_text(encoding="utf-8")
    )
    if outlier_summary.get("status") != "TTM_OUTLIER_AUDIT_COMPLETE":
        raise SystemExit("TTM outlier audit is not complete")
    if bool(outlier_summary.get("factor_values_changed", True)):
        raise SystemExit("TTM outlier audit reports factor-value changes")
    if bool(outlier_summary.get("scoring_changed", True)):
        raise SystemExit("TTM outlier audit reports scoring changes")

    if ttm["ttm_q4_difference_dir"].exists():
        raise SystemExit(
            "Q4 difference diagnostic output already exists; preserve or "
            f"rename it before another run: {ttm['ttm_q4_difference_dir']}"
        )

    print("V2 Q4 TTM RECONCILIATION DIFFERENCE DIAGNOSTIC", flush=True)
    q4 = pd.read_csv(ttm["ttm_q4_reconciliation"], low_memory=False)
    material = q4.loc[
        q4["material_difference_gt_1pct"].fillna(False)
        & q4["concept"].isin({"revenue", "net_income"})
    ].copy()
    print(f"Material revenue/net-income rows: {len(material):,}", flush=True)
    print("Characterizing derivations, tags, amendments, and magnitudes...", flush=True)

    detail = characterize_q4_material_differences(q4)
    by_magnitude = summarize_q4_difference_dimension(
        detail, "difference_magnitude_band"
    )
    by_derivation = summarize_q4_difference_dimension(
        detail, "derivation_pattern"
    )
    by_tags = summarize_q4_difference_dimension(
        detail, "source_tag_continuity"
    )
    by_amendment = summarize_q4_difference_dimension(
        detail, "amendment_present"
    )
    repeat_ciks = repeated_q4_difference_ciks(detail)

    gt20 = int(
        detail["difference_magnitude_band"].eq("gt20pct").sum()
    ) if not detail.empty else 0
    multiple_tags = int(
        detail["source_tag_continuity"].eq("multiple_tags").sum()
    ) if not detail.empty else 0
    amendments = int(
        detail["amendment_present"].fillna(False).sum()
    ) if not detail.empty else 0
    both_direct = int(
        detail["derivation_pattern"].eq("q2_q3_both_direct").sum()
    ) if not detail.empty else 0
    repeated_cik_count = int(
        repeat_ciks["cik"].nunique()
    ) if not repeat_ciks.empty else 0

    summary = {
        "schema_version": 1,
        "status": "TTM_Q4_DIFFERENCE_DIAGNOSTIC_COMPLETE",
        "as_of": args.as_of.isoformat(),
        "material_difference_rows": len(detail),
        "material_difference_unique_ciks": (
            int(detail["cik"].nunique()) if not detail.empty else 0
        ),
        "gt20pct_rows": gt20,
        "multiple_source_tag_rows": multiple_tags,
        "amendment_present_rows": amendments,
        "q2_q3_both_direct_rows": both_direct,
        "repeat_cik_rows": len(repeat_ciks),
        "repeat_unique_ciks": repeated_cik_count,
        "diagnostic_only": True,
        "factor_values_changed": False,
        "scoring_changed": False,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    ttm["ttm_q4_difference_dir"].mkdir(parents=True, exist_ok=False)
    detail.to_csv(ttm["ttm_q4_difference_detail"], index=False)
    by_magnitude.to_csv(
        ttm["ttm_q4_difference_by_magnitude"], index=False
    )
    by_derivation.to_csv(
        ttm["ttm_q4_difference_by_derivation"], index=False
    )
    by_tags.to_csv(
        ttm["ttm_q4_difference_by_tag_continuity"], index=False
    )
    by_amendment.to_csv(
        ttm["ttm_q4_difference_by_amendment"], index=False
    )
    repeat_ciks.to_csv(
        ttm["ttm_q4_difference_repeat_ciks"], index=False
    )
    ttm["ttm_q4_difference_summary"].write_text(
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
    ttm["ttm_q4_difference_fingerprints"].write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V2 Q4 TTM DIFFERENCE DIAGNOSTIC COMPLETE")
    print(f"Material rows:              {len(detail):,}")
    print(
        "Material unique CIKs:       "
        f"{detail['cik'].nunique() if not detail.empty else 0:,}"
    )
    print(f">20% difference rows:       {gt20:,}")
    print(f"Multiple-source-tag rows:   {multiple_tags:,}")
    print(f"Amendment-present rows:     {amendments:,}")
    print(f"Q2/Q3 both direct rows:     {both_direct:,}")
    print(f"Repeated unique CIKs:       {repeated_cik_count:,}")
    print(f"Output directory:           {ttm['ttm_q4_difference_dir']}")
    print("NO FACTOR VALUES OR SCORES WERE CHANGED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
