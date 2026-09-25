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
            "Build the deterministic Issue #26 raw-SEC shares validation cohort: "
            "all pilot shares residuals plus a larger SEC-supported control set."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--control-count", type=int, default=50)
    return parser.parse_args()


def _rank(as_of: str, ticker: str) -> str:
    return hashlib.sha256(
        f"{as_of}|raw_share_control|{ticker.upper()}".encode("utf-8")
    ).hexdigest()


def main() -> None:
    args = parse_args()
    if args.control_count < 1:
        raise SystemExit("--control-count must be >= 1")

    root = args.repo_root.resolve()
    base = root / "reports" / "v3" / "data_sources" / args.as_of.isoformat()
    pilot_path = base / "gap_inventory" / "overlap_validation_sample.csv"
    if not pilot_path.exists():
        raise SystemExit(f"Missing pilot validation sample: {pilot_path}")

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    snapshot_path = v2["current_snapshot"]
    if not snapshot_path.exists():
        raise SystemExit(f"Missing V2 current snapshot: {snapshot_path}")

    pilot = pd.read_csv(pilot_path, low_memory=False)
    snapshot = pd.read_csv(snapshot_path, low_memory=False)

    required_pilot = {"ticker", "cik", "sample_cohort", "field"}
    missing = sorted(required_pilot - set(pilot.columns))
    if missing:
        raise SystemExit(
            "Pilot sample missing required columns: " + ", ".join(missing)
        )

    required_snapshot = {"ticker", "cik", "shares_outstanding"}
    missing = sorted(required_snapshot - set(snapshot.columns))
    if missing:
        raise SystemExit(
            "Current snapshot missing required columns: " + ", ".join(missing)
        )

    pilot["ticker"] = pilot["ticker"].astype(str).str.upper().str.strip()
    residual = pilot.loc[
        pilot["field"].astype(str).str.contains("shares_outstanding", regex=False)
        & ~pilot["sample_cohort"].astype(str).eq("sec_supported_control")
    ].copy()
    residual["raw_share_validation_role"] = "residual"
    if "discovery_accessions" not in residual.columns:
        residual["discovery_accessions"] = ""

    snap = snapshot.copy()
    snap["ticker"] = snap["ticker"].astype(str).str.upper().str.strip()
    shares = pd.to_numeric(snap["shares_outstanding"], errors="coerce")
    controls = snap.loc[
        shares.gt(0)
        & ~snap["ticker"].isin(set(pilot["ticker"]))
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
            f"Only {len(controls)} eligible SEC-supported controls available; "
            f"requested {args.control_count}."
        )

    controls["sample_cohort"] = "raw_share_supported_control"
    controls["raw_share_validation_role"] = "control"
    controls["field"] = "shares_outstanding"
    controls["classification"] = "sec_supported_control"
    controls["recommended_action"] = "validate_raw_share_overlap"
    controls["discovery_accessions"] = ""
    controls["sample_rank"] = controls["_sample_rank"]
    controls = controls.drop(columns=["_sample_rank"])

    desired = [
        "ticker",
        "cik",
        "company_name",
        "sample_cohort",
        "raw_share_validation_role",
        "field",
        "classification",
        "recommended_action",
        "discovery_accessions",
        "sample_rank",
    ]
    for column in desired:
        if column not in residual.columns:
            residual[column] = ""
        if column not in controls.columns:
            controls[column] = ""

    cohort = pd.concat(
        [residual[desired], controls[desired]],
        ignore_index=True,
        sort=False,
    )

    output_dir = base / "raw_share_validation"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "validation_cohort.csv"
    cohort.to_csv(output, index=False)

    print("V3 RAW SHARE VALIDATION COHORT")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Residual share cases:        {len(residual)}")
    print(f"SEC-supported controls:      {len(controls)}")
    print(f"Total cohort rows:           {len(cohort)}")
    print(f"Cohort:                      {output}")
    print("NO MODEL INPUTS WERE MODIFIED.")


if __name__ == "__main__":
    main()
