from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd

from finance.data.historical_market_tickers import (
    load_historical_market_ticker_overrides,
)
from finance.data.sources.pitindex import load_pitindex_sp500
from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.price_provider_resolution import (
    cache_files_by_symbol,
    classify_resolution_state,
    report_attempt_map,
    segment_cache_summary,
    split_market_segments,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose unresolved Issue #7 market-data names against existing "
            "Tiingo recovery attempts, cache files, and historical ticker mappings."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument(
        "--baseline-queue",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "baseline_v2/unresolved_security_queue.csv"
        ),
    )
    parser.add_argument(
        "--tiingo-report",
        type=Path,
        default=Path("reports/tiingo_priority_coverage.csv"),
    )
    parser.add_argument(
        "--tiingo-cache-dir",
        type=Path,
        default=Path("data/cache/tiingo"),
    )
    parser.add_argument(
        "--historical-market-tickers",
        type=Path,
        default=Path("data/reference/historical_market_ticker_overrides.csv"),
    )
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "provider_resolution_v1"
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _windows(intervals, *, start: date, end: date):
    end_exclusive = end + timedelta(days=1)
    result: dict[str, list[tuple[date, date]]] = {}
    for row in intervals:
        left = max(row.start_date, start)
        right = min(row.end_date or end_exclusive, end_exclusive)
        if left >= right:
            continue
        result.setdefault(row.ticker.upper(), []).append((left, right))
    return result


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    queue_path = _resolve(root, args.baseline_queue)
    report_path = _resolve(root, args.tiingo_report)
    cache_dir = _resolve(root, args.tiingo_cache_dir)
    overrides_path = _resolve(root, args.historical_market_tickers)
    output_dir = _resolve(root, args.output_dir)

    required = {
        "baseline queue": queue_path,
        "Tiingo cache": cache_dir,
        "historical market ticker overrides": overrides_path,
        "PITIndex": args.pitindex_data,
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing Issue #7 provider-resolution input(s):\n  "
            + "\n  ".join(missing)
        )

    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit(
            "Issue #7 provider-resolution audit requires a clean tracked worktree"
        )

    queue = pd.read_csv(queue_path, low_memory=False)
    tiingo_report = (
        pd.read_csv(report_path, low_memory=False)
        if report_path.exists()
        else pd.DataFrame()
    )
    attempts = report_attempt_map(tiingo_report)
    cache_index = cache_files_by_symbol(cache_dir)
    cache_symbols = set(cache_index)

    intervals = load_pitindex_sp500(args.pitindex_data)
    windows = _windows(
        intervals,
        start=date(args.start_year, 1, 1),
        end=date(args.end_year, 12, 31),
    )
    overrides = load_historical_market_ticker_overrides(overrides_path)

    rows = []
    segment_rows = []

    for queue_row in queue.itertuples(index=False):
        ticker = str(queue_row.pit_ticker).upper()
        ticker_windows = windows.get(ticker, [])
        segments = split_market_segments(
            pit_ticker=ticker,
            windows=ticker_windows,
            overrides=overrides,
        )
        cache_summary = segment_cache_summary(
            segments=segments,
            cache_index=cache_index,
        )
        attempt = attempts.get(ticker)
        row_series = pd.Series(queue_row._asdict())
        expected_symbols = [
            value
            for value in str(
                cache_summary["expected_tiingo_symbols"]
            ).split("|")
            if value
        ]
        resolution = classify_resolution_state(
            queue_row=row_series,
            expected_provider_symbols=expected_symbols,
            cache_symbols=cache_symbols,
            attempt_row=attempt,
        )

        rows.append({
            "priority_rank": int(queue_row.priority_rank),
            "pit_ticker": ticker,
            "membership_days": int(queue_row.membership_days),
            "failure_class": str(queue_row.failure_class),
            "selected_status": str(queue_row.selected_status),
            "tiingo_status": str(queue_row.tiingo_status),
            "stooq_status": str(queue_row.stooq_status),
            **cache_summary,
            "priority_report_recorded": attempt is not None,
            "priority_report_status": (
                "" if attempt is None else str(attempt.get("status") or "")
            ),
            "priority_report_symbols": (
                "" if attempt is None
                else str(attempt.get("market_tickers_used") or "")
            ),
            "priority_report_error": (
                "" if attempt is None else str(attempt.get("error") or "")
            ),
            "resolution_state": resolution,
        })

        for left, right, market_ticker, provider_symbol in segments:
            segment_rows.append({
                "pit_ticker": ticker,
                "segment_start": left.isoformat(),
                "segment_end_exclusive": right.isoformat(),
                "canonical_market_ticker": market_ticker,
                "expected_tiingo_symbol": provider_symbol,
                "provider_symbol_cached": provider_symbol in cache_symbols,
                "cache_file_count": len(cache_index.get(provider_symbol, [])),
            })

    detail = pd.DataFrame(rows).sort_values(
        ["membership_days", "priority_rank"],
        ascending=[False, True],
        kind="stable",
    )
    segments = pd.DataFrame(segment_rows)

    summary_rows = (
        detail.groupby("resolution_state", as_index=False)
        .agg(
            tickers=("pit_ticker", "size"),
            membership_days=("membership_days", "sum"),
        )
        .sort_values(
            ["membership_days", "resolution_state"],
            ascending=[False, True],
            kind="stable",
        )
    )
    total_days = int(detail["membership_days"].sum())
    summary_rows["membership_day_share_of_unresolved"] = (
        summary_rows["membership_days"] / total_days
        if total_days
        else 0.0
    )

    summary = {
        "schema_version": 1,
        "status": "ISSUE7_PROVIDER_RESOLUTION_AUDIT_COMPLETE",
        "unresolved_tickers": len(detail),
        "unresolved_membership_days": total_days,
        "resolution_states": {
            row.resolution_state: {
                "tickers": int(row.tickers),
                "membership_days": int(row.membership_days),
                "share": float(row.membership_day_share_of_unresolved),
            }
            for row in summary_rows.itertuples(index=False)
        },
        "canonical_market_data_modified": False,
        "network_requests_made": False,
        "partial_source_stitching_changed": False,
    }

    output_dir.mkdir(parents=True, exist_ok=False)
    detail.to_csv(output_dir / "provider_resolution_detail.csv", index=False)
    segments.to_csv(output_dir / "expected_market_segments.csv", index=False)
    summary_rows.to_csv(output_dir / "resolution_summary.csv", index=False)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    fingerprint_paths = [queue_path, overrides_path]
    if report_path.exists():
        fingerprint_paths.append(report_path)
    (output_dir / "input_fingerprints.json").write_text(
        json.dumps({
            "schema_version": 1,
            "direct_inputs": fingerprint_files(
                root=root,
                paths=fingerprint_paths,
            ),
            "pitindex_inputs": fingerprint_files(
                root=args.pitindex_data,
                paths=[
                    args.pitindex_data / "sp500_seed.csv",
                    args.pitindex_data / "sp500_changes.csv",
                    args.pitindex_data / "sp500_current.csv",
                ],
            ),
            "cache_symbols": sorted(cache_symbols),
            "code": provenance,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("ISSUE #7 PROVIDER RESOLUTION AUDIT")
    print(f"Unresolved tickers:          {len(detail):,}")
    print(f"Unresolved membership-days:  {total_days:,}")
    print()
    print("RESOLUTION STATES")
    for row in summary_rows.itertuples(index=False):
        print(
            f"{row.resolution_state:45s} "
            f"tickers={int(row.tickers):3d} "
            f"days={int(row.membership_days):7,d} "
            f"share={row.membership_day_share_of_unresolved:7.2%}"
        )
    print()
    print("TOP PRIORITY DETAIL")
    for row in detail.head(20).itertuples(index=False):
        print(
            f"{int(row.priority_rank):>2}. {row.pit_ticker:<6} "
            f"days={int(row.membership_days):5,d} "
            f"{row.resolution_state} "
            f"expected={row.expected_tiingo_symbols or '-'} "
            f"cached={row.cached_expected_symbols or '-'}"
        )
    print()
    print(f"Output directory:            {output_dir}")
    print("NO NETWORK REQUESTS WERE MADE.")
    print("CANONICAL MARKET DATA WAS NOT MODIFIED.")


if __name__ == "__main__":
    main()
