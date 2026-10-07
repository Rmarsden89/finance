from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from finance.backtest import (\n    BacktestConfig,\n    BacktestPriceStore,\n    run_ranked_accumulation_backtest,\n    run_single_asset_accumulation_backtest,\n)


def interaction_severity(growth, valuation, neutral):
    gp = ((growth - neutral) / (100.0 - neutral)).clip(0.0, 1.0)
    vw = ((neutral - valuation) / neutral).clip(0.0, 1.0)
    return gp * vw


def add_cand003_score(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    result = frame.copy()
    base = config["base_family_weights"]
    neutral = float(config["modifier"]["neutral_score"])
    max_shift = float(config["modifier"]["maximum_growth_to_valuation_shift"])

    values = {
        family: pd.to_numeric(result[family + "_score"], errors="coerce")
        for family in base
    }
    severity = interaction_severity(values["growth"], values["valuation"], neutral)
    gw = float(base["growth"]) - max_shift * severity
    vw = float(base["valuation"]) + max_shift * severity

    score = pd.Series(float("nan"), index=result.index, dtype="float64")
    count = pd.Series(0, index=result.index, dtype="int64")

    for idx in result.index:
        weights = {
            "quality": float(base["quality"]),
            "financial_health": float(base["financial_health"]),
            "growth": float(gw.loc[idx]),
            "valuation": float(vw.loc[idx]),
        }
        weighted = 0.0
        available_weight = 0.0
        available_count = 0
        for family, weight in weights.items():
            value = values[family].loc[idx]
            if pd.notna(value):
                weighted += float(value) * weight
                available_weight += weight
                available_count += 1
        count.loc[idx] = available_count
        if available_count >= int(config["minimum_families"]) and available_weight > 0:
            score.loc[idx] = weighted / available_weight

    result["cand003_score"] = score
    result["cand003_family_count"] = count
    result["cand003_top_conviction_eligible"] = (
        score.notna() & count.eq(len(base))
        if bool(config["top_conviction_requires_full_family_coverage"])
        else score.notna() & count.ge(int(config["minimum_families"]))
    )
    return result


def add_rank_and_momentum_quintile(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["cand003_rank"] = np.nan
    result["momentum_quintile"] = np.nan

    for _, group in result.groupby("decision_date", sort=True):
        eligible = group.loc[
            group["cand003_top_conviction_eligible"].fillna(False).astype(bool)
            & pd.to_numeric(group["cand003_score"], errors="coerce").notna()
        ].copy()
        eligible["cand003_score"] = pd.to_numeric(eligible["cand003_score"], errors="coerce")
        eligible = eligible.sort_values(
            ["cand003_score", "ticker"], ascending=[False, True], kind="mergesort"
        )
        result.loc[eligible.index, "cand003_rank"] = np.arange(1, len(eligible) + 1)

        mom = pd.to_numeric(group["momentum_score"], errors="coerce")
        valid = group.loc[mom.notna()].copy()
        if not valid.empty:
            pct = pd.to_numeric(valid["momentum_score"], errors="coerce").rank(
                method="first", pct=True
            )
            result.loc[valid.index, "momentum_quintile"] = np.ceil(pct * 5).clip(1, 5)

    return result


def add_structures(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    result = frame.copy()
    momentum = pd.to_numeric(result["momentum_score"], errors="coerce")
    result["mom_add_score"] = (
        0.95 * pd.to_numeric(result["cand003_score"], errors="coerce")
        + 0.05 * momentum
    )
    result["mom_add_eligible"] = (
        result["cand003_top_conviction_eligible"] & momentum.notna()
    )

    result["mom_conf_selected"] = False
    swap_count = 0
    for _, group in result.groupby("decision_date", sort=True):
        selected = group.loc[group["cand003_rank"].between(1, 10)].copy()
        if len(selected) != 10:
            continue

        result.loc[selected.index, "mom_conf_selected"] = True
        rank10 = group.loc[group["cand003_rank"].eq(10)]
        rank11 = group.loc[group["cand003_rank"].eq(11)]
        if len(rank10) != 1 or len(rank11) != 1:
            continue

        r10 = rank10.iloc[0]
        r11 = rank11.iloc[0]
        if (
            int(r10["momentum_quintile"]) == 1
            and int(r11["momentum_quintile"]) == 5
        ):
            result.loc[rank10.index, "mom_conf_selected"] = False
            result.loc[rank11.index, "mom_conf_selected"] = True
            swap_count += 1

    result["mom_conf_score"] = pd.to_numeric(result["cand003_score"], errors="coerce")
    return result, swap_count


def run_model(
    frame,
    *,
    store,
    model_id,
    score_column,
    selection_flag,
    start=date(2016, 1, 1),
    end=date(2025, 12, 31),
):
    return run_ranked_accumulation_backtest(
        frame,
        price_store=store,
        model_id=model_id,
        score_column=score_column,
        config=BacktestConfig(
            weekly_contribution=10.0,
            top_n=10,
            selection_flag=selection_flag,
            max_addon_position_weight=0.10,
        ),
        start=start,
        end=end,
    )


def rolling_robustness(frame, *, store, benchmark_store):
    rows = []
    specs = (
        ("v1", "long_growth_v1", "long_growth_v1_score", "top_conviction_eligible"),
        ("base", "V5-FUND-CAND-003", "cand003_score", "cand003_top_conviction_eligible"),
        ("additive", "V5-MOM-ADD-EXP-001", "mom_add_score", "mom_add_eligible"),
        ("confirmation", "V5-MOM-CONF-EXP-001", "mom_conf_score", "mom_conf_selected"),
    )

    for years in (3, 5):
        last_start = 2025 - years + 1
        for start_year in range(2016, last_start + 1):
            end_year = start_year + years - 1
            start = date(start_year, 1, 1)
            end = date(end_year, 12, 31)

            decision_dates = sorted(
                pd.to_datetime(
                    frame.loc[
                        pd.to_datetime(frame["decision_date"]).dt.year.between(
                            start_year,
                            end_year,
                        ),
                        "decision_date",
                    ]
                ).dt.date.unique()
            )
            benchmark = run_single_asset_accumulation_backtest(
                price_store=benchmark_store,
                ticker="SPY",
                decision_dates=decision_dates,
                weekly_contribution=10.0,
                model_id="SPY",
            )
            bench_xirr = float(benchmark.summary["xirr"])

            model_results = {}
            for key, model_id, score_column, selection_flag in specs:
                result = run_model(
                    frame,
                    store=store,
                    model_id=model_id,
                    score_column=score_column,
                    selection_flag=selection_flag,
                    start=start,
                    end=end,
                )
                model_results[key] = {
                    "xirr": float(result.summary["xirr"]),
                    "terminal_value": float(result.summary["terminal_value"]),
                }

            rows.append({
                "window_years": years,
                "start_year": start_year,
                "end_year": end_year,
                "benchmark_xirr": bench_xirr,
                "v1_xirr": model_results["v1"]["xirr"],
                "base_xirr": model_results["base"]["xirr"],
                "additive_xirr": model_results["additive"]["xirr"],
                "confirmation_xirr": model_results["confirmation"]["xirr"],
                "additive_delta_vs_base": (
                    model_results["additive"]["xirr"] - model_results["base"]["xirr"]
                ),
                "confirmation_delta_vs_base": (
                    model_results["confirmation"]["xirr"] - model_results["base"]["xirr"]
                ),
                "additive_delta_vs_v1": (
                    model_results["additive"]["xirr"] - model_results["v1"]["xirr"]
                ),
                "confirmation_delta_vs_v1": (
                    model_results["confirmation"]["xirr"] - model_results["v1"]["xirr"]
                ),
                "additive_beats_benchmark": model_results["additive"]["xirr"] > bench_xirr,
                "confirmation_beats_benchmark": model_results["confirmation"]["xirr"] > bench_xirr,
                "base_beats_benchmark": model_results["base"]["xirr"] > bench_xirr,
                "v1_beats_benchmark": model_results["v1"]["xirr"] > bench_xirr,
            })

    return pd.DataFrame(rows)


def rolling_summary(rolling: pd.DataFrame, key: str) -> dict:
    result = {}
    for years in (3, 5):
        group = rolling.loc[rolling["window_years"].eq(years)]
        delta_base = pd.to_numeric(group[key + "_delta_vs_base"], errors="coerce")
        delta_v1 = pd.to_numeric(group[key + "_delta_vs_v1"], errors="coerce")
        result[str(years) + "y"] = {
            "windows": int(len(group)),
            "win_rate_vs_base": float(delta_base.gt(0).mean()),
            "median_xirr_delta_vs_base": float(delta_base.median()),
            "win_rate_vs_v1": float(delta_v1.gt(0).mean()),
            "median_xirr_delta_vs_v1": float(delta_v1.median()),
            "benchmark_beating_count": int(group[key + "_beats_benchmark"].sum()),
            "base_benchmark_beating_count": int(group["base_beats_benchmark"].sum()),
            "v1_benchmark_beating_count": int(group["v1_beats_benchmark"].sum()),
        }
    return result


def top10_sets(frame, score_column, selection_flag):
    out = {}
    for day, group in frame.groupby("decision_date", sort=True):
        selected = group.loc[
            group[selection_flag].fillna(False).astype(bool)
            & pd.to_numeric(group[score_column], errors="coerce").notna()
        ].copy()
        selected[score_column] = pd.to_numeric(selected[score_column], errors="coerce")
        selected = selected.sort_values(
            [score_column, "ticker"], ascending=[False, True], kind="mergesort"
        ).head(10)
        if len(selected) == 10:
            out[str(day)[:10]] = selected["ticker"].astype(str).str.upper().tolist()
    return out


def mean_replacement_rate(sets):
    dates = sorted(sets)
    vals = []
    for a, b in zip(dates, dates[1:]):
        vals.append((10 - len(set(sets[a]) & set(sets[b]))) / 10.0)
    return float(pd.Series(vals).mean()) if vals else float("nan")


def reconstruct_holding_path(trades, *, store, weekly):
    holdings = defaultdict(float)
    groups = {d: g for d, g in trades.groupby("decision_date", sort=False)}
    rows = []
    for w in weekly.sort_values("decision_date").itertuples(index=False):
        decision_day = pd.Timestamp(w.decision_date).date()
        valuation_day = pd.Timestamp(w.valuation_date).date()
        group = groups.get(decision_day)
        if group is not None:
            for trade in group.itertuples(index=False):
                ticker = str(trade.ticker).upper()
                if str(trade.side) == "buy":
                    holdings[ticker] += float(trade.units)
                elif str(trade.side) == "forced_exit":
                    holdings.pop(ticker, None)
        values = []
        total = float(w.cash)
        for ticker, units in holdings.items():
            quote = store.latest_as_of(ticker, valuation_day)
            if quote is None:
                continue
            value = units * quote.mark_price
            if value > 0:
                values.append((ticker, value))
                total += value
        values.sort(key=lambda x: x[1], reverse=True)
        rows.append({
            "decision_date": decision_day,
            "largest_position_weight": values[0][1] / total if values and total > 0 else np.nan,
            "top5_position_weight": sum(v for _, v in values[:5]) / total if total > 0 else np.nan,
        })
    return pd.DataFrame(rows)


def summary(result, sets, store):
    path = reconstruct_holding_path(result.trades, store=store, weekly=result.weekly)
    return {
        "terminal_value": float(result.summary["terminal_value"]),
        "xirr": float(result.summary["xirr"]),
        "max_drawdown": float(result.summary["max_drawdown"]),
        "replacement_rate": mean_replacement_rate(sets),
        "max_largest_position_weight": float(path["largest_position_weight"].max()),
        "max_top5_position_weight": float(path["top5_position_weight"].max()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("reports/v5/attribution_dataset/v5_historical_attribution_dataset.csv"))
    parser.add_argument("--cand003-config", type=Path, default=Path("config/v5_fund_cand_003.json"))
    parser.add_argument("--prices", type=Path, default=Path("data/market/daily_prices.csv.gz"))\n    parser.add_argument("--benchmark", type=Path, default=Path("data/market/benchmark_spy_historical.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/v5/confirmation_risk/momentum_structure_comparison"))
    args = parser.parse_args()

    config = json.loads(args.cand003_config.read_text(encoding="utf-8"))
    frame = pd.read_csv(args.dataset, low_memory=False)
    scored = add_rank_and_momentum_quintile(add_cand003_score(frame, config))
    scored, swaps = add_structures(scored)

    store = BacktestPriceStore(args.prices)\n    benchmark_store = BacktestPriceStore(args.benchmark, ticker_column="ticker")
    base = run_model(scored, store=store, model_id="V5-FUND-CAND-003", score_column="cand003_score", selection_flag="cand003_top_conviction_eligible")
    additive = run_model(scored, store=store, model_id="V5-MOM-ADD-EXP-001", score_column="mom_add_score", selection_flag="mom_add_eligible")
    confirm = run_model(scored, store=store, model_id="V5-MOM-CONF-EXP-001", score_column="mom_conf_score", selection_flag="mom_conf_selected")

    base_sets = top10_sets(scored, "cand003_score", "cand003_top_conviction_eligible")
    add_sets = top10_sets(scored, "mom_add_score", "mom_add_eligible")
    conf_sets = top10_sets(scored, "mom_conf_score", "mom_conf_selected")

    summaries = {
        "base": summary(base, base_sets, store),
        "additive": summary(additive, add_sets, store),
        "confirmation": summary(confirm, conf_sets, store),
        "confirmation_swaps": swaps,
    }

    rolling = rolling_robustness(
        scored,
        store=store,
        benchmark_store=benchmark_store,
    )
    summaries["rolling"] = {
        "additive": rolling_summary(rolling, "additive"),
        "confirmation": rolling_summary(rolling, "confirmation"),
    }

    for name in ("additive", "confirmation"):
        for metric in (
            "terminal_value", "xirr", "max_drawdown", "replacement_rate",
            "max_largest_position_weight", "max_top5_position_weight"
        ):
            summaries[name][metric + "_delta_vs_base"] = (
                summaries[name][metric] - summaries["base"][metric]
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rolling.to_csv(args.output_dir / "rolling_windows.csv", index=False)\n    (args.output_dir / "summary.json").write_text(
        json.dumps(summaries, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V5 #48 MOMENTUM STRUCTURE COMPARISON")
    print("Base:                       V5-FUND-CAND-003")
    print("Confirmation swaps:         {}".format(swaps))
    for label, key in (("ADDITIVE 5%", "additive"), ("BOUNDARY CONFIRM", "confirmation")):
        s = summaries[key]
        print(label)
        print("  terminal delta:           {:+,.2f}".format(s["terminal_value_delta_vs_base"]))
        print("  XIRR delta:               {:+.4%}".format(s["xirr_delta_vs_base"]))
        print("  max DD delta:             {:+.2%}".format(s["max_drawdown_delta_vs_base"]))
        print("  replacement delta:        {:+.2%}".format(s["replacement_rate_delta_vs_base"]))
        print("  largest-position delta:   {:+.2%}".format(s["max_largest_position_weight_delta_vs_base"]))
        print("  Top-5 concentration delta:{:+.2%}".format(s["max_top5_position_weight_delta_vs_base"]))
        roll = summaries["rolling"][key]
        print("  3y win vs CAND-003:       {:.2%} median={:+.4%}".format(
            roll["3y"]["win_rate_vs_base"],
            roll["3y"]["median_xirr_delta_vs_base"],
        ))
        print("  5y win vs CAND-003:       {:.2%} median={:+.4%}".format(
            roll["5y"]["win_rate_vs_base"],
            roll["5y"]["median_xirr_delta_vs_base"],
        ))
        print("  3y win vs V1:             {:.2%} median={:+.4%}".format(
            roll["3y"]["win_rate_vs_v1"],
            roll["3y"]["median_xirr_delta_vs_v1"],
        ))
        print("  5y win vs V1:             {:.2%} median={:+.4%}".format(
            roll["5y"]["win_rate_vs_v1"],
            roll["5y"]["median_xirr_delta_vs_v1"],
        ))
        print("  benchmark wins 3y:        {}/{}/{}".format(
            roll["3y"]["benchmark_beating_count"],
            roll["3y"]["base_benchmark_beating_count"],
            roll["3y"]["v1_benchmark_beating_count"],
        ))
        print("  benchmark wins 5y:        {}/{}/{}".format(
            roll["5y"]["benchmark_beating_count"],
            roll["5y"]["base_benchmark_beating_count"],
            roll["5y"]["v1_benchmark_beating_count"],
        ))
    print("Output:                     " + str(args.output_dir))


if __name__ == "__main__":
    main()
