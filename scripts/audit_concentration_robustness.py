from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

from finance.backtest import (
    BacktestConfig,
    BacktestPriceStore,
    run_ranked_accumulation_backtest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit concentration and winner-dependence for the Top-10 "
            "long_growth_v1 portfolio candidate."
        )
    )
    parser.add_argument("--long-growth", type=Path, required=True)
    parser.add_argument(
        "--prices",
        type=Path,
        default=Path("data/market/daily_prices.csv.gz"),
    )
    parser.add_argument("--weekly-contribution", type=float, default=10.0)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--start-year", type=int, default=2016)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--leave-out-count", type=int, default=5)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/concentration_robustness_v1"),
    )
    return parser.parse_args()


def run_strategy(
    frame: pd.DataFrame,
    *,
    store: BacktestPriceStore,
    weekly_contribution: float,
    top_n: int,
    start: date,
    end: date,
    excluded_tickers: set[str] | None = None,
    model_id: str = "long_growth_v1_top10",
):
    signals = frame.copy()
    if excluded_tickers:
        signals = signals.loc[
            ~signals["ticker"].astype(str).str.upper().isin(excluded_tickers)
        ].copy()

    return run_ranked_accumulation_backtest(
        signals,
        price_store=store,
        model_id=model_id,
        score_column="long_growth_v1_score",
        config=BacktestConfig(
            weekly_contribution=weekly_contribution,
            top_n=top_n,
            selection_flag="top_conviction_eligible",
        ),
        start=start,
        end=end,
    )


def reconstruct_holding_path(
    trades: pd.DataFrame,
    *,
    store: BacktestPriceStore,
    weekly: pd.DataFrame,
) -> pd.DataFrame:
    holdings = defaultdict(float)
    trade_groups = {
        day: group
        for day, group in trades.groupby("decision_date", sort=False)
    }

    rows = []

    for weekly_row in weekly.sort_values("decision_date").itertuples(index=False):
        decision_day = pd.Timestamp(weekly_row.decision_date).date()
        valuation_day = pd.Timestamp(weekly_row.valuation_date).date()

        group = trade_groups.get(decision_day)
        if group is not None:
            for trade in group.itertuples(index=False):
                ticker = str(trade.ticker).upper()
                side = str(trade.side)

                if side == "buy":
                    holdings[ticker] += float(trade.units)
                elif side == "forced_exit":
                    holdings.pop(ticker, None)

        values = []
        total_value = float(weekly_row.cash)

        for ticker, units in holdings.items():
            quote = store.latest_as_of(ticker, valuation_day)
            if quote is None:
                continue
            value = units * quote.mark_price
            if value <= 0:
                continue
            values.append((ticker, value))
            total_value += value

        values.sort(key=lambda item: item[1], reverse=True)

        top1 = values[0][1] if len(values) >= 1 else 0.0
        top3 = sum(value for _, value in values[:3])
        top5 = sum(value for _, value in values[:5])

        rows.append({
            "decision_date": decision_day,
            "valuation_date": valuation_day,
            "portfolio_value": total_value,
            "holding_count": len(values),
            "largest_position_weight": (
                top1 / total_value if total_value > 0 else float("nan")
            ),
            "top3_position_weight": (
                top3 / total_value if total_value > 0 else float("nan")
            ),
            "top5_position_weight": (
                top5 / total_value if total_value > 0 else float("nan")
            ),
            "largest_position_ticker": (
                values[0][0] if values else ""
            ),
        })

    return pd.DataFrame(rows)


def ticker_attribution(
    trades: pd.DataFrame,
    *,
    store: BacktestPriceStore,
    terminal_date: date,
) -> pd.DataFrame:
    buys = trades.loc[trades["side"] == "buy"].copy()
    if buys.empty:
        return pd.DataFrame()

    buy_dollars = (
        buys.groupby("ticker", as_index=False)["dollars"]
        .sum()
        .rename(columns={"dollars": "total_buy_dollars"})
    )
    buy_units = (
        buys.groupby("ticker", as_index=False)["units"]
        .sum()
        .rename(columns={"units": "total_buy_units"})
    )

    forced = trades.loc[trades["side"] == "forced_exit"].copy()
    forced_proceeds = (
        forced.groupby("ticker", as_index=False)["dollars"]
        .sum()
        .rename(columns={"dollars": "forced_exit_proceeds"})
        if not forced.empty
        else pd.DataFrame(columns=["ticker", "forced_exit_proceeds"])
    )
    forced_units = (
        forced.groupby("ticker", as_index=False)["units"]
        .sum()
        .rename(columns={"units": "forced_exit_units"})
        if not forced.empty
        else pd.DataFrame(columns=["ticker", "forced_exit_units"])
    )

    result = buy_dollars.merge(buy_units, on="ticker", how="outer")
    result = result.merge(forced_proceeds, on="ticker", how="left")
    result = result.merge(forced_units, on="ticker", how="left")
    result["forced_exit_proceeds"] = result[
        "forced_exit_proceeds"
    ].fillna(0.0)
    result["forced_exit_units"] = result["forced_exit_units"].fillna(0.0)

    terminal_values = []
    for row in result.itertuples(index=False):
        ticker = str(row.ticker).upper()
        remaining_units = max(
            0.0,
            float(row.total_buy_units) - float(row.forced_exit_units),
        )
        quote = store.latest_as_of(ticker, terminal_date)
        terminal_value = (
            remaining_units * quote.mark_price
            if quote is not None
            else 0.0
        )
        terminal_values.append(terminal_value)

    result["terminal_holding_value"] = terminal_values
    result["realized_plus_terminal_value"] = (
        result["forced_exit_proceeds"]
        + result["terminal_holding_value"]
    )
    result["approx_gain_dollars"] = (
        result["realized_plus_terminal_value"]
        - result["total_buy_dollars"]
    )
    result["approx_return_on_allocated_capital"] = (
        result["realized_plus_terminal_value"]
        / result["total_buy_dollars"]
        - 1.0
    )

    total_allocated = result["total_buy_dollars"].sum()
    total_gain = result["approx_gain_dollars"].sum()

    result["allocation_share"] = (
        result["total_buy_dollars"] / total_allocated
        if total_allocated > 0
        else float("nan")
    )
    result["gain_share"] = (
        result["approx_gain_dollars"] / total_gain
        if total_gain != 0
        else float("nan")
    )

    return result.sort_values(
        "approx_gain_dollars",
        ascending=False,
    ).reset_index(drop=True)


def main() -> None:
    args = parse_args()

    frame = pd.read_csv(args.long_growth, low_memory=False)
    store = BacktestPriceStore(args.prices)

    start = date(args.start_year, 1, 1)
    end = date(args.end_year, 12, 31)

    print("Running baseline Top-" + str(args.top_n) + "...", flush=True)

    baseline = run_strategy(
        frame,
        store=store,
        weekly_contribution=args.weekly_contribution,
        top_n=args.top_n,
        start=start,
        end=end,
    )

    print(
        "Baseline complete: value="
        + format(baseline.summary["terminal_value"], ",.2f")
        + " XIRR="
        + format(baseline.summary["xirr"], ".2%"),
        flush=True,
    )

    terminal_date = pd.Timestamp(
        baseline.summary["terminal_date"]
    ).date()

    print("Reconstructing position concentration...", flush=True)
    concentration = reconstruct_holding_path(
        baseline.trades,
        store=store,
        weekly=baseline.weekly,
    )

    print("Calculating ticker attribution...", flush=True)
    attribution = ticker_attribution(
        baseline.trades,
        store=store,
        terminal_date=terminal_date,
    )

    top_winners = (
        attribution.loc[
            attribution["approx_gain_dollars"] > 0,
            "ticker",
        ]
        .head(args.leave_out_count)
        .astype(str)
        .str.upper()
        .tolist()
    )

    leave_out_rows = []
    total_tests = len(top_winners)

    for number, ticker in enumerate(top_winners, start=1):
        print(
            "["
            + str(number)
            + "/"
            + str(total_tests)
            + "] Leave-one-out: excluding "
            + ticker
            + "...",
            flush=True,
        )

        result = run_strategy(
            frame,
            store=store,
            weekly_contribution=args.weekly_contribution,
            top_n=args.top_n,
            start=start,
            end=end,
            excluded_tickers={ticker},
            model_id="exclude_" + ticker,
        )

        row = dict(result.summary)
        row["excluded_ticker"] = ticker
        row["terminal_value_delta_vs_baseline"] = (
            row["terminal_value"]
            - baseline.summary["terminal_value"]
        )
        row["xirr_delta_vs_baseline"] = (
            row["xirr"] - baseline.summary["xirr"]
        )
        row["max_drawdown_delta_vs_baseline"] = (
            row["max_drawdown"]
            - baseline.summary["max_drawdown"]
        )
        leave_out_rows.append(row)

    cumulative_rows = []
    excluded = set()

    for number, ticker in enumerate(top_winners, start=1):
        excluded.add(ticker)

        print(
            "Cumulative leave-out: excluding top "
            + str(number)
            + " winners...",
            flush=True,
        )

        result = run_strategy(
            frame,
            store=store,
            weekly_contribution=args.weekly_contribution,
            top_n=args.top_n,
            start=start,
            end=end,
            excluded_tickers=excluded,
            model_id="exclude_top_" + str(number),
        )

        row = dict(result.summary)
        row["excluded_count"] = number
        row["excluded_tickers"] = "|".join(top_winners[:number])
        row["terminal_value_delta_vs_baseline"] = (
            row["terminal_value"]
            - baseline.summary["terminal_value"]
        )
        row["xirr_delta_vs_baseline"] = (
            row["xirr"] - baseline.summary["xirr"]
        )
        row["max_drawdown_delta_vs_baseline"] = (
            row["max_drawdown"]
            - baseline.summary["max_drawdown"]
        )
        cumulative_rows.append(row)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    pd.DataFrame([baseline.summary]).to_csv(
        args.output_dir / "concentration_baseline_summary.csv",
        index=False,
    )
    concentration.to_csv(
        args.output_dir / "historical_position_concentration.csv",
        index=False,
    )
    attribution.to_csv(
        args.output_dir / "ticker_attribution.csv",
        index=False,
    )
    pd.DataFrame(leave_out_rows).to_csv(
        args.output_dir / "leave_one_winner_out.csv",
        index=False,
    )
    pd.DataFrame(cumulative_rows).to_csv(
        args.output_dir / "leave_top_winners_out.csv",
        index=False,
    )

    summary = pd.DataFrame([{
        "max_largest_position_weight": concentration[
            "largest_position_weight"
        ].max(),
        "median_largest_position_weight": concentration[
            "largest_position_weight"
        ].median(),
        "ending_largest_position_weight": concentration.iloc[-1][
            "largest_position_weight"
        ],
        "max_top3_position_weight": concentration[
            "top3_position_weight"
        ].max(),
        "ending_top3_position_weight": concentration.iloc[-1][
            "top3_position_weight"
        ],
        "max_top5_position_weight": concentration[
            "top5_position_weight"
        ].max(),
        "ending_top5_position_weight": concentration.iloc[-1][
            "top5_position_weight"
        ],
        "largest_gain_ticker": (
            attribution.iloc[0]["ticker"]
            if not attribution.empty
            else ""
        ),
        "largest_gain_share": (
            attribution.iloc[0]["gain_share"]
            if not attribution.empty
            else float("nan")
        ),
        "top3_gain_share": (
            attribution.head(3)["approx_gain_dollars"].sum()
            / attribution["approx_gain_dollars"].sum()
            if not attribution.empty
            and attribution["approx_gain_dollars"].sum() != 0
            else float("nan")
        ),
        "top5_gain_share": (
            attribution.head(5)["approx_gain_dollars"].sum()
            / attribution["approx_gain_dollars"].sum()
            if not attribution.empty
            and attribution["approx_gain_dollars"].sum() != 0
            else float("nan")
        ),
    }])

    summary.to_csv(
        args.output_dir / "concentration_summary.csv",
        index=False,
    )

    print("Concentration robustness audit complete.", flush=True)
    print("Reports: " + str(args.output_dir), flush=True)


if __name__ == "__main__":
    main()
