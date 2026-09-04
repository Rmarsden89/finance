from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from finance.data.audit import audit_dates, unique_constituents
from finance.data.sources.pitindex import load_pitindex_sp500
from finance.data.sources.sec_tickers import sec_cik_map


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit point-in-time S&P 500 identity coverage."
    )
    parser.add_argument(
        "--pitindex-data",
        type=Path,
        required=True,
        help="Directory containing pitindex sp500_seed/current/changes CSVs.",
    )
    parser.add_argument(
        "--sec-tickers",
        type=Path,
        help="Optional SEC company_tickers.json for current ticker->CIK enrichment.",
    )
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sec_map = sec_cik_map(args.sec_tickers) if args.sec_tickers else None
    intervals = load_pitindex_sp500(
        args.pitindex_data,
        sec_cik_by_ticker=sec_map,
    )

    snapshot_dates = [
        date(year, 1, 2)
        for year in range(args.start_year, args.end_year + 1)
    ]
    audit = audit_dates(intervals, snapshot_dates)
    unique = unique_constituents(
        intervals,
        start_date=date(args.start_year, 1, 1),
    )
    unresolved = [row for row in unique if row.cik is None]

    print("HISTORICAL S&P 500 UNIVERSE AUDIT")
    print(f"Start year: {args.start_year}")
    print(f"Unique tickers active since start: {len(unique)}")
    print(f"CIK resolved: {len(unique) - len(unresolved)}")
    print(f"CIK unresolved: {len(unresolved)}")
    print()
    print("date        members  resolved  unresolved  cik_coverage")
    for row in audit:
        print(
            f"{row.as_of}  {row.members:7d}  {row.cik_resolved:8d}  "
            f"{row.cik_unresolved:10d}  {row.cik_coverage:11.1%}"
        )

    if unresolved:
        print()
        print("UNRESOLVED HISTORICAL TICKERS")
        for row in unresolved:
            print(
                f"{row.ticker:8s} "
                f"{row.company_name or '':40.40s} "
                f"{row.start_date} -> {row.end_date or 'present'}"
            )


if __name__ == "__main__":
    main()
