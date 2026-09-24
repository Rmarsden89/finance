from __future__ import annotations

import argparse
from datetime import date
import hashlib
from pathlib import Path

import pandas as pd

from finance.research.v2 import resolve_v2_sec_artifact_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic SEC-only control cohort for validating "
            "LiabilitiesCurrent + LiabilitiesNoncurrent against direct Liabilities."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--control-count", type=int, default=50)
    return parser.parse_args()


def _rank(as_of: str, ticker: str) -> str:
    return hashlib.sha256(
        f"{as_of}|liabilities_current_noncurrent_control|{ticker.upper()}".encode(
            "utf-8"
        )
    ).hexdigest()


def main() -> None:
    args = parse_args()
    if args.control_count < 1:
        raise SystemExit("--control-count must be >= 1")

    root = args.repo_root.resolve()
    base = root / "reports" / "v3" / "data_sources" / args.as_of.isoformat()
    inventory_path = base / "gap_inventory" / "residual_gap_inventory.csv"
    if not inventory_path.exists():
        raise SystemExit(f"Missing V3 residual gap inventory: {inventory_path}")

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    snapshot_path = v2["current_snapshot"]
    if not snapshot_path.exists():
        raise SystemExit(f"Missing V2 current snapshot: {snapshot_path}")

    inventory = pd.read_csv(inventory_path, low_memory=False)
    snapshot = pd.read_csv(snapshot_path, low_memory=False)
    snapshot["ticker"] = snapshot["ticker"].astype(str).str.upper().str.strip()

    alternate = set(
        inventory.loc[
            inventory["field"].astype(str).eq("total_liabilities")
            & inventory["recommended_action"].astype(str).eq(
                "research_alternate_tag"
            ),
            "ticker",
        ]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    liabilities = pd.to_numeric(snapshot["total_liabilities"], errors="coerce")
    controls = snapshot.loc[
        liabilities.gt(0)
        & ~snapshot["ticker"].isin(alternate)
    ].copy()
    controls["_sample_rank"] = controls["ticker"].map(
        lambda ticker: _rank(args.as_of.isoformat(), ticker)
    )
    controls = (
        controls.sort_values(["_sample_rank", "ticker"], kind="stable")
        .drop_duplicates("ticker", keep="first")
        .head(args.control_count)
        .copy()
    )
    if len(controls) < args.control_count:
        raise SystemExit(
            f"Only {len(controls)} eligible direct-liabilities controls available; "
            f"requested {args.control_count}."
        )

    controls["sample_cohort"] = "liabilities_current_noncurrent_control"
    controls["raw_share_validation_role"] = "control"
    controls["discovery_accessions"] = ""
    controls["sample_rank"] = controls["_sample_rank"]
    controls = controls.drop(columns=["_sample_rank"])

    output_dir = base / "liabilities_current_noncurrent"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "validation_cohort.csv"
    controls.to_csv(output, index=False)

    print("V3 LIABILITIES CURRENT+NONCURRENT CONTROL COHORT")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Controls:                    {len(controls)}")
    print(f"Cohort:                      {output}")
    print("NO MODEL INPUTS WERE MODIFIED.")


if __name__ == "__main__":
    main()
