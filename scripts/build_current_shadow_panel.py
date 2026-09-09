from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from finance.data.current_shadow_panel import (
    assemble_current_scoring_panel,
    build_current_shadow_row,
    build_sec_only_weekly_extension,
)
from finance.data.sources.pitindex import load_pitindex_sp500


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a trailing current scoring panel from the frozen 2025 panel, "
            "2026 SEC-only weekly snapshots, and a current Robinhood quote snapshot."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument("--historical-panel", type=Path, required=True)
    parser.add_argument("--shadow-winners", type=Path, required=True)
    parser.add_argument("--market-snapshot", type=Path, required=True)
    parser.add_argument("--as-of", type=pd.Timestamp, required=True)
    parser.add_argument(
        "--extension-start",
        type=pd.Timestamp,
        default=pd.Timestamp("2026-01-02"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/current_shadow_scoring_panel.csv"),
    )
    parser.add_argument(
        "--current-output",
        type=Path,
        default=Path("reports/current_shadow_snapshot.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print("CURRENT SHADOW SCORING PANEL", flush=True)
    print(f"As of:                     {args.as_of}", flush=True)

    intervals = load_pitindex_sp500(args.pitindex_data)

    print("Loading SEC shadow winner facts...", flush=True)
    winners = pd.read_csv(args.shadow_winners, low_memory=False)
    print(f"Winner rows:               {len(winners):,}", flush=True)

    print("Loading normalized Robinhood snapshot...", flush=True)
    market = pd.read_csv(args.market_snapshot, low_memory=False)
    print(
        f"Valid Robinhood prices:    "
        f"{int(market['price_valid'].fillna(False).astype(bool).sum()):,}",
        flush=True,
    )

    last_friday = args.as_of - pd.offsets.Week(weekday=4)
    if last_friday.date() == args.as_of.date():
        last_friday = last_friday - pd.Timedelta(days=7)

    print(
        f"Building SEC-only weekly extension through {last_friday.date()}...",
        flush=True,
    )
    extension = build_sec_only_weekly_extension(
        intervals,
        winners,
        start=args.extension_start,
        end=last_friday,
    )

    print("Building current SEC + Robinhood snapshot...", flush=True)
    current = build_current_shadow_row(
        intervals,
        winners,
        market,
        as_of=args.as_of,
    )

    print("Loading trailing historical research panel...", flush=True)
    historical = pd.read_csv(args.historical_panel, low_memory=False)

    combined = assemble_current_scoring_panel(
        historical,
        extension,
        current,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.current_output.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(args.output, index=False)
    current.to_csv(args.current_output, index=False)

    print()
    print("CURRENT SHADOW PANEL COMPLETE", flush=True)
    print(f"Historical tail rows:      {len(historical):,} source rows", flush=True)
    print(f"2026 extension rows:       {len(extension):,}", flush=True)
    print(f"Current rows:              {len(current):,}", flush=True)
    print(f"Current identity resolved: {int(current['identity_resolved'].sum()):,}", flush=True)
    print(f"Current fundamentals:      {int(current['fundamentals_available'].sum()):,}", flush=True)
    print(f"Current prices:            {int(current['price_available'].sum()):,}", flush=True)
    print(f"Current research ready:    {int(current['research_ready'].sum()):,}", flush=True)
    print(f"Scoring panel rows:        {len(combined):,}", flush=True)
    print(f"Scoring panel:             {args.output}", flush=True)
    print(f"Current snapshot:          {args.current_output}", flush=True)


if __name__ == "__main__":
    main()
