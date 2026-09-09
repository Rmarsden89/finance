from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class OrderIntent:
    rank: int
    ticker: str
    side: str
    order_type: str
    market_hours: str
    time_in_force: str
    amount_dollars: float
    decision_hash: str
    idempotency_key: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OrderIntentBatch:
    decision_hash: str
    total_dollars: float
    order_count: int
    intents: tuple[OrderIntent, ...]

    def to_dict(self) -> dict:
        return {
            "decision_hash": self.decision_hash,
            "total_dollars": self.total_dollars,
            "order_count": self.order_count,
            "intents": [row.to_dict() for row in self.intents],
        }


def build_order_intents(
    decision_payload: dict,
    execution_gate_payload: dict,
) -> OrderIntentBatch:
    if not execution_gate_payload.get("ready"):
        raise ValueError("Execution gate is not READY")
    if execution_gate_payload.get("decision_hash") != decision_payload.get("decision_hash"):
        raise ValueError("Execution gate decision_hash does not match decision artifact")

    decision_hash = str(decision_payload["decision_hash"])
    intents: list[OrderIntent] = []
    total = 0.0

    for row in decision_payload.get("decisions", []):
        if str(row.get("status") or "").lower() != "buy":
            continue
        amount = float(row.get("allocation_dollars", 0.0))
        if amount <= 0:
            continue
        ticker = str(row.get("ticker") or "").upper()
        rank = int(row.get("rank"))
        key_payload = {
            "decision_hash": decision_hash,
            "ticker": ticker,
            "rank": rank,
            "amount_dollars": round(amount, 10),
            "side": "buy",
            "order_type": "market",
            "market_hours": "regular_hours",
            "time_in_force": "gfd",
        }
        encoded = json.dumps(
            key_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        idempotency_key = hashlib.sha256(encoded).hexdigest()

        intents.append(
            OrderIntent(
                rank=rank,
                ticker=ticker,
                side="buy",
                order_type="market",
                market_hours="regular_hours",
                time_in_force="gfd",
                amount_dollars=amount,
                decision_hash=decision_hash,
                idempotency_key=idempotency_key,
            )
        )
        total += amount

    planned = float(decision_payload.get("planned_investment", 0.0))
    if abs(total - planned) > 1e-9:
        raise ValueError(
            f"Order intent dollars {total:.10f} do not match planned investment "
            f"{planned:.10f}"
        )

    return OrderIntentBatch(
        decision_hash=decision_hash,
        total_dollars=total,
        order_count=len(intents),
        intents=tuple(intents),
    )
