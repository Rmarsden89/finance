from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class PortfolioPosition:
    ticker: str
    market_value: float


@dataclass(frozen=True)
class ShadowDecision:
    rank: int
    ticker: str
    score: float
    current_market_value: float
    pre_contribution_weight: float
    status: str
    allocation_dollars: float
    reason: str


@dataclass(frozen=True)
class ShadowDecisionPlan:
    decision_date: date
    as_of: date
    model_id: str
    top_n: int
    weekly_contribution: float
    max_addon_position_weight: float
    starting_cash: float
    pre_contribution_portfolio_value: float
    selected_count: int
    buyable_count: int
    blocked_count: int
    planned_investment: float
    unallocated_contribution: float
    decision_hash: str
    decisions: tuple[ShadowDecision, ...]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["decision_date"] = self.decision_date.isoformat()
        payload["as_of"] = self.as_of.isoformat()
        payload["decisions"] = [asdict(row) for row in self.decisions]
        return payload


def load_portfolio_positions(path: str | Path | None) -> list[PortfolioPosition]:
    if path is None:
        return []

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Portfolio state not found: {path}")

    positions: list[PortfolioPosition] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        if "ticker" not in fields:
            raise ValueError("Portfolio state requires a 'ticker' column")
        if "market_value" not in fields:
            raise ValueError(
                "Portfolio state requires a 'market_value' column. "
                "Use current marked market value before the weekly contribution."
            )

        for row in reader:
            ticker = (row.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            try:
                market_value = float(row.get("market_value") or 0.0)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid market_value for {ticker}: {row.get('market_value')!r}"
                ) from exc
            if market_value < 0:
                raise ValueError(f"market_value cannot be negative for {ticker}")
            positions.append(
                PortfolioPosition(ticker=ticker, market_value=market_value)
            )

    return positions


def build_shadow_decision_plan(
    signals: pd.DataFrame,
    *,
    as_of: date,
    positions: list[PortfolioPosition] | None = None,
    starting_cash: float = 0.0,
    weekly_contribution: float = 10.0,
    top_n: int = 10,
    max_addon_position_weight: float = 0.10,
    model_id: str = "long_growth_v1",
    score_column: str = "long_growth_v1_score",
    selection_flag: str = "top_conviction_eligible",
    decision_date: date | None = None,
    max_signal_age_days: int = 7,
) -> ShadowDecisionPlan:
    if weekly_contribution < 0:
        raise ValueError("weekly_contribution cannot be negative")
    if starting_cash < 0:
        raise ValueError("starting_cash cannot be negative")
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if not 0 < max_addon_position_weight <= 1:
        raise ValueError("max_addon_position_weight must be in (0, 1]")
    if max_signal_age_days < 0:
        raise ValueError("max_signal_age_days cannot be negative")

    required = {"decision_date", "ticker", score_column, selection_flag}
    missing = sorted(required - set(signals.columns))
    if missing:
        raise ValueError(
            "Signal file missing required columns: " + ", ".join(missing)
        )

    frame = signals.copy()
    frame["decision_date"] = pd.to_datetime(
        frame["decision_date"], errors="coerce"
    ).dt.date
    frame[score_column] = pd.to_numeric(frame[score_column], errors="coerce")
    frame["ticker"] = frame["ticker"].astype(str).str.upper()

    available_dates = sorted(
        value
        for value in frame["decision_date"].dropna().unique()
        if value <= as_of
    )
    if not available_dates:
        raise ValueError(f"No signal decision date available on or before {as_of}")

    selected_date = decision_date or available_dates[-1]
    if selected_date > as_of:
        raise ValueError("decision_date cannot be after as_of")
    if selected_date not in available_dates:
        raise ValueError(
            f"Requested decision_date {selected_date} is not present in signals"
        )

    signal_age_days = (as_of - selected_date).days
    if signal_age_days > max_signal_age_days:
        raise ValueError(
            f"Latest signal is stale: {selected_date} is {signal_age_days} days "
            f"behind as_of {as_of}; maximum allowed is {max_signal_age_days}"
        )

    snapshot = frame.loc[frame["decision_date"] == selected_date].copy()
    eligible = snapshot.loc[
        snapshot[selection_flag].fillna(False).astype(bool)
        & snapshot[score_column].notna()
    ].copy()

    # Explicit ticker tiebreak makes the decision set deterministic.
    eligible = eligible.sort_values(
        [score_column, "ticker"],
        ascending=[False, True],
        kind="mergesort",
    ).head(top_n)

    if len(eligible) < top_n:
        raise ValueError(
            f"Only {len(eligible)} eligible scored names are available on "
            f"{selected_date}; expected at least {top_n}"
        )

    position_values: dict[str, float] = {}
    for position in positions or []:
        ticker = position.ticker.upper()
        position_values[ticker] = (
            position_values.get(ticker, 0.0) + position.market_value
        )

    pre_contribution_value = starting_cash + sum(position_values.values())

    selected_rows: list[tuple[int, str, float, float, float, bool]] = []
    for rank, row in enumerate(eligible.itertuples(index=False), start=1):
        ticker = str(getattr(row, "ticker")).upper()
        score = float(getattr(row, score_column))
        current_value = position_values.get(ticker, 0.0)
        current_weight = (
            current_value / pre_contribution_value
            if pre_contribution_value > 0
            else 0.0
        )
        blocked = current_weight >= max_addon_position_weight
        selected_rows.append(
            (rank, ticker, score, current_value, current_weight, blocked)
        )

    buyable_count = sum(not row[5] for row in selected_rows)
    allocation = (
        weekly_contribution / buyable_count
        if buyable_count > 0
        else 0.0
    )

    decisions: list[ShadowDecision] = []
    for rank, ticker, score, current_value, current_weight, blocked in selected_rows:
        if blocked:
            decisions.append(
                ShadowDecision(
                    rank=rank,
                    ticker=ticker,
                    score=score,
                    current_market_value=current_value,
                    pre_contribution_weight=current_weight,
                    status="blocked",
                    allocation_dollars=0.0,
                    reason="position_cap",
                )
            )
        else:
            decisions.append(
                ShadowDecision(
                    rank=rank,
                    ticker=ticker,
                    score=score,
                    current_market_value=current_value,
                    pre_contribution_weight=current_weight,
                    status="buy",
                    allocation_dollars=allocation,
                    reason="",
                )
            )

    planned_investment = sum(row.allocation_dollars for row in decisions)
    unallocated = weekly_contribution - planned_investment

    hash_payload = {
        "decision_date": selected_date.isoformat(),
        "as_of": as_of.isoformat(),
        "model_id": model_id,
        "top_n": top_n,
        "weekly_contribution": round(weekly_contribution, 10),
        "max_addon_position_weight": round(max_addon_position_weight, 10),
        "starting_cash": round(starting_cash, 10),
        "pre_contribution_portfolio_value": round(pre_contribution_value, 10),
        "decisions": [
            {
                "rank": row.rank,
                "ticker": row.ticker,
                "score": round(row.score, 10),
                "current_market_value": round(row.current_market_value, 10),
                "pre_contribution_weight": round(row.pre_contribution_weight, 10),
                "status": row.status,
                "allocation_dollars": round(row.allocation_dollars, 10),
                "reason": row.reason,
            }
            for row in decisions
        ],
    }
    encoded = json.dumps(
        hash_payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    decision_hash = hashlib.sha256(encoded).hexdigest()

    return ShadowDecisionPlan(
        decision_date=selected_date,
        as_of=as_of,
        model_id=model_id,
        top_n=top_n,
        weekly_contribution=weekly_contribution,
        max_addon_position_weight=max_addon_position_weight,
        starting_cash=starting_cash,
        pre_contribution_portfolio_value=pre_contribution_value,
        selected_count=len(decisions),
        buyable_count=buyable_count,
        blocked_count=len(decisions) - buyable_count,
        planned_investment=planned_investment,
        unallocated_contribution=unallocated,
        decision_hash=decision_hash,
        decisions=tuple(decisions),
    )
