from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.share_cleanup import classify_residual_shares
from finance.research.v2 import (
    LONG_GROWTH_V2_RESEARCH,
    resolve_v2_sec_artifact_paths,
    resolve_v2_share_cleanup_paths,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Classify every remaining nonpositive V2 shares value and identify "
            "only defensible targeted cleanup actions."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    v2 = resolve_v2_sec_artifact_paths(repo_root, args.as_of)
    cleanup = resolve_v2_share_cleanup_paths(repo_root, args.as_of)
    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    if manifest.get("status") != "SEC_RESEARCH_COMPLETE":
        raise SystemExit("V2 SEC research must be complete before cleanup audit")
    discovery_value = manifest.get("input_artifacts", {}).get("discovery")
    if not discovery_value:
        raise SystemExit("V2 manifest is missing its discovery input")
    cache_value = manifest.get("input_artifacts", {}).get("SEC cache")
    if not cache_value:
        raise SystemExit("V2 manifest is missing its SEC cache input")

    required = {
        "snapshot": v2["current_snapshot"],
        "discovery": Path(discovery_value),
        "candidate audit": v2["sec_candidate_audit"],
        "candidates": v2["sec_candidates"],
        "SEC cache": Path(cache_value),
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.exists()]
    if missing:
        raise SystemExit("Missing cleanup input(s):\n  " + "\n  ".join(missing))

    detail, summary = classify_residual_shares(
        snapshot=pd.read_csv(required["snapshot"], low_memory=False),
        discovery=pd.read_csv(required["discovery"], low_memory=False),
        candidate_audit=pd.read_csv(required["candidate audit"], low_memory=False),
        candidates=pd.read_csv(required["candidates"], low_memory=False),
        cache_dir=required["SEC cache"],
    )
    cleanup["cleanup_dir"].mkdir(parents=True, exist_ok=True)
    detail.to_csv(cleanup["detail"], index=False)
    cleanup["summary"].write_text(
        json.dumps(asdict(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V2 RESIDUAL SHARES CLEANUP AUDIT")
    print(f"Positive shares:             {summary.positive_shares}/{summary.universe_rows}")
    print(f"Residual rows:               {summary.residual_rows}")
    print(f"Blank shares:                {summary.blank_shares}")
    print(f"Nonpositive shares:          {summary.nonpositive_shares}")
    print(f"Targeted SEC refresh:        {summary.targeted_sec_refresh}")
    print(f"Invalid-value investigation: {summary.investigate_invalid_value}")
    print(f"Candidate-not-selected bugs: {summary.candidate_not_selected}")
    print(f"No supported current fact:   {summary.documented_no_supported_fact}")
    print(f"Detail:                      {cleanup['detail']}")
    print(f"Summary:                     {cleanup['summary']}")
    print("NO DATA WAS REFRESHED OR PROMOTED.")


if __name__ == "__main__":
    main()
