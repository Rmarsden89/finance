from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from finance.data.historical_identity_overrides import (
    load_historical_identity_overrides,
)
from finance.data.historical_market_tickers import (
    load_historical_market_ticker_overrides,
)
from finance.data.sec_entity_history import load_sec_entity_events
from finance.data.universe_identity import build_enriched_sp500_intervals
from finance.data.weekly_research_panel import build_weekly_research_panel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build weekly PIT S&P 500 research panel."
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument("--sec-tickers", type=Path)
    parser.add_argument("--sec-historical-names", type=Path)
    parser.add_argument("--datamule-dir", type=Path)
    parser.add_argument("--ticker-renames", type=Path)
    parser.add_argument("--sec-financial-statements-dir", type=Path, required=True)
    parser.add_argument(
        "--identity-overrides",
        type=Path,
        default=Path("data/reference/historical_identity_overrides.csv"),
    )
    parser.add_argument(
        "--market-ticker-overrides",
        type=Path,
        default=Path("data/reference/historical_market_ticker_overrides.csv"),
    )
    parser.add_argument("--winner-facts", type=Path, required=True)
    parser.add_argument("--tiingo-cache-dir", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/cache/research/weekly_research_panel.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    intervals = build_enriched_sp500_intervals(
        args.pitindex_data,
        sec_tickers=args.sec_tickers,
        ticker_renames=args.ticker_renames,
        datamule_dir=args.datamule_dir,
        sec_historical_names=args.sec_historical_names,
        start_year=args.start.year,
    )

    winner_facts = pd.read_csv(
        args.winner_facts,
        parse_dates=["accepted_at"],
        low_memory=False,
    )
    for column in ("period_date", "filed_date", "ddate_date"):
        if column in winner_facts.columns:
            winner_facts[column] = pd.to_datetime(
                winner_facts[column],
                errors="coerce",
            ).dt.date

    zip_paths = sorted(args.sec_financial_statements_dir.glob("*.zip"))
    through = pd.Timestamp.combine(
        args.end,
        pd.Timestamp("16:00:00").time(),
    ).to_pydatetime()
    sec_entity_events = load_sec_entity_events(
        zip_paths,
        through=through,
    )

    identity_overrides = (
        load_historical_identity_overrides(args.identity_overrides)
        if args.identity_overrides.exists()
        else []
    )
    market_ticker_overrides = (
        load_historical_market_ticker_overrides(args.market_ticker_overrides)
        if args.market_ticker_overrides.exists()
        else []
    )

    panel, audit = build_weekly_research_panel(
        intervals,
        winner_facts=winner_facts,
        sec_entity_events=sec_entity_events,
        tiingo_cache_dir=args.tiingo_cache_dir,
        start=args.start,
        end=args.end,
        identity_overrides=identity_overrides,
        market_ticker_overrides=market_ticker_overrides,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(args.output, index=False)

    print("WEEKLY PIT RESEARCH PANEL")
    print(f"Start:                       {args.start}")
    print(f"End:                         {args.end}")
    print(f"Decision weeks:              {audit.decision_weeks}")
    print(f"Panel rows:                  {audit.panel_rows:,}")
    print(f"Average S&P members/week:    {audit.average_members:.1f}")
    print(f"Identity-resolved rows:      {audit.identity_resolved_rows:,}")
    print(f"Price-available rows:        {audit.price_available_rows:,}")
    print(f"Fundamentals-available rows: {audit.fundamentals_available_rows:,}")
    print(f"Research-ready rows:         {audit.research_ready_rows:,}")
    print(f"Output:                      {args.output}")


if __name__ == "__main__":
    main()
