from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd

from finance.backtest import BacktestConfig, BacktestPriceStore, run_ranked_accumulation_backtest

EXPECTED_DATASET_SHA256 = "36a30bc1115c1496ee34efa0dfe2ba4e030b927e68e4d018c882d4b97831e0dc"
EXPECTED_PROTOCOL_SHA256 = "86624fd115f1f4adf51d6c4877fec08bae2bd04330928e3b73933252de34709c"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def add_candidate_score(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    result = frame.copy()
    weights = config["family_weights"]
    if abs(sum(float(v) for v in weights.values()) - 1.0) > 1e-12:
        raise ValueError("Candidate family weights must sum to 1.0")

    weighted = pd.Series(0.0, index=result.index, dtype="float64")
    available_weight = pd.Series(0.0, index=result.index, dtype="float64")
    available_count = pd.Series(0, index=result.index, dtype="int64")

    for family, weight in weights.items():
        column = family + "_score"
        values = pd.to_numeric(result[column], errors="coerce")
        available = values.notna()
        weighted.loc[available] += values.loc[available] * float(weight)
        available_weight.loc[available] += float(weight)
        available_count.loc[available] += 1

    eligible = (available_count >= int(config["minimum_families"])) & available_weight.gt(0)
    score = pd.Series(float("nan"), index=result.index, dtype="float64")
    score.loc[eligible] = weighted.loc[eligible] / available_weight.loc[eligible]

    result["v5_candidate_score"] = score
    result["v5_candidate_family_count"] = available_count
    result["v5_candidate_weight_coverage"] = available_weight
    result["v5_candidate_eligible"] = eligible
    result["v5_candidate_top_conviction_eligible"] = (
        score.notna() & available_count.eq(len(weights))
        if bool(config["top_conviction_requires_full_family_coverage"])
        else score.notna() & eligible
    )
    return result


def top10_overlap(frame: pd.DataFrame) -> dict:
    overlaps = []
    for _, group in frame.groupby("decision_date", sort=True):
        v1 = group.loc[
            group["top_conviction_eligible"].astype(bool)
            & group["long_growth_v1_score"].notna()
        ].sort_values(["long_growth_v1_score", "ticker"], ascending=[False, True]).head(10)

        candidate = group.loc[
            group["v5_candidate_top_conviction_eligible"].astype(bool)
            & group["v5_candidate_score"].notna()
        ].sort_values(["v5_candidate_score", "ticker"], ascending=[False, True]).head(10)

        if len(v1) == 10 and len(candidate) == 10:
            overlaps.append(len(set(v1["ticker"]) & set(candidate["ticker"])))

    series = pd.Series(overlaps, dtype="float64")
    return {
        "comparable_dates": len(overlaps),
        "mean_top10_overlap": float(series.mean()) if overlaps else None,
        "median_top10_overlap": float(series.median()) if overlaps else None,
        "mean_replacement_rate": float(((10.0 - series) / 10.0).mean()) if overlaps else None,
        "minimum_top10_overlap": int(min(overlaps)) if overlaps else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run V5 fundamental exploratory experiment 001.")
    parser.add_argument("--config", type=Path, default=Path("config/v5_fund_exp_001.json"))
    parser.add_argument("--dataset", type=Path, default=Path("reports/v5/attribution_dataset/v5_historical_attribution_dataset.csv"))
    parser.add_argument("--prices", type=Path, default=Path("data/market/daily_prices.csv.gz"))
    parser.add_argument("--output", type=Path, default=Path("reports/v5/fundamental/V5-FUND-EXP-001/exploratory_result.json"))
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    if config.get("experiment_id") != "V5-FUND-EXP-001" or config.get("status") != "exploratory":
        raise SystemExit("Unexpected experiment config.")
    if config.get("research_only") is not True:
        raise SystemExit("Experiment must be research-only.")
    if any((config.get("execution_capabilities") or {}).values()):
        raise SystemExit("Execution capabilities must all be false.")

    dataset_sha = sha256_file(args.dataset)
    if dataset_sha != EXPECTED_DATASET_SHA256:
        raise SystemExit("Attribution dataset fingerprint mismatch: " + dataset_sha)

    frame = pd.read_csv(args.dataset, low_memory=False)
    duplicate_keys = int(frame[["decision_date", "ticker"]].duplicated().sum())
    if duplicate_keys:
        raise SystemExit("Duplicate decision_date/ticker keys: " + str(duplicate_keys))

    scored = add_candidate_score(frame, config)
    price_store = BacktestPriceStore(args.prices)

    common_start = date(2016, 1, 1)
    common_end = date(2025, 12, 31)

    v1 = run_ranked_accumulation_backtest(
        frame,
        price_store=price_store,
        model_id="long_growth_v1",
        score_column="long_growth_v1_score",
        config=BacktestConfig(
            weekly_contribution=10.0,
            top_n=10,
            selection_flag="top_conviction_eligible",
            max_addon_position_weight=0.10,
        ),
        start=common_start,
        end=common_end,
    )

    candidate = run_ranked_accumulation_backtest(
        scored,
        price_store=price_store,
        model_id=config["experiment_id"],
        score_column="v5_candidate_score",
        config=BacktestConfig(
            weekly_contribution=10.0,
            top_n=10,
            selection_flag="v5_candidate_top_conviction_eligible",
            max_addon_position_weight=0.10,
        ),
        start=common_start,
        end=common_end,
    )

    v1_terminal = float(v1.summary["terminal_value"])
    candidate_terminal = float(candidate.summary["terminal_value"])
    v1_xirr = float(v1.summary["xirr"])
    candidate_xirr = float(candidate.summary["xirr"])
    overlap = top10_overlap(scored)

    payload = {
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "status": "exploratory_result",
        "research_only": True,
        "config_sha256": canonical_json_sha256(config),
        "dataset_sha256": dataset_sha,
        "protocol_sha256": EXPECTED_PROTOCOL_SHA256,
        "duplicate_keys": duplicate_keys,
        "v1": {
            "terminal_value": v1_terminal,
            "xirr": v1_xirr,
            "max_drawdown": float(v1.summary["max_drawdown"]),
        },
        "candidate": {
            "terminal_value": candidate_terminal,
            "xirr": candidate_xirr,
            "max_drawdown": float(candidate.summary["max_drawdown"]),
        },
        "delta": {
            "terminal_value_dollars": candidate_terminal - v1_terminal,
            "terminal_value_pct": candidate_terminal / v1_terminal - 1.0,
            "xirr": candidate_xirr - v1_xirr,
            "max_drawdown": float(candidate.summary["max_drawdown"]) - float(v1.summary["max_drawdown"]),
        },
        "selection_effect": overlap,
        "screen_only": True,
        "frozen_candidate_gate_decision": "NOT_RUN",
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("V5 #47 FUNDAMENTAL EXPLORATORY SCREEN")
    print("Experiment:                 " + config["experiment_id"])
    print("Research only:              YES")
    print("Config SHA-256:             " + payload["config_sha256"])
    print("V1 terminal / XIRR:         USD {:,.2f} / {:.4%}".format(v1_terminal, v1_xirr))
    print("Candidate terminal / XIRR:  USD {:,.2f} / {:.4%}".format(candidate_terminal, candidate_xirr))
    print("Terminal delta:             USD {:+,.2f} ({:+.2%})".format(payload["delta"]["terminal_value_dollars"], payload["delta"]["terminal_value_pct"]))
    print("XIRR delta:                 {:+.4%}".format(payload["delta"]["xirr"]))
    print("Max DD delta:               {:+.2%}".format(payload["delta"]["max_drawdown"]))
    print("Mean Top-10 overlap:        {:.2f}/10".format(overlap["mean_top10_overlap"]))
    print("Mean replacement rate:      {:.2%}".format(overlap["mean_replacement_rate"]))
    print("Minimum Top-10 overlap:     {}/10".format(overlap["minimum_top10_overlap"]))
    print("Frozen gate decision:       NOT RUN (exploratory screen only)")
    print("Output:                     " + str(args.output))


if __name__ == "__main__":
    main()
