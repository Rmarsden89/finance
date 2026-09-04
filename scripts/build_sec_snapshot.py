from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from finance.data.sec_snapshot import (
    build_winner_facts,
    latest_facts_as_of,
    pivot_snapshot,
)
from finance.data.sources.sec_financial_statements import (
    load_sec_financial_statement_zip,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and export deterministic PIT SEC canonical fundamentals."
    )
    parser.add_argument("zip_path", type=Path)
    parser.add_argument(
        "--winner-output",
        type=Path,
        default=Path("data/cache/sec/2015q1_winner_facts.csv"),
    )
    parser.add_argument(
        "--as-of",
        help="Optional ISO timestamp, e.g. 2015-03-15T16:00:00.",
    )
    parser.add_argument(
        "--snapshot-output",
        type=Path,
        default=Path("reports/sec_snapshot.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    quarter = load_sec_financial_statement_zip(args.zip_path)

    winners = build_winner_facts(
        quarter.submissions,
        quarter.numeric_facts,
        quarter.presentation,
    )

    args.winner_output.parent.mkdir(parents=True, exist_ok=True)
    winners.to_csv(args.winner_output, index=False)

    print("SEC WINNER FACT MATERIALIZATION")
    print(f"Winner rows:     {len(winners):,}")
    print(f"Unique CIKs:     {winners['cik'].nunique(dropna=True):,}")
    print(f"Winner output:   {args.winner_output}")

    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of)
        latest = latest_facts_as_of(winners, as_of)
        snapshot = pivot_snapshot(latest)

        args.snapshot_output.parent.mkdir(parents=True, exist_ok=True)
        snapshot.to_csv(args.snapshot_output, index=False)

        print()
        print("PIT SNAPSHOT")
        print(f"As of:           {as_of}")
        print(f"Latest facts:    {len(latest):,}")
        print(f"Snapshot CIKs:   {len(snapshot):,}")
        print(f"Snapshot output: {args.snapshot_output}")


if __name__ == "__main__":
    main()
