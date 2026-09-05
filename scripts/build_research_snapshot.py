from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

from finance.data.historical_identity_overrides import load_historical_identity_overrides
from finance.data.historical_market_tickers import load_historical_market_ticker_overrides
from finance.data.research_panel import build_research_snapshot
from finance.data.sec_entity_history import load_sec_entity_evidence
from finance.data.universe_identity import build_enriched_sp500_intervals


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a PIT S&P 500 research snapshot from membership, SEC, and Tiingo."
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument("--sec-tickers", type=Path)
    parser.add_argument("--sec-historical-names", type=Path)
    parser.add_argument("--datamule-dir", type=Path)
    parser.add_argument("--ticker-renames", type=Path)
    parser.add_argument("--sec-financial-statements-dir", type=Path)
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
    parser.add_argument("--as-of", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/research_snapshot.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    as_of = datetime.fromisoformat(args.as_of)

    intervals = build_enriched_sp500_intervals(
        args.pitindex_data,
        sec_tickers=args.sec_tickers,
        ticker_renames=args.ticker_renames,
        datamule_dir=args.datamule_dir,
        sec_historical_names=args.sec_historical_names,
        start_year=2015,
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

    sec_entity_evidence = None
    if args.sec_financial_statements_dir:
        zip_paths = sorted(args.sec_financial_statements_dir.glob("*.zip"))
        sec_entity_evidence = load_sec_entity_evidence(
            zip_paths,
            as_of=as_of,
        )

    identity_overrides = (
        load_historical_identity_overrides(args.identity_overrides)
        if args.identity_overrides and args.identity_overrides.exists()
        else []
    )

    market_ticker_overrides = (
        load_historical_market_ticker_overrides(args.market_ticker_overrides)
        if args.market_ticker_overrides and args.market_ticker_overrides.exists()
        else []
    )

    panel = build_research_snapshot(
        intervals,
        winner_facts=winner_facts,
        tiingo_cache_dir=args.tiingo_cache_dir,
        as_of=as_of,
        sec_entity_evidence=sec_entity_evidence,
        identity_overrides=identity_overrides,
        market_ticker_overrides=market_ticker_overrides,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(args.output, index=False)

    members = len(panel)
    identity = int(panel["identity_resolved"].sum())
    repaired = int(
        panel["identity_resolution_method"].isin(
            ["sec_name_as_of", "curated_sec_override"]
        ).sum()
    )
    prices = int(panel["price_available"].sum())
    fundamentals = int(panel["fundamentals_available"].sum())
    both = int(
        (
            panel["identity_resolved"]
            & panel["price_available"]
            & panel["fundamentals_available"]
        ).sum()
    )

    print("PIT RESEARCH SNAPSHOT")
    print(f"As of:                    {as_of}")
    print(f"S&P members:              {members}")
    print(f"Identity resolved:         {identity}")
    print(f"Identity repaired as-of:   {repaired}")
    print(f"Price available in cache: {prices}")
    print(f"Fundamentals available:   {fundamentals}")
    print(f"Fully research-ready:      {both}")
    print(f"Output:                    {args.output}")


if __name__ == "__main__":
    main()
