from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from finance.broker.robinhood_gateway import RobinhoodBrokerGateway, run


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export long_growth_v1 broker/account and full-universe market inputs "
            "directly from Robinhood Trading MCP. No LLM is involved."
        )
    )
    parser.add_argument("--symbols-file", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--symbol-column",
        default=None,
        help="Ticker column. Auto-detects ticker or symbol when omitted.",
    )
    parser.add_argument(
        "--tradability-symbols",
        default="",
        help="Optional comma-separated symbols to include in the account snapshot tradability check.",
    )
    parser.add_argument(
        "--check-universe-tradability",
        action="store_true",
        help="Include tradability for every symbol in the universe in broker_snapshot_pre.json.",
    )
    return parser.parse_args()


def load_symbols(path: Path, column: str | None) -> list[str]:
    frame = pd.read_csv(path, low_memory=False)
    if column is None:
        for candidate in ("ticker", "symbol"):
            if candidate in frame.columns:
                column = candidate
                break
    if column is None or column not in frame.columns:
        raise ValueError(
            f"Could not find ticker column in {path}; specify --symbol-column"
        )
    symbols = sorted(
        {
            str(value).strip().upper()
            for value in frame[column].dropna().tolist()
            if str(value).strip()
        }
    )
    if not symbols:
        raise ValueError(f"No symbols found in {path}")
    return symbols


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> None:
    args = parse_args()
    symbols = load_symbols(args.symbols_file, args.symbol_column)
    tradability = [
        item.strip().upper()
        for item in args.tradability_symbols.split(",")
        if item.strip()
    ]
    if args.check_universe_tradability:
        tradability = symbols

    gateway = RobinhoodBrokerGateway()
    args.run_dir.mkdir(parents=True, exist_ok=True)

    print("ROBINHOOD DIRECT LIVE INPUT EXPORT", flush=True)
    print("No LLM is involved.", flush=True)
    print(f"Universe symbols:          {len(symbols):,}", flush=True)
    print("MCP session mode:          persistent", flush=True)
    if tradability:
        print(f"Tradability symbols:       {len(tradability):,}", flush=True)

    async def export_all():
        async with gateway.client.session():
            print("Fetching Agentic account snapshot...", flush=True)
            broker_snapshot = await gateway.get_account_snapshot(
                tradability_symbols=tradability or None
            )
            broker_path = args.run_dir / "broker_snapshot_pre.json"
            write_json(broker_path, broker_snapshot)
            print(f"Broker snapshot:           {broker_path}", flush=True)

            print("Fetching full-universe Robinhood quotes...", flush=True)
            market_snapshot = await gateway.get_market_snapshot(symbols)
            market_path = args.run_dir / "robinhood_market_snapshot.json"
            write_json(market_path, market_snapshot)
            print(f"Market snapshot:           {market_path}", flush=True)
            return market_snapshot

    market_snapshot = run(export_all())

    quote_count = market_snapshot["export_metadata"]["quote_record_count"]
    unresolved = len(symbols) - int(quote_count)
    print()
    print("EXPORT COMPLETE", flush=True)
    print(f"Quotes returned:           {quote_count:,}", flush=True)
    print(f"Unresolved/not returned:   {unresolved:,}", flush=True)


if __name__ == "__main__":
    main()
