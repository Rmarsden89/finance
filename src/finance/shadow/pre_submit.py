from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json


@dataclass(frozen=True)
class PreSubmitResult:
    ready: bool
    decision_hash: str
    order_count: int
    total_dollars: float
    buying_power: float
    snapshot_created_at: str
    snapshot_age_minutes: float
    non_final_equity_orders: int
    tradable_count: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def evaluate_pre_submit(
    order_intents: dict,
    broker_state: dict,
    *,
    now: datetime | None = None,
    max_snapshot_age_minutes: float = 5.0,
) -> PreSubmitResult:
    reasons: list[str] = []
    now = now or datetime.now(timezone.utc)

    metadata = broker_state.get("export_metadata", {})
    account_label = str(metadata.get("account_label") or "")
    if "agentic" not in account_label.lower():
        reasons.append("broker_account_is_not_agentic")

    created_raw = str(\n        metadata.get("created_at")\n        or metadata.get("capture_completed_at")\n        or metadata.get("capture_started_at")\n        or ""\n    )
    created = None
    if created_raw:
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            reasons.append("invalid_broker_snapshot_timestamp")
    else:
        reasons.append("missing_broker_snapshot_timestamp")

    snapshot_age_minutes = float("inf")
    if created is not None:
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        snapshot_age_minutes = (now - created.astimezone(timezone.utc)).total_seconds() / 60.0
        if snapshot_age_minutes < -1.0:
            reasons.append("broker_snapshot_from_future")
        elif snapshot_age_minutes > max_snapshot_age_minutes:
            reasons.append("broker_snapshot_stale")

    raw = broker_state.get("raw_responses", {})
    portfolio = (
        raw.get("portfolio", {})
        .get("response", {})
        .get("structuredContent", {})
        .get("data", {})
    )
    buying_power = float(
        (portfolio.get("buying_power") or {}).get("buying_power") or 0.0
    )

    non_final = broker_state.get("non_final_equity_orders")
    if non_final is None:
        non_final = []
    if len(non_final) > 0:
        reasons.append("non_final_equity_orders_present")

    total_dollars = float(order_intents.get("total_dollars") or 0.0)
    if total_dollars <= 0:
        reasons.append("no_order_dollars")
    if buying_power + 1e-9 < total_dollars:
        reasons.append("insufficient_buying_power")

    intents = order_intents.get("intents", [])
    decision_hash = str(order_intents.get("decision_hash") or "")
    seen_keys: set[str] = set()
    intent_tickers: set[str] = set()
    for row in intents:
        if str(row.get("decision_hash") or "") != decision_hash:
            reasons.append("intent_decision_hash_mismatch")
        key = str(row.get("idempotency_key") or "")
        if not key:
            reasons.append("missing_idempotency_key")
        elif key in seen_keys:
            reasons.append("duplicate_idempotency_key")
        seen_keys.add(key)
        ticker = str(row.get("ticker") or "").upper()
        if ticker:
            intent_tickers.add(ticker)
        if str(row.get("side") or "").lower() != "buy":
            reasons.append(f"unsupported_side:{ticker}")
        if str(row.get("order_type") or "").lower() != "market":
            reasons.append(f"unsupported_order_type:{ticker}")
        if str(row.get("market_hours") or "").lower() != "regular_hours":
            reasons.append(f"unsupported_market_hours:{ticker}")
        if float(row.get("amount_dollars") or 0.0) <= 0:
            reasons.append(f"invalid_order_amount:{ticker}")

    tradability_rows = (
        raw.get("tradability", {})
        .get("response", {})
        .get("structuredContent", {})
        .get("data", {})
        .get("results", [])
    )
    tradability = {
        str(row.get("symbol") or "").upper(): row for row in tradability_rows
    }
    tradable_count = 0
    for ticker in sorted(intent_tickers):
        row = tradability.get(ticker)
        if row is None:
            reasons.append(f"tradability_missing:{ticker}")
            continue
        ok = (
            bool(row.get("tradeable"))
            and str(row.get("state") or "").lower() == "active"
            and str(row.get("fractional_tradability") or "").lower() == "tradable"
            and any(
                str(item.get("account_type_tradability") or "").lower() == "tradable"
                for item in row.get("account_type_tradabilities", [])
            )
        )
        if ok:
            tradable_count += 1
        else:
            reasons.append(f"not_fractional_tradable:{ticker}")

    if tradable_count != len(intent_tickers):
        reasons.append("not_all_intents_tradable")

    return PreSubmitResult(
        ready=not reasons,
        decision_hash=decision_hash,
        order_count=len(intents),
        total_dollars=total_dollars,
        buying_power=buying_power,
        snapshot_created_at=created_raw,
        snapshot_age_minutes=snapshot_age_minutes,
        non_final_equity_orders=len(non_final),
        tradable_count=tradable_count,
        reasons=tuple(dict.fromkeys(reasons)),
    )
