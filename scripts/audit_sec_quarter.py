from __future__ import annotations

import argparse
from pathlib import Path

from finance.data.sources.sec_financial_statements import load_sec_financial_statement_zip


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect one SEC Financial Statement Data Set quarterly ZIP."
    )
    parser.add_argument("zip_path", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    quarter = load_sec_financial_statement_zip(args.zip_path)

    submissions = quarter.submissions
    numeric = quarter.numeric_facts
    print("SEC QUARTER AUDIT")
    print(f"ZIP:                 {args.zip_path}")
    print(f"Submissions:         {len(submissions):,}")
    print(f"Unique CIKs:         {submissions['cik'].nunique(dropna=True):,}")
    print(f"Numeric facts:       {len(numeric):,}")
    print(f"Unique XBRL tags:    {numeric['tag'].nunique(dropna=True):,}")
    print(f"Earliest filed date: {submissions['filed_date'].dropna().min()}")
    print(f"Latest filed date:   {submissions['filed_date'].dropna().max()}")
    print(f"Earliest accepted:   {submissions['accepted_at'].dropna().min()}")
    print(f"Latest accepted:     {submissions['accepted_at'].dropna().max()}")

    print()
    print("TOP FORMS")
    for form, count in submissions['form'].value_counts().head(10).items():
        print(f"{form:12s} {count:8,d}")

    print()
    print("TOP NUMERIC TAGS")
    for tag, count in numeric['tag'].value_counts().head(20).items():
        print(f"{str(tag):45.45s} {count:8,d}")


if __name__ == "__main__":
    main()
