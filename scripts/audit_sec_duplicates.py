from __future__ import annotations

import argparse
from pathlib import Path

from finance.data.sec_canonical import build_canonical_facts
from finance.data.sec_duplicates import audit_duplicate_groups
from finance.data.sources.sec_financial_statements import (
    load_sec_financial_statement_zip,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit remaining duplicate SEC canonical fact groups."
    )
    parser.add_argument("zip_path", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/sec_duplicate_audit.csv"),
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
    duplicates, summary = audit_duplicate_groups(canonical)

    print("SEC CANONICAL DUPLICATE AUDIT")
    print(f"Duplicate groups:        {summary.duplicate_groups:,}")
    print(f"Same-value groups:       {summary.same_value_groups:,}")
    print(f"Conflicting-value groups:{summary.conflicting_value_groups:>10,}")
    print()

    if not duplicates.empty:
        print("TOP TAG COMBINATIONS")
        counts = (
            duplicates["source_tags"]
            .value_counts()
            .head(20)
        )
        for tags, count in counts.items():
            print(f"{str(tags):80.80s} {count:8,d}")

        print()
        print("CONFLICTS BY CONCEPT")
        conflicts = duplicates.loc[
            duplicates["classification"].eq("conflicting_value")
        ]
        for concept, count in conflicts["concept"].value_counts().items():
            print(f"{concept:24s} {count:8,d}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    duplicates.to_csv(args.output, index=False)
    print()
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
