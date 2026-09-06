from __future__ import annotations

import argparse
import csv
import gzip
import math
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from statistics import median


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run provider-quality checks on canonical market-price data."
    )
    parser.add_argument(
        "--prices",
        type=Path,
        default=Path("data/market/daily_prices.csv.gz"),
    )
    parser.add_argument(
        "--coverage",
        type=Path,
        default=Path("data/market/price_coverage.csv"),
    )
    parser.add_argument(
        "--tiingo-cache-dir",
        type=Path,
        default=Path("data/cache/tiingo"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/market_price_quality_issues.csv"),
    )
    parser.add_argument(
        "--high-price-threshold",
        type=float,
        default=10_000.0,
        help="Flag any ticker whose canonical close exceeds this level.",
    )
    parser.add_argument(
        "--extreme-return-threshold",
        type=float,
        default=0.75,
        help="Flag absolute one-day close returns above this fraction.",
    )
    parser.add_argument(
        "--scale-jump-ratio",
        type=float,
        default=5.0,
        help="Flag adjacent closes whose ratio exceeds this value or its inverse.",
    )
    parser.add_argument(
        "--lifetime-range-ratio",
        type=float,
        default=1_000.0,
        help="Flag max/min close ratios above this value.",
    )
    parser.add_argument(
        "--overlap-min-days",
        type=int,
        default=20,
    )
    parser.add_argument(
        "--overlap-sample-days",
        type=int,
        default=60,
        help="Use the most recent N overlapping raw-close observations.",
    )
    parser.add_argument(
        "--overlap-median-diff-threshold",
        type=float,
        default=0.05,
        help="Flag median Tiingo-vs-canonical raw-close difference above this fraction.",
    )
    return parser.parse_args()


def _open_csv(path: Path):
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def load_coverage(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {
            row["pit_ticker"].strip().upper(): row
            for row in csv.DictReader(handle)
            if row.get("pit_ticker")
        }


def load_tiingo_raw_closes(cache_dir: Path) -> dict[str, dict[date, float]]:
    result: dict[str, dict[date, float]] = defaultdict(dict)

    for path in sorted(cache_dir.glob("*.csv")):
        symbol = path.name.split("_", 1)[0].upper()
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "date" not in reader.fieldnames:
                continue

            for row in reader:
                try:
                    row_date = date.fromisoformat(row["date"])
                except (KeyError, TypeError, ValueError):
                    continue

                close = _float(row.get("close"))
                if close is not None and close > 0:
                    result[symbol][row_date] = close

    return dict(result)


def add_issue(
    issues: list[dict[str, str]],
    *,
    ticker: str,
    source: str,
    severity: str,
    issue_type: str,
    detail: str,
    date_value: date | None = None,
    value: float | None = None,
) -> None:
    issues.append(
        {
            "pit_ticker": ticker,
            "source": source,
            "severity": severity,
            "issue_type": issue_type,
            "date": "" if date_value is None else date_value.isoformat(),
            "value": "" if value is None else f"{value:.10g}",
            "detail": detail,
        }
    )


def main() -> None:
    args = parse_args()
    coverage = load_coverage(args.coverage)

    ticker_rows: dict[str, list[tuple[date, float, str]]] = defaultdict(list)
    issues: list[dict[str, str]] = []
    source_rows = Counter()

    with _open_csv(args.prices) as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ticker = row["pit_ticker"].strip().upper()
            source = row["source"].strip()
            row_date = date.fromisoformat(row["date"])
            open_value = _float(row.get("open"))
            high = _float(row.get("high"))
            low = _float(row.get("low"))
            close = _float(row.get("close"))

            if close is None or close <= 0:
                continue

            source_rows[source] += 1
            ticker_rows[ticker].append((row_date, close, source))

            if (
                open_value is not None
                and high is not None
                and low is not None
                and (
                    high < max(open_value, close)
                    or low > min(open_value, close)
                    or high < low
                )
            ):
                add_issue(
                    issues,
                    ticker=ticker,
                    source=source,
                    severity="high",
                    issue_type="ohlc_inconsistency",
                    date_value=row_date,
                    value=close,
                    detail=(
                        f"open={open_value}, high={high}, low={low}, close={close}"
                    ),
                )

    ticker_stats = {}
    for ticker, rows in ticker_rows.items():
        rows.sort(key=lambda item: item[0])
        closes = [value for _, value, _ in rows]
        source = rows[0][2]
        min_close = min(closes)
        max_close = max(closes)
        range_ratio = max_close / min_close if min_close > 0 else math.inf

        ticker_stats[ticker] = {
            "source": source,
            "rows": len(rows),
            "min_close": min_close,
            "max_close": max_close,
            "range_ratio": range_ratio,
        }

        if max_close > args.high_price_threshold:
            peak_date, peak_value, _ = max(rows, key=lambda item: item[1])
            add_issue(
                issues,
                ticker=ticker,
                source=source,
                severity="high",
                issue_type="suspicious_price_scale",
                date_value=peak_date,
                value=peak_value,
                detail=(
                    f"max close {peak_value:.4f} exceeds "
                    f"threshold {args.high_price_threshold:.4f}"
                ),
            )

        if range_ratio > args.lifetime_range_ratio:
            add_issue(
                issues,
                ticker=ticker,
                source=source,
                severity="medium",
                issue_type="large_lifetime_price_range",
                value=range_ratio,
                detail=(
                    f"max/min close ratio={range_ratio:.2f}; "
                    f"min={min_close:.6g}, max={max_close:.6g}"
                ),
            )

        previous_date = None
        previous_close = None
        for row_date, close, _ in rows:
            if previous_close is not None and previous_close > 0:
                ratio = close / previous_close
                daily_return = ratio - 1.0

                if abs(daily_return) > args.extreme_return_threshold:
                    add_issue(
                        issues,
                        ticker=ticker,
                        source=source,
                        severity="medium",
                        issue_type="extreme_one_day_return",
                        date_value=row_date,
                        value=daily_return,
                        detail=(
                            f"{previous_date}->{row_date}: "
                            f"{previous_close:.6g}->{close:.6g} "
                            f"({daily_return:+.2%})"
                        ),
                    )

                if (
                    ratio >= args.scale_jump_ratio
                    or ratio <= 1.0 / args.scale_jump_ratio
                ):
                    add_issue(
                        issues,
                        ticker=ticker,
                        source=source,
                        severity="high",
                        issue_type="price_scale_jump",
                        date_value=row_date,
                        value=ratio,
                        detail=(
                            f"{previous_date}->{row_date}: "
                            f"{previous_close:.6g}->{close:.6g}, ratio={ratio:.4f}"
                        ),
                    )

            previous_date = row_date
            previous_close = close

    print("Loading Tiingo cache for provider-overlap checks...")
    tiingo_closes = load_tiingo_raw_closes(args.tiingo_cache_dir)

    overlap_checked = 0
    overlap_flagged = 0

    for ticker, stats in sorted(ticker_stats.items()):
        if stats["source"] != "stooq_bulk":
            continue

        tiingo = tiingo_closes.get(ticker)
        if not tiingo:
            continue

        canonical = {
            row_date: close
            for row_date, close, source in ticker_rows[ticker]
            if source == "stooq_bulk"
        }
        common_dates = sorted(set(canonical) & set(tiingo))
        if len(common_dates) < args.overlap_min_days:
            continue

        sample_dates = common_dates[-args.overlap_sample_days :]
        relative_diffs = []
        ratios = []

        for row_date in sample_dates:
            left = canonical[row_date]
            right = tiingo[row_date]
            if left <= 0 or right <= 0:
                continue
            relative_diffs.append(abs(left - right) / right)
            ratios.append(left / right)

        if len(relative_diffs) < args.overlap_min_days:
            continue

        overlap_checked += 1
        median_diff = median(relative_diffs)
        median_ratio = median(ratios)

        if median_diff > args.overlap_median_diff_threshold:
            overlap_flagged += 1
            add_issue(
                issues,
                ticker=ticker,
                source="stooq_bulk",
                severity="high",
                issue_type="tiingo_stooq_overlap_mismatch",
                value=median_diff,
                detail=(
                    f"{len(relative_diffs)} recent common dates; "
                    f"median absolute relative difference={median_diff:.2%}; "
                    f"median Stooq/Tiingo close ratio={median_ratio:.6g}"
                ),
            )

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    issues.sort(
        key=lambda row: (
            severity_rank.get(row["severity"], 9),
            row["pit_ticker"],
            row["issue_type"],
            row["date"],
        )
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "pit_ticker",
        "source",
        "severity",
        "issue_type",
        "date",
        "value",
        "detail",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(issues)

    issue_tickers = sorted({row["pit_ticker"] for row in issues})
    high_tickers = sorted(
        {
            row["pit_ticker"]
            for row in issues
            if row["severity"] == "high"
        }
    )
    by_type = Counter(row["issue_type"] for row in issues)

    stooq_selected = sum(
        row.get("selected_source", "").strip() == "stooq_bulk"
        for row in coverage.values()
    )

    print()
    print("MARKET PRICE QUALITY AUDIT")
    print(f"Canonical tickers:            {len(ticker_stats):,}")
    print(f"Stooq-selected tickers:       {stooq_selected:,}")
    print(f"Overlap comparisons run:      {overlap_checked:,}")
    print(f"Overlap comparisons flagged:  {overlap_flagged:,}")
    print(f"Total issues:                 {len(issues):,}")
    print(f"Tickers with issues:          {len(issue_tickers):,}")
    print(f"High-severity tickers:        {len(high_tickers):,}")
    print()
    print("ISSUES BY TYPE")
    for issue_type, count in sorted(by_type.items()):
        print(f"{issue_type:32s} {count:6d}")

    if high_tickers:
        print()
        print("HIGH-SEVERITY TICKERS")
        print(", ".join(high_tickers))

    print()
    print(f"Report: {args.output}")
    print("RESULT: REVIEW" if issues else "RESULT: CLEAN")


if __name__ == "__main__":
    main()
