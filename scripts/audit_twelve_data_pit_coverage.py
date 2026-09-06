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
from finance.data.sources.twelve_data import TwelveDataClient, TwelveDataSymbol


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit Twelve Data as a second fallback for PIT S&P 500 tickers "
            "currently unresolved by the canonical market-data layer."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument(
        "--coverage",
        type=Path,
        default=Path("data/market/price_coverage.csv"),
    )
    parser.add_argument(
        "--api-key",
        help="Twelve Data API key; defaults to TWELVE_DATA_API_KEY.",
    )
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
        default=Path("data/cache/twelve_data"),
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("data/cache/twelve_data/pit_coverage_checkpoint.json"),
    )
    parser.add_argument(
        "--reference-cache-dir",
        type=Path,
        default=Path("data/cache/twelve_data/reference"),
        help="Cache Twelve Data symbol/exchange resolution results.",
    )
    parser.add_argument(
        "--verified-resolutions",
        type=Path,
        default=Path("data/reference/twelve_data_verified_resolutions.csv"),
        help="Verified Twelve Data fills to exclude from future unresolved audits.",
    )
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Initial unresolved-universe offset when no checkpoint exists.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum PIT tickers for this run.",
    )
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=8.0,
        help="Delay after each uncached API request; Basic allows 8 credits/minute.",
    )
    parser.add_argument(
        "--boundary-tolerance-days",
        type=int,
        default=7,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/twelve_data_pit_coverage.csv"),
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


def verified_tickers(path: Path) -> set[str]:
    if not path.exists():
        return set()

    result = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            ticker = (row.get("pit_ticker") or "").strip().upper()
            status = (row.get("verification_status") or "").strip().lower()
            if ticker and status == "verified_full_coverage":
                result.add(ticker)
    return result


def unresolved_tickers(path: Path) -> set[str]:
    result = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            ticker = (row.get("pit_ticker") or "").strip().upper()
            source = (row.get("selected_source") or "").strip()
            if ticker and source == "unresolved":
                result.add(ticker)
    return result


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


def reference_cache_path(cache_dir: Path, symbol: str) -> Path:
    safe = symbol.upper().replace("/", "_").replace("\\", "_")
    return cache_dir / f"{safe}.json"


def choose_reference_candidate(
    requested_symbol: str,
    candidates: list[TwelveDataSymbol],
) -> TwelveDataSymbol | None:
    requested = requested_symbol.upper()
    allowed_countries = ("", "united states", "us", "usa")

    exact = [
        row
        for row in candidates
        if row.symbol == requested
        and (row.country or "").lower() in allowed_countries
    ]
    if not exact:
        return None

    stock_like = [
        row
        for row in exact
        if any(
            token in (row.instrument_type or "").lower()
            for token in ("stock", "common", "equity", "depositary")
        )
    ]
    return (stock_like or exact)[0]


def load_reference_cache(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_reference_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def resolve_market_ticker(
    client: TwelveDataClient,
    *,
    market_ticker: str,
    cache_dir: Path,
    request_delay_seconds: float,
) -> tuple[dict, int]:
    path = reference_cache_path(cache_dir, market_ticker)
    cached = load_reference_cache(path)
    if cached is not None:
        cached["cache_hit"] = True
        return cached, 0

    requests = 0
    candidates: list[TwelveDataSymbol] = []
    method = "stocks"

    try:
        candidates = client.list_stocks(market_ticker)
        requests += 1
        time.sleep(request_delay_seconds)
    except Exception as exc:
        if is_rate_limit(str(exc)):
            raise
        candidates = []

    chosen = choose_reference_candidate(market_ticker, candidates)

    if chosen is None:
        method = "symbol_search"
        try:
            candidates = client.symbol_search(market_ticker)
            requests += 1
            time.sleep(request_delay_seconds)
        except Exception as exc:
            if is_rate_limit(str(exc)):
                raise
            candidates = []
        chosen = choose_reference_candidate(market_ticker, candidates)

    if chosen is None:
        payload = {
            "requested_symbol": market_ticker.upper(),
            "resolved_symbol": market_ticker.upper(),
            "exchange": "",
            "name": "",
            "country": "",
            "instrument_type": "",
            "resolution_method": "raw_fallback",
            "cache_hit": False,
        }
    else:
        payload = {
            "requested_symbol": market_ticker.upper(),
            "resolved_symbol": chosen.symbol or market_ticker.upper(),
            "exchange": chosen.exchange or "",
            "name": chosen.name or "",
            "country": chosen.country or "",
            "instrument_type": chosen.instrument_type or "",
            "resolution_method": method,
            "cache_hit": False,
        }

    write_reference_cache(path, payload)
    return payload, requests


def classify_provider_error(error: str | None) -> str:
    text = (error or "").lower()
    if not text:
        return ""
    if is_rate_limit(text):
        return "rate_limit"
    if "available starting with the" in text and "plan" in text:
        return "entitlement"
    if "no data is available on the specified dates" in text:
        return "no_data_for_dates"
    if "symbol" in text and ("missing or invalid" in text or "invalid symbol" in text):
        return "invalid_symbol"
    return "provider_error"


def cache_path(
    cache_dir: Path,
    symbol: str,
    start: date,
    end: date,
) -> Path:
    return cache_dir / (
        f"{symbol.upper()}_{start.isoformat()}_{end.isoformat()}.csv"
    )


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
                    ticker=(row.get("ticker") or path.stem.split("_", 1)[0]).upper(),
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
                    adjusted_close=None,
                    source="twelve_data",
                )
            )

    rows.sort(key=lambda value: value.date)
    return rows


def write_cache(path: Path, prices: list[DailyPrice]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["ticker", "date", "open", "high", "low", "close", "volume"]
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
                ]
            )
    temp.replace(path)


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
    universe_size: int,
    last_ticker: str | None,
    reason: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "next_offset": next_offset,
        "universe_size": universe_size,
        "last_ticker": last_ticker,
        "reason": reason,
    }
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def is_rate_limit(error: str | None) -> bool:
    text = (error or "").lower()
    return any(
        marker in text
        for marker in (
            "http 429",
            "code=429",
            "too many requests",
            "api credits",
            "rate limit",
            "run out of credits",
        )
    )


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


def load_existing_report(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {
            row["ticker"].strip().upper(): row
            for row in csv.DictReader(handle)
            if row.get("ticker")
        }


def main() -> None:
    args = parse_args()
    api_key = args.api_key or os.environ.get("TWELVE_DATA_API_KEY")
    if not api_key:
        raise SystemExit(
            "Twelve Data API key required. Pass --api-key or set TWELVE_DATA_API_KEY."
        )

    audit_start = date(args.start_year, 1, 1)
    audit_end = date(args.end_year, 12, 31)

    intervals = load_pitindex_sp500(args.pitindex_data)
    windows = membership_windows(intervals, start=audit_start, end=audit_end)
    unresolved = unresolved_tickers(args.coverage)
    verified = verified_tickers(args.verified_resolutions)
    unresolved -= verified
    overrides = load_historical_market_ticker_overrides(
        args.historical_market_tickers
    )

    tickers = sorted(ticker for ticker in windows if ticker in unresolved)

    if args.reset_checkpoint and args.checkpoint.exists():
        args.checkpoint.unlink()

    checkpoint = load_checkpoint(args.checkpoint)
    start_offset = checkpoint if checkpoint is not None else args.offset
    if start_offset < 0 or start_offset > len(tickers):
        raise SystemExit(
            f"Checkpoint/offset {start_offset} outside unresolved universe "
            f"size {len(tickers)}."
        )

    end_index = (
        len(tickers)
        if args.limit is None
        else min(len(tickers), start_offset + args.limit)
    )
    selected = tickers[start_offset:end_index]

    client = TwelveDataClient(api_key)
    report_rows = []
    api_requests = 0
    cache_hits = 0
    rate_limit_hit = False
    last_completed_ticker = None
    next_offset = start_offset

    print("TWELVE DATA PIT FALLBACK AUDIT")
    print(f"Verified fills skipped: {len(verified)}")
    print(f"Unresolved PIT tickers: {len(tickers)}")
    print(f"Start offset:           {start_offset}")
    print(f"Selected this run:      {len(selected)}")
    print()

    for number, ticker in enumerate(selected, start=1):
        absolute_index = start_offset + number - 1
        ticker_windows = windows[ticker]
        by_date: dict[date, DailyPrice] = {}
        symbols_used: list[str] = []
        resolved_symbols_used: list[str] = []
        exchanges_used: list[str] = []
        resolution_methods: list[str] = []
        resolution_names: list[str] = []
        error_classes: list[str] = []
        errors: list[str] = []
        ticker_rate_limited = False

        for window_start, window_end in ticker_windows:
            segments = split_market_segments(
                pit_ticker=ticker,
                start=window_start,
                end_exclusive=window_end,
                overrides=overrides,
            )

            for segment_start, segment_end, market_ticker in segments:
                if market_ticker not in symbols_used:
                    symbols_used.append(market_ticker)

                request_end = segment_end - timedelta(days=1)

                try:
                    resolution, reference_requests = resolve_market_ticker(
                        client,
                        market_ticker=market_ticker,
                        cache_dir=args.reference_cache_dir,
                        request_delay_seconds=args.request_delay_seconds,
                    )
                    api_requests += reference_requests
                except Exception as exc:
                    error_text = str(exc)
                    if is_rate_limit(error_text):
                        ticker_rate_limited = True
                        errors.append(error_text)
                        break
                    resolution = {
                        "resolved_symbol": market_ticker,
                        "exchange": "",
                        "name": "",
                        "resolution_method": "resolution_error_raw_fallback",
                    }

                resolved_symbol = (
                    str(resolution.get("resolved_symbol") or market_ticker)
                    .strip()
                    .upper()
                )
                exchange = str(resolution.get("exchange") or "").strip()
                method = str(
                    resolution.get("resolution_method") or "raw_fallback"
                ).strip()
                resolved_name = str(resolution.get("name") or "").strip()

                if resolved_symbol not in resolved_symbols_used:
                    resolved_symbols_used.append(resolved_symbol)
                if exchange and exchange not in exchanges_used:
                    exchanges_used.append(exchange)
                if method and method not in resolution_methods:
                    resolution_methods.append(method)
                if resolved_name and resolved_name not in resolution_names:
                    resolution_names.append(resolved_name)

                path = cache_path(
                    args.cache_dir,
                    resolved_symbol,
                    segment_start,
                    request_end,
                )
                cached = load_cache(path)

                if cached:
                    prices = cached
                    cache_hits += 1
                else:
                    try:
                        prices = client.daily_prices(
                            resolved_symbol,
                            start=segment_start,
                            end=request_end,
                            exchange=exchange or None,
                        )
                        api_requests += 1
                    except Exception as exc:
                        api_requests += 1
                        error_text = str(exc)
                        error_class = classify_provider_error(error_text)
                        if is_rate_limit(error_text):
                            ticker_rate_limited = True
                            errors.append(error_text)
                            break
                        error_classes.append(error_class)
                        errors.append(
                            f"{market_ticker}->{resolved_symbol}"
                            f"{':' + exchange if exchange else ''} "
                            f"{segment_start}->{request_end}: {error_text}"
                        )
                        prices = []

                    if prices:
                        write_cache(path, prices)

                    if not ticker_rate_limited:
                        time.sleep(args.request_delay_seconds)

                for price in prices:
                    if segment_start <= price.date < segment_end:
                        by_date[price.date] = price

            if ticker_rate_limited:
                break

        if ticker_rate_limited:
            rate_limit_hit = True
            next_offset = absolute_index
            write_checkpoint(
                args.checkpoint,
                next_offset=next_offset,
                universe_size=len(tickers),
                last_ticker=last_completed_ticker,
                reason="rate_limit",
            )
            print(
                f"[{number:03d}/{len(selected):03d}] {ticker:6s} "
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
                "ticker": ticker,
                "membership_start": min(x[0] for x in ticker_windows),
                "membership_end_exclusive": max(x[1] for x in ticker_windows),
                "market_tickers_used": "|".join(symbols_used),
                "td_symbols_used": "|".join(resolved_symbols_used),
                "td_exchanges_used": "|".join(exchanges_used),
                "resolution_methods": "|".join(resolution_methods),
                "resolved_names": "|".join(resolution_names),
                "price_start": prices[0].date if prices else "",
                "price_end": prices[-1].date if prices else "",
                "rows": len(prices),
                "start_gap_days": "" if start_gap is None else start_gap,
                "end_gap_days": "" if end_gap is None else end_gap,
                "status": status,
                "error_class": "|".join(sorted(set(x for x in error_classes if x))),
                "error": " | ".join(errors),
            }
        )

        print(
            f"[{number:03d}/{len(selected):03d}] {ticker:6s} "
            f"{status:25s} "
            f"{prices[0].date if prices else '-'} -> "
            f"{prices[-1].date if prices else '-'} "
            f"symbols={','.join(symbols_used) or '-'} "
            f"resolved={','.join(resolved_symbols_used) or '-'} "
            f"exchange={','.join(exchanges_used) or '-'}"
        )
        if errors:
            print(f"             error={' | '.join(errors)}")

        last_completed_ticker = ticker
        next_offset = absolute_index + 1
        write_checkpoint(
            args.checkpoint,
            next_offset=next_offset,
            universe_size=len(tickers),
            last_ticker=last_completed_ticker,
            reason="progress",
        )

    else:
        write_checkpoint(
            args.checkpoint,
            next_offset=next_offset,
            universe_size=len(tickers),
            last_ticker=last_completed_ticker,
            reason=(
                "complete"
                if next_offset >= len(tickers)
                else "limit_reached"
            ),
        )

    fields = [
        "ticker",
        "membership_start",
        "membership_end_exclusive",
        "market_tickers_used",
        "td_symbols_used",
        "td_exchanges_used",
        "resolution_methods",
        "resolved_names",
        "price_start",
        "price_end",
        "rows",
        "start_gap_days",
        "end_gap_days",
        "status",
        "error_class",
        "error",
    ]

    cumulative = load_existing_report(args.output)
    for row in report_rows:
        cumulative[row["ticker"]] = {
            key: str(row.get(key, ""))
            for key in fields
        }

    ticker_order = {ticker: index for index, ticker in enumerate(tickers)}
    cumulative_rows = sorted(
        cumulative.values(),
        key=lambda row: ticker_order.get(
            row["ticker"].upper(),
            len(ticker_order),
        ),
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(cumulative_rows)

    full = sum(
        row["status"] == "full_boundary_coverage"
        for row in cumulative_rows
    )
    partial = sum(
        row["status"] == "partial_boundary_coverage"
        for row in cumulative_rows
    )
    missing = sum(row["status"] == "missing" for row in cumulative_rows)

    print()
    print("CUMULATIVE TWELVE DATA FALLBACK COVERAGE")
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
