from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.v2 import (
    resolve_v2_liabilities_audit_paths,
    resolve_v2_share_cleanup_paths,
)
from finance.research.v3_data_sources import (
    build_residual_gap_inventory,
    summarize_gap_inventory,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the Issue #26 V3 residual fundamental-gap inventory from "
            "already-classified V2 shares/liabilities evidence."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()

    shares = resolve_v2_share_cleanup_paths(repo_root, args.as_of)
    liabilities = resolve_v2_liabilities_audit_paths(repo_root, args.as_of)

    required = {
        "shares detail": shares["detail"],
        "liabilities detail": liabilities["detail"],
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.exists()]
    if missing:
        raise SystemExit(
            "Missing Issue #26 source artifact(s). Run the completed V2 gap audits "
            "for this as-of date first:\n  " + "\n  ".join(missing)
        )

    inventory, summary = build_residual_gap_inventory(
        shares_detail=pd.read_csv(required["shares detail"], low_memory=False),
        liabilities_detail=pd.read_csv(
            required["liabilities detail"], low_memory=False
        ),
    )
    grouped = summarize_gap_inventory(inventory)

    output_dir = (
        repo_root
        / "reports"
        / "v3"
        / "data_sources"
        / args.as_of.isoformat()
        / "gap_inventory"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    inventory_path = output_dir / "residual_gap_inventory.csv"
    grouped_path = output_dir / "residual_gap_summary.csv"
    summary_path = output_dir / "summary.json"

    inventory.to_csv(inventory_path, index=False)
    grouped.to_csv(grouped_path, index=False)
    summary_path.write_text(
        json.dumps(asdict(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V3 RESIDUAL FUNDAMENTAL GAP INVENTORY")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Total gap rows:              {summary.total_gap_rows}")
    print(f"Unique gap tickers:          {summary.unique_gap_tickers}")
    print(f"Shares gap rows:             {summary.shares_gap_rows}")
    print(f"Liabilities gap rows:        {summary.liabilities_gap_rows}")
    print(f"Tickers missing both:        {summary.tickers_missing_both}")
    print(f"Inventory:                   {inventory_path}")
    print(f"Grouped summary:             {grouped_path}")
    print(f"Summary:                     {summary_path}")
    print("NO MODEL INPUTS OR LIVE/SHADOW ARTIFACTS WERE MODIFIED.")


if __name__ == "__main__":
    main()
