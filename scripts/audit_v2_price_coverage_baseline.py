from __future__ import annotations

import argparse
import csv
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.data.sources.pitindex import load_pitindex_sp500
from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.price_coverage_audit import (
    classify_unresolved_price_row,
    identity_summary_by_ticker,
    membership_days_by_ticker_and_year,
    yearly_panel_coverage,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the immutable Issue #7 baseline for unresolved historical "
            "market-data coverage. Research-only; does not modify canonical data."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument(
        "--coverage",
        type=Path,
        default=Path("data/market/price_coverage.csv"),
    )
    parser.add_argument(
        "--panel",
        type=Path,
        default=Path("reports/weekly_research_panel_2015_2025.csv"),
    )
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/baseline"
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _load_coverage(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    required = {
        "pit_ticker",
        "selected_source",
        "selected_status",
        "tiingo_status",
        "stooq_status",
    }
    missing = required - set(frame.columns)
    if missing:
        raise SystemExit(
            "Coverage manifest lacks columns: " + ", ".join(sorted(missing))
        )
    frame["pit_ticker"] = frame["pit_ticker"].astype(str).str.upper()
    return frame


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    coverage_path = (
        args.coverage if args.coverage.is_absolute() else root / args.coverage
    )
    panel_path = (
        args.panel if args.panel.is_absolute() else root / args.panel
    )
    output_dir = (
        args.output_dir
        if args.output_dir.is_absolute()
        else root / args.output_dir
    )

    required = {
        "coverage": coverage_path,
        "panel": panel_path,
        "pitindex": args.pitindex_data,
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit("Missing Issue #7 baseline input(s):\n  " + "\n  ".join(missing))

    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit("Issue #7 baseline requires a clean tracked worktree")

    intervals = load_pitindex_sp500(args.pitindex_data)
    start = date(args.start_year, 1, 1)
    end = date(args.end_year, 12, 31)
    membership_days, yearly_days = membership_days_by_ticker_and_year(
        intervals,
        start=start,
        end=end,
    )

    coverage = _load_coverage(coverage_path)
    panel = pd.read_csv(panel_path, low_memory=False)
    identity = identity_summary_by_ticker(panel)
    yearly_panel = yearly_panel_coverage(panel)

    queue = coverage.loc[
        coverage["selected_source"].astype(str).eq("unresolved")
    ].copy()
    queue = queue.merge(
        identity,
        left_on="pit_ticker",
        right_on="ticker",
        how="left",
        validate="one_to_one",
    ).drop(columns=["ticker"], errors="ignore")
    queue["membership_days"] = (
        queue["pit_ticker"].map(membership_days).fillna(0).astype(int)
    )
    queue["identity_status"] = queue["identity_status"].fillna(
        "identity_unknown"
    )
    queue["failure_class"] = queue.apply(
        classify_unresolved_price_row,
        axis=1,
    )
    queue["identity_failure"] = queue["identity_status"].isin(
        {"identity_unresolved", "identity_partial", "identity_unknown"}
    )
    queue["provider_failure"] = True

    yearly_rows = []
    for row in queue.itertuples(index=False):
        ticker = str(row.pit_ticker)
        for year, days in sorted(yearly_days.get(ticker, {}).items()):
            yearly_rows.append({
                "pit_ticker": ticker,
                "year": year,
                "membership_days": days,
                "identity_status": row.identity_status,
                "failure_class": row.failure_class,
                "selected_status": row.selected_status,
                "tiingo_status": row.tiingo_status,
                "stooq_status": row.stooq_status,
            })
    yearly_unresolved = pd.DataFrame(yearly_rows)

    queue = queue.sort_values(
        ["membership_days", "pit_ticker"],
        ascending=[False, True],
        kind="stable",
    ).reset_index(drop=True)
    queue.insert(0, "priority_rank", range(1, len(queue) + 1))

    total_membership_days = sum(membership_days.values())
    unresolved_days = int(queue["membership_days"].sum())
    identity_failure_days = int(
        queue.loc[queue["identity_failure"], "membership_days"].sum()
    )
    provider_only_days = int(
        queue.loc[~queue["identity_failure"], "membership_days"].sum()
    )

    summary = {
        "schema_version": 1,
        "status": "ISSUE7_MARKET_COVERAGE_BASELINE_COMPLETE",
        "period": {
            "start_year": args.start_year,
            "end_year": args.end_year,
        },
        "pit_tickers": len(membership_days),
        "coverage_manifest_rows": len(coverage),
        "unresolved_tickers": len(queue),
        "total_membership_days": total_membership_days,
        "unresolved_membership_days": unresolved_days,
        "unresolved_membership_day_pct": (
            unresolved_days / total_membership_days
            if total_membership_days
            else 0.0
        ),
        "identity_failure_unresolved_tickers": int(queue["identity_failure"].sum()),
        "identity_failure_membership_days": identity_failure_days,
        "provider_only_unresolved_tickers": int((~queue["identity_failure"]).sum()),
        "provider_only_membership_days": provider_only_days,
        "no_partial_source_stitching_changed": False,
        "canonical_market_data_modified": False,
    }

    output_dir.mkdir(parents=True, exist_ok=False)
    queue.to_csv(output_dir / "unresolved_security_queue.csv", index=False)
    yearly_unresolved.to_csv(
        output_dir / "unresolved_membership_days_by_year.csv",
        index=False,
    )
    yearly_panel.to_csv(
        output_dir / "baseline_research_ready_coverage_by_year.csv",
        index=False,
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "input_fingerprints.json").write_text(
        json.dumps({
            "schema_version": 1,
            "direct_inputs": fingerprint_files(
                root=root,
                paths=[coverage_path, panel_path],
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

    print("ISSUE #7 MARKET COVERAGE BASELINE")
    print(f"Period:                      {args.start_year}-{args.end_year}")
    print(f"PIT tickers:                 {len(membership_days):,}")
    print(f"Unresolved tickers:          {len(queue):,}")
    print(f"Total membership-days:       {total_membership_days:,}")
    print(f"Unresolved membership-days:  {unresolved_days:,}")
    print(
        "Unresolved share:            "
        f"{summary['unresolved_membership_day_pct']:.3%}"
    )
    print(
        "Identity-failure exposure:   "
        f"{identity_failure_days:,} days / "
        f"{int(queue['identity_failure'].sum()):,} tickers"
    )
    print(
        "Provider-only exposure:      "
        f"{provider_only_days:,} days / "
        f"{int((~queue['identity_failure']).sum()):,} tickers"
    )
    print()
    print("TOP UNRESOLVED QUEUE")
    for row in queue.head(20).itertuples(index=False):
        print(
            f"{int(row.priority_rank):>2}. {row.pit_ticker:<6} "
            f"days={int(row.membership_days):5,d} "
            f"{row.failure_class}"
        )
    print()
    print(f"Output directory:            {output_dir}")
    print("CANONICAL MARKET DATA WAS NOT MODIFIED.")
    print("NO PARTIAL-SOURCE STITCHING WAS INTRODUCED.")


if __name__ == "__main__":
    main()
