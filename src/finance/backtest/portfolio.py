from __future__ import annotations

import csv
import gzip
import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class BacktestConfig:
    weekly_contribution: float = 10.0
    top_n: int = 5
    selection_flag: str = "top_conviction_eligible"
    max_execution_delay_days: int = 7
    max_position_weight: float | None = None


@dataclass(frozen=True)
class PriceQuote:
    ticker: str
    date: date
    open: float
    close: float
    adjusted_close: float | None
    source: str | None

    @property
    def mark_price(self) -> float:
        return (
            self.adjusted_close
            if self.adjusted_close is not None and self.adjusted_close > 0
            else self.close
        )

    @property
    def execution_price(self) -> float:
        if (
            self.adjusted_close is not None
            and self.adjusted_close > 0
            and self.close > 0
        ):
            return self.open * self.adjusted_close / self.close
        return self.open


class BacktestPriceStore:
    """Daily canonical price lookup for execution and portfolio valuation."""

    def __init__(self, path: str | Path, *, ticker_column: str = "pit_ticker") -> None:
        self._rows: dict[str, list[PriceQuote]] = {}
        self._dates: dict[str, list[date]] = {}

        path = Path(path)
        opener = gzip.open if path.suffix.lower() == ".gz" else open
        with opener(path, "rt", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                ticker = (row.get(ticker_column) or row.get("ticker") or "").strip().upper()
                if not ticker:
                    continue
                try:
                    row_date = date.fromisoformat(str(row["date"])[:10])
                    row_open = float(row["open"])
                    row_close = float(row["close"])
                except (KeyError, TypeError, ValueError):
                    continue

                if row_open <= 0 or row_close <= 0:
                    continue

                adjusted_close = _optional_float(row.get("adjusted_close"))
                quote = PriceQuote(
                    ticker=ticker,
                    date=row_date,
                    open=row_open,
                    close=row_close,
                    adjusted_close=adjusted_close,
                    source=(row.get("source") or "").strip() or None,
                )
                self._rows.setdefault(ticker, []).append(quote)

        for ticker, rows in self._rows.items():
            deduped = {row.date: row for row in rows}
            ordered = [deduped[d] for d in sorted(deduped)]
            self._rows[ticker] = ordered
            self._dates[ticker] = [row.date for row in ordered]

    def next_after(
        self,
        ticker: str,
        after: date,
        *,
        max_delay_days: int = 7,
    ) -> PriceQuote | None:
        symbol = ticker.upper()
        dates = self._dates.get(symbol)
        if not dates:
            return None
        position = bisect_right(dates, after)
        if position >= len(dates):
            return None
        quote = self._rows[symbol][position]
        if (quote.date - after).days > max_delay_days:
            return None
        return quote

    def latest_as_of(self, ticker: str, as_of: date) -> PriceQuote | None:
        symbol = ticker.upper()
        dates = self._dates.get(symbol)
        if not dates:
            return None
        position = bisect_right(dates, as_of) - 1
        if position < 0:
            return None
        return self._rows[symbol][position]


@dataclass
class BacktestResult:
    summary: dict
    weekly: pd.DataFrame
    trades: pd.DataFrame


def run_ranked_accumulation_backtest(
    signals: pd.DataFrame,
    *,
    price_store: BacktestPriceStore,
    model_id: str,
    score_column: str,
    config: BacktestConfig = BacktestConfig(),
    start: date = date(2016, 1, 1),
    end: date | None = None,
) -> BacktestResult:
    """Accumulate top-ranked names weekly with no discretionary selling.

    Held names are force-liquidated only after they disappear from the current
    PIT investable-universe snapshot, because the canonical market dataset is
    membership-window filtered and cannot value them afterward.
    """

    frame = signals.copy()
    frame["decision_date"] = pd.to_datetime(
        frame["decision_date"], errors="coerce"
    ).dt.date
    frame[score_column] = pd.to_numeric(frame[score_column], errors="coerce")

    if config.selection_flag not in frame.columns:
        raise ValueError(
            f"Signal file missing selection flag: {config.selection_flag}"
        )

    frame = frame.loc[
        frame["decision_date"].notna()
        & (frame["decision_date"] >= start)
    ].copy()
    if end is not None:
        frame = frame.loc[frame["decision_date"] <= end].copy()

    decision_dates = sorted(frame["decision_date"].unique())

    cash = 0.0
    holdings: dict[str, float] = {}
    total_contributed = 0.0
    external_cashflows: list[tuple[date, float]] = []
    trade_rows: list[dict] = []
    weekly_rows: list[dict] = []
    previous_portfolio_value: float | None = None
    wealth_index = 1.0
    peak_wealth_index = 1.0
    max_drawdown = 0.0

    for decision_date in decision_dates:
        snapshot = frame.loc[frame["decision_date"] == decision_date]
        current_universe = set(
            snapshot["ticker"].dropna().astype(str).str.upper()
        )

        # Mandatory data-boundary exits are not strategy sells.
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
                "decision_date": decision_date,
                "execution_date": quote.date,
                "ticker": ticker,
                "side": "forced_exit",
                "rank": "",
                "dollars": proceeds,
                "units": units,
                "execution_price": quote.mark_price,
                "price_source": quote.source,
                "reason": "left_investable_universe",
            })

        selected = snapshot.loc[
            snapshot[config.selection_flag].fillna(False).astype(bool)
            & snapshot[score_column].notna()
        ].sort_values(score_column, ascending=False)

        candidates = []
        for rank, row in enumerate(
            selected.head(config.top_n).itertuples(index=False),
            start=1,
        ):
            ticker = str(getattr(row, "ticker")).upper()
            quote = price_store.next_after(
                ticker,
                decision_date,
                max_delay_days=config.max_execution_delay_days,
            )
            if quote is None:
                trade_rows.append({
                    "model_id": model_id,
                    "decision_date": decision_date,
                    "execution_date": "",
                    "ticker": ticker,
                    "side": "unfilled",
                    "rank": rank,
                    "dollars": 0.0,
                    "units": 0.0,
                    "execution_price": "",
                    "price_source": "",
                    "reason": "no_next_open_within_execution_window",
                })
                continue
            candidates.append((rank, ticker, quote))

        contribution = config.weekly_contribution
        cash += contribution
        total_contributed += contribution

        contribution_date = (
            min((quote.date for _, _, quote in candidates), default=decision_date)
        )
        external_cashflows.append((contribution_date, -contribution))

        allocations: dict[str, float] = {}
        if candidates:
            if config.max_position_weight is None:
                equal_allocation = contribution / len(candidates)
                allocations = {
                    ticker: equal_allocation
                    for _, ticker, _ in candidates
                }
            else:
                if not 0 < config.max_position_weight <= 1:
                    raise ValueError(
                        "max_position_weight must be in (0, 1] when set"
                    )

                pre_contribution_value = cash - contribution
                current_position_values: dict[str, float] = {}

                for held_ticker, units in holdings.items():
                    mark = price_store.latest_as_of(
                        held_ticker,
                        decision_date,
                    )
                    if mark is None:
                        continue
                    value = units * mark.mark_price
                    pre_contribution_value += value
                    current_position_values[held_ticker] = value

                projected_total = pre_contribution_value + contribution
                max_position_value = (
                    projected_total * config.max_position_weight
                )

                capacities = {
                    ticker: max(
                        0.0,
                        max_position_value
                        - current_position_values.get(ticker, 0.0),
                    )
                    for _, ticker, _ in candidates
                }

                remaining = contribution
                active = {
                    ticker
                    for _, ticker, _ in candidates
                    if capacities[ticker] > 1e-12
                }
                allocations = {ticker: 0.0 for _, ticker, _ in candidates}

                while remaining > 1e-12 and active:
                    equal_share = remaining / len(active)
                    spent = 0.0
                    next_active = set()

                    for ticker in active:
                        room = capacities[ticker] - allocations[ticker]
                        amount = min(equal_share, max(0.0, room))
                        allocations[ticker] += amount
                        spent += amount

                        if room - amount > 1e-12:
                            next_active.add(ticker)

                    if spent <= 1e-12:
                        break
                    remaining -= spent
                    active = next_active

            for rank, ticker, quote in candidates:
                allocation = allocations.get(ticker, 0.0)
                if allocation <= 1e-12:
                    trade_rows.append({
                        "model_id": model_id,
                        "decision_date": decision_date,
                        "execution_date": quote.date,
                        "ticker": ticker,
                        "side": "skipped",
                        "rank": rank,
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
                trade_rows.append({
                    "model_id": model_id,
                    "decision_date": decision_date,
                    "execution_date": quote.date,
                    "ticker": ticker,
                    "side": "buy",
                    "rank": rank,
                    "dollars": allocation,
                    "units": units,
                    "execution_price": execution_price,
                    "price_source": quote.source,
                    "reason": "",
                })

        valuation_date = max(
            (quote.date for _, _, quote in candidates),
            default=decision_date,
        )
        holdings_value = 0.0
        unpriced_holdings = 0
        for ticker, units in holdings.items():
            quote = price_store.latest_as_of(ticker, valuation_date)
            if quote is None:
                unpriced_holdings += 1
                continue
            holdings_value += units * quote.mark_price

        portfolio_value = cash + holdings_value

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
            "decision_date": decision_date,
            "valuation_date": valuation_date,
            "weekly_contribution": contribution,
            "total_contributed": total_contributed,
            "cash": cash,
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
            "period_return": period_return,
            "wealth_index": wealth_index,
            "drawdown": (
                1.0 - wealth_index / peak_wealth_index
                if peak_wealth_index > 0
                else float("nan")
            ),
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

    trades = pd.DataFrame(trade_rows)
    weekly = pd.DataFrame(weekly_rows)
    buys = trades.loc[trades["side"] == "buy"] if not trades.empty else trades
    forced = (
        trades.loc[trades["side"] == "forced_exit"]
        if not trades.empty
        else trades
    )
    unfilled = (
        trades.loc[trades["side"] == "unfilled"]
        if not trades.empty
        else trades
    )

    annualized_time_weighted_return = float("nan")
    if weekly_rows and len(weekly_rows) > 1:
        first_date = weekly_rows[0]["valuation_date"]
        last_date = weekly_rows[-1]["valuation_date"]
        elapsed_years = (last_date - first_date).days / 365.25
        if elapsed_years > 0 and wealth_index > 0:
            annualized_time_weighted_return = (
                wealth_index ** (1.0 / elapsed_years) - 1.0
            )

    summary = {
        "model_id": model_id,
        "start_date": decision_dates[0] if decision_dates else None,
        "end_date": decision_dates[-1] if decision_dates else None,
        "terminal_date": terminal_date,
        "weekly_contribution": config.weekly_contribution,
        "top_n": config.top_n,
        "selection_flag": config.selection_flag,
        "max_position_weight": config.max_position_weight,
        "decision_weeks": len(decision_dates),
        "total_contributed": total_contributed,
        "terminal_value": terminal_value,
        "gain_dollars": terminal_value - total_contributed,
        "gain_on_contributions_pct": (
            terminal_value / total_contributed - 1.0
            if total_contributed > 0
            else float("nan")
        ),
        "xirr": xirr,
        "time_weighted_return": wealth_index - 1.0,
        "annualized_time_weighted_return": annualized_time_weighted_return,
        "max_drawdown": max_drawdown,
        "buy_count": len(buys),
        "forced_exit_count": len(forced),
        "unfilled_order_count": len(unfilled),
        "ending_holding_count": len(holdings),
    }
    return BacktestResult(summary=summary, weekly=weekly, trades=trades)


def run_single_asset_accumulation_backtest(
    *,
    price_store: BacktestPriceStore,
    ticker: str,
    decision_dates: list[date],
    weekly_contribution: float = 10.0,
    max_execution_delay_days: int = 7,
    model_id: str | None = None,
) -> BacktestResult:
    """Invest the same weekly contribution into one benchmark security."""

    synthetic = pd.DataFrame({
        "decision_date": decision_dates,
        "ticker": ticker,
        "benchmark_score": 100.0,
        "top_conviction_eligible": True,
    })
    return run_ranked_accumulation_backtest(
        synthetic,
        price_store=price_store,
        model_id=model_id or ticker.upper(),
        score_column="benchmark_score",
        config=BacktestConfig(
            weekly_contribution=weekly_contribution,
            top_n=1,
            selection_flag="top_conviction_eligible",
            max_execution_delay_days=max_execution_delay_days,
        ),
        start=min(decision_dates) if decision_dates else date.min,
        end=max(decision_dates) if decision_dates else None,
    )


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _xirr(cashflows: list[tuple[date, float]]) -> float:
    if not cashflows:
        return float("nan")
    if not any(value < 0 for _, value in cashflows):
        return float("nan")
    if not any(value > 0 for _, value in cashflows):
        return float("nan")

    base_date = min(day for day, _ in cashflows)

    def npv(rate: float) -> float:
        total = 0.0
        for day, value in cashflows:
            years = (day - base_date).days / 365.25
            total += value / ((1.0 + rate) ** years)
        return total

    low = -0.9999
    high = 1.0
    low_value = npv(low)
    high_value = npv(high)

    while low_value * high_value > 0 and high < 1_000:
        high *= 2.0
        high_value = npv(high)

    if low_value * high_value > 0:
        return float("nan")

    for _ in range(200):
        mid = (low + high) / 2.0
        value = npv(mid)
        if abs(value) < 1e-10:
            return mid
        if low_value * value <= 0:
            high = mid
            high_value = value
        else:
            low = mid
            low_value = value

    return (low + high) / 2.0
