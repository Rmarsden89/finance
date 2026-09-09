from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
import json

import pandas as pd

from finance.shadow.robinhood_state import (
    load_robinhood_shadow_export,
    normalize_robinhood_shadow_state,
)


@dataclass(frozen=True)
class ExecutionGateResult:
    ready: bool
    account_label: str
    decision_date: str
    decision_hash: str
    planned_investment: float
    buying_power: float
    broker_cash: float
    broker_account_value: float
    broker_positions: int
    non_final_equity_orders: int
    selected_buy_orders: int
    selected_tradable: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def evaluate_execution_gate(
    decision_payload: dict,
    broker_payload: dict,
    *,
    require_agentic_account: bool = True,
    max_weekly_contribution: float = 10.0,
    reconciliation_tolerance: float = 0.05,
) -> ExecutionGateResult:
    reasons: list[str] = []

    account_label = str(
        broker_payload.get("export_metadata", {}).get("account_label", "")
    )
    if require_agentic_account and "agentic" not in account_label.lower():
        reasons.append("broker_account_is_not_agentic")

    decision_date = str(decision_payload.get("decision_date") or "")
    decision_hash = str(decision_payload.get("decision_hash") or "")
    weekly_contribution = float(decision_payload.get("weekly_contribution", 0.0))
    planned_investment = float(decision_payload.get("planned_investment", 0.0))

    if weekly_contribution > max_weekly_contribution + 1e-9:
        reasons.append("weekly_contribution_exceeds_v1_cap")
    if planned_investment > max_weekly_contribution + 1e-9:
        reasons.append("planned_investment_exceeds_v1_cap")

    export_created = str(
        broker_payload.get("export_metadata", {}).get("created_at", "")
    )
    if export_created and decision_date:
        export_day = pd.to_datetime(export_created, errors="coerce", utc=True)
        if pd.notna(export_day) and export_day.date() != date.fromisoformat(decision_date):
            reasons.append("broker_snapshot_date_mismatch")

    positions, orders, audit = normalize_robinhood_shadow_state(broker_payload)

    if audit.buying_power + 1e-9 < planned_investment:
        reasons.append("insufficient_buying_power")
    if audit.non_final_equity_orders:
        reasons.append("non_final_equity_orders_present")
    if not positions.empty and positions["market_value"].isna().any():
        reasons.append("unvalued_broker_positions")

    broker_reconciled_value = audit.cash
    if not positions.empty and positions["market_value"].notna().all():
        broker_reconciled_value += float(positions["market_value"].sum())

    planned_pre_value = float(
        decision_payload.get("pre_contribution_portfolio_value", 0.0)
    )
    if abs(broker_reconciled_value - planned_pre_value) > reconciliation_tolerance:
        reasons.append("portfolio_value_reconciliation_mismatch")

    buy_decisions = [
        row
        for row in decision_payload.get("decisions", [])
        if str(row.get("status") or "").lower() == "buy"
        and float(row.get("allocation_dollars", 0.0)) > 0
    ]
    buy_tickers = {str(row.get("ticker") or "").upper() for row in buy_decisions}

    raw = broker_payload.get("raw_responses", {})
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

    selected_tradable = 0
    for ticker in sorted(buy_tickers):
        row = tradability.get(ticker)
        if row is None:
            reasons.append(f"tradability_missing:{ticker}")
            continue
        is_tradable = (
            bool(row.get("tradeable"))
            and str(row.get("state") or "").lower() == "active"
            and str(row.get("fractional_tradability") or "").lower() == "tradable"
            and any(
                str(item.get("account_type_tradability") or "").lower()
                == "tradable"
                for item in row.get("account_type_tradabilities", [])
            )
        )
        if is_tradable:
            selected_tradable += 1
        else:
            reasons.append(f"ticker_not_fractional_tradable:{ticker}")

    if selected_tradable != len(buy_tickers):
        reasons.append("not_all_selected_names_tradable")

    return ExecutionGateResult(
        ready=not reasons,
        account_label=account_label,
        decision_date=decision_date,
        decision_hash=decision_hash,
        planned_investment=planned_investment,
        buying_power=audit.buying_power,
        broker_cash=audit.cash,
        broker_account_value=audit.account_value,
        broker_positions=audit.positions,
        non_final_equity_orders=audit.non_final_equity_orders,
        selected_buy_orders=len(buy_tickers),
        selected_tradable=selected_tradable,
        reasons=tuple(reasons),
    )


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
