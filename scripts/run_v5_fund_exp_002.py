from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

from finance.backtest import BacktestConfig, BacktestPriceStore, run_ranked_accumulation_backtest


EXPECTED_DATASET_SHA256 = "36a30bc1115c1496ee34efa0dfe2ba4e030b927e68e4d018c882d4b97831e0dc"


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


def add_exp_002_score(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    result = frame.copy()
    default_weights = config["default_family_weights"]
    modifier = config["modifier"]
    triggered_weights = modifier["family_weights_when_triggered"]

    if abs(sum(default_weights.values()) - 1.0) > 1e-12:
        raise ValueError("Default family weights must sum to 1.0")
    if abs(sum(triggered_weights.values()) - 1.0) > 1e-12:
        raise ValueError("Triggered family weights must sum to 1.0")

    family_values = {}
    for family in default_weights:
        column = family + "_score"
        if column not in result.columns:
            raise ValueError("Missing family score column: " + column)
        family_values[family] = pd.to_numeric(result[column], errors="coerce")

    trigger = (
        family_values["growth"].ge(float(modifier["growth_score_min"]))
        & family_values["valuation"].le(float(modifier["valuation_score_max"]))
    )

    score = pd.Series(float("nan"), index=result.index, dtype="float64")
    family_count = pd.Series(0, index=result.index, dtype="int64")
    weight_coverage = pd.Series(0.0, index=result.index, dtype="float64")

    for idx in result.index:
        weights = triggered_weights if bool(trigger.loc[idx]) else default_weights
        weighted = 0.0
        available_weight = 0.0
        available_count = 0
        for family, weight in weights.items():
            value = family_values[family].loc[idx]
            if pd.notna(value):
                weighted += float(value) * float(weight)
                available_weight += float(weight)
                available_count += 1

        family_count.loc[idx] = available_count
        weight_coverage.loc[idx] = available_weight
        if (
            available_count >= int(config["minimum_families"])
            and available_weight > 0
        ):
            score.loc[idx] = weighted / available_weight

    result["v5_exp_002_modifier_triggered"] = trigger
    result["v5_exp_002_score"] = score
    result["v5_exp_002_family_count"] = family_count
    result["v5_exp_002_weight_coverage"] = weight_coverage
    result["v5_exp_002_eligible"] = (
        family_count >= int(config["minimum_families"])
    ) & score.notna()

    if bool(config["top_conviction_requires_full_family_coverage"]):
        result["v5_exp_002_top_conviction_eligible"] = (
            score.notna() & family_count.eq(len(default_weights))
        )
    else:
        result["v5_exp_002_top_conviction_eligible"] = (
            result["v5_exp_002_eligible"]
        )

    return result


def reconstruct_holding_path(
    trades: pd.DataFrame,
    *,
    store: BacktestPriceStore,
    weekly: pd.DataFrame,
) -> pd.DataFrame:
    holdings = defaultdict(float)
    groups = {
        day: group
        for day, group in trades.groupby("decision_date", sort=False)
    }

    rows = []
    for weekly_row in weekly.sort_values("decision_date").itertuples(index=False):
        decision_day = pd.Timestamp(weekly_row.decision_date).date()
        valuation_day = pd.Timestamp(weekly_row.valuation_date).date()

        group = groups.get(decision_day)
        if group is not None:
            for trade in group.itertuples(index=False):
                ticker = str(trade.ticker).upper()
                if str(trade.side) == "buy":
                    holdings[ticker] += float(trade.units)
                elif str(trade.side) == "forced_exit":
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
        rows.append({
            "decision_date": decision_day,
            "valuation_date": valuation_day,
            "largest_position_ticker": values[0][0] if values else "",
            "largest_position_weight": (
                values[0][1] / total_value
                if values and total_value > 0
                else float("nan")
            ),
        })

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run V5 fundamental exploratory experiment 002."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/v5_fund_exp_002.json"),
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
        "--output",
        type=Path,
        default=Path(
            "reports/v5/fundamental/V5-FUND-EXP-002/"
            "exploratory_result.json"
        ),
    )
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    if config.get("experiment_id") != "V5-FUND-EXP-002":
        raise SystemExit("Unexpected experiment config.")
    if config.get("status") != "exploratory":
        raise SystemExit("EXP-002 must remain exploratory.")
    if any((config.get("execution_capabilities") or {}).values()):
        raise SystemExit("Execution capabilities must all be false.")

    dataset_sha = sha256_file(args.dataset)
    if dataset_sha != EXPECTED_DATASET_SHA256:
        raise SystemExit("Attribution dataset fingerprint mismatch: " + dataset_sha)

    frame = pd.read_csv(args.dataset, low_memory=False)
    scored = add_exp_002_score(frame, config)
    store = BacktestPriceStore(args.prices)

    start = date(2016, 1, 1)
    end = date(2025, 12, 31)

    v1 = run_ranked_accumulation_backtest(
        frame,
        price_store=store,
        model_id="long_growth_v1",
        score_column="long_growth_v1_score",
        config=BacktestConfig(
            weekly_contribution=10.0,
            top_n=10,
            selection_flag="top_conviction_eligible",
            max_addon_position_weight=0.10,
        ),
        start=start,
        end=end,
    )
    candidate = run_ranked_accumulation_backtest(
        scored,
        price_store=store,
        model_id="V5-FUND-EXP-002",
        score_column="v5_exp_002_score",
        config=BacktestConfig(
            weekly_contribution=10.0,
            top_n=10,
            selection_flag="v5_exp_002_top_conviction_eligible",
            max_addon_position_weight=0.10,
        ),
        start=start,
        end=end,
    )

    v1_path = reconstruct_holding_path(v1.trades, store=store, weekly=v1.weekly)
    candidate_path = reconstruct_holding_path(
        candidate.trades,
        store=store,
        weekly=candidate.weekly,
    )

    v1_max = v1_path.loc[v1_path["largest_position_weight"].idxmax()]
    candidate_max = candidate_path.loc[
        candidate_path["largest_position_weight"].idxmax()
    ]

    v1_terminal = float(v1.summary["terminal_value"])
    candidate_terminal = float(candidate.summary["terminal_value"])
    v1_xirr = float(v1.summary["xirr"])
    candidate_xirr = float(candidate.summary["xirr"])

    payload = {
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "research_only": True,
        "config_sha256": canonical_json_sha256(config),
        "dataset_sha256": dataset_sha,
        "modifier_trigger_rows": int(
            scored["v5_exp_002_modifier_triggered"].sum()
        ),
        "v1": {
            "terminal_value": v1_terminal,
            "xirr": v1_xirr,
            "max_drawdown": float(v1.summary["max_drawdown"]),
            "max_largest_position_weight": float(
                v1_max["largest_position_weight"]
            ),
            "max_largest_position_ticker": str(
                v1_max["largest_position_ticker"]
            ),
        },
        "candidate": {
            "terminal_value": candidate_terminal,
            "xirr": candidate_xirr,
            "max_drawdown": float(candidate.summary["max_drawdown"]),
            "max_largest_position_weight": float(
                candidate_max["largest_position_weight"]
            ),
            "max_largest_position_ticker": str(
                candidate_max["largest_position_ticker"]
            ),
        },
        "delta": {
            "terminal_value_dollars": candidate_terminal - v1_terminal,
            "terminal_value_pct": candidate_terminal / v1_terminal - 1.0,
            "xirr": candidate_xirr - v1_xirr,
            "max_drawdown": (
                float(candidate.summary["max_drawdown"])
                - float(v1.summary["max_drawdown"])
            ),
            "max_largest_position_weight": (
                float(candidate_max["largest_position_weight"])
                - float(v1_max["largest_position_weight"])
            ),
        },
        "screen_only": True,
        "frozen_candidate_gate_decision": "NOT_RUN",
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V5 #47 FUNDAMENTAL EXPLORATORY SCREEN 002")
    print("Experiment:                 " + config["experiment_id"])
    print("Modifier-trigger rows:      {:,}".format(
        payload["modifier_trigger_rows"]
    ))
    print("Terminal delta:             USD {:+,.2f} ({:+.2%})".format(
        payload["delta"]["terminal_value_dollars"],
        payload["delta"]["terminal_value_pct"],
    ))
    print("XIRR delta:                 {:+.4%}".format(
        payload["delta"]["xirr"]
    ))
    print("Max DD delta:               {:+.2%}".format(
        payload["delta"]["max_drawdown"]
    ))
    print("Largest-position delta:     {:+.2%}".format(
        payload["delta"]["max_largest_position_weight"]
    ))
    print("V1 max concentration:       {} {:.2%}".format(
        payload["v1"]["max_largest_position_ticker"],
        payload["v1"]["max_largest_position_weight"],
    ))
    print("EXP-002 max concentration:  {} {:.2%}".format(
        payload["candidate"]["max_largest_position_ticker"],
        payload["candidate"]["max_largest_position_weight"],
    ))
    print("Frozen gate decision:       NOT RUN (exploratory screen only)")
    print("Output:                     " + str(args.output))


if __name__ == "__main__":
    main()
