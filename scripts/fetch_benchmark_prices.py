from __future__ import annotations

import argparse
import csv
import os
from datetime import date
from pathlib import Path

from finance.data.sources.tiingo import TiingoClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch benchmark daily prices from Tiingo for backtesting."
    )
    parser.add_argument("--symbol", default="VOO")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2015, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--token")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/market/benchmark_voo.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = args.token or os.getenv("TIINGO_API_TOKEN")
    if not token:
        raise ValueError(
            "Tiingo token required via --token or TIINGO_API_TOKEN."
        )

    client = TiingoClient(token)
    rows = client.daily_prices(
        args.symbol,
        start=args.start,
        end=args.end,
    )
    if not rows:
        raise RuntimeError(f"No Tiingo prices returned for {args.symbol.upper()}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "ticker",
                "date",
                "open",
                "high",
                "low",
                "close",
                "adjusted_close",
                "volume",
                "source",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "ticker": args.symbol.upper(),
                "date": row.date.isoformat(),
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "adjusted_close": (
                    "" if row.adjusted_close is None else row.adjusted_close
                ),
                "volume": "" if row.volume is None else row.volume,
                "source": row.source,
            })

    print("BENCHMARK PRICE FETCH")
    print(f"Symbol: {args.symbol.upper()}")
    print(f"Rows: {len(rows):,}")
    print(f"First: {rows[0].date}")
    print(f"Last: {rows[-1].date}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
