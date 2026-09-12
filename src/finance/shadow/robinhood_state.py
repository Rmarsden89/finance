from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

import pandas as pd


FINAL_ORDER_STATES = {"filled", "cancelled", "canceled", "rejected", "failed", "expired"}


@dataclass(frozen=True)
class RobinhoodBrokerAudit:
    account_value: float
    equity_value: float
    cash: float
    buying_power: float
    positions: int
    valued_positions: int
    non_final_equity_orders: int
    top10_tradable: int
    top10_checked: int


def load_robinhood_shadow_export(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _response_data(response: dict) -> dict:
    structured = response.get("structuredContent") or response.get("structured_content") or {}
    data = structured.get("data")
    if not isinstance(data, dict):
        raise KeyError("Robinhood response is missing structuredContent/structured_content.data")
    return data


def normalize_robinhood_shadow_state(payload: dict) -> tuple[pd.DataFrame, pd.DataFrame, RobinhoodBrokerAudit]:
    raw = payload["raw_responses"]
    portfolio = _response_data(raw["portfolio"]["response"])
    positions = _response_data(raw["positions"]["response"]).get("positions", [])

    valuations = {
        row["symbol"].upper(): row
        for row in payload.get("derived_position_valuations", [])
        if row.get("symbol")
    }

    position_rows = []
    for row in positions:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        valuation = valuations.get(symbol, {})
        raw_value = valuation.get("derived_market_value")
        market_value = None if raw_value in (None, "") else float(raw_value)
        position_rows.append(
            {
                "ticker": symbol,
                "quantity": float(row["quantity"]),
                "average_buy_price": float(row["average_buy_price"]) if row.get("average_buy_price") not in (None, "") else None,
                "market_value": market_value,
                "marked_price": float(valuation["derived_last_price"]) if valuation.get("derived_last_price") not in (None, "") else None,
                "price_timestamp": valuation.get("price_timestamp"),
                "instrument_id": valuation.get("instrument_id"),
            }
        )

    orders = payload.get("non_final_equity_orders")
    if orders is None:
        all_orders = _response_data(raw["orders"]["response"]).get("orders", [])
        orders = [
            row for row in all_orders
            if str(row.get("state") or "").lower() not in FINAL_ORDER_STATES
        ]

    order_rows = []
    for row in orders:
        order_rows.append(
            {
                "order_id": row.get("id"),
                "ticker": str(row.get("symbol") or "").upper(),
                "side": row.get("side"),
                "state": row.get("state"),
                "quantity": row.get("quantity"),
                "filled_quantity": row.get("cumulative_quantity"),
                "dollar_amount": (row.get("dollar_based_amount") or {}).get("amount"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("last_transaction_at"),
            }
        )

    tradability_response = raw.get("tradability", {}).get("response", {})
    try:
        tradability = _response_data(tradability_response).get("results", []) if tradability_response else []
    except KeyError:
        tradability = []
    top10_checked = len(tradability)
    top10_tradable = sum(
        bool(row.get("tradeable"))
        and str(row.get("state") or "").lower() == "active"
        and any(
            str(item.get("account_type") or "").lower() == "individual"
            and str(item.get("account_type_tradability") or "").lower() == "tradable"
            for item in row.get("account_type_tradabilities", [])
        )
        and str(row.get("fractional_tradability") or "").lower() == "tradable"
        for row in tradability
    )

    position_frame = pd.DataFrame(position_rows)
    order_frame = pd.DataFrame(order_rows)
    audit = RobinhoodBrokerAudit(
        account_value=float(portfolio["total_value"]),
        equity_value=float(portfolio["equity_value"]),
        cash=float(portfolio["cash"]),
        buying_power=float(portfolio["buying_power"]["buying_power"]),
        positions=len(position_frame),
        valued_positions=int(position_frame["market_value"].notna().sum()) if not position_frame.empty else 0,
        non_final_equity_orders=len(order_frame),
        top10_tradable=top10_tradable,
        top10_checked=top10_checked,
    )
    return position_frame, order_frame, audit
