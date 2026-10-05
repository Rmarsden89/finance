from __future__ import annotations

"""Capture read-only execution-price evidence for one saved shadow decision.

This script never previews, reviews, places, modifies, or cancels an order.
It is intentionally run after the shadow computation completes so the quote
latency is part of the hypothetical model's observed execution cost.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from finance.broker.robinhood_gateway import RobinhoodBrokerGateway, run
from finance.shadow.run_log import utc_now_iso


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def top10_tickers(decision: dict) -> list[str]:
    rows = decision.get("top10")
    if rows is None:
        rows = decision.get("decisions")
    if not isinstance(rows, list):
        raise ValueError("Decision is missing top10/decisions")
    ranked = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed decision row")
        rank = int(row["rank"])
        if rank <= 10:
            ranked.append((rank, str(row["ticker"]).strip().upper()))
    ranked.sort()
    if [rank for rank, _ in ranked] != list(range(1, 11)):
        raise ValueError("Expected ranks 1..10")
    tickers = [ticker for _, ticker in ranked]
    if len(set(tickers)) != 10 or any(not ticker for ticker in tickers):
        raise ValueError("Expected 10 unique nonblank Top-10 tickers")
    return tickers


def parse_utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only post-shadow quote capture for hypothetical execution."
    )
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--history-glob",
        default="",
        help=(
            "Optional repo-relative glob of prior execution captures for this "
            "model. Prior quoted tickers are recaptured so existing holdings "
            "can be marked at the current model's execution timestamp."
        ),
    )
    parser.add_argument("--max-quote-age-seconds", type=float, default=120.0)
    args = parser.parse_args()

    decision = read_json(args.decision)
    selected_tickers = top10_tickers(decision)
    tickers = set(selected_tickers)
    tickers.add("SPY")
    if args.history_glob:
        for prior_path in args.repo_root.resolve().glob(args.history_glob):
            if prior_path.resolve() == args.output.resolve():
                continue
            try:
                prior = read_json(prior_path)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if str(prior.get("model_id") or "") != args.model_id:
                continue
            for row in prior.get("quotes") or []:
                if isinstance(row, dict):
                    ticker = str(row.get("ticker") or "").strip().upper()
                    if ticker and ticker != "SPY":
                        tickers.add(ticker)
    tickers = sorted(tickers)
    decision_hash = str(decision.get("decision_hash") or "")
    if not decision_hash:
        raise SystemExit("Shadow decision is missing decision_hash")
    if args.output.exists():
        raise SystemExit(
            f"Execution-price capture is immutable and already exists: {args.output}"
        )

    capture_started_at = utc_now_iso()
    gateway = RobinhoodBrokerGateway()

    async def get_quotes(session_client):
        scoped = RobinhoodBrokerGateway(client=session_client)
        return await scoped.get_quotes(tickers)

    quotes = run(gateway.client.run_with_session(get_quotes))
    if len(quotes) != len(tickers):
        raise SystemExit(
            f"Expected {len(tickers)} quotes, received {len(quotes)}"
        )

    captured_at = utc_now_iso()
    captured_utc = parse_utc(captured_at)
    rows = []
    seen: set[str] = set()
    for quote in quotes:
        ticker = str(
            quote.get("symbol") or quote.get("ticker") or ""
        ).strip().upper()
        if ticker not in set(tickers) or ticker in seen:
            raise SystemExit(f"Unexpected or duplicate quote symbol: {ticker!r}")
        price, timestamp, price_field = RobinhoodBrokerGateway.select_latest_price(
            quote
        )
        if price is None or not timestamp or not price_field:
            raise SystemExit(f"No usable timestamped price returned for {ticker}")
        age = (captured_utc - parse_utc(timestamp)).total_seconds()
        if age < -1.0 or age > args.max_quote_age_seconds:
            raise SystemExit(
                f"{ticker} quote age {age:.1f}s exceeds "
                f"{args.max_quote_age_seconds:.1f}s"
            )
        rows.append(
            {
                "ticker": ticker,
                "price": float(price),
                "price_field": price_field,
                "quote_timestamp": timestamp,
                "quote_age_seconds": age,
                "bid_price": quote.get("bid_price"),
                "ask_price": quote.get("ask_price"),
                "selected_now": ticker in set(selected_tickers),
                "benchmark": ticker == "SPY",
            }
        )
        seen.add(ticker)

    if seen != set(tickers):
        raise SystemExit(
            "Missing Top-10 quote(s): " + ", ".join(sorted(set(tickers) - seen))
        )

    rank = {ticker: index for index, ticker in enumerate(selected_tickers, start=1)}
    rows.sort(
        key=lambda row: (
            0 if row["ticker"] in rank else 1,
            rank.get(row["ticker"], 999),
            row["ticker"],
        )
    )
    payload = {
        "schema_version": 1,
        "status": "SHADOW_EXECUTION_PRICE_CAPTURE_COMPLETE",
        "model_id": args.model_id,
        "as_of": str(decision.get("as_of") or ""),
        "decision_hash": decision_hash,
        "capture_started_at": capture_started_at,
        "captured_at": captured_at,
        "hypothetical_execution_only": True,
        "broker_order_capability": False,
        "timing_policy": "captured_immediately_after_shadow_computation",
        "quotes": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("SHADOW EXECUTION PRICE CAPTURE COMPLETE")
    print(f"Model:                     {args.model_id}")
    print(f"Decision hash:             {decision_hash}")
    print(f"Selected quotes:           {len(selected_tickers)}/10")
    print(f"Total marked symbols:      {len(rows)}")
    print(f"Captured at:               {captured_at}")
    print(f"Output:                    {args.output}")
    print("NO ORDER PREVIEW, REVIEW, OR PLACEMENT CAPABILITY WAS USED.")


if __name__ == "__main__":
    main()
