from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


HORIZONS = (4, 13, 26, 52)
SIGNALS = ("momentum", "stability")


def interaction_severity(
    growth: pd.Series,
    valuation: pd.Series,
    neutral: float,
) -> pd.Series:
    growth_pressure = ((growth - neutral) / (100.0 - neutral)).clip(0.0, 1.0)
    valuation_weakness = ((neutral - valuation) / neutral).clip(0.0, 1.0)
    return growth_pressure * valuation_weakness


def add_cand003_score(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
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
    count = pd.Series(0, index=result.index, dtype="int64")

    for idx in result.index:
        weights = {
            "quality": float(base["quality"]),
            "financial_health": float(base["financial_health"]),
            "growth": float(growth_weight.loc[idx]),
            "valuation": float(valuation_weight.loc[idx]),
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


def add_cand003_rank(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["cand003_rank"] = np.nan
    for _, group in result.groupby("decision_date", sort=True):
        eligible = group.loc[
            group["cand003_top_conviction_eligible"].fillna(False).astype(bool)
            & pd.to_numeric(group["cand003_score"], errors="coerce").notna()
        ].copy()
        eligible["cand003_score"] = pd.to_numeric(
            eligible["cand003_score"], errors="coerce"
        )
        eligible = eligible.sort_values(
            ["cand003_score", "ticker"],
            ascending=[False, True],
            kind="mergesort",
        )
        result.loc[eligible.index, "cand003_rank"] = np.arange(1, len(eligible) + 1)
    return result


def add_cross_sectional_quintiles(frame: pd.DataFrame, signal: str) -> pd.DataFrame:
    result = frame.copy()
    score_col = signal + "_score"
    quintile_col = signal + "_quintile"
    result[quintile_col] = np.nan

    for _, group in result.groupby("decision_date", sort=True):
        values = pd.to_numeric(group[score_col], errors="coerce")
        valid = group.loc[values.notna()].copy()
        if valid.empty:
            continue
        ranked = pd.to_numeric(valid[score_col], errors="coerce").rank(
            method="first",
            pct=True,
        )
        result.loc[valid.index, quintile_col] = np.ceil(ranked * 5).clip(1, 5)
    return result


def summarize_selected_quintiles(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    selected = frame.loc[frame["cand003_rank"].between(1, 10)].copy()

    for signal in SIGNALS:
        qcol = signal + "_quintile"
        for horizon in HORIZONS:
            status = "fwd_{}w_status".format(horizon)
            excess = "fwd_{}w_excess_return".format(horizon)
            mature = selected.loc[
                selected[status].astype(str).eq("mature")
                & pd.to_numeric(selected[excess], errors="coerce").notna()
                & pd.to_numeric(selected[qcol], errors="coerce").notna()
            ].copy()
            mature["_excess"] = pd.to_numeric(mature[excess], errors="coerce")
            for quintile, group in mature.groupby(qcol):
                rows.append({
                    "signal": signal,
                    "horizon_weeks": horizon,
                    "quintile": int(quintile),
                    "rows": int(len(group)),
                    "mean_excess_return": float(group["_excess"].mean()),
                    "median_excess_return": float(group["_excess"].median()),
                    "positive_excess_rate": float(group["_excess"].gt(0).mean()),
                })
    return pd.DataFrame(rows)


def summarize_boundary_confirmation(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    top10 = frame.loc[frame["cand003_rank"].between(1, 10)].copy()
    near = frame.loc[frame["cand003_rank"].between(11, 25)].copy()

    for signal in SIGNALS:
        qcol = signal + "_quintile"
        weak_selected = top10.loc[pd.to_numeric(top10[qcol], errors="coerce").eq(1)]
        strong_near = near.loc[pd.to_numeric(near[qcol], errors="coerce").eq(5)]

        for horizon in HORIZONS:
            status = "fwd_{}w_status".format(horizon)
            excess = "fwd_{}w_excess_return".format(horizon)

            weak = weak_selected.loc[
                weak_selected[status].astype(str).eq("mature")
                & pd.to_numeric(weak_selected[excess], errors="coerce").notna()
            ].copy()
            strong = strong_near.loc[
                strong_near[status].astype(str).eq("mature")
                & pd.to_numeric(strong_near[excess], errors="coerce").notna()
            ].copy()
            weak_ret = pd.to_numeric(weak[excess], errors="coerce")
            strong_ret = pd.to_numeric(strong[excess], errors="coerce")

            rows.append({
                "signal": signal,
                "horizon_weeks": horizon,
                "weak_top10_rows": int(len(weak)),
                "strong_rank11_25_rows": int(len(strong)),
                "weak_top10_mean_excess": float(weak_ret.mean()) if len(weak) else np.nan,
                "strong_rank11_25_mean_excess": float(strong_ret.mean()) if len(strong) else np.nan,
                "strong_minus_weak_mean_excess": (
                    float(strong_ret.mean() - weak_ret.mean())
                    if len(weak) and len(strong)
                    else np.nan
                ),
                "weak_top10_positive_rate": float(weak_ret.gt(0).mean()) if len(weak) else np.nan,
                "strong_rank11_25_positive_rate": float(strong_ret.gt(0).mean()) if len(strong) else np.nan,
            })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose Momentum/Stability confirmation value around CAND-003 selection boundary."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("reports/v5/attribution_dataset/v5_historical_attribution_dataset.csv"),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/v5_fund_cand_003.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/v5/confirmation_risk/diagnostic"),
    )
    args = parser.parse_args()

    frame = pd.read_csv(args.dataset, low_memory=False)
    config = json.loads(args.config.read_text(encoding="utf-8"))

    required = {
        "decision_date", "ticker",
        "quality_score", "financial_health_score",
        "growth_score", "valuation_score",
        "momentum_score", "stability_score",
    }
    for horizon in HORIZONS:
        required.add("fwd_{}w_status".format(horizon))
        required.add("fwd_{}w_excess_return".format(horizon))
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit("Missing columns: " + ", ".join(missing))

    scored = add_cand003_rank(add_cand003_score(frame, config))
    for signal in SIGNALS:
        scored = add_cross_sectional_quintiles(scored, signal)

    selected = summarize_selected_quintiles(scored)
    boundary = summarize_boundary_confirmation(scored)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    selected.to_csv(args.output_dir / "cand003_selected_signal_quintiles.csv", index=False)
    boundary.to_csv(args.output_dir / "cand003_boundary_confirmation.csv", index=False)

    payload = {
        "schema_version": 1,
        "hypothesis_generation_only": True,
        "candidate": "V5-FUND-CAND-003",
        "signals": list(SIGNALS),
        "horizons_weeks": list(HORIZONS),
        "thresholds": {
            "weak_selected": "cross-sectional bottom quintile",
            "strong_near_miss": "cross-sectional top quintile among ranks 11-25",
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V5 #48 CAND-003 CONFIRMATION/RISK DIAGNOSTIC")
    print("Hypothesis generation only: YES")
    print("Candidate:                  V5-FUND-CAND-003")
    print("BOUNDARY CONFIRMATION: strong rank 11-25 minus weak Top-10")
    for row in boundary.itertuples(index=False):
        print(
            "  {:9s} {:2d}w {:+.4%}  weak_n={} strong_n={}".format(
                row.signal,
                int(row.horizon_weeks),
                row.strong_minus_weak_mean_excess,
                int(row.weak_top10_rows),
                int(row.strong_rank11_25_rows),
            )
        )

    print("TOP-10 QUINTILE SPREADS: Q5 minus Q1")
    for signal in SIGNALS:
        group = selected.loc[selected["signal"].eq(signal)]
        for horizon in HORIZONS:
            h = group.loc[group["horizon_weeks"].eq(horizon)]
            q1 = h.loc[h["quintile"].eq(1), "mean_excess_return"]
            q5 = h.loc[h["quintile"].eq(5), "mean_excess_return"]
            if q1.empty or q5.empty:
                continue
            print(
                "  {:9s} {:2d}w {:+.4%}".format(
                    signal,
                    horizon,
                    float(q5.iloc[0] - q1.iloc[0]),
                )
            )
    print("Output:                     " + str(args.output_dir))


if __name__ == "__main__":
    main()
