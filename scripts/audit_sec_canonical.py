from __future__ import annotations

import argparse
from pathlib import Path

from finance.data.sec_canonical import build_canonical_facts
from finance.data.sources.sec_financial_statements import (
    load_sec_financial_statement_zip,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit canonical PIT-ready SEC facts for one quarterly ZIP."
    )
    parser.add_argument("zip_path", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    quarter = load_sec_financial_statement_zip(args.zip_path)
    canonical, audit = build_canonical_facts(
        quarter.submissions,
        quarter.numeric_facts,
        quarter.presentation,
    )

    print("SEC PIT CANONICAL FACT AUDIT")
    print(f"ZIP:                  {args.zip_path}")
    print(f"Raw numeric facts:    {audit.rows_input:,}")
    print(f"Mapped facts:         {audit.rows_mapped:,}")
    print(f"Supported forms:      {audit.rows_supported_forms:,}")
    print(f"Consolidated facts:   {audit.rows_consolidated:,}")
    print(f"Statement matched:    {audit.rows_statement_matched:,}")
    print(f"Period matched:       {audit.rows_period_matched:,}")\n    print(f"Current-period facts: {audit.rows_current_period:,}")\n    print(f"Duplicate groups:     {audit.duplicate_groups:,}")
    print()

    print("ROWS BY FORM")
    for form, count in canonical["form"].value_counts().items():
        print(f"{form:10s} {count:10,d}")

    print()
    print("ROWS BY CONCEPT")
    for concept, count in canonical["concept"].value_counts().sort_index().items():
        print(f"{concept:24s} {count:10,d}")

    if "qtrs" in canonical.columns:
        print()
        print("QTRS BY CONCEPT")
        pivot = (
            canonical.groupby(["concept", "qtrs"], dropna=False)
            .size()
            .unstack(fill_value=0)
        )
        print(pivot.to_string())


if __name__ == "__main__":
    main()
