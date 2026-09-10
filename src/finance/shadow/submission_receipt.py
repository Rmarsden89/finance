from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


FINAL_SUCCESS_STATES = {"filled"}
ACCEPTED_NONFINAL_STATES = {
    "queued",
    "confirmed",
    "unconfirmed",
    "pending",
    "partially_filled",
    "partially-filled",
}
FINAL_FAILURE_STATES = {"rejected", "cancelled", "canceled", "failed", "expired"}


@dataclass(frozen=True)
class ReceiptMatch:
    ticker: str
    idempotency_key: str
    requested_dollars: float
    broker_order_id: str | None
    broker_state: str | None
    broker_dollars: float | None
    filled_quantity: float | None
    average_price: float | None
    submitted_at: str | None
    status: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SubmissionReconciliation:
    decision_hash: str
    expected_orders: int
    broker_orders_seen: int
    matched_orders: int
    accepted_orders: int
    filled_orders: int
    failed_orders: int
    missing_orders: int
    duplicate_matches: int
    unexpected_orders: int
    reconciled: bool
    all_accepted: bool
    retry_blocked: bool
    reasons: tuple[str, ...]
    matches: tuple[ReceiptMatch, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        payload["matches"] = [row.to_dict() for row in self.matches]
        return payload


def _extract_broker_orders(payload: dict) -> list[dict]:
    for key in ("submitted_orders", "orders", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    raw = payload.get("raw_responses", {})
    orders = (
        raw.get("orders", {})
        .get("response", {})
        .get("structuredContent", {})
        .get("data", {})
        .get("orders")
    )
    if isinstance(orders, list):
        return orders

    return []


def _broker_amount(row: dict) -> float | None:
    amount = row.get("requested_dollars")
    if amount not in (None, ""):
        return float(amount)
    dollar = row.get("dollar_based_amount")
    if isinstance(dollar, dict) and dollar.get("amount") not in (None, ""):
        return float(dollar["amount"])
    for key in ("amount_dollars", "dollar_amount", "amount"):
        if row.get(key) not in (None, ""):
            return float(row[key])
    return None


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def reconcile_submission_receipt(
    order_intents: dict,
    broker_receipt: dict,
    *,
    dollar_tolerance: float = 0.01,
) -> SubmissionReconciliation:
    decision_hash = str(order_intents.get("decision_hash") or "")
    intents = order_intents.get("intents", [])
    broker_orders = _extract_broker_orders(broker_receipt)

    by_ticker: dict[str, list[dict]] = {}
    for row in broker_orders:
        ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
        if ticker:
            by_ticker.setdefault(ticker, []).append(row)

    reasons: list[str] = []
    matches: list[ReceiptMatch] = []
    matched_order_ids: set[str] = set()
    duplicate_matches = 0

    expected_tickers = {
        str(row.get("ticker") or "").upper().strip()
        for row in intents
        if row.get("ticker")
    }

    for intent in intents:
        ticker = str(intent.get("ticker") or "").upper().strip()
        expected_amount = float(intent.get("amount_dollars") or 0.0)
        idempotency_key = str(intent.get("idempotency_key") or "")

        candidates = by_ticker.get(ticker, [])
        exact_amount = [
            row
            for row in candidates
            if _broker_amount(row) is not None
            and abs(float(_broker_amount(row)) - expected_amount) <= dollar_tolerance
            and str(row.get("side") or "buy").lower() == "buy"
        ]

        if len(exact_amount) == 0:
            matches.append(
                ReceiptMatch(
                    ticker=ticker,
                    idempotency_key=idempotency_key,
                    requested_dollars=expected_amount,
                    broker_order_id=None,
                    broker_state=None,
                    broker_dollars=None,
                    filled_quantity=None,
                    average_price=None,
                    submitted_at=None,
                    status="missing",
                    reason="no_matching_broker_order",
                )
            )
            reasons.append(f"missing_order:{ticker}")
            continue

        if len(exact_amount) > 1:
            duplicate_matches += 1
            reasons.append(f"duplicate_matching_orders:{ticker}")

        row = exact_amount[0]
        order_id = str(row.get("order_id") or row.get("id") or "").strip() or None
        state = str(row.get("state") or row.get("status") or "").lower().strip() or None
        broker_amount = _broker_amount(row)
        filled_quantity = _float_or_none(
            row.get("filled_quantity")
            if row.get("filled_quantity") not in (None, "")
            else row.get("cumulative_quantity")
        )
        average_price = _float_or_none(row.get("average_price"))
        submitted_at = (
            row.get("submitted_at")
            or row.get("created_at")
            or row.get("last_transaction_at")
        )

        if order_id:
            if order_id in matched_order_ids:
                duplicate_matches += 1
                reasons.append(f"duplicate_broker_order_id:{order_id}")
            matched_order_ids.add(order_id)
        else:
            reasons.append(f"missing_broker_order_id:{ticker}")

        if state in FINAL_SUCCESS_STATES:
            status = "filled"
            reason = ""
        elif state in ACCEPTED_NONFINAL_STATES:
            status = "accepted"
            reason = ""
        elif state in FINAL_FAILURE_STATES:
            status = "failed"
            reason = f"broker_state:{state}"
            reasons.append(f"broker_order_failed:{ticker}:{state}")
        else:
            status = "ambiguous"
            reason = f"unknown_broker_state:{state or 'missing'}"
            reasons.append(f"ambiguous_broker_state:{ticker}:{state or 'missing'}")

        matches.append(
            ReceiptMatch(
                ticker=ticker,
                idempotency_key=idempotency_key,
                requested_dollars=expected_amount,
                broker_order_id=order_id,
                broker_state=state,
                broker_dollars=broker_amount,
                filled_quantity=filled_quantity,
                average_price=average_price,
                submitted_at=submitted_at,
                status=status,
                reason=reason,
            )
        )

    unexpected = [
        row
        for row in broker_orders
        if str(row.get("ticker") or row.get("symbol") or "").upper().strip()
        not in expected_tickers
    ]
    if unexpected:
        reasons.append("unexpected_broker_orders_present")

    matched_orders = sum(row.broker_order_id is not None for row in matches)
    accepted_orders = sum(row.status in {"accepted", "filled"} for row in matches)
    filled_orders = sum(row.status == "filled" for row in matches)
    failed_orders = sum(row.status == "failed" for row in matches)
    missing_orders = sum(row.status == "missing" for row in matches)

    reconciled = (
        len(matches) == len(intents)
        and missing_orders == 0
        and duplicate_matches == 0
        and len(unexpected) == 0
        and all(row.broker_order_id for row in matches)
    )
    all_accepted = reconciled and accepted_orders == len(intents)

    retry_blocked = any(row.broker_order_id for row in matches) or not reconciled

    return SubmissionReconciliation(
        decision_hash=decision_hash,
        expected_orders=len(intents),
        broker_orders_seen=len(broker_orders),
        matched_orders=matched_orders,
        accepted_orders=accepted_orders,
        filled_orders=filled_orders,
        failed_orders=failed_orders,
        missing_orders=missing_orders,
        duplicate_matches=duplicate_matches,
        unexpected_orders=len(unexpected),
        reconciled=reconciled,
        all_accepted=all_accepted,
        retry_blocked=retry_blocked,
        reasons=tuple(dict.fromkeys(reasons)),
        matches=tuple(matches),
    )
