from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PositionDelta:
    ticker: str
    pre_quantity: float
    post_quantity: float
    quantity_delta: float
    expected_filled_quantity: float
    quantity_matches: bool
    post_market_value: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PostFillReconciliation:
    decision_hash: str
    expected_filled_orders: int
    matched_position_deltas: int
    post_positions: int
    valued_post_positions: int
    pre_cash: float
    post_cash: float
    cash_change: float
    pre_buying_power: float
    post_buying_power: float
    buying_power_change: float
    reconciled: bool
    portfolio_state_ready: bool
    reasons: tuple[str, ...]
    position_deltas: tuple[PositionDelta, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        payload["position_deltas"] = [row.to_dict() for row in self.position_deltas]
        return payload


def _response_data(payload: dict, key: str) -> dict:
    response = (
        payload.get("raw_responses", {})
        .get(key, {})
        .get("response", {})
    )
    structured = response.get("structuredContent") or response.get("structured_content") or {}
    data = structured.get("data") if isinstance(structured, dict) else None
    return data if isinstance(data, dict) else {}


def _portfolio(payload: dict) -> dict:
    return _response_data(payload, "portfolio")


def _positions(payload: dict) -> dict[str, dict]:
    rows = _response_data(payload, "positions").get("positions", [])
    return {
        str(row.get("symbol") or "").upper().strip(): row
        for row in rows
        if str(row.get("symbol") or "").strip()
    }


def _valuations(payload: dict) -> dict[str, dict]:
    return {
        str(row.get("symbol") or "").upper().strip(): row
        for row in payload.get("derived_position_valuations", [])
        if str(row.get("symbol") or "").strip()
    }


def _quantity(row: dict | None) -> float:
    if not row:
        return 0.0
    value = row.get("quantity")
    return 0.0 if value in (None, "") else float(value)


def _money(portfolio: dict, key: str) -> float:
    value = portfolio.get(key)
    return 0.0 if value in (None, "") else float(value)


def _buying_power(portfolio: dict) -> float:
    value = (portfolio.get("buying_power") or {}).get("buying_power")
    return 0.0 if value in (None, "") else float(value)


def reconcile_post_fill_portfolio(
    submission_reconciliation: dict,
    pre_broker_state: dict,
    post_broker_state: dict,
    *,
    quantity_tolerance: float = 1e-8,
) -> PostFillReconciliation:
    reasons: list[str] = []
    decision_hash = str(submission_reconciliation.get("decision_hash") or "")

    pre_label = str(pre_broker_state.get("export_metadata", {}).get("account_label") or "")
    post_label = str(post_broker_state.get("export_metadata", {}).get("account_label") or "")
    if "agentic" not in pre_label.lower():
        reasons.append("pre_state_not_agentic")
    if "agentic" not in post_label.lower():
        reasons.append("post_state_not_agentic")
    if pre_label and post_label and pre_label != post_label:
        reasons.append("broker_account_changed")

    matches = submission_reconciliation.get("matches", [])
    filled = [
        row
        for row in matches
        if str(row.get("status") or "").lower() == "filled"
    ]
    nonfilled = [
        row
        for row in matches
        if str(row.get("status") or "").lower() != "filled"
    ]
    if nonfilled:
        reasons.append("not_all_submission_orders_filled")

    pre_positions = _positions(pre_broker_state)
    post_positions = _positions(post_broker_state)
    valuations = _valuations(post_broker_state)

    deltas: list[PositionDelta] = []
    matched_position_deltas = 0
    for row in filled:
        ticker = str(row.get("ticker") or "").upper().strip()
        expected = row.get("filled_quantity")
        if expected in (None, ""):
            reasons.append(f"missing_filled_quantity:{ticker}")
            expected_qty = 0.0
        else:
            expected_qty = float(expected)

        pre_qty = _quantity(pre_positions.get(ticker))
        post_qty = _quantity(post_positions.get(ticker))
        delta = post_qty - pre_qty
        quantity_matches = abs(delta - expected_qty) <= quantity_tolerance
        if quantity_matches:
            matched_position_deltas += 1
        else:
            reasons.append(f"position_delta_mismatch:{ticker}")

        valuation = valuations.get(ticker, {})
        market_value_raw = valuation.get("derived_market_value")
        market_value = None if market_value_raw in (None, "") else float(market_value_raw)

        deltas.append(
            PositionDelta(
                ticker=ticker,
                pre_quantity=pre_qty,
                post_quantity=post_qty,
                quantity_delta=delta,
                expected_filled_quantity=expected_qty,
                quantity_matches=quantity_matches,
                post_market_value=market_value,
            )
        )

    pre_portfolio = _portfolio(pre_broker_state)
    post_portfolio = _portfolio(post_broker_state)
    pre_cash = _money(pre_portfolio, "cash")
    post_cash = _money(post_portfolio, "cash")
    pre_bp = _buying_power(pre_portfolio)
    post_bp = _buying_power(post_portfolio)

    valued_post_positions = sum(
        1
        for ticker in post_positions
        if valuations.get(ticker, {}).get("derived_market_value") not in (None, "")
    )

    expected_filled_orders = len(filled)
    reconciled = (
        not reasons
        and expected_filled_orders == len(matches)
        and matched_position_deltas == expected_filled_orders
    )

    portfolio_state_ready = reconciled and len(post_positions) == valued_post_positions
    if reconciled and not portfolio_state_ready:
        reasons.append("post_positions_missing_market_values")

    return PostFillReconciliation(
        decision_hash=decision_hash,
        expected_filled_orders=expected_filled_orders,
        matched_position_deltas=matched_position_deltas,
        post_positions=len(post_positions),
        valued_post_positions=valued_post_positions,
        pre_cash=pre_cash,
        post_cash=post_cash,
        cash_change=post_cash - pre_cash,
        pre_buying_power=pre_bp,
        post_buying_power=post_bp,
        buying_power_change=post_bp - pre_bp,
        reconciled=reconciled,
        portfolio_state_ready=portfolio_state_ready,
        reasons=tuple(dict.fromkeys(reasons)),
        position_deltas=tuple(deltas),
    )
