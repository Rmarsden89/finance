from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd

from finance.data.current_shadow_panel import (
    assemble_current_scoring_panel,
    build_current_shadow_row,
    build_sec_only_weekly_extension,
)
from finance.data.sec_shadow_merge import merge_current_sec_shadow
from finance.data.sources.pitindex import load_pitindex_sp500
from finance.research.v2 import (
    LONG_GROWTH_V2_RESEARCH,
    resolve_v2_sec_artifact_paths,
    write_v2_research_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build isolated V2 SEC candidates, shadow winners, and current "
            "research panel. This command has no broker or order capabilities."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--data-baseline-id", required=True)
    parser.add_argument("--pitindex-data", type=Path, required=True)
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
    parser.add_argument(
        "--historical-sec",
        type=Path,
        default=Path("data/cache/sec/sec_winner_facts_all.csv"),
    )
    parser.add_argument(
        "--historical-panel",
        type=Path,
        default=Path("reports/weekly_research_panel_2015_2025.csv"),
    )
    parser.add_argument(
        "--market-snapshot",
        type=Path,
        help=(
            "Existing normalized market snapshot. Default: the dated V1 run "
            "artifact under reports/shadow/<as-of>. Read-only input."
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _resolve_input(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo_root / path


def _run_candidate_build(
    *,
    repo_root: Path,
    discovery: Path,
    cache_dir: Path,
    candidates: Path,
    audit: Path,
) -> None:
    command = [
        sys.executable,
        str(repo_root / "scripts" / "build_sec_current_candidates.py"),
        "--discovery",
        str(discovery),
        "--cache-dir",
        str(cache_dir),
        "--output",
        str(candidates),
        "--audit-output",
        str(audit),
        "--share-fallback-policy",
        "v2_dei_cover_date",
    ]
    subprocess.run(command, cwd=repo_root, check=True)


def main() -> None:
    args = parse_args()
    config = LONG_GROWTH_V2_RESEARCH
    config.validate()
    repo_root = args.repo_root.resolve()
    paths = resolve_v2_sec_artifact_paths(
        repo_root,
        args.as_of,
        config=config,
    )
    paths["run_dir"].mkdir(parents=True, exist_ok=True)

    discovery = _resolve_input(repo_root, args.discovery)
    cache_dir = _resolve_input(repo_root, args.cache_dir)
    historical_sec = _resolve_input(repo_root, args.historical_sec)
    historical_panel = _resolve_input(repo_root, args.historical_panel)
    pitindex_data = _resolve_input(repo_root, args.pitindex_data)
    market_snapshot = _resolve_input(
        repo_root,
        args.market_snapshot
        or Path("reports")
        / "shadow"
        / args.as_of.isoformat()
        / "robinhood_market_snapshot_normalized.csv",
    )

    required = {
        "discovery": discovery,
        "SEC cache": cache_dir,
        "historical SEC winners": historical_sec,
        "historical panel": historical_panel,
        "PITIndex data": pitindex_data,
        "normalized market snapshot": market_snapshot,
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.exists()]
    if missing:
        raise SystemExit("Missing required V2 input(s):\n  " + "\n  ".join(missing))

    write_v2_research_manifest(
        repo_root=repo_root,
        as_of=args.as_of,
        data_baseline_id=args.data_baseline_id,
        config=config,
    )

    print("V2 SEC RESEARCH BUILD", flush=True)
    print(f"Model:                      {config.model_id}", flush=True)
    print(f"As of:                      {args.as_of.isoformat()}", flush=True)
    print(f"Run directory:              {paths['run_dir']}", flush=True)
    print("Execution capabilities:     disabled", flush=True)

    _run_candidate_build(
        repo_root=repo_root,
        discovery=discovery,
        cache_dir=cache_dir,
        candidates=paths["sec_candidates"],
        audit=paths["sec_candidate_audit"],
    )

    historical = pd.read_csv(historical_sec, low_memory=False)
    current = pd.read_csv(paths["sec_candidates"], low_memory=False)
    merged, merge_audit, merge_summary = merge_current_sec_shadow(
        historical,
        current,
        as_of=pd.Timestamp(args.as_of),
    )
    merged.to_csv(paths["sec_shadow"], index=False)
    merge_audit.to_csv(paths["sec_merge_audit"], index=False)
    pd.DataFrame([asdict(merge_summary)]).to_csv(
        paths["sec_merge_summary"], index=False
    )

    intervals = load_pitindex_sp500(pitindex_data)
    market = pd.read_csv(market_snapshot, low_memory=False)
    current_snapshot = build_current_shadow_row(
        intervals,
        merged,
        market,
        as_of=pd.Timestamp(args.as_of),
    )
    last_friday = pd.Timestamp(args.as_of) - pd.offsets.Week(weekday=4)
    if last_friday.date() == args.as_of:
        last_friday -= pd.Timedelta(days=7)
    extension = build_sec_only_weekly_extension(
        intervals,
        merged,
        start=pd.Timestamp("2026-01-02"),
        end=last_friday,
    )
    historical_panel_frame = pd.read_csv(historical_panel, low_memory=False)
    scoring_panel = assemble_current_scoring_panel(
        historical_panel_frame,
        extension,
        current_snapshot,
    )
    scoring_panel.to_csv(paths["scoring_panel"], index=False)
    current_snapshot.to_csv(paths["current_snapshot"], index=False)

    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["status"] = "SEC_RESEARCH_COMPLETE"
    manifest["input_artifacts"] = {
        name: str(path) for name, path in required.items()
    }
    manifest["output_artifacts"] = {
        name: str(path)
        for name, path in paths.items()
        if name not in {"run_dir", "manifest"}
    }
    manifest["coverage"] = {
        "universe_rows": int(len(current_snapshot)),
        "shares_outstanding_present": int(
            pd.to_numeric(
                current_snapshot.get("shares_outstanding"), errors="coerce"
            ).gt(0).sum()
        ),
    }
    paths["manifest"].write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V2 SEC RESEARCH COMPLETE", flush=True)
    print(f"Current snapshot rows:      {len(current_snapshot):,}", flush=True)
    print(
        "Shares coverage:           "
        f"{manifest['coverage']['shares_outstanding_present']:,}/"
        f"{manifest['coverage']['universe_rows']:,}",
        flush=True,
    )
    print(f"Manifest:                   {paths['manifest']}", flush=True)
    print("V1 reports and SEC shadow cache were NOT modified.", flush=True)
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.", flush=True)


if __name__ == "__main__":
    main()
