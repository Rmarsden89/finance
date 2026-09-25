from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.v2 import resolve_v2_sec_artifact_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a V3-only current coverage and Financial Health impact report "
            "using only historically clean liabilities recoveries. "
            "Does not modify V1 or frozen V2."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    base = root / "reports" / "v3" / "data_sources" / args.as_of.isoformat()
    validation_dir = base / "liabilities_historical_validation"

    collapsed_path = (
        validation_dir / "current_candidate_identity_collapsed_by_ticker.csv"
    )
    if not collapsed_path.exists():
        raise SystemExit(f"Missing collapsed corroboration file: {collapsed_path}")

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    snapshot_path = v2["current_snapshot"]
    if not snapshot_path.exists():
        raise SystemExit(f"Missing current snapshot: {snapshot_path}")

    clean = pd.read_csv(collapsed_path, low_memory=False)
    clean["ticker"] = clean["ticker"].astype(str).str.upper().str.strip()
    clean = clean.loc[
        clean["candidate_classification"].astype(str).eq(
            "historically_corroborated_clean"
        )
    ].copy()

    recovery_sources = [
        base / "liabilities_identity_raw_strict" / "raw_sec_detail.csv",
        base / "liabilities_no_supported_current" / "raw_sec_detail.csv",
        base / "liabilities_alternate_tags" / "alternate_tag_detail.csv",
    ]
    recovery_frames = []
    for path in recovery_sources:
        if path.exists():
            frame = pd.read_csv(path, low_memory=False)
            frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
            recovery_frames.append(frame)
    if not recovery_frames:
        raise SystemExit("No current liabilities recovery detail files found")

    recoveries = pd.concat(recovery_frames, ignore_index=True)
    recoveries = recoveries.loc[
        recoveries["status"].astype(str).eq("current_plus_noncurrent_candidate")
    ].copy()
    recoveries = recoveries.loc[
        recoveries["ticker"].isin(set(clean["ticker"]))
    ].copy()
    recoveries["constructed_liabilities"] = pd.to_numeric(
        recoveries["current_plus_noncurrent"], errors="coerce"
    )
    recoveries = recoveries.loc[
        recoveries["constructed_liabilities"].gt(0)
    ].drop_duplicates("ticker", keep="first")

    snapshot = pd.read_csv(snapshot_path, low_memory=False)
    snapshot["ticker"] = snapshot["ticker"].astype(str).str.upper().str.strip()

    baseline_liabilities = pd.to_numeric(
        snapshot["total_liabilities"], errors="coerce"
    )
    snapshot["_baseline_positive_liabilities"] = baseline_liabilities.gt(0)

    recovery_map = recoveries.set_index("ticker")[
        "constructed_liabilities"
    ].to_dict()
    snapshot["_v3_candidate_liabilities"] = baseline_liabilities
    missing_mask = ~snapshot["_baseline_positive_liabilities"]
    snapshot.loc[
        missing_mask & snapshot["ticker"].isin(recovery_map),
        "_v3_candidate_liabilities",
    ] = snapshot.loc[
        missing_mask & snapshot["ticker"].isin(recovery_map),
        "ticker",
    ].map(recovery_map)

    snapshot["_v3_positive_liabilities"] = pd.to_numeric(
        snapshot["_v3_candidate_liabilities"], errors="coerce"
    ).gt(0)

    detail = snapshot.loc[
        snapshot["ticker"].isin(set(recoveries["ticker"]))
    ].copy()
    detail = detail.merge(
        recoveries[
            [
                "ticker",
                "constructed_liabilities",
                "current_value",
                "noncurrent_value",
                "context_instant",
            ]
        ],
        on="ticker",
        how="left",
        validate="one_to_one",
    )

    # Financial Health impact is reported only from fields already present in the
    # current snapshot. We do not reproduce or alter the model's scoring code.
    health_count_col = next(
        (
            c
            for c in (
                "financial_health_factor_count",
                "health_factor_count",
            )
            if c in snapshot.columns
        ),
        None,
    )
    health_eligible_col = next(
        (
            c
            for c in (
                "financial_health_eligible",
                "health_eligible",
            )
            if c in snapshot.columns
        ),
        None,
    )

    health_detail = detail[
        ["ticker", "cik", "company_name"]
        + ([health_count_col] if health_count_col else [])
        + ([health_eligible_col] if health_eligible_col else [])
    ].copy()

    if health_count_col:
        counts = pd.to_numeric(
            health_detail[health_count_col], errors="coerce"
        )
        health_detail["baseline_health_factor_count"] = counts
        # The V3 candidate changes only liabilities availability. The report
        # shows the mechanical +1 availability effect for rows where liabilities
        # were previously missing; it does not recompute scores or eligibility.
        health_detail["v3_candidate_health_factor_count"] = counts + 1
    else:
        health_detail["baseline_health_factor_count"] = pd.NA
        health_detail["v3_candidate_health_factor_count"] = pd.NA

    if health_eligible_col:
        health_detail["baseline_health_eligible"] = health_detail[
            health_eligible_col
        ]
    else:
        health_detail["baseline_health_eligible"] = pd.NA

    summary = {
        "as_of": args.as_of.isoformat(),
        "universe_rows": int(len(snapshot)),
        "historically_clean_recovery_candidates": int(len(recoveries)),
        "baseline_positive_liabilities": int(
            snapshot["_baseline_positive_liabilities"].sum()
        ),
        "v3_candidate_positive_liabilities": int(
            snapshot["_v3_positive_liabilities"].sum()
        ),
        "positive_liabilities_gain": int(
            snapshot["_v3_positive_liabilities"].sum()
            - snapshot["_baseline_positive_liabilities"].sum()
        ),
        "baseline_liabilities_coverage_pct": float(
            snapshot["_baseline_positive_liabilities"].mean()
        ),
        "v3_candidate_liabilities_coverage_pct": float(
            snapshot["_v3_positive_liabilities"].mean()
        ),
        "health_factor_count_available": health_count_col is not None,
        "health_eligibility_available": health_eligible_col is not None,
        "score_recomputed": False,
        "eligibility_recomputed": False,
        "model_inputs_modified": False,
        "v1_modified": False,
        "v2_modified": False,
        "research_only": True,
    }

    output_dir = base / "v3_liabilities_impact"
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "recovery_detail.csv"
    health_path = output_dir / "financial_health_impact_detail.csv"
    summary_path = output_dir / "summary.json"

    detail.to_csv(detail_path, index=False)
    health_detail.to_csv(health_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V3-ONLY LIABILITIES COVERAGE / HEALTH IMPACT")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Universe rows:               {summary['universe_rows']}")
    print(
        f"Historically clean recoveries: "
        f"{summary['historically_clean_recovery_candidates']}"
    )
    print(
        f"Positive liabilities:        "
        f"{summary['baseline_positive_liabilities']} -> "
        f"{summary['v3_candidate_positive_liabilities']} "
        f"(+{summary['positive_liabilities_gain']})"
    )
    print(
        f"Coverage:                    "
        f"{summary['baseline_liabilities_coverage_pct']:.2%} -> "
        f"{summary['v3_candidate_liabilities_coverage_pct']:.2%}"
    )
    print(
        f"Health factor count present: "
        f"{summary['health_factor_count_available']}"
    )
    print(
        f"Health eligibility present:  "
        f"{summary['health_eligibility_available']}"
    )
    print(f"Recovery detail:             {detail_path}")
    print(f"Health detail:               {health_path}")
    print(f"Summary:                     {summary_path}")
    print("NO V1 OR V2 INPUTS, SCORES, OR ELIGIBILITY RULES WERE MODIFIED.")


if __name__ == "__main__":
    main()
