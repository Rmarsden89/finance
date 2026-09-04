from __future__ import annotations

import argparse
import csv
import os
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from finance.data.prices import DailyPrice, PriceCoverageResult
from finance.data.sources.pitindex import load_pitindex_sp500
from finance.data.sources.tiingo import TiingoClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit Tiingo price coverage against PIT S&P 500 membership windows."
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument("--token", help="Tiingo token; defaults to TIINGO_API_TOKEN.")
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cache/tiingo"),
        help="Local per-ticker cache for successful Tiingo price downloads.",
    )
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=1.5,
        help="Delay between successful API requests.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Retries for transient provider errors. HTTP 429 is not retried.",
    )
    parser.add_argument(
        "--retry-base-seconds",
        type=float,
        default=10.0,
        help="Base exponential backoff delay for transient retries.",
    )
    parser.add_argument(
        "--boundary-tolerance-days",
        type=int,
        default=7,
        help="Calendar-day tolerance for weekends/holidays at membership boundaries.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/tiingo_pit_price_coverage.csv"),
    )
    return parser.parse_args()


def _membership_windows(intervals, *, start: date, end: date):
    windows = defaultdict(list)
    audit_end_exclusive = end + timedelta(days=1)
    for interval in intervals:
        interval_end = interval.end_date or audit_end_exclusive
        window_start = max(interval.start_date, start)
        window_end = min(interval_end, audit_end_exclusive)
        if window_start < window_end:
            windows[interval.ticker].append((window_start, window_end))
    return dict(windows)


def _classify(
    *,
    windows: list[tuple[date, date]],
    first_price: date | None,
    last_price: date | None,
    tolerance_days: int,
    error: str | None,
) -> tuple[str, int | None, int | None]:
    if error:
        return "provider_error", None, None
    if first_price is None or last_price is None:
        return "missing", None, None

    membership_start = min(start for start, _ in windows)
    membership_end_inclusive = max(end for _, end in windows) - timedelta(days=1)
    start_gap = (first_price - membership_start).days
    end_gap = (membership_end_inclusive - last_price).days

    if start_gap <= tolerance_days and end_gap <= tolerance_days:
        return "full_boundary_coverage", start_gap, end_gap
    return "partial_boundary_coverage", start_gap, end_gap


def _cache_path(cache_dir: Path, ticker: str, start: date, end: date) -> Path:
    return cache_dir / f"{ticker.upper()}_{start.isoformat()}_{end.isoformat()}.csv"


def _load_cache(path: Path) -> list[DailyPrice]:
    if not path.exists():
        return []

    prices: list[DailyPrice] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            prices.append(
                DailyPrice(
                    ticker=row["ticker"],
                    date=date.fromisoformat(row["date"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"]) if row["volume"] else None,
                    adjusted_close=(
                        float(row["adjusted_close"])
                        if row["adjusted_close"]
                        else None
                    ),
                    source=row.get("source") or "tiingo",
                )
            )
    return prices


def _write_cache(path: Path, prices: list[DailyPrice]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
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


def _result_from_prices(
    ticker: str,
    *,
    start: date,
    end: date,
    prices: list[DailyPrice],
    error: str | None = None,
) -> PriceCoverageResult:
    return PriceCoverageResult(
        ticker=ticker.upper(),
        requested_start=start,
        requested_end=end,
        first_price_date=prices[0].date if prices else None,
        last_price_date=prices[-1].date if prices else None,
        rows=len(prices),
        covered=bool(prices),
        error=error,
    )


def _coverage_with_cache_and_retry(
    client: TiingoClient,
    ticker: str,
    *,
    start: date,
    end: date,
    cache_dir: Path,
    max_retries: int,
    retry_base_seconds: float,
) -> tuple[PriceCoverageResult, bool]:
    cache_path = _cache_path(cache_dir, ticker, start, end)
    cached = _load_cache(cache_path)
    if cached:
        return _result_from_prices(
            ticker,
            start=start,
            end=end,
            prices=cached,
        ), True

    attempt = 0
    while True:
        try:
            prices = client.daily_prices(ticker, start=start, end=end)
            if prices:
                _write_cache(cache_path, prices)
            return _result_from_prices(
                ticker,
                start=start,
                end=end,
                prices=prices,
            ), False
        except Exception as exc:
            error = str(exc)
            error_text = error.lower()

            if "429" in error_text or "too many requests" in error_text:
                return _result_from_prices(
                    ticker,
                    start=start,
                    end=end,
                    prices=[],
                    error=error,
                ), False

            retryable = any(
                marker in error_text
                for marker in (
                    "timeout",
                    "temporarily unavailable",
                    "502",
                    "503",
                    "504",
                )
            )
            if not retryable or attempt >= max_retries:
                return _result_from_prices(
                    ticker,
                    start=start,
                    end=end,
                    prices=[],
                    error=error,
                ), False

            wait_seconds = retry_base_seconds * (2 ** attempt)
            print(
                f"             retrying in {wait_seconds:.0f}s "
                f"(attempt {attempt + 1}/{max_retries})"
            )
            time.sleep(wait_seconds)
            attempt += 1


def main() -> None:
    args = parse_args()
    token = args.token or os.environ.get("TIINGO_API_TOKEN")
    if not token:
        raise SystemExit("Tiingo token required. Pass --token or set TIINGO_API_TOKEN.")

    audit_start = date(args.start_year, 1, 1)
    requested_end = date(args.end_year, 12, 31)
    audit_end = min(requested_end, date.today())

    intervals = load_pitindex_sp500(args.pitindex_data)
    windows = _membership_windows(intervals, start=audit_start, end=audit_end)
    tickers = sorted(windows)

    end_index = None if args.limit is None else args.offset + args.limit
    selected = tickers[args.offset:end_index]

    client = TiingoClient(token)
    rows = []
    rate_limit_hit = False
    cache_hits = 0
    api_requests = 0

    for number, ticker in enumerate(selected, start=1):
        ticker_windows = windows[ticker]
        request_start = min(start for start, _ in ticker_windows)
        request_end = max(end for _, end in ticker_windows) - timedelta(days=1)

        result, cache_hit = _coverage_with_cache_and_retry(
            client,
            ticker,
            start=request_start,
            end=request_end,
            cache_dir=args.cache_dir,
            max_retries=args.max_retries,
            retry_base_seconds=args.retry_base_seconds,
        )
        if cache_hit:
            cache_hits += 1
        else:
            api_requests += 1

        status, start_gap, end_gap = _classify(
            windows=ticker_windows,
            first_price=result.first_price_date,
            last_price=result.last_price_date,
            tolerance_days=args.boundary_tolerance_days,
            error=result.error,
        )

        rows.append(
            {
                "ticker": ticker,
                "interval_count": len(ticker_windows),
                "membership_start": request_start,
                "membership_end_exclusive": request_end + timedelta(days=1),
                "price_start": result.first_price_date or "",
                "price_end": result.last_price_date or "",
                "rows": result.rows,
                "start_gap_days": "" if start_gap is None else start_gap,
                "end_gap_days": "" if end_gap is None else end_gap,
                "status": status,
                "cache_hit": cache_hit,
                "error": result.error or "",
            }
        )

        source_label = "CACHE" if cache_hit else "API"
        print(
            f"[{number:03d}/{len(selected):03d}] {ticker:6s} "
            f"{status:25s} {source_label:5s} "
            f"{request_start} -> {request_end} | "
            f"{result.first_price_date or '-'} -> {result.last_price_date or '-'}"
        )
        if result.error:
            print(f"             error={result.error}")

        error_text = (result.error or "").lower()
        if "429" in error_text or "too many requests" in error_text:
            rate_limit_hit = True
            print()
            print("Tiingo hourly request limit reached; stopping this batch cleanly.")
            print("Successful downloads are already cached locally.")
            break

        if not cache_hit and number < len(selected):
            time.sleep(args.request_delay_seconds)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "ticker",
        "interval_count",
        "membership_start",
        "membership_end_exclusive",
        "price_start",
        "price_end",
        "rows",
        "start_gap_days",
        "end_gap_days",
        "status",
        "cache_hit",
        "error",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    full = sum(row["status"] == "full_boundary_coverage" for row in rows)
    partial = sum(row["status"] == "partial_boundary_coverage" for row in rows)
    missing = sum(row["status"] == "missing" for row in rows)
    provider_errors = sum(row["status"] == "provider_error" for row in rows)

    print()
    print("TIINGO PIT MEMBERSHIP COVERAGE")
    print(f"Universe tickers: {len(tickers)}")
    print(f"Audit end:        {audit_end}")
    print(f"Batch offset:     {args.offset}")
    print(f"Batch tested:     {len(rows)}")
    print(f"Cache hits:       {cache_hits}")
    print(f"API requests:     {api_requests}")
    print(f"Full boundary:    {full}")
    print(f"Partial boundary: {partial}")
    print(f"Missing:          {missing}")
    print(f"Provider errors:  {provider_errors}")
    print(f"Rate limit hit:   {rate_limit_hit}")
    print(f"Report:           {args.output}")
    print(f"Price cache:      {args.cache_dir}")


if __name__ == "__main__":
    main()
