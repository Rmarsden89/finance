from __future__ import annotations

import argparse
from pathlib import Path

from finance.data.sec_concepts import map_canonical_facts
from finance.data.sources.sec_financial_statements import (
    load_sec_financial_statement_zip,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit canonical SEC concept coverage for one quarterly ZIP."
    )
    parser.add_argument("zip_path", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    quarter = load_sec_financial_statement_zip(args.zip_path)
    mapped = map_canonical_facts(quarter.numeric_facts)

    print("SEC CANONICAL CONCEPT AUDIT")
    print(f"ZIP:              {args.zip_path}")
    print(f"Numeric facts:    {len(quarter.numeric_facts):,}")
    print(f"Mapped facts:     {len(mapped):,}")
    print(f"Mapped coverage:  {len(mapped) / len(quarter.numeric_facts):.1%}")
    print()

    for concept, count in mapped["concept"].value_counts().sort_index().items():
        print(f"{concept:24s} {count:10,d}")

    print()
    print("SOURCE TAGS BY CONCEPT")
    for concept in sorted(mapped["concept"].unique()):
        print()
        print(concept)
        counts = (
            mapped.loc[mapped["concept"] == concept, "source_tag"]
            .value_counts()
            .head(10)
        )
        for tag, count in counts.items():
            print(f"  {tag:55.55s} {count:8,d}")


if __name__ == "__main__":
    main()
