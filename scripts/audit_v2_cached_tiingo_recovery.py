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
    cached_coverage_status,
    cached_tiingo_rows_for_windows,
    load_tiingo_cache_rows,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Simulate segment-aware Tiingo recovery for unresolved Issue #7 "
            "tickers using only existing cache files. No network requests."
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
    parser.add_argument("--boundary-tolerance-days", type=int, default=7)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "cached_tiingo_recovery_v1"
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
        if left < right:
            result.setdefault(row.ticker.upper(), []).append((left, right))
    return result


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    queue_path = _resolve(root, args.baseline_queue)
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
            "Missing cached Tiingo recovery input(s):\n  "
            + "\n  ".join(missing)
        )

    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit(
            "Cached Tiingo recovery audit requires a clean tracked worktree"
        )

    queue = pd.read_csv(queue_path, low_memory=False)
    intervals = load_pitindex_sp500(args.pitindex_data)
    windows = _windows(
        intervals,
        start=date(args.start_year, 1, 1),
        end=date(args.end_year, 12, 31),
    )
    overrides = load_historical_market_ticker_overrides(overrides_path)
    cache = load_tiingo_cache_rows(cache_dir)

    rows = []
    for row in queue.itertuples(index=False):
        ticker = str(row.pit_ticker).upper()
        ticker_windows = windows.get(ticker, [])
        prices, symbols = cached_tiingo_rows_for_windows(
            pit_ticker=ticker,
            windows=ticker_windows,
            overrides=overrides,
            cache=cache,
        )
        status, start_gap, end_gap = cached_coverage_status(
            prices,
            ticker_windows,
            tolerance_days=args.boundary_tolerance_days,
        )
        rows.append({
            "priority_rank": int(row.priority_rank),
            "pit_ticker": ticker,
            "membership_days": int(row.membership_days),
            "current_selected_status": str(row.selected_status),
            "current_tiingo_status": str(row.tiingo_status),
            "failure_class": str(row.failure_class),
            "market_symbols_used": "|".join(symbols),
            "cached_rows": len(prices),
            "cached_price_start": prices[0].date.isoformat() if prices else "",
            "cached_price_end": prices[-1].date.isoformat() if prices else "",
            "cached_start_gap_days": "" if start_gap is None else start_gap,
            "cached_end_gap_days": "" if end_gap is None else end_gap,
            "simulated_tiingo_status": status,
            "recoverable_from_existing_cache": (
                status == "full_boundary_coverage"
            ),
        })

    detail = pd.DataFrame(rows).sort_values(
        ["membership_days", "priority_rank"],
        ascending=[False, True],
        kind="stable",
    )
    full = detail.loc[detail["recoverable_from_existing_cache"]].copy()
    partial = detail.loc[
        detail["simulated_tiingo_status"].eq("partial_boundary_coverage")
    ].copy()
    missing = detail.loc[
        detail["simulated_tiingo_status"].eq("missing")
    ].copy()

    unresolved_days = int(detail["membership_days"].sum())
    recoverable_days = int(full["membership_days"].sum())
    partial_days = int(partial["membership_days"].sum())
    missing_days = int(missing["membership_days"].sum())

    summary = {
        "schema_version": 1,
        "status": "ISSUE7_CACHED_TIINGO_RECOVERY_SIMULATION_COMPLETE",
        "unresolved_tickers": len(detail),
        "unresolved_membership_days": unresolved_days,
        "full_cached_recovery_tickers": len(full),
        "full_cached_recovery_membership_days": recoverable_days,
        "full_cached_recovery_share_of_unresolved": (
            recoverable_days / unresolved_days if unresolved_days else 0.0
        ),
        "partial_cached_tickers": len(partial),
        "partial_cached_membership_days": partial_days,
        "missing_cached_tickers": len(missing),
        "missing_cached_membership_days": missing_days,
        "network_requests_made": False,
        "canonical_market_data_modified": False,
        "partial_source_stitching_changed": False,
    }

    output_dir.mkdir(parents=True, exist_ok=False)
    detail.to_csv(output_dir / "cached_tiingo_recovery_detail.csv", index=False)
    full.to_csv(output_dir / "full_cached_recovery_candidates.csv", index=False)
    partial.to_csv(output_dir / "partial_cached_candidates.csv", index=False)
    missing.to_csv(output_dir / "missing_cached_candidates.csv", index=False)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "input_fingerprints.json").write_text(
        json.dumps({
            "schema_version": 1,
            "direct_inputs": fingerprint_files(
                root=root,
                paths=[queue_path, overrides_path],
            ),
            "pitindex_inputs": fingerprint_files(
                root=args.pitindex_data,
                paths=[
                    args.pitindex_data / "sp500_seed.csv",
                    args.pitindex_data / "sp500_changes.csv",
                    args.pitindex_data / "sp500_current.csv",
                ],
            ),
            "code": provenance,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("ISSUE #7 CACHED TIINGO RECOVERY SIMULATION")
    print(f"Unresolved tickers:          {len(detail):,}")
    print(f"Unresolved membership-days:  {unresolved_days:,}")
    print(
        f"Full cached recovery:        {len(full):,} tickers / "
        f"{recoverable_days:,} days "
        f"({summary['full_cached_recovery_share_of_unresolved']:.2%})"
    )
    print(
        f"Partial cached coverage:     {len(partial):,} tickers / "
        f"{partial_days:,} days"
    )
    print(
        f"No cached recovery:          {len(missing):,} tickers / "
        f"{missing_days:,} days"
    )
    print()
    print("FULL CACHED RECOVERY CANDIDATES")
    for row in full.itertuples(index=False):
        print(
            f"{int(row.priority_rank):>2}. {row.pit_ticker:<6} "
            f"days={int(row.membership_days):5,d} "
            f"symbols={row.market_symbols_used or '-'} "
            f"{row.cached_price_start}->{row.cached_price_end}"
        )
    print()
    print(f"Output directory:            {output_dir}")
    print("NO NETWORK REQUESTS WERE MADE.")
    print("CANONICAL MARKET DATA WAS NOT MODIFIED.")


if __name__ == "__main__":
    main()
