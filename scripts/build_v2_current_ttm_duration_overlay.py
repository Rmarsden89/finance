from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.ttm_current_duration import (
    load_current_ttm_duration_candidates,
)
from finance.research.v2 import (
    resolve_v2_sec_artifact_paths,
    resolve_v2_ttm_diagnostic_paths,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Overlay current cached CompanyFacts duration evidence onto the "
            "V2-only quarterly TTM duration cache. Research-only."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--discovery",
        type=Path,
        default=Path("reports/sec_current_filing_discovery.csv"),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cache/sec/current"),
    )
    return parser.parse_args()


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    ttm = resolve_v2_ttm_diagnostic_paths(root, args.as_of)

    discovery_path = _resolve(root, args.discovery)
    cache_dir = _resolve(root, args.cache_dir)

    required = {
        "V2 manifest": v2["manifest"],
        "base duration winners": ttm["duration_winners"],
        "base duration summary": ttm["duration_summary"],
        "discovery": discovery_path,
        "SEC current cache": cache_dir,
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing current TTM duration overlay input(s):\n  "
            + "\n  ".join(missing)
        )

    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    if manifest.get("status") != "SEC_RESEARCH_COMPLETE":
        raise SystemExit("V2 SEC research must be complete")
    if any(
        bool(value)
        for value in manifest.get("execution_capabilities", {}).values()
    ):
        raise SystemExit("V2 manifest enables an execution capability")

    base_summary = json.loads(
        ttm["duration_summary"].read_text(encoding="utf-8")
    )
    if base_summary.get("status") != "TTM_DURATION_CACHE_COMPLETE":
        raise SystemExit("Base V2 TTM duration cache is not complete")
    if int(base_summary.get("unresolved_winner_groups", 1)) != 0:
        raise SystemExit("Base V2 TTM duration cache has unresolved winner groups")

    if ttm["current_duration_overlay_dir"].exists():
        raise SystemExit(
            "Current duration overlay already exists; preserve or rename it: "
            f"{ttm['current_duration_overlay_dir']}"
        )

    discovery = pd.read_csv(discovery_path, low_memory=False)
    bad_statuses = discovery.loc[
        discovery["status"].astype(str).isin(
            {"new_filing_partial", "submissions_error"}
        )
    ]
    if not bad_statuses.empty:
        raise SystemExit(
            "Current SEC discovery contains unresolved partial/error rows"
        )

    print("V2 CURRENT TTM DURATION OVERLAY")
    print(f"As of:                      {args.as_of.isoformat()}")
    print("Extracting current CompanyFacts duration evidence...", flush=True)

    current, audit = load_current_ttm_duration_candidates(
        discovery=discovery,
        cache_dir=cache_dir,
    )
    if not audit.empty and audit["status"].astype(str).eq("error").any():
        errors = audit.loc[audit["status"].astype(str).eq("error")]
        raise SystemExit(
            "Current TTM duration extraction failed for "
            f"{len(errors)} filing(s); inspect audit output after fixing source data."
        )

    base = pd.read_csv(ttm["duration_winners"], low_memory=False)
    current = current.copy()
    if not current.empty:
        for column in base.columns:
            if column not in current.columns:
                current[column] = pd.NA
        current = current[[*base.columns, *[
            c for c in current.columns if c not in base.columns
        ]]]

    combined = pd.concat([base, current], ignore_index=True, sort=False)
    before = len(combined)
    dedup_columns = [
        column
        for column in (
            "adsh",
            "cik",
            "concept",
            "ddate_date",
            "qtrs",
            "uom",
            "accepted_at",
            "value",
            "source_tag",
        )
        if column in combined.columns
    ]
    if dedup_columns:
        combined = combined.drop_duplicates(
            subset=dedup_columns,
            keep="last",
        )
    sort_columns = [
        column
        for column in (
            "cik",
            "accepted_at",
            "concept",
            "ddate_date",
            "qtrs",
            "source_tag",
        )
        if column in combined.columns
    ]
    if sort_columns:
        combined = combined.sort_values(
            sort_columns,
            kind="stable",
        ).reset_index(drop=True)

    ttm["current_duration_overlay_dir"].mkdir(
        parents=True,
        exist_ok=False,
    )
    current.to_csv(ttm["current_duration_candidates"], index=False)
    audit.to_csv(ttm["current_duration_audit"], index=False)
    combined.to_csv(ttm["current_duration_winners"], index=False)

    usable_filings = int(
        discovery["status"].astype(str).eq("new_filing_cached").sum()
    )
    summary = {
        "schema_version": 1,
        "status": "CURRENT_TTM_DURATION_OVERLAY_COMPLETE",
        "as_of": args.as_of.isoformat(),
        "base_duration_rows": len(base),
        "current_duration_rows": len(current),
        "combined_rows_before_dedup": before,
        "combined_rows_after_dedup": len(combined),
        "duplicates_removed": before - len(combined),
        "usable_current_filings": usable_filings,
        "current_filings_with_duration_rows": (
            int(audit["status"].astype(str).eq("ok").sum())
            if not audit.empty and "status" in audit.columns
            else 0
        ),
        "current_filings_without_duration_rows": (
            int(audit["status"].astype(str).eq("no_candidates").sum())
            if not audit.empty and "status" in audit.columns
            else 0
        ),
        "current_extraction_errors": (
            int(audit["status"].astype(str).eq("error").sum())
            if not audit.empty and "status" in audit.columns
            else 0
        ),
        "v1_artifacts_modified": False,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }
    ttm["current_duration_summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    ttm["current_duration_fingerprints"].write_text(
        json.dumps({
            "schema_version": 1,
            "decision_date": args.as_of.isoformat(),
            "direct_inputs": fingerprint_files(
                root=root,
                paths=[
                    v2["manifest"],
                    ttm["duration_winners"],
                    ttm["duration_summary"],
                    discovery_path,
                ],
            ),
            "code": git_provenance(root),
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V2 CURRENT TTM DURATION OVERLAY COMPLETE")
    print(f"Base duration rows:         {len(base):,}")
    print(f"Current duration rows:      {len(current):,}")
    print(f"Combined duration rows:     {len(combined):,}")
    print(f"Usable current filings:     {usable_filings:,}")
    print(f"Output:                     {ttm['current_duration_winners']}")
    print("V1 ARTIFACTS WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
