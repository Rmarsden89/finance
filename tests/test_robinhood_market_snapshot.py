import pandas as pd

from finance.data.robinhood_market_snapshot import normalize_robinhood_market_snapshot


def test_valid_exact_current_quote() -> None:
    payload = {
        "export_metadata": {"created_at": "2026-09-09T19:53:00Z"},
        "records": [
            {
                "ticker": "AAA",
                "symbol": "AAA",
                "last_price": "10.50",
                "price_timestamp": "2026-09-09T19:47:00Z",
                "price_field": "last_trade_price",
                "instrument_id": "x",
                "instrument_state": "active",
                "quote_status": "returned",
                "instrument_match_status": "exact_symbol_match",
                "universe_record": {"ticker": "AAA", "cik": "1", "name": "AAA"},
            }
        ],
    }
    frame, audit = normalize_robinhood_market_snapshot(
        payload,
        as_of=pd.Timestamp("2026-09-09T15:50:00", tz="America/New_York"),
    )
    assert frame.iloc[0]["price_valid"]
    assert frame.iloc[0]["close"] == 10.5
    assert audit.valid_prices == 1


def test_inactive_missing_quote_fails_closed() -> None:
    payload = {
        "records": [
            {
                "ticker": "AAA",
                "symbol": "AAA",
                "last_price": None,
                "price_timestamp": None,
                "instrument_state": "inactive",
                "quote_status": "inactive_instrument",
                "instrument_match_status": "no_exact_symbol_match",
                "universe_record": {"ticker": "AAA", "cik": "1"},
            }
        ],
    }
    frame, audit = normalize_robinhood_market_snapshot(
        payload,
        as_of=pd.Timestamp("2026-09-09T15:50:00", tz="America/New_York"),
    )
    assert not frame.iloc[0]["price_valid"]
    assert audit.missing_prices == 1
    assert audit.inactive_instruments == 1
    assert audit.unresolved_symbols == 1


def test_current_export_schema_is_supported() -> None:
    payload = {
        "export_metadata": {"export_created_at": "2026-09-11T13:06:25Z"},
        "records": [
            {
                "symbol": "AAA",
                "last_trade_price": "10.50",
                "venue_last_trade_time": "2026-09-11T13:00:00Z",
                "bid_price": "10.40",
                "ask_price": "10.60",
                "instrument_id": "x",
                "instrument_state": "active",
                "instrument_status": "exact_symbol_match",
                "quote_status": "returned",
                "robinhood_tradability": {"tradeable": True},
                "tradability_status": "returned",
            }
        ],
    }
    frame, audit = normalize_robinhood_market_snapshot(
        payload,
        as_of=pd.Timestamp("2026-09-11T13:06:25Z"),
    )
    assert frame.iloc[0]["ticker"] == "AAA"
    assert frame.iloc[0]["close"] == 10.5
    assert frame.iloc[0]["price_valid"]
    assert frame.iloc[0]["bid"] == 10.4
    assert frame.iloc[0]["ask"] == 10.6
    assert audit.exact_symbol_matches == 1
    assert audit.valid_prices == 1
