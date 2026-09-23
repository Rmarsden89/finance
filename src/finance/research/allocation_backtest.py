from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from finance.backtest import BacktestPriceStore, BacktestResult
from finance.backtest.portfolio import _xirr
from finance.research.allocation_challengers import allocation_weights


@dataclass(frozen=True)
class AllocationBacktestConfig:
    weekly_contribution: float = 10.0
    top_n: int = 10
    selection_flag: str = "top_conviction_eligible"
    max_execution_delay_days: int = 7
    max_addon_position_weight: float = 0.10
    rule_id: str = "equal_dollar"


def run_allocation_backtest(
    signals: pd.DataFrame,
    *,
    price_store: BacktestPriceStore,
    model_id: str,
    score_column: str,
    config: AllocationBacktestConfig,
    start: date,
    end: date | None = None,
) -> BacktestResult:
    """Research-only ranked accumulation with a frozen contribution rule."""

    frame = signals.copy()
    frame["decision_date"] = pd.to_datetime(
        frame["decision_date"], errors="coerce"
    ).dt.date
    frame[score_column] = pd.to_numeric(frame[score_column], errors="coerce")

    if config.selection_flag not in frame.columns:
        raise ValueError(
            f"Signal file missing selection flag: {config.selection_flag}"
        )
    if not 0 < config.max_addon_position_weight <= 1:
        raise ValueError("max_addon_position_weight must be in (0, 1]")

    frame = frame.loc[
        frame["decision_date"].notna()
        & frame["decision_date"].ge(start)
    ].copy()
    if end is not None:
        frame = frame.loc[frame["decision_date"].le(end)].copy()

    decision_dates = sorted(frame["decision_date"].unique())
    cash = 0.0
    holdings: dict[str, float] = {}
    total_contributed = 0.0
    external_cashflows: list[tuple[date, float]] = []
    trade_rows: list[dict[str, object]] = []
    weekly_rows: list[dict[str, object]] = []
    previous_portfolio_value: float | None = None
    wealth_index = 1.0
    peak_wealth_index = 1.0
    max_drawdown = 0.0
    prior_contribution_weights: dict[str, float] | None = None
    cap_blocked_tickers: set[str] = set()
    position_cap_reentry_count = 0

    for decision_date in decision_dates:
        snapshot = frame.loc[frame["decision_date"].eq(decision_date)]
        current_universe = set(
            snapshot["ticker"].dropna().astype(str).str.upper()
        )

        for ticker in list(holdings):
            if ticker in current_universe:
                continue
            quote = price_store.latest_as_of(ticker, decision_date)
            if quote is None:
                continue
            units = holdings.pop(ticker)
            proceeds = units * quote.mark_price
            cash += proceeds
            trade_rows.append({
                "model_id": model_id,
                "allocation_rule": config.rule_id,
                "decision_date": decision_date,
                "execution_date": quote.date,
                "ticker": ticker,
                "side": "forced_exit",
                "rank": "",
                "score": "",
                "target_weight": "",
                "dollars": proceeds,
                "units": units,
                "execution_price": quote.mark_price,
                "price_source": quote.source,
                "reason": "left_investable_universe",
            })

        selected = snapshot.loc[
            snapshot[config.selection_flag].fillna(False).astype(bool)
            & snapshot[score_column].notna()
        ].sort_values(
            [score_column, "ticker"],
            ascending=[False, True],
            kind="stable",
        )

        candidates: list[tuple[int, str, float, object]] = []
        for rank, row in enumerate(
            selected.head(config.top_n).itertuples(index=False),
            start=1,
        ):
            ticker = str(getattr(row, "ticker")).upper()
            score = float(getattr(row, score_column))
            quote = price_store.next_after(
                ticker,
                decision_date,
                max_delay_days=config.max_execution_delay_days,
            )
            if quote is None:
                trade_rows.append({
                    "model_id": model_id,
                    "allocation_rule": config.rule_id,
                    "decision_date": decision_date,
                    "execution_date": "",
                    "ticker": ticker,
                    "side": "unfilled",
                    "rank": rank,
                    "score": score,
                    "target_weight": 0.0,
                    "dollars": 0.0,
                    "units": 0.0,
                    "execution_price": "",
                    "price_source": "",
                    "reason": "no_next_open_within_execution_window",
                })
                continue
            candidates.append((rank, ticker, score, quote))

        contribution = config.weekly_contribution
        cash += contribution
        total_contributed += contribution
        contribution_date = min(
            (quote.date for _, _, _, quote in candidates),
            default=decision_date,
        )
        external_cashflows.append((contribution_date, -contribution))

        pre_contribution_value = cash - contribution
        current_position_values: dict[str, float] = {}
        for held_ticker, units in holdings.items():
            mark = price_store.latest_as_of(held_ticker, decision_date)
            if mark is None:
                continue
            value = units * mark.mark_price
            pre_contribution_value += value
            current_position_values[held_ticker] = value

        eligible: list[tuple[int, str, float]] = []
        blocked: set[str] = set()
        for rank, ticker, score, _ in candidates:
            current_value = current_position_values.get(ticker, 0.0)
            current_weight = (
                current_value / pre_contribution_value
                if pre_contribution_value > 0
                else 0.0
            )
            if current_weight >= config.max_addon_position_weight:
                blocked.add(ticker)
            else:
                eligible.append((rank, ticker, score))

        weights = allocation_weights(config.rule_id, eligible)
        allocations = {
            ticker: contribution * weights.get(ticker, 0.0)
            for _, ticker, _, _ in candidates
        }

        for rank, ticker, score, quote in candidates:
            allocation = allocations.get(ticker, 0.0)
            target_weight = weights.get(ticker, 0.0)
            if allocation <= 1e-12:
                cap_blocked_tickers.add(ticker)
                trade_rows.append({
                    "model_id": model_id,
                    "allocation_rule": config.rule_id,
                    "decision_date": decision_date,
                    "execution_date": quote.date,
                    "ticker": ticker,
                    "side": "skipped",
                    "rank": rank,
                    "score": score,
                    "target_weight": target_weight,
                    "dollars": 0.0,
                    "units": 0.0,
                    "execution_price": quote.execution_price,
                    "price_source": quote.source,
                    "reason": "position_cap",
                })
                continue

            execution_price = quote.execution_price
            if not math.isfinite(execution_price) or execution_price <= 0:
                continue
            units = allocation / execution_price
            holdings[ticker] = holdings.get(ticker, 0.0) + units
            cash -= allocation

            reason = ""
            if ticker in cap_blocked_tickers:
                reason = "position_cap_reentry"
                cap_blocked_tickers.discard(ticker)
                position_cap_reentry_count += 1

            trade_rows.append({
                "model_id": model_id,
                "allocation_rule": config.rule_id,
                "decision_date": decision_date,
                "execution_date": quote.date,
                "ticker": ticker,
                "side": "buy",
                "rank": rank,
                "score": score,
                "target_weight": target_weight,
                "dollars": allocation,
                "units": units,
                "execution_price": execution_price,
                "price_source": quote.source,
                "reason": reason,
            })

        valuation_date = max(
            (quote.date for _, _, _, quote in candidates),
            default=decision_date,
        )
        holding_values: dict[str, float] = {}
        unpriced_holdings = 0
        for ticker, units in holdings.items():
            quote = price_store.latest_as_of(ticker, valuation_date)
            if quote is None:
                unpriced_holdings += 1
                continue
            holding_values[ticker] = units * quote.mark_price

        holdings_value = sum(holding_values.values())
        portfolio_value = cash + holdings_value
        cash_pct = (
            cash / portfolio_value if portfolio_value > 0 else float("nan")
        )

        position_weights = (
            {
                ticker: value / portfolio_value
                for ticker, value in holding_values.items()
                if portfolio_value > 0
            }
            if portfolio_value > 0
            else {}
        )
        portfolio_hhi = sum(value * value for value in position_weights.values())
        largest_position_weight = (
            max(position_weights.values()) if position_weights else 0.0
        )

        contribution_hhi = sum(value * value for value in weights.values())
        largest_contribution_weight = max(weights.values()) if weights else 0.0
        effective_contribution_names = (
            1.0 / contribution_hhi if contribution_hhi > 0 else 0.0
        )

        allocation_turnover = float("nan")
        if prior_contribution_weights is not None:
            tickers = set(prior_contribution_weights) | set(weights)
            allocation_turnover = 0.5 * sum(
                abs(
                    weights.get(ticker, 0.0)
                    - prior_contribution_weights.get(ticker, 0.0)
                )
                for ticker in tickers
            )
        prior_contribution_weights = dict(weights)

        period_return = float("nan")
        if previous_portfolio_value is not None and previous_portfolio_value > 0:
            period_return = (
                (portfolio_value - contribution)
                / previous_portfolio_value
                - 1.0
            )
            if math.isfinite(period_return):
                wealth_index *= 1.0 + period_return
                peak_wealth_index = max(peak_wealth_index, wealth_index)
                if peak_wealth_index > 0:
                    max_drawdown = max(
                        max_drawdown,
                        1.0 - wealth_index / peak_wealth_index,
                    )

        weekly_rows.append({
            "model_id": model_id,
            "allocation_rule": config.rule_id,
            "decision_date": decision_date,
            "valuation_date": valuation_date,
            "weekly_contribution": contribution,
            "total_contributed": total_contributed,
            "cash": cash,
            "cash_pct": cash_pct,
            "holdings_value": holdings_value,
            "portfolio_value": portfolio_value,
            "gain_dollars": portfolio_value - total_contributed,
            "gain_on_contributions_pct": (
                portfolio_value / total_contributed - 1.0
                if total_contributed > 0
                else float("nan")
            ),
            "holding_count": len(holdings),
            "unpriced_holdings": unpriced_holdings,
            "selected_count": len(candidates),
            "eligible_after_cap_count": len(eligible),
            "blocked_candidate_count": len(blocked),
            "period_return": period_return,
            "wealth_index": wealth_index,
            "drawdown": (
                1.0 - wealth_index / peak_wealth_index
                if peak_wealth_index > 0
                else float("nan")
            ),
            "portfolio_hhi": portfolio_hhi,
            "largest_position_weight": largest_position_weight,
            "contribution_hhi": contribution_hhi,
            "largest_contribution_weight": largest_contribution_weight,
            "effective_contribution_names": effective_contribution_names,
            "allocation_turnover": allocation_turnover,
        })
        previous_portfolio_value = portfolio_value

    if weekly_rows:
        terminal_date = weekly_rows[-1]["valuation_date"]
        terminal_value = weekly_rows[-1]["portfolio_value"]
        external_cashflows.append((terminal_date, terminal_value))
        xirr = _xirr(external_cashflows)
    else:
        terminal_date = None
        terminal_value = 0.0
        xirr = float("nan")

    weekly = pd.DataFrame(weekly_rows)
    trades = pd.DataFrame(trade_rows)
    buys = trades.loc[trades["side"].eq("buy")] if not trades.empty else trades
    forced = (
        trades.loc[trades["side"].eq("forced_exit")]
        if not trades.empty else trades
    )
    unfilled = (
        trades.loc[trades["side"].eq("unfilled")]
        if not trades.empty else trades
    )
    skipped = (
        trades.loc[trades["side"].eq("skipped")]
        if not trades.empty else trades
    )

    annualized_twr = float("nan")
    if len(weekly) > 1:
        first_date = weekly.iloc[0]["valuation_date"]
        last_date = weekly.iloc[-1]["valuation_date"]
        elapsed_years = (last_date - first_date).days / 365.25
        if elapsed_years > 0 and wealth_index > 0:
            annualized_twr = wealth_index ** (1.0 / elapsed_years) - 1.0

    summary = {
        "model_id": model_id,
        "allocation_rule": config.rule_id,
        "start_date": decision_dates[0] if decision_dates else None,
        "end_date": decision_dates[-1] if decision_dates else None,
        "terminal_date": terminal_date,
        "weekly_contribution": config.weekly_contribution,
        "top_n": config.top_n,
        "selection_flag": config.selection_flag,
        "max_addon_position_weight": config.max_addon_position_weight,
        "decision_weeks": len(decision_dates),
        "total_contributed": total_contributed,
        "terminal_value": terminal_value,
        "gain_dollars": terminal_value - total_contributed,
        "gain_on_contributions_pct": (
            terminal_value / total_contributed - 1.0
            if total_contributed > 0 else float("nan")
        ),
        "xirr": xirr,
        "time_weighted_return": wealth_index - 1.0,
        "annualized_time_weighted_return": annualized_twr,
        "max_drawdown": max_drawdown,
        "buy_count": len(buys),
        "forced_exit_count": len(forced),
        "unfilled_order_count": len(unfilled),
        "position_cap_skip_count": len(skipped),
        "position_cap_reentry_count": position_cap_reentry_count,
        "ending_cap_blocked_ticker_count": len(cap_blocked_tickers),
        "ending_holding_count": len(holdings),
        "ending_cash": cash,
        "ending_cash_pct": (
            cash / terminal_value if terminal_value > 0 else float("nan")
        ),
        "mean_cash_pct": (
            float(pd.to_numeric(weekly["cash_pct"], errors="coerce").mean())
            if not weekly.empty else float("nan")
        ),
        "mean_portfolio_hhi": (
            float(weekly["portfolio_hhi"].mean())
            if not weekly.empty else float("nan")
        ),
        "max_portfolio_hhi": (
            float(weekly["portfolio_hhi"].max())
            if not weekly.empty else float("nan")
        ),
        "mean_largest_position_weight": (
            float(weekly["largest_position_weight"].mean())
            if not weekly.empty else float("nan")
        ),
        "max_largest_position_weight": (
            float(weekly["largest_position_weight"].max())
            if not weekly.empty else float("nan")
        ),
        "mean_contribution_hhi": (
            float(weekly["contribution_hhi"].mean())
            if not weekly.empty else float("nan")
        ),
        "mean_largest_contribution_weight": (
            float(weekly["largest_contribution_weight"].mean())
            if not weekly.empty else float("nan")
        ),
        "mean_effective_contribution_names": (
            float(weekly["effective_contribution_names"].mean())
            if not weekly.empty else float("nan")
        ),
        "mean_allocation_turnover": (
            float(
                pd.to_numeric(
                    weekly["allocation_turnover"], errors="coerce"
                ).mean()
            )
            if not weekly.empty else float("nan")
        ),
    }
    return BacktestResult(summary=summary, weekly=weekly, trades=trades)
