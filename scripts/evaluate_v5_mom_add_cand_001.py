from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd

from finance.backtest import (
    BacktestConfig,
    BacktestPriceStore,
    run_ranked_accumulation_backtest,
    run_single_asset_accumulation_backtest,
)
from collections import defaultdict

EXPECTED_DATASET_SHA256 = "36a30bc1115c1496ee34efa0dfe2ba4e030b927e68e4d018c882d4b97831e0dc"
EXPECTED_PROTOCOL_SHA256 = "86624fd115f1f4adf51d6c4877fec08bae2bd04330928e3b73933252de34709c"

TERMINAL_IMPROVEMENT_GATE = 0.01
XIRR_DELTA_GATE = 0.001
ROLLING_WIN_RATE_GATE = 0.55
ROLLING_MEDIAN_XIRR_DELTA_GATE = 0.0
MAX_DRAWDOWN_INCREASE = 0.025
MAX_REPLACEMENT_RATE_INCREASE = 0.05
MAX_LARGEST_POSITION_WEIGHT_INCREASE = 0.05
MAX_TOP5_POSITION_WEIGHT_INCREASE = 0.10


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def run_model(
    frame: pd.DataFrame,
    *,
    store: BacktestPriceStore,
    model_id: str,
    score_column: str,
    selection_flag: str,
    start: date,
    end: date,
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


def interaction_severity(growth: pd.Series, valuation: pd.Series, neutral: float) -> pd.Series:
    growth_pressure = ((growth - neutral) / (100.0 - neutral)).clip(0.0, 1.0)
    valuation_weakness = ((neutral - valuation) / neutral).clip(0.0, 1.0)
    return growth_pressure * valuation_weakness


def add_base_score(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    result = frame.copy()
    base = config["base_family_weights"]
    neutral = float(config["modifier"]["neutral_score"])
    max_shift = float(config["modifier"]["maximum_growth_to_valuation_shift"])

    values = {
        family: pd.to_numeric(result[family + "_score"], errors="coerce")
        for family in base
    }
    severity = interaction_severity(
        values["growth"],
        values["valuation"],
        neutral,
    )

    growth_weight = float(base["growth"]) - max_shift * severity
    valuation_weight = float(base["valuation"]) + max_shift * severity

    score = pd.Series(float("nan"), index=result.index, dtype="float64")
    family_count = pd.Series(0, index=result.index, dtype="int64")
    weight_coverage = pd.Series(0.0, index=result.index, dtype="float64")

    for idx in result.index:
        row_weights = {
            "quality": float(base["quality"]),
            "financial_health": float(base["financial_health"]),
            "growth": float(growth_weight.loc[idx]),
            "valuation": float(valuation_weight.loc[idx]),
        }
        weighted = 0.0
        available_weight = 0.0
        available_count = 0
        for family, weight in row_weights.items():
            value = values[family].loc[idx]
            if pd.notna(value):
                weighted += float(value) * weight
                available_weight += weight
                available_count += 1

        family_count.loc[idx] = available_count
        weight_coverage.loc[idx] = available_weight
        if available_count >= int(config["minimum_families"]) and available_weight > 0:
            score.loc[idx] = weighted / available_weight

    result["base_interaction_severity"] = severity
    result["base_growth_weight"] = growth_weight
    result["base_valuation_weight"] = valuation_weight
    result["base_score"] = score
    result["base_family_count"] = family_count
    result["base_weight_coverage"] = weight_coverage
    result["base_eligible"] = (
        family_count >= int(config["minimum_families"])
    ) & score.notna()
    result["base_top_conviction_eligible"] = (
        score.notna() & family_count.eq(len(base))
        if bool(config["top_conviction_requires_full_family_coverage"])
        else result["v5_candidate_eligible"]
    )
    return result


def add_candidate_score(
    frame: pd.DataFrame,
    *,
    base_config: dict,
    momentum_config: dict,
) -> pd.DataFrame:
    result = add_base_score(frame, base_config)
    momentum = pd.to_numeric(result["momentum_score"], errors="coerce")
    result["v5_candidate_score"] = (
        float(momentum_config["candidate_weight"])
        * pd.to_numeric(result["base_score"], errors="coerce")
        + float(momentum_config["momentum_weight"]) * momentum
    )
    result["v5_candidate_top_conviction_eligible"] = (
        result["base_top_conviction_eligible"].fillna(False).astype(bool)
        & momentum.notna()
        & result["v5_candidate_score"].notna()
    )
    return result

def top10_sets(
    frame: pd.DataFrame,
    *,
    score_column: str,
    selection_flag: str,
) -> dict[str, list[str]]:
    result = {}
    for decision_date, group in frame.groupby("decision_date", sort=True):
        selected = group.loc[
            group[selection_flag].fillna(False).astype(bool)
            & pd.to_numeric(group[score_column], errors="coerce").notna()
        ].copy()
        selected[score_column] = pd.to_numeric(
            selected[score_column], errors="coerce"
        )
        selected = selected.sort_values(
            [score_column, "ticker"],
            ascending=[False, True],
            kind="mergesort",
        ).head(10)
        if len(selected) == 10:
            result[str(decision_date)[:10]] = (
                selected["ticker"].astype(str).str.upper().tolist()
            )
    return result


def mean_replacement_rate(sets: dict[str, list[str]]) -> float:
    dates = sorted(sets)
    values = []
    for previous, current in zip(dates, dates[1:]):
        prior_set = set(sets[previous])
        current_set = set(sets[current])
        values.append((10 - len(prior_set & current_set)) / 10.0)
    return float(pd.Series(values).mean()) if values else float("nan")


def selection_attribution(
    v1_sets: dict[str, list[str]],
    candidate_sets: dict[str, list[str]],
) -> pd.DataFrame:
    rows = []
    for day in sorted(set(v1_sets) & set(candidate_sets)):
        v1 = v1_sets[day]
        candidate = candidate_sets[day]
        v1_set = set(v1)
        candidate_set = set(candidate)
        rows.append({
            "decision_date": day,
            "overlap": len(v1_set & candidate_set),
            "candidate_only": "|".join(sorted(candidate_set - v1_set)),
            "v1_only": "|".join(sorted(v1_set - candidate_set)),
            "replacement_rate_vs_v1": (10 - len(v1_set & candidate_set)) / 10.0,
        })
    return pd.DataFrame(rows)


def rolling_windows(
    frame: pd.DataFrame,
    scored: pd.DataFrame,
    *,
    price_store: BacktestPriceStore,
    benchmark_store: BacktestPriceStore,
) -> pd.DataFrame:
    rows = []
    for years in (3, 5):
        last_start = 2025 - years + 1
        for start_year in range(2016, last_start + 1):
            end_year = start_year + years - 1
            start = date(start_year, 1, 1)
            end = date(end_year, 12, 31)

            v1 = run_model(
                frame,
                store=price_store,
                model_id="long_growth_v1",
                score_column="long_growth_v1_score",
                selection_flag="top_conviction_eligible",
                start=start,
                end=end,
            )
            candidate = run_model(
                scored,
                store=price_store,
                model_id="V5-MOM-ADD-CAND-001",
                score_column="v5_candidate_score",
                selection_flag="v5_candidate_top_conviction_eligible",
                start=start,
                end=end,
            )

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

            rows.append({
                "window_years": years,
                "start_year": start_year,
                "end_year": end_year,
                "v1_terminal_value": float(v1.summary["terminal_value"]),
                "candidate_terminal_value": float(candidate.summary["terminal_value"]),
                "terminal_value_delta": float(candidate.summary["terminal_value"]) - float(v1.summary["terminal_value"]),
                "v1_xirr": float(v1.summary["xirr"]),
                "candidate_xirr": float(candidate.summary["xirr"]),
                "xirr_delta": float(candidate.summary["xirr"]) - float(v1.summary["xirr"]),
                "benchmark_xirr": float(benchmark.summary["xirr"]),
                "v1_beats_benchmark_xirr": float(v1.summary["xirr"]) > float(benchmark.summary["xirr"]),
                "candidate_beats_benchmark_xirr": float(candidate.summary["xirr"]) > float(benchmark.summary["xirr"]),
            })
    return pd.DataFrame(rows)


def rolling_gate_summary(rolling: pd.DataFrame) -> dict:
    result = {}
    for years in (3, 5):
        group = rolling.loc[rolling["window_years"].eq(years)]
        result[str(years) + "y"] = {
            "windows": int(len(group)),
            "xirr_win_rate": float(group["xirr_delta"].gt(0).mean()),
            "median_xirr_delta": float(group["xirr_delta"].median()),
            "v1_benchmark_beating_count": int(
                group["v1_beats_benchmark_xirr"].sum()
            ),
            "candidate_benchmark_beating_count": int(
                group["candidate_beats_benchmark_xirr"].sum()
            ),
        }
    return result


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
        top1 = values[0][1] if values else 0.0
        top5 = sum(value for _, value in values[:5])

        rows.append({
            "decision_date": decision_day,
            "valuation_date": valuation_day,
            "portfolio_value": total_value,
            "largest_position_ticker": values[0][0] if values else "",
            "largest_position_weight": (
                top1 / total_value if total_value > 0 else float("nan")
            ),
            "top5_position_weight": (
                top5 / total_value if total_value > 0 else float("nan")
            ),
        })

    return pd.DataFrame(rows)


def concentration_metrics(result, store: BacktestPriceStore) -> dict:
    path = reconstruct_holding_path(
        result.trades,
        store=store,
        weekly=result.weekly,
    )
    return {
        "max_largest_position_weight": float(
            path["largest_position_weight"].max()
        ),
        "max_top5_position_weight": float(
            path["top5_position_weight"].max()
        ),
    }


def serializable_concentration_row(row: pd.Series) -> dict:
    result = row.to_dict()
    for key in ("decision_date", "valuation_date"):
        value = result.get(key)
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
    return result


def ticker_selection_stats(
    ticker: str,
    *,
    sets: dict[str, list[str]],
    trades: pd.DataFrame,
) -> dict:
    symbol = ticker.upper()
    selected_dates = [
        day for day, names in sets.items()
        if symbol in set(names)
    ]
    buys = trades.loc[
        trades["ticker"].astype(str).str.upper().eq(symbol)
        & trades["side"].astype(str).eq("buy")
    ].copy()
    return {
        "ticker": symbol,
        "selected_weeks": int(len(selected_dates)),
        "first_selected_date": selected_dates[0] if selected_dates else None,
        "last_selected_date": selected_dates[-1] if selected_dates else None,
        "total_buy_dollars": float(pd.to_numeric(buys["dollars"], errors="coerce").fillna(0).sum()) if not buys.empty else 0.0,
        "buy_count": int(len(buys)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate frozen V5 additive Momentum candidate 001."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/v5_mom_add_cand_001.json"),
    )
    parser.add_argument(
        "--base-config",
        type=Path,
        default=Path("config/v5_fund_cand_003.json"),
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(
            "reports/v5/attribution_dataset/"
            "v5_historical_attribution_dataset.csv"
        ),
    )
    parser.add_argument(
        "--prices",
        type=Path,
        default=Path("data/market/daily_prices.csv.gz"),
    )
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=Path("data/market/benchmark_spy_historical.csv"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "reports/v5/confirmation_risk/V5-MOM-ADD-CAND-001"
        ),
    )
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    if (
        config.get("experiment_id") != "V5-MOM-ADD-CAND-001"
        or config.get("status") != "frozen_candidate"
    ):
        raise SystemExit("Unexpected frozen candidate config.")
    if config.get("research_only") is not True:
        raise SystemExit("Candidate must be research-only.")
    if any((config.get("execution_capabilities") or {}).values()):
        raise SystemExit("Execution capabilities must all be false.")

    dataset_sha = sha256_file(args.dataset)
    if dataset_sha != EXPECTED_DATASET_SHA256:
        raise SystemExit("Attribution dataset fingerprint mismatch: " + dataset_sha)

    base_config = json.loads(args.base_config.read_text(encoding="utf-8"))
    if (
        base_config.get("experiment_id") != "V5-FUND-CAND-003"
        or base_config.get("status") != "frozen_candidate"
    ):
        raise SystemExit("Unexpected frozen base candidate config.")

    frame = pd.read_csv(args.dataset, low_memory=False)
    duplicate_keys = int(
        frame[["decision_date", "ticker"]].duplicated().sum()
    )
    if duplicate_keys:
        raise SystemExit(
            "Duplicate decision_date/ticker keys: " + str(duplicate_keys)
        )

    scored = add_candidate_score(
        frame,
        base_config=base_config,
        momentum_config=config,
    )
    price_store = BacktestPriceStore(args.prices)
    benchmark_store = BacktestPriceStore(args.benchmark, ticker_column="ticker")

    start = date(2016, 1, 1)
    end = date(2025, 12, 31)

    v1 = run_model(
        frame,
        store=price_store,
        model_id="long_growth_v1",
        score_column="long_growth_v1_score",
        selection_flag="top_conviction_eligible",
        start=start,
        end=end,
    )
    candidate = run_model(
        scored,
        store=price_store,
        model_id="V5-MOM-ADD-CAND-001",
        score_column="v5_candidate_score",
        selection_flag="v5_candidate_top_conviction_eligible",
        start=start,
        end=end,
    )

    v1_sets = top10_sets(
        frame,
        score_column="long_growth_v1_score",
        selection_flag="top_conviction_eligible",
    )
    candidate_sets = top10_sets(
        scored,
        score_column="v5_candidate_score",
        selection_flag="v5_candidate_top_conviction_eligible",
    )
    attribution = selection_attribution(v1_sets, candidate_sets)

    v1_replacement = mean_replacement_rate(v1_sets)
    candidate_replacement = mean_replacement_rate(candidate_sets)
    replacement_delta = candidate_replacement - v1_replacement

    v1_concentration_path = reconstruct_holding_path(
        v1.trades,
        store=price_store,
        weekly=v1.weekly,
    )
    candidate_concentration_path = reconstruct_holding_path(
        candidate.trades,
        store=price_store,
        weekly=candidate.weekly,
    )
    v1_concentration = {
        "max_largest_position_weight": float(
            v1_concentration_path["largest_position_weight"].max()
        ),
        "max_top5_position_weight": float(
            v1_concentration_path["top5_position_weight"].max()
        ),
    }
    candidate_concentration = {
        "max_largest_position_weight": float(
            candidate_concentration_path["largest_position_weight"].max()
        ),
        "max_top5_position_weight": float(
            candidate_concentration_path["top5_position_weight"].max()
        ),
    }

    rolling = rolling_windows(
        frame,
        scored,
        price_store=price_store,
        benchmark_store=benchmark_store,
    )
    rolling_summary = rolling_gate_summary(rolling)

    v1_terminal = float(v1.summary["terminal_value"])
    candidate_terminal = float(candidate.summary["terminal_value"])
    terminal_pct = candidate_terminal / v1_terminal - 1.0
    xirr_delta = float(candidate.summary["xirr"]) - float(v1.summary["xirr"])
    drawdown_delta = (
        float(candidate.summary["max_drawdown"])
        - float(v1.summary["max_drawdown"])
    )
    largest_delta = (
        candidate_concentration["max_largest_position_weight"]
        - v1_concentration["max_largest_position_weight"]
    )
    top5_delta = (
        candidate_concentration["max_top5_position_weight"]
        - v1_concentration["max_top5_position_weight"]
    )

    gates = {
        "integrity_no_duplicate_keys": duplicate_keys == 0,
        "economic_terminal_value": terminal_pct >= TERMINAL_IMPROVEMENT_GATE,
        "economic_xirr": xirr_delta >= XIRR_DELTA_GATE,
        "robustness_3y_win_rate": rolling_summary["3y"]["xirr_win_rate"] >= ROLLING_WIN_RATE_GATE,
        "robustness_5y_win_rate": rolling_summary["5y"]["xirr_win_rate"] >= ROLLING_WIN_RATE_GATE,
        "robustness_3y_median_xirr_delta": rolling_summary["3y"]["median_xirr_delta"] >= ROLLING_MEDIAN_XIRR_DELTA_GATE,
        "robustness_5y_median_xirr_delta": rolling_summary["5y"]["median_xirr_delta"] >= ROLLING_MEDIAN_XIRR_DELTA_GATE,
        "benchmark_3y_count_not_lower": rolling_summary["3y"]["candidate_benchmark_beating_count"] >= rolling_summary["3y"]["v1_benchmark_beating_count"],
        "benchmark_5y_count_not_lower": rolling_summary["5y"]["candidate_benchmark_beating_count"] >= rolling_summary["5y"]["v1_benchmark_beating_count"],
        "risk_drawdown": drawdown_delta <= MAX_DRAWDOWN_INCREASE,
        "risk_replacement_rate": replacement_delta <= MAX_REPLACEMENT_RATE_INCREASE,
        "concentration_largest_position": largest_delta <= MAX_LARGEST_POSITION_WEIGHT_INCREASE,
        "concentration_top5": top5_delta <= MAX_TOP5_POSITION_WEIGHT_INCREASE,
    }
    passed = all(gates.values())

    args.output_dir.mkdir(parents=True, exist_ok=True)
    v1_concentration_path.to_csv(
        args.output_dir / "v1_concentration_path.csv",
        index=False,
    )
    candidate_concentration_path.to_csv(
        args.output_dir / "candidate_concentration_path.csv",
        index=False,
    )
    attribution.to_csv(
        args.output_dir / "selection_attribution.csv",
        index=False,
    )
    rolling.to_csv(
        args.output_dir / "rolling_windows.csv",
        index=False,
    )

    payload = {
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "status": "PASS" if passed else "FAIL",
        "research_only": True,
        "config_sha256": canonical_json_sha256(config),
        "dataset_sha256": dataset_sha,
        "protocol_sha256": EXPECTED_PROTOCOL_SHA256,
        "duplicate_keys": duplicate_keys,
        "v1": {
            "terminal_value": v1_terminal,
            "xirr": float(v1.summary["xirr"]),
            "max_drawdown": float(v1.summary["max_drawdown"]),
            "mean_top10_replacement_rate": v1_replacement,
            **v1_concentration,
        },
        "candidate": {
            "terminal_value": candidate_terminal,
            "xirr": float(candidate.summary["xirr"]),
            "max_drawdown": float(candidate.summary["max_drawdown"]),
            "mean_top10_replacement_rate": candidate_replacement,
            **candidate_concentration,
        },
        "delta": {
            "terminal_value_dollars": candidate_terminal - v1_terminal,
            "terminal_value_pct": terminal_pct,
            "xirr": xirr_delta,
            "max_drawdown": drawdown_delta,
            "mean_top10_replacement_rate": replacement_delta,
            "max_largest_position_weight": largest_delta,
            "max_top5_position_weight": top5_delta,
        },
        "concentration_max_rows": {
            "v1": serializable_concentration_row(
                v1_concentration_path.loc[
                    v1_concentration_path["largest_position_weight"].idxmax()
                ]
            ),
            "candidate": serializable_concentration_row(
                candidate_concentration_path.loc[
                    candidate_concentration_path["largest_position_weight"].idxmax()
                ]
            ),
        },
        "concentration_driver": {
            "v1": ticker_selection_stats(
                "NVDA",
                sets=v1_sets,
                trades=v1.trades,
            ),
            "candidate": ticker_selection_stats(
                "NVDA",
                sets=candidate_sets,
                trades=candidate.trades,
            ),
        },
        "selection_effect": {
            "comparable_dates": int(len(attribution)),
            "mean_top10_overlap": float(attribution["overlap"].mean()),
            "mean_replacement_vs_v1": float(
                attribution["replacement_rate_vs_v1"].mean()
            ),
            "minimum_top10_overlap": int(attribution["overlap"].min()),
        },
        "rolling": rolling_summary,
        "gates": gates,
        "all_hard_gates_pass": passed,
        "historical_pass_authorizes_live_trading": False,
    }

    (args.output_dir / "evaluation.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V5 #48 FROZEN MOMENTUM CANDIDATE EVALUATION")
    print("Experiment:                 " + config["experiment_id"])
    print("Research only:              YES")
    print("Config SHA-256:             " + payload["config_sha256"])
    print("Terminal delta:             USD {:+,.2f} ({:+.2%})".format(
        payload["delta"]["terminal_value_dollars"],
        payload["delta"]["terminal_value_pct"],
    ))
    print("XIRR delta:                 {:+.4%}".format(payload["delta"]["xirr"]))
    print("Max DD delta:               {:+.2%}".format(payload["delta"]["max_drawdown"]))
    print("Replacement-rate delta:     {:+.2%}".format(
        payload["delta"]["mean_top10_replacement_rate"]
    ))
    print("Largest-position delta:     {:+.2%}".format(
        payload["delta"]["max_largest_position_weight"]
    ))
    print("Top-5 position delta:       {:+.2%}".format(
        payload["delta"]["max_top5_position_weight"]
    ))
    print("V1 max concentration:       {} {:.2%}".format(
        payload["concentration_max_rows"]["v1"].get("largest_position_ticker", ""),
        payload["concentration_max_rows"]["v1"]["largest_position_weight"],
    ))
    print("Candidate max concentration:{} {:.2%}".format(
        payload["concentration_max_rows"]["candidate"].get("largest_position_ticker", ""),
        payload["concentration_max_rows"]["candidate"]["largest_position_weight"],
    ))
    print("V1 peak date:               {}".format(
        payload["concentration_max_rows"]["v1"]["valuation_date"]
    ))
    print("Candidate peak date:        {}".format(
        payload["concentration_max_rows"]["candidate"]["valuation_date"]
    ))
    print("NVDA selected weeks V1/Cand:{}/{}".format(
        payload["concentration_driver"]["v1"]["selected_weeks"],
        payload["concentration_driver"]["candidate"]["selected_weeks"],
    ))
    print("NVDA buy dollars V1/Cand:   {:.2f}/{:.2f}".format(
        payload["concentration_driver"]["v1"]["total_buy_dollars"],
        payload["concentration_driver"]["candidate"]["total_buy_dollars"],
    ))
    print("3y win rate / median delta: {:.2%} / {:+.4%}".format(
        payload["rolling"]["3y"]["xirr_win_rate"],
        payload["rolling"]["3y"]["median_xirr_delta"],
    ))
    print("5y win rate / median delta: {:.2%} / {:+.4%}".format(
        payload["rolling"]["5y"]["xirr_win_rate"],
        payload["rolling"]["5y"]["median_xirr_delta"],
    ))
    print("Benchmark wins 3y V1/Cand:  {}/{}".format(
        payload["rolling"]["3y"]["v1_benchmark_beating_count"],
        payload["rolling"]["3y"]["candidate_benchmark_beating_count"],
    ))
    print("Benchmark wins 5y V1/Cand:  {}/{}".format(
        payload["rolling"]["5y"]["v1_benchmark_beating_count"],
        payload["rolling"]["5y"]["candidate_benchmark_beating_count"],
    ))
    print("GATES")
    for name, value in gates.items():
        print("  {:38s} {}".format(name, "PASS" if value else "FAIL"))
    print("ALL HARD GATES:             " + ("PASS" if passed else "FAIL"))
    print("Live authorization:         NO")
    print("Output:                     " + str(args.output_dir))


if __name__ == "__main__":
    main()
