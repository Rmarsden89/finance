from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.liabilities_audit import (
    build_same_context_liabilities_identities,
    validate_liabilities_identity,
)
from finance.research.v2 import resolve_v2_sec_artifact_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Issue #26 SEC-only liabilities identity validation using "
            "strict same-accession, same-period, same-unit, same-acceptance "
            "Assets - Equity evidence."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    base = root / "reports" / "v3" / "data_sources" / args.as_of.isoformat()
    inventory_path = base / "gap_inventory" / "residual_gap_inventory.csv"
    if not inventory_path.exists():
        raise SystemExit(f"Missing V3 residual gap inventory: {inventory_path}")

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    candidates_path = v2["sec_candidates"]
    if not candidates_path.exists():
        raise SystemExit(f"Missing V2 SEC candidates: {candidates_path}")

    inventory = pd.read_csv(inventory_path, low_memory=False)
    candidates = pd.read_csv(candidates_path, low_memory=False)

    gap_tickers = set(
        inventory.loc[
            inventory["field"].astype(str).eq("total_liabilities")
            & inventory["recommended_action"].astype(str).eq(
                "research_identity_candidate"
            ),
            "ticker",
        ]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    identities = build_same_context_liabilities_identities(
        candidates,
        as_of=args.as_of,
    )
    if not identities.empty:
        identities["ticker"] = identities["ticker"].astype(str).str.upper()
    gap_candidates = identities.loc[
        identities["ticker"].isin(gap_tickers)
    ].copy()

    comparison, tag_summary = validate_liabilities_identity(
        candidates,
        as_of=args.as_of,
    )

    if gap_candidates.empty:
        ambiguity = pd.DataFrame(
            columns=[
                "ticker",
                "candidate_rows",
                "unique_derived_values",
                "min_derived_liabilities",
                "max_derived_liabilities",
            ]
        )
    else:
        ambiguity = (
            gap_candidates.groupby("ticker", as_index=False)
            .agg(
                candidate_rows=("derived_liabilities", "size"),
                unique_derived_values=("derived_liabilities", "nunique"),
                min_derived_liabilities=("derived_liabilities", "min"),
                max_derived_liabilities=("derived_liabilities", "max"),
            )
            .sort_values(
                ["unique_derived_values", "ticker"],
                ascending=[False, True],
                kind="stable",
            )
        )

    if comparison.empty:
        control_summary = {
            "comparison_rows": 0,
            "comparison_tickers": 0,
            "exact_matches": 0,
            "within_0_01_pct": 0,
            "within_0_1_pct": 0,
            "material_differences": 0,
            "exact_or_within_0_01_pct_rate": None,
            "within_0_1_pct_rate": None,
        }
    else:
        bands = comparison["validation_band"].value_counts()
        exact = int(bands.get("exact_match", 0))
        within_001 = int(bands.get("within_0_01_pct", 0))
        within_01 = int(bands.get("within_0_1_pct", 0))
        material = int(bands.get("material_difference", 0))
        total = len(comparison)
        control_summary = {
            "comparison_rows": int(total),
            "comparison_tickers": int(comparison["ticker"].nunique()),
            "exact_matches": exact,
            "within_0_01_pct": within_001,
            "within_0_1_pct": within_01,
            "material_differences": material,
            "exact_or_within_0_01_pct_rate": (
                float((exact + within_001) / total) if total else None
            ),
            "within_0_1_pct_rate": (
                float((exact + within_001 + within_01) / total)
                if total
                else None
            ),
        }

    summary = {
        "as_of": args.as_of.isoformat(),
        "identity_gap_tickers": int(len(gap_tickers)),
        "identity_gap_candidate_rows": int(len(gap_candidates)),
        "identity_gap_tickers_with_candidates": int(
            gap_candidates["ticker"].nunique()
            if not gap_candidates.empty
            else 0
        ),
        "identity_gap_single_value_tickers": int(
            ambiguity["unique_derived_values"].eq(1).sum()
            if not ambiguity.empty
            else 0
        ),
        "identity_gap_multi_value_tickers": int(
            ambiguity["unique_derived_values"].gt(1).sum()
            if not ambiguity.empty
            else 0
        ),
        **control_summary,
        "strict_context_keys": [
            "ticker",
            "accession",
            "period_date",
            "unit",
            "acceptance_timestamp",
            "quarter_context_when_present",
        ],
        "research_only": True,
        "model_inputs_modified": False,
        "paid_vendor_used": False,
    }

    output_dir = base / "liabilities_identity"
    output_dir.mkdir(parents=True, exist_ok=True)
    gap_path = output_dir / "gap_identity_candidates.csv"
    ambiguity_path = output_dir / "gap_identity_ambiguity.csv"
    comparison_path = output_dir / "control_identity_validation.csv"
    tag_path = output_dir / "control_identity_by_equity_tag.csv"
    summary_path = output_dir / "summary.json"

    gap_candidates.to_csv(gap_path, index=False)
    ambiguity.to_csv(ambiguity_path, index=False)
    comparison.to_csv(comparison_path, index=False)
    tag_summary.to_csv(tag_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V3 SEC-ONLY LIABILITIES IDENTITY VALIDATION")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Gap tickers:                 {summary['identity_gap_tickers']}")
    print(
        f"Gap tickers with candidates: "
        f"{summary['identity_gap_tickers_with_candidates']}"
    )
    print(
        f"Single derived value:        "
        f"{summary['identity_gap_single_value_tickers']}"
    )
    print(
        f"Multiple derived values:     "
        f"{summary['identity_gap_multi_value_tickers']}"
    )
    print(f"Control comparison rows:     {summary['comparison_rows']}")
    print(f"Control comparison tickers:  {summary['comparison_tickers']}")
    print(f"Exact matches:               {summary['exact_matches']}")
    print(f"Within 0.01%:                {summary['within_0_01_pct']}")
    print(f"Within 0.1%:                 {summary['within_0_1_pct']}")
    print(f"Material differences:        {summary['material_differences']}")
    print(f"Gap candidates:              {gap_path}")
    print(f"Gap ambiguity:               {ambiguity_path}")
    print(f"Control validation:          {comparison_path}")
    print(f"By equity tag:               {tag_path}")
    print(f"Summary:                     {summary_path}")
    print("NO PAID VENDOR WAS USED.")
    print("NO LIABILITIES CANDIDATES WERE PROMOTED INTO V1 OR V2.")


if __name__ == "__main__":
    main()
