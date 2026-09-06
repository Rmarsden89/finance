from __future__ import annotations

import argparse
import csv
import json
import os
import time
from collections import defaultdict
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
            "Run Tiingo against a priority queue, using PIT membership windows "
            "and historical market-ticker overrides."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument(
        "--priority-queue",
        type=Path,
        default=Path("reports/tiingo_priority_queue.csv"),
    )
    parser.add_argument(
        "--max-priority",
        type=int,
        default=4,
        help="Only process queue rows at or above this priority threshold.",
    )
    parser.add_argument("--token", help="Tiingo token; defaults to TIINGO_API_TOKEN.")
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--historical-market-tickers",
        type=Path,
        default=Path("data/reference/historical_market_ticker_overrides.csv"),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cache/tiingo"),
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("data/cache/tiingo/priority_coverage_checkpoint.json"),
    )
    parser.add_argument("--reset-checkpoint", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--request-delay-seconds", type=float, default=1.5)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--retry-base-seconds", type=float, default=10.0)
    parser.add_argument("--boundary-tolerance-days", type=int, default=7)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/tiingo_priority_coverage.csv"),
    )
    return parser.parse_args()


def membership_windows(intervals, *, start: date, end: date):
    result = defaultdict(list)
    end_exclusive = end + timedelta(days=1)
    for interval in intervals:
        interval_end = interval.end_date or end_exclusive
        window_start = max(interval.start_date, start)
        window_end = min(interval_end, end_exclusive)
        if window_start < window_end:
            result[interval.ticker].append((window_start, window_end))
    return dict(result)


def load_priority_queue(path: Path, *, max_priority: int) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if int(row["priority"]) <= max_priority
        ]
    rows.sort(key=lambda row: (int(row["priority"]), row["pit_ticker"].upper()))
    return rows


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


def cache_path(cache_dir: Path, symbol: str, start: date, end: date) -> Path:
    return cache_dir / f"{symbol.upper()}_{start.isoformat()}_{end.isoformat()}.csv"


def load_cache(path: Path) -> list[DailyPrice]:
    if not path.exists():
        return []

    rows = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                price_date = date.fromisoformat(row["date"])
                close = float(row["close"])
            except (KeyError, TypeError, ValueError):
                continue

            rows.append(
                DailyPrice(
                    ticker=(row.get("ticker") or "").upper(),
                    date=price_date,
                    open=float(row.get("open") or close),
                    high=float(row.get("high") or close),
                    low=float(row.get("low") or close),
                    close=close,
                    volume=(
                        int(float(row["volume"]))
                        if (row.get("volume") or "").strip()
                        else None
                    ),
                    adjusted_close=(
                        float(row["adjusted_close"])
                        if (row.get("adjusted_close") or "").strip()
                        else None
                    ),
                    source=row.get("source") or "tiingo",
                )
            )

    rows.sort(key=lambda row: row.date)
    return rows


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
                for marker in ("timeout", "502", "503", "504", "temporarily unavailable")
            )
            if not retryable or attempt >= max_retries:
                return [], error

            wait_seconds = retry_base_seconds * (2 ** attempt)
            print(
                f"             retrying in {wait_seconds:.0f}s "
                f"(attempt {attempt + 1}/{max_retries})"
            )
            time.sleep(wait_seconds)
            attempt += 1


def coverage_status(
    prices: list[DailyPrice],
    windows: list[tuple[date, date]],
    *,
    tolerance_days: int,
) -> tuple[str, int | None, int | None]:
    if not prices:
        return "missing", None, None

    ordered = sorted(prices, key=lambda row: row.date)
    membership_start = min(start for start, _ in windows)
    membership_end = max(end for _, end in windows) - timedelta(days=1)
    start_gap = (ordered[0].date - membership_start).days
    end_gap = (membership_end - ordered[-1].date).days

    if start_gap <= tolerance_days and end_gap <= tolerance_days:
        return "full_boundary_coverage", start_gap, end_gap
    return "partial_boundary_coverage", start_gap, end_gap


def load_checkpoint(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(payload["next_offset"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def write_checkpoint(
    path: Path,
    *,
    next_offset: int,
    queue_size: int,
    last_ticker: str | None,
    reason: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "next_offset": next_offset,
        "queue_size": queue_size,
        "last_ticker": last_ticker,
        "reason": reason,
    }
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def load_existing_report(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {
            row["pit_ticker"].upper(): row
            for row in csv.DictReader(handle)
            if row.get("pit_ticker")
        }


def main() -> None:
    args = parse_args()
    token = args.token or os.environ.get("TIINGO_API_TOKEN")
    if not token:
        raise SystemExit("Tiingo token required. Pass --token or set TIINGO_API_TOKEN.")

    audit_start = date(args.start_year, 1, 1)
    audit_end = date(args.end_year, 12, 31)

    intervals = load_pitindex_sp500(args.pitindex_data)
    windows = membership_windows(intervals, start=audit_start, end=audit_end)
    overrides = load_historical_market_ticker_overrides(
        args.historical_market_tickers
    )
    queue = load_priority_queue(args.priority_queue, max_priority=args.max_priority)
    queue = [row for row in queue if row["pit_ticker"].upper() in windows]

    if args.reset_checkpoint and args.checkpoint.exists():
        args.checkpoint.unlink()

    checkpoint = load_checkpoint(args.checkpoint)
    start_offset = checkpoint if checkpoint is not None else args.offset
    if start_offset < 0 or start_offset > len(queue):
        raise SystemExit(
            f"Checkpoint/offset {start_offset} outside queue size {len(queue)}."
        )

    end_index = (
        len(queue)
        if args.limit is None
        else min(len(queue), start_offset + args.limit)
    )
    selected = queue[start_offset:end_index]

    print("TIINGO PRIORITY PIT COVERAGE")
    print(f"Queue rows:       {len(queue)}")
    print(f"Max priority:     {args.max_priority}")
    print(f"Start offset:     {start_offset}")
    print(f"Selected this run:{len(selected):>6}")
    print()

    client = TiingoClient(token)
    report_rows = []
    api_requests = 0
    cache_hits = 0
    rate_limit_hit = False
    next_offset = start_offset
    last_completed_ticker = None

    for number, queue_row in enumerate(selected, start=1):
        absolute_index = start_offset + number - 1
        pit_ticker = queue_row["pit_ticker"].upper()
        ticker_windows = windows[pit_ticker]

        by_date: dict[date, DailyPrice] = {}
        market_tickers_used: list[str] = []
        errors: list[str] = []
        ticker_rate_limited = False

        for window_start, window_end in ticker_windows:
            for segment_start, segment_end, market_ticker in split_market_segments(
                pit_ticker=pit_ticker,
                start=window_start,
                end_exclusive=window_end,
                overrides=overrides,
            ):
                if market_ticker not in market_tickers_used:
                    market_tickers_used.append(market_ticker)

                request_end = segment_end - timedelta(days=1)
                path = cache_path(
                    args.cache_dir,
                    market_ticker,
                    segment_start,
                    request_end,
                )
                cached = load_cache(path)
                if cached:
                    prices = cached
                    cache_hits += 1
                else:
                    prices, error = fetch_with_retry(
                        client,
                        symbol=market_ticker,
                        start=segment_start,
                        end=request_end,
                        max_retries=args.max_retries,
                        retry_base_seconds=args.retry_base_seconds,
                    )
                    api_requests += 1

                    if error:
                        error_text = error.lower()
                        throttle_like = any(
                            marker in error_text
                            for marker in (
                                "429",
                                "too many requests",
                                "tiingo empty response",
                                "tiingo non-json response",
                                "expecting value: line 1 column 1",
                            )
                        )
                        if throttle_like:
                            ticker_rate_limited = True
                            errors.append(error)
                            break
                        errors.append(
                            f"{market_ticker} {segment_start}->{request_end}: {error}"
                        )
                    elif prices:
                        write_cache(path, prices)

                    if not ticker_rate_limited:
                        time.sleep(args.request_delay_seconds)

                for price in prices:
                    if segment_start <= price.date < segment_end:
                        by_date[price.date] = DailyPrice(
                            ticker=pit_ticker,
                            date=price.date,
                            open=price.open,
                            high=price.high,
                            low=price.low,
                            close=price.close,
                            volume=price.volume,
                            adjusted_close=price.adjusted_close,
                            source="tiingo",
                        )

            if ticker_rate_limited:
                break

        if ticker_rate_limited:
            rate_limit_hit = True
            next_offset = absolute_index
            write_checkpoint(
                args.checkpoint,
                next_offset=next_offset,
                queue_size=len(queue),
                last_ticker=last_completed_ticker,
                reason="rate_limit",
            )
            print(
                f"[{number:03d}/{len(selected):03d}] {pit_ticker:6s} "
                "RATE_LIMIT — checkpoint saved; retry this ticker next run"
            )
            break

        prices = [by_date[value] for value in sorted(by_date)]
        status, start_gap, end_gap = coverage_status(
            prices,
            ticker_windows,
            tolerance_days=args.boundary_tolerance_days,
        )

        report_rows.append(
            {
                "priority": queue_row["priority"],
                "pit_ticker": pit_ticker,
                "reason": queue_row["reason"],
                "market_tickers_used": "|".join(market_tickers_used),
                "membership_start": min(x[0] for x in ticker_windows),
                "membership_end_exclusive": max(x[1] for x in ticker_windows),
                "price_start": prices[0].date if prices else "",
                "price_end": prices[-1].date if prices else "",
                "rows": len(prices),
                "start_gap_days": "" if start_gap is None else start_gap,
                "end_gap_days": "" if end_gap is None else end_gap,
                "status": status,
                "error": " | ".join(errors),
            }
        )

        print(
            f"[{number:03d}/{len(selected):03d}] "
            f"P{queue_row['priority']} {pit_ticker:6s} "
            f"{status:25s} "
            f"{prices[0].date if prices else '-'} -> "
            f"{prices[-1].date if prices else '-'} "
            f"symbols={','.join(market_tickers_used) or '-'}"
        )
        if errors:
            print(f"             error={' | '.join(errors)}")

        last_completed_ticker = pit_ticker
        next_offset = absolute_index + 1
        write_checkpoint(
            args.checkpoint,
            next_offset=next_offset,
            queue_size=len(queue),
            last_ticker=last_completed_ticker,
            reason="progress",
        )

    else:
        write_checkpoint(
            args.checkpoint,
            next_offset=next_offset,
            queue_size=len(queue),
            last_ticker=last_completed_ticker,
            reason="complete" if next_offset >= len(queue) else "limit_reached",
        )

    fields = [
        "priority",
        "pit_ticker",
        "reason",
        "market_tickers_used",
        "membership_start",
        "membership_end_exclusive",
        "price_start",
        "price_end",
        "rows",
        "start_gap_days",
        "end_gap_days",
        "status",
        "error",
    ]

    cumulative = load_existing_report(args.output)
    for row in report_rows:
        cumulative[row["pit_ticker"]] = {
            field: str(row.get(field, ""))
            for field in fields
        }

    queue_order = {
        row["pit_ticker"].upper(): index
        for index, row in enumerate(queue)
    }
    cumulative_rows = sorted(
        cumulative.values(),
        key=lambda row: queue_order.get(
            row["pit_ticker"].upper(),
            len(queue_order),
        ),
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(cumulative_rows)

    full = sum(row["status"] == "full_boundary_coverage" for row in cumulative_rows)
    partial = sum(
        row["status"] == "partial_boundary_coverage"
        for row in cumulative_rows
    )
    missing = sum(row["status"] == "missing" for row in cumulative_rows)

    print()
    print("CUMULATIVE TIINGO PRIORITY COVERAGE")
    print(f"Tickers recorded: {len(cumulative_rows)}")
    print(f"Full boundary:    {full}")
    print(f"Partial boundary: {partial}")
    print(f"Missing:          {missing}")
    print(f"API requests:     {api_requests}")
    print(f"Cache hits:       {cache_hits}")
    print(f"Rate limit hit:   {rate_limit_hit}")
    print(f"Next offset:      {next_offset}")
    print(f"Checkpoint:       {args.checkpoint}")
    print(f"Report:           {args.output}")
    print(f"Cache:            {args.cache_dir}")


if __name__ == "__main__":
    main()
