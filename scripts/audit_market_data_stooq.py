from __future__ import annotations

import argparse
import csv
import os
from datetime import date
from pathlib import Path

from finance.data.audit import unique_constituents
from finance.data.sources.pitindex import load_pitindex_sp500
from finance.data.sources.stooq import StooqClient


CANARY_TICKERS = [
    "AAPL",  # active control
    "MSFT",  # active control
    "ATVI",  # acquired 2023
    "ABMD",  # acquired 2022
    "CELG",  # acquired 2019
    "TIF",   # acquired 2021
    "TWTR",  # acquired/taken private 2022
    "FRC",   # bank failure 2023
    "SIVB",  # bank failure 2023
    "WFM",   # acquired 2017
    "RHT",   # acquired 2019
    "XLNX",  # acquired 2022
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit Stooq daily-price coverage for historical S&P 500 tickers."
    )
    parser.add_argument(
        "--pitindex-data",
        type=Path,
        required=True,
        help="Directory containing pitindex S&P 500 CSVs.",
    )
    parser.add_argument(
        "--api-key",
        help="Stooq API key. If omitted, STOOQ_API_KEY is used.",
    )
    parser.add_argument(
        "--mode",
        choices=["canary", "universe"],
        default="canary",
        help="Canary tests a small active+delisted set; universe tests all PIT tickers.",
    )
    parser.add_argument(
        "--tickers",
        help="Optional comma-separated ticker override.",
    )
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum ticker count, useful for quota-safe universe batches.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/stooq_price_coverage.csv"),
    )
    return parser.parse_args()


def _select_tickers(args: argparse.Namespace) -> list[str]:
    if args.tickers:
        tickers = [
            value.strip().upper()
            for value in args.tickers.split(",")
            if value.strip()
        ]
    elif args.mode == "canary":
        tickers = CANARY_TICKERS.copy()
    else:
        intervals = load_pitindex_sp500(args.pitindex_data)
        unique = unique_constituents(
            intervals,
            start_date=date(args.start_year, 1, 1),
        )
        tickers = [row.ticker for row in unique]

    if args.limit:
        tickers = tickers[: args.limit]

    return tickers


def main() -> None:
    args = parse_args()
    api_key = args.api_key or os.environ.get("STOOQ_API_KEY")
    if not api_key:
        raise SystemExit(
            "Stooq API key required. Pass --api-key or set STOOQ_API_KEY."
        )

    client = StooqClient(api_key)
    tickers = _select_tickers(args)
    start = date(args.start_year, 1, 1)
    end = date(args.end_year, 12, 31)

    results = []
    for index, ticker in enumerate(tickers, start=1):
        result = client.coverage(ticker, start=start, end=end)
        results.append(result)

        status = "OK" if result.covered else "MISSING"
        print(
            f"[{index:03d}/{len(tickers):03d}] "
            f"{ticker:6s} {status:7s} rows={result.rows:5d} "
            f"{result.first_price_date or '-'} -> {result.last_price_date or '-'}"
        )
        if result.error:
            print(f"             error={result.error}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "ticker",
                "requested_start",
                "requested_end",
                "first_price_date",
                "last_price_date",
                "rows",
                "covered",
                "error",
            ]
        )
        for row in results:
            writer.writerow(
                [
                    row.ticker,
                    row.requested_start,
                    row.requested_end,
                    row.first_price_date or "",
                    row.last_price_date or "",
                    row.rows,
                    row.covered,
                    row.error or "",
                ]
            )

    covered = sum(row.covered for row in results)
    print()
    print("STOOQ PRICE COVERAGE")
    print(f"Tickers tested: {len(results)}")
    print(f"Covered:        {covered}")
    print(f"Missing/error:  {len(results) - covered}")
    print(f"Coverage:       {covered / len(results):.1%}" if results else "Coverage: n/a")
    print(f"Report:         {args.output}")


if __name__ == "__main__":
    main()
