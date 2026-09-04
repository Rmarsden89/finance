from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finance.data.sec_multi_quarter import build_multi_quarter_winner_facts
from finance.data.sec_snapshot import latest_facts_as_of, pivot_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build continuous SEC winner facts from a directory of quarterly ZIPs."
    )
    parser.add_argument(
        "zip_dir",
        type=Path,
        help="Directory containing SEC quarterly ZIPs such as 2015q1.zip ... 2015q4.zip.",
    )
    parser.add_argument(
        "--pattern",
        default="*.zip",
        help="Glob pattern inside zip_dir. Default: *.zip",
    )
    parser.add_argument(
        "--winner-output",
        type=Path,
        default=Path("data/cache/sec/sec_winner_facts_multi.csv"),
    )
    parser.add_argument(
        "--as-of",
        help="Optional ISO timestamp for PIT snapshot, e.g. 2015-09-15T16:00:00.",
    )
    parser.add_argument(
        "--snapshot-output",
        type=Path,
        default=Path("reports/sec_snapshot_multi.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    zip_paths = sorted(args.zip_dir.glob(args.pattern))

    if not zip_paths:
        raise SystemExit(
            f"No SEC ZIPs found in {args.zip_dir} matching {args.pattern!r}"
        )

    winners, audit = build_multi_quarter_winner_facts(zip_paths)

    args.winner_output.parent.mkdir(parents=True, exist_ok=True)
    winners.to_csv(args.winner_output, index=False)

    print("SEC MULTI-QUARTER MATERIALIZATION")
    print(f"ZIPs:                     {audit.zip_count}")
    print(f"Winner rows before dedup: {audit.winner_rows_before_dedup:,}")
    print(f"Winner rows after dedup:  {audit.winner_rows_after_dedup:,}")
    print(f"Duplicate rows removed:   {audit.duplicate_rows_removed:,}")
    print(f"Unique CIKs:              {audit.unique_ciks:,}")
    print(f"Winner output:            {args.winner_output}")

    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of)
        latest = latest_facts_as_of(winners, as_of)
        snapshot = pivot_snapshot(latest)

        args.snapshot_output.parent.mkdir(parents=True, exist_ok=True)
        snapshot.to_csv(args.snapshot_output, index=False)

        print()
        print("MULTI-QUARTER PIT SNAPSHOT")
        print(f"As of:                    {as_of}")
        print(f"Latest facts:             {len(latest):,}")
        print(f"Snapshot CIKs:            {len(snapshot):,}")
        print(f"Snapshot output:          {args.snapshot_output}")


if __name__ == "__main__":
    main()
