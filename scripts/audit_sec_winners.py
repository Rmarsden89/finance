from __future__ import annotations

import argparse
from pathlib import Path

from finance.data.sec_canonical import build_canonical_facts
from finance.data.sec_winners import select_canonical_winners
from finance.data.sources.sec_financial_statements import (
    load_sec_financial_statement_zip,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit deterministic winner selection for SEC canonical facts."
    )
    parser.add_argument("zip_path", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/sec_winner_audit.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    quarter = load_sec_financial_statement_zip(args.zip_path)
    canonical, _ = build_canonical_facts(
        quarter.submissions,
        quarter.numeric_facts,
        quarter.presentation,
    )

    winners, audit, summary = select_canonical_winners(canonical)

    print("SEC CANONICAL WINNER AUDIT")
    print(f"Input canonical rows:        {summary.rows_input:,}")
    print(f"Duplicate groups:            {summary.duplicate_groups_input:,}")
    print(f"Resolved same-value:         {summary.groups_resolved_by_same_value:,}")
    print(f"Resolved by tag priority:    {summary.groups_resolved_by_tag_priority:,}")
    print(f"Unresolved priority ties:    {summary.unresolved_groups:,}")
    print(f"Winner rows:                 {summary.rows_output:,}")

    if not audit.empty:
        print()
        print("WINNERS BY RESOLUTION")
        for resolution, count in audit["resolution"].value_counts().items():
            print(f"{resolution:26s} {count:8,d}")

        print()
        print("TAG-PRIORITY WINNERS")
        priority = audit.loc[audit["resolution"].eq("tag_priority")]
        if priority.empty:
            print("none")
        else:
            for tag, count in priority["winner_tag"].value_counts().items():
                print(f"{tag:70.70s} {count:8,d}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    audit.to_csv(args.output, index=False)
    print()
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
