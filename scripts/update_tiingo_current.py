from __future__ import annotations

import argparse
import csv
import os
import time
from datetime import date, timedelta
from pathlib import Path

from finance.data.historical_market_tickers import (
    HistoricalMarketTickerOverride,
    load_historical_market_ticker_overrides,
    market_ticker_as_of,
)
from finance.data.prices import DailyPrice
from finance.data.sources.pitindex import load_pitindex_sp500
from finance.data.sources.tiingo import TiingoClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Incrementally extend Tiingo cache data for S&P 500 members "
            "active as of a requested date."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        required=True,
        help="Inclusive market-data end date.",
    )
    parser.add_argument(
        "--token",
        help="Tiingo token; defaults to TIINGO_API_TOKEN.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cache/tiingo"),
    )
    parser.add_argument(
        "--historical-market-tickers",
        type=Path,
        default=Path("data/reference/historical_market_ticker_overrides.csv"),
    )
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=1.5,
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
    )
    parser.add_argument(
        "--retry-base-seconds",
        type=float,
        default=10.0,
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of active PIT tickers to process.",
    )
    parser.add_argument(
        "--max-api-requests",
        type=int,
        default=45,
        help=(
            "Maximum Tiingo API requests in one run. Default 45 leaves "
            "headroom below the known 50 requests/hour account limit."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/tiingo_current_update.csv"),
    )
    return parser.parse_args()


def active_intervals(intervals, *, as_of: date):
    return sorted(
        [
            row
            for row in intervals
            if row.start_date <= as_of
            and (row.end_date is None or as_of < row.end_date)
        ],
        key=lambda row: row.ticker,
    )


def split_market_segments(
    *,
    pit_ticker: str,
    start: date,
    end_exclusive: date,
    overrides: list[HistoricalMarketTickerOverride],
) -> list[tuple[date, date, str]]:
    boundaries = {start, end_exclusive}
    for row in overrides:
        if row.pit_ticker != pit_ticker.upper():
            continue
        if start < row.valid_from < end_exclusive:
            boundaries.add(row.valid_from)
        if row.valid_to is not None and start < row.valid_to < end_exclusive:
            boundaries.add(row.valid_to)

    ordered = sorted(boundaries)
    return [
        (
            left,
            right,
            market_ticker_as_of(
                overrides,
                pit_ticker=pit_ticker,
                as_of=left,
            ),
        )
        for left, right in zip(ordered, ordered[1:])
        if left < right
    ]


def latest_cached_dates(cache_dir: Path) -> dict[str, date]:
    latest: dict[str, date] = {}

    for path in sorted(cache_dir.glob("*.csv")):
        if path.name.lower().endswith("coverage.csv"):
            continue

        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "date" not in reader.fieldnames:
                continue

            for row in reader:
                symbol = (row.get("ticker") or "").strip().upper()
                if not symbol:
                    symbol = path.name.split("_", 1)[0].upper()
                try:
                    price_date = date.fromisoformat(str(row["date"])[:10])
                except (KeyError, TypeError, ValueError):
                    continue

                prior = latest.get(symbol)
                if prior is None or price_date > prior:
                    latest[symbol] = price_date

    return latest


def tiingo_symbol(symbol: str) -> str:
    """Translate canonical/PIT ticker spelling to Tiingo REST symbology.

    Tiingo uses hyphens rather than periods for share classes, e.g.
    BRK.B -> BRK-B and BF.B -> BF-B.
    """
    return symbol.strip().upper().replace(".", "-")


def cache_path(cache_dir: Path, symbol: str, start: date, end: date) -> Path:
    return cache_dir / f"{symbol.upper()}_{start.isoformat()}_{end.isoformat()}.csv"


def write_cache(path: Path, prices: list[DailyPrice]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")

    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "ticker",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "adjusted_close",
                "source",
            ]
        )
        for row in prices:
            writer.writerow(
                [
                    row.ticker,
                    row.date,
                    row.open,
                    row.high,
                    row.low,
                    row.close,
                    "" if row.volume is None else row.volume,
                    "" if row.adjusted_close is None else row.adjusted_close,
                    row.source or "tiingo",
                ]
            )

    temp.replace(path)


def fetch_with_retry(
    client: TiingoClient,
    *,
    symbol: str,
    start: date,
    end: date,
    max_retries: int,
    retry_base_seconds: float,
) -> tuple[list[DailyPrice], str | None]:
    attempt = 0

    while True:
        try:
            return client.daily_prices(symbol, start=start, end=end), None
        except Exception as exc:
            error = str(exc)
            text = error.lower()

            throttle_like = any(
                marker in text
                for marker in (
                    "429",
                    "too many requests",
                    "tiingo empty response",
                    "tiingo non-json response",
                    "expecting value: line 1 column 1",
                )
            )
            if throttle_like:
                return [], error

            retryable = any(
                marker in text
                for marker in (
                    "timeout",
                    "502",
                    "503",
                    "504",
                    "temporarily unavailable",
                )
            )
            if not retryable or attempt >= max_retries:
                return [], error

            wait_seconds = retry_base_seconds * (2 ** attempt)
            print(
                f"             retrying in {wait_seconds:.0f}s "
                f"(attempt {attempt + 1}/{max_retries})",
                flush=True,
            )
            time.sleep(wait_seconds)
            attempt += 1


def is_throttle_like(error: str | None) -> bool:
    text = (error or "").lower()
    return any(
        marker in text
        for marker in (
            "429",
            "too many requests",
            "tiingo empty response",
            "tiingo non-json response",
            "expecting value: line 1 column 1",
        )
    )


def main() -> None:
    args = parse_args()

    token = args.token or os.environ.get("TIINGO_API_TOKEN")
    if not token:
        raise SystemExit(
            "Tiingo token required. Pass --token or set TIINGO_API_TOKEN."
        )

    intervals = load_pitindex_sp500(args.pitindex_data)
    active = active_intervals(intervals, as_of=args.as_of)

    if args.limit is not None:
        active = active[: args.limit]

    overrides = load_historical_market_ticker_overrides(
        args.historical_market_tickers
    )
    latest = latest_cached_dates(args.cache_dir)
    client = TiingoClient(token)

    if args.max_api_requests <= 0:
        raise SystemExit("--max-api-requests must be positive")

    report_rows: list[dict] = []
    api_requests = 0
    already_current = 0
    updated = 0
    no_rows = 0
    failures = 0
    throttle_hit = False

    print("TIINGO CURRENT UPDATE")
    print(f"As of:          {args.as_of}")
    print(f"Active members: {len(active)}")
    print(f"Cached symbols: {len(latest)}")
    print()

    for number, interval in enumerate(active, start=1):
        pit_ticker = interval.ticker.upper()
        interval_end_exclusive = args.as_of + timedelta(days=1)

        segments = split_market_segments(
            pit_ticker=pit_ticker,
            start=interval.start_date,
            end_exclusive=interval_end_exclusive,
            overrides=overrides,
        )

        ticker_requests = 0
        ticker_rows = 0
        ticker_errors: list[str] = []
        symbols_used: list[str] = []
        ticker_was_current = True

        for segment_start, segment_end, market_ticker in segments:
            canonical_market_ticker = market_ticker.upper()
            provider_ticker = tiingo_symbol(canonical_market_ticker)
            if provider_ticker not in symbols_used:
                symbols_used.append(provider_ticker)

            request_end = segment_end - timedelta(days=1)
            cached_through = latest.get(provider_ticker)

            request_start = segment_start
            if cached_through is not None:
                request_start = max(
                    request_start,
                    cached_through + timedelta(days=1),
                )

            if request_start > request_end:
                continue

            ticker_was_current = False

            if api_requests >= args.max_api_requests:
                throttle_hit = False
                report_rows.append(
                    {
                        "pit_ticker": pit_ticker,
                        "membership_start": interval.start_date,
                        "as_of": args.as_of,
                        "market_tickers_used": "|".join(symbols_used),
                        "status": "request_budget_exhausted",
                        "api_requests": ticker_requests,
                        "rows_added": ticker_rows,
                        "error": "",
                    }
                )
                print()
                print(
                    f"Configured API request budget reached "
                    f"({args.max_api_requests}); stopping cleanly.",
                    flush=True,
                )
                print(
                    "Rerun later with the same command; cached-through dates "
                    "will make completed names skip automatically.",
                    flush=True,
                )
                args.output.parent.mkdir(parents=True, exist_ok=True)
                fields = [
                    "pit_ticker",
                    "membership_start",
                    "as_of",
                    "market_tickers_used",
                    "status",
                    "api_requests",
                    "rows_added",
                    "error",
                ]
                with args.output.open(
                    "w", encoding="utf-8", newline=""
                ) as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(report_rows)
                print(f"Report:          {args.output}")
                print(f"Cache:           {args.cache_dir}")
                return

            prices, error = fetch_with_retry(
                client,
                symbol=provider_ticker,
                start=request_start,
                end=request_end,
                max_retries=args.max_retries,
                retry_base_seconds=args.retry_base_seconds,
            )
            api_requests += 1
            ticker_requests += 1

            if error:
                ticker_errors.append(
                    f"{provider_ticker} {request_start}->{request_end}: {error}"
                )
                if is_throttle_like(error):
                    throttle_hit = True
                    break
            elif prices:
                path = cache_path(
                    args.cache_dir,
                    provider_ticker,
                    request_start,
                    request_end,
                )
                write_cache(path, prices)
                ticker_rows += len(prices)
                latest[provider_ticker] = max(row.date for row in prices)
            else:
                no_rows += 1

            time.sleep(args.request_delay_seconds)

        if ticker_was_current:
            status = "already_current"
            already_current += 1
        elif ticker_errors:
            status = "provider_error"
            failures += 1
        elif ticker_rows:
            status = "updated"
            updated += 1
        else:
            status = "no_rows"

        report_rows.append(
            {
                "pit_ticker": pit_ticker,
                "membership_start": interval.start_date,
                "as_of": args.as_of,
                "market_tickers_used": "|".join(symbols_used),
                "status": status,
                "api_requests": ticker_requests,
                "rows_added": ticker_rows,
                "error": " | ".join(ticker_errors),
            }
        )

        print(
            f"[{number:03d}/{len(active):03d}] "
            f"{pit_ticker:6s} {status:16s} "
            f"requests={ticker_requests:<2d} rows={ticker_rows:<4d} "
            f"symbols={','.join(symbols_used) or '-'}",
            flush=True,
        )
        if ticker_errors:
            print(f"             error={' | '.join(ticker_errors)}", flush=True)

        if throttle_hit:
            print()
            print(
                "Tiingo throttle-like response detected; stopping cleanly.",
                flush=True,
            )
            print(
                "Successful incremental downloads are already cached. "
                "Rerun the same command later to resume automatically.",
                flush=True,
            )
            break

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "pit_ticker",
        "membership_start",
        "as_of",
        "market_tickers_used",
        "status",
        "api_requests",
        "rows_added",
        "error",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report_rows)

    print()
    print("CURRENT UPDATE SUMMARY")
    print(f"Processed:       {len(report_rows)}")
    print(f"Already current: {already_current}")
    print(f"Updated:         {updated}")
    print(f"No rows:         {no_rows}")
    print(f"Failures:        {failures}")
    print(f"API requests:    {api_requests}")
    print(f"Throttle hit:    {throttle_hit}")
    print(f"Report:          {args.output}")
    print(f"Cache:           {args.cache_dir}")


if __name__ == "__main__":
    main()
