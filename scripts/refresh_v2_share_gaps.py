from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd

from finance.data.sec_recovery import merge_targeted_recovery
from finance.research.v2 import (
    resolve_v2_sec_artifact_paths,
    resolve_v2_share_cleanup_paths,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Retry only residual V2 shares tickers classified as SEC "
            "discovery/cache gaps. Does not build winners or contact a broker."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument("--request-delay", type=float, default=0.5)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    v2 = resolve_v2_sec_artifact_paths(repo_root, args.as_of)
    cleanup = resolve_v2_share_cleanup_paths(repo_root, args.as_of)
    if not cleanup["detail"].exists():
        raise SystemExit(
            "Missing cleanup plan; run audit_v2_residual_shares.py first"
        )
    if not os.environ.get("SEC_USER_AGENT", "").strip():
        raise SystemExit("SEC_USER_AGENT is required for targeted SEC refresh")

    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    inputs = manifest.get("input_artifacts", {})
    required_inputs = {
        "discovery": "discovery",
        "SEC cache": "SEC cache",
        "historical SEC winners": "historical SEC winners",
        "historical panel": "historical panel",
        "normalized market snapshot": "normalized market snapshot",
    }
    missing_keys = [key for key in required_inputs if not str(inputs.get(key, "")).strip()]
    if missing_keys:
        raise SystemExit(
            "Manifest missing cleanup inputs: " + ", ".join(missing_keys)
        )
    discovery = Path(inputs["discovery"])
    cache_dir = Path(inputs["SEC cache"])
    historical_sec = Path(inputs["historical SEC winners"])
    historical_panel = Path(inputs["historical panel"])
    market_snapshot = Path(inputs["normalized market snapshot"])
    missing = [
        str(path)
        for path in (
            discovery,
            cache_dir,
            historical_sec,
            historical_panel,
            market_snapshot,
            args.pitindex_data,
        )
        if not path.exists()
    ]
    if missing:
        raise SystemExit("Missing cleanup dependency: " + ", ".join(missing))

    plan = pd.read_csv(cleanup["detail"], low_memory=False)
    targets = sorted(
        set(
            plan.loc[
                plan["recommended_action"].astype(str).eq("targeted_sec_refresh"),
                "ticker",
            ].astype(str).str.upper()
        )
    )
    if not targets:
        print("No targeted SEC discovery/cache gaps remain.")
        return

    command = [
        sys.executable,
        str(repo_root / "scripts" / "update_sec_current.py"),
        "--pitindex-data", str(args.pitindex_data),
        "--winner-facts", str(historical_sec),
        "--as-of", args.as_of.isoformat(),
        "--cache-dir", str(cache_dir),
        "--output", str(cleanup["targeted_discovery"]),
        "--request-delay", str(args.request_delay),
        "--recovery-attempts", "2",
        "--recovery-request-delay", str(max(args.request_delay, 0.5)),
    ]
    for ticker in targets:
        command.extend(["--ticker", ticker])

    print("V2 TARGETED SHARES GAP REFRESH", flush=True)
    print(f"Tickers:                    {', '.join(targets)}", flush=True)
    print("Provider:                   SEC only", flush=True)
    subprocess.run(command, cwd=repo_root, check=True)

    base = pd.read_csv(discovery, low_memory=False)
    retry = pd.read_csv(cleanup["targeted_discovery"], low_memory=False)
    merged = merge_targeted_recovery(base, retry)
    merged.to_csv(cleanup["merged_discovery"], index=False)

    unresolved = retry.loc[
        retry["status"].astype(str).isin(
            {"new_filing_partial", "submissions_error"}
        )
    ]
    print()
    print("TARGETED REFRESH COMPLETE")
    print(f"Requested tickers:          {len(targets)}")
    print(f"Retry rows:                 {len(retry)}")
    print(f"Unresolved error rows:      {len(unresolved)}")
    print(f"Merged discovery:           {cleanup['merged_discovery']}")
    print("V1 reports and winner caches were NOT modified.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")
    print()
    print("Next isolated rebuild:")
    print(
        f'py scripts\\run_v2_sec_research.py --as-of {args.as_of.isoformat()} '
        f'--data-baseline-id "{manifest.get("data_baseline_id", "")}" '
        f'--pitindex-data "{args.pitindex_data}" '
        f'--discovery "{cleanup["merged_discovery"]}" '
        f'--market-snapshot "{market_snapshot}"'
    )


if __name__ == "__main__":
    main()
