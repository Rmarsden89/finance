from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the full Issue #26 research cohort containing every current "
            "shares_outstanding residual from the deterministic V3 gap inventory."
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

    inventory = pd.read_csv(inventory_path, low_memory=False)
    required = {
        "field",
        "ticker",
        "cik",
        "classification",
        "recommended_action",
    }
    missing = sorted(required - set(inventory.columns))
    if missing:
        raise SystemExit(
            "Residual gap inventory missing required columns: "
            + ", ".join(missing)
        )

    cohort = inventory.loc[
        inventory["field"].astype(str).eq("shares_outstanding")
    ].copy()
    cohort["ticker"] = cohort["ticker"].astype(str).str.upper().str.strip()
    cohort = (
        cohort.sort_values(["ticker", "classification"], kind="stable")
        .drop_duplicates("ticker", keep="first")
        .reset_index(drop=True)
    )
    cohort["sample_cohort"] = "full_current_share_residual"
    cohort["raw_share_validation_role"] = "residual"

    if "discovery_accessions" not in cohort.columns:
        cohort["discovery_accessions"] = ""

    output_dir = base / "raw_share_full_residual"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "validation_cohort.csv"
    cohort.to_csv(output, index=False)

    print("V3 FULL RAW SHARE RESIDUAL COHORT")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Residual share tickers:      {len(cohort)}")
    print(f"Cohort:                      {output}")
    print("NO MODEL INPUTS WERE MODIFIED.")


if __name__ == "__main__":
    main()
