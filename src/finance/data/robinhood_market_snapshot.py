from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class RobinhoodMarketAudit:
    records: int
    exact_symbol_matches: int
    valid_prices: int
    missing_prices: int
    inactive_instruments: int
    unresolved_symbols: int
    stale_prices: int


def load_robinhood_market_snapshot(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalize_robinhood_market_snapshot(
    payload: dict,
    *,
    as_of: pd.Timestamp | None = None,
    max_price_age_minutes: float = 30.0,
) -> tuple[pd.DataFrame, RobinhoodMarketAudit]:
    records = payload.get("records") or []
    rows: list[dict] = []

    if as_of is not None:
        cutoff = pd.Timestamp(as_of)
        if cutoff.tzinfo is None:
            cutoff = cutoff.tz_localize("America/New_York")
        cutoff_utc = cutoff.tz_convert("UTC")
    else:
        cutoff_utc = None

    metadata = payload.get("export_metadata") or {}
    snapshot_created_raw = (
        metadata.get("created_at")
        or metadata.get("export_created_at")
        or metadata.get("capture_completed_at")
    )

    for record in records:
        universe = record.get("universe_record") or {}
        ticker = str(
            record.get("ticker")
            or universe.get("ticker")
            or record.get("symbol")
            or ""
        ).strip().upper()
        symbol = str(record.get("symbol") or "").strip().upper()

        price_raw = (
            record.get("last_price")
            if record.get("last_price") not in (None, "")
            else record.get("last_trade_price")
        )
        timestamp_raw = (
            record.get("price_timestamp")
            if record.get("price_timestamp") not in (None, "")
            else record.get("venue_last_trade_time")
        )
        price = pd.to_numeric(price_raw, errors="coerce")
        timestamp = pd.to_datetime(timestamp_raw, errors="coerce", utc=True)

        match_status = (
            record.get("instrument_match_status")
            or record.get("instrument_status")
        )
        exact_match = (
            match_status == "exact_symbol_match"
            and ticker
            and symbol == ticker
        )
        instrument_active = record.get("instrument_state") == "active"
        quote_returned = record.get("quote_status") == "returned"

        if cutoff_utc is not None and pd.notna(timestamp):
            age_minutes = (cutoff_utc - timestamp).total_seconds() / 60.0
        else:
            age_minutes = float("nan")

        stale = (
            pd.notna(age_minutes)
            and (age_minutes < 0 or age_minutes > max_price_age_minutes)
        )
        valid_price = bool(
            exact_match
            and instrument_active
            and quote_returned
            and pd.notna(price)
            and float(price) > 0
            and pd.notna(timestamp)
            and not stale
        )

        tradability_value = record.get("tradability")
        if tradability_value is None:
            tradability_value = record.get("robinhood_tradability")

        rows.append(
            {
                "ticker": ticker,
                "cik": universe.get("cik"),
                "company_name": universe.get("name"),
                "gics_sector": universe.get("gics_sector"),
                "close": float(price) if pd.notna(price) else float("nan"),
                "price_timestamp": timestamp,
                "price_age_minutes": age_minutes,
                "price_valid": valid_price,
                "price_source": "robinhood",
                "price_field": (
                    record.get("price_field")
                    or ("last_trade_price" if price_raw not in (None, "") else None)
                ),
                "bid": pd.to_numeric(
                    record.get("bid")
                    if record.get("bid") not in (None, "")
                    else record.get("bid_price"),
                    errors="coerce",
                ),
                "ask": pd.to_numeric(
                    record.get("ask")
                    if record.get("ask") not in (None, "")
                    else record.get("ask_price"),
                    errors="coerce",
                ),
                "instrument_id": record.get("instrument_id"),
                "instrument_state": record.get("instrument_state"),
                "quote_status": record.get("quote_status"),
                "instrument_match_status": match_status,
                "tradability": tradability_value,
                "tradability_status": record.get("tradability_status"),
                "market_state": record.get("market_state"),
                "market_state_status": record.get("market_state_status"),
                "snapshot_created_at": pd.to_datetime(
                    snapshot_created_raw,
                    errors="coerce",
                    utc=True,
                ),
            }
        )

    frame = pd.DataFrame(rows)

    audit = RobinhoodMarketAudit(
        records=len(frame),
        exact_symbol_matches=int(
            frame["instrument_match_status"].eq("exact_symbol_match").sum()
        ) if not frame.empty else 0,
        valid_prices=int(frame["price_valid"].sum()) if not frame.empty else 0,
        missing_prices=int(frame["close"].isna().sum()) if not frame.empty else 0,
        inactive_instruments=int(
            frame["instrument_state"].eq("inactive").sum()
        ) if not frame.empty else 0,
        unresolved_symbols=int(
            (~frame["instrument_match_status"].eq("exact_symbol_match")).sum()
        ) if not frame.empty else 0,
        stale_prices=int(
            (
                frame["price_age_minutes"].notna()
                & (
                    (frame["price_age_minutes"] < 0)
                    | (frame["price_age_minutes"] > max_price_age_minutes)
                )
            ).sum()
        ) if not frame.empty else 0,
    )

    return frame, audit
