from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finance.data.sec_incremental import materialize_sec_quarters_incrementally
from finance.data.sec_snapshot import latest_facts_as_of, pivot_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Incrementally build continuous SEC winner facts from quarterly ZIPs."
    )
    parser.add_argument(
        "zip_dir",
        type=Path,
        help="Directory containing SEC quarterly ZIPs.",
    )
    parser.add_argument(
        "--pattern",
        default="*.zip",
        help="Glob pattern inside zip_dir. Default: *.zip",
    )
    parser.add_argument(
        "--quarter-cache-dir",
        type=Path,
        default=Path("data/cache/sec/quarters"),
        help="Per-quarter winner-fact cache directory.",
    )
    parser.add_argument(
        "--combined-output",
        type=Path,
        default=Path("data/cache/sec/sec_winner_facts_all.csv"),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess quarterly ZIPs even when quarter caches already exist.",
    )
    parser.add_argument(
        "--as-of",
        help="Optional ISO timestamp for a PIT snapshot.",
    )
    parser.add_argument(
        "--snapshot-output",
        type=Path,
        default=Path("reports/sec_snapshot_all.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    zip_paths = sorted(args.zip_dir.glob(args.pattern))

    if not zip_paths:
        raise SystemExit(
            f"No SEC ZIPs found in {args.zip_dir} matching {args.pattern!r}"
        )

    winners, audit = materialize_sec_quarters_incrementally(
        zip_paths,
        quarter_cache_dir=args.quarter_cache_dir,
        force=args.force,
    )

    args.combined_output.parent.mkdir(parents=True, exist_ok=True)
    winners.to_csv(args.combined_output, index=False)

    print("SEC INCREMENTAL MATERIALIZATION")
    print(f"Source ZIPs:               {audit.zip_count}")
    print(f"Processed quarters:        {audit.processed_quarters}")
    print(f"Reused quarter caches:     {audit.reused_quarters}")
    print(f"Rows before dedup:         {audit.combined_rows_before_dedup:,}")
    print(f"Rows after dedup:          {audit.combined_rows_after_dedup:,}")
    print(f"Duplicate rows removed:    {audit.duplicate_rows_removed:,}")
    print(f"Unique CIKs:               {audit.unique_ciks:,}")
    print(f"Quarter cache dir:         {args.quarter_cache_dir}")
    print(f"Combined output:           {args.combined_output}")

    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of)
        latest = latest_facts_as_of(winners, as_of)
        snapshot = pivot_snapshot(latest)

        args.snapshot_output.parent.mkdir(parents=True, exist_ok=True)
        snapshot.to_csv(args.snapshot_output, index=False)

        print()
        print("PIT SNAPSHOT")
        print(f"As of:                     {as_of}")
        print(f"Latest facts:              {len(latest):,}")
        print(f"Snapshot CIKs:             {len(snapshot):,}")
        print(f"Snapshot output:           {args.snapshot_output}")


if __name__ == "__main__":
    main()
