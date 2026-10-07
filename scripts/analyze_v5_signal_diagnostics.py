from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


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


REGIMES = (
    ("2016-2019", 2016, 2019),
    ("2020-2022", 2020, 2022),
    ("2023-2025", 2023, 2025),
)


def momentum_regime_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    selected = frame.loc[frame["cand003_rank"].between(1, 10)].copy()
    selected["year"] = pd.to_datetime(selected["decision_date"]).dt.year

    for label, start, end in REGIMES:
        regime = selected.loc[selected["year"].between(start, end)].copy()
        for horizon in (13, 26):
            status = f"fwd_{horizon}w_status"
            excess = f"fwd_{horizon}w_excess_return"
            mature = regime.loc[
                regime[status].astype(str).eq("mature")
                & pd.to_numeric(regime[excess], errors="coerce").notna()
                & pd.to_numeric(regime["momentum_quintile"], errors="coerce").notna()
            ].copy()
            mature["_excess"] = pd.to_numeric(mature[excess], errors="coerce")
            q1 = mature.loc[mature["momentum_quintile"].eq(1), "_excess"]
            q5 = mature.loc[mature["momentum_quintile"].eq(5), "_excess"]
            rows.append({
                "regime": label,
                "horizon_weeks": horizon,
                "q1_rows": int(len(q1)),
                "q5_rows": int(len(q5)),
                "q1_mean_excess": float(q1.mean()) if len(q1) else np.nan,
                "q5_mean_excess": float(q5.mean()) if len(q5) else np.nan,
                "q5_minus_q1_mean_excess": (
                    float(q5.mean() - q1.mean()) if len(q1) and len(q5) else np.nan
                ),
            })
    return pd.DataFrame(rows)


def stability_tail_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    selected = frame.loc[frame["cand003_rank"].between(1, 10)].copy()

    for horizon in (4, 13, 26, 52):
        status = f"fwd_{horizon}w_status"
        excess = f"fwd_{horizon}w_excess_return"
        mature = selected.loc[
            selected[status].astype(str).eq("mature")
            & pd.to_numeric(selected[excess], errors="coerce").notna()
            & pd.to_numeric(selected["stability_quintile"], errors="coerce").notna()
        ].copy()
        mature["_excess"] = pd.to_numeric(mature[excess], errors="coerce")
        for quintile in (1, 5):
            group = mature.loc[mature["stability_quintile"].eq(quintile), "_excess"]
            rows.append({
                "horizon_weeks": horizon,
                "stability_quintile": quintile,
                "rows": int(len(group)),
                "mean_excess_return": float(group.mean()) if len(group) else np.nan,
                "p10_excess_return": float(group.quantile(0.10)) if len(group) else np.nan,
                "negative_excess_rate": float(group.lt(0).mean()) if len(group) else np.nan,
            })
    return pd.DataFrame(rows)


def momentum_flip_summary(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.loc[
        frame["cand003_rank"].between(1, 25)
        & pd.to_numeric(frame["momentum_quintile"], errors="coerce").notna()
    ].copy()
    work["decision_date"] = pd.to_datetime(work["decision_date"])
    work = work.sort_values(["ticker", "decision_date"])

    rows = []
    for ticker, group in work.groupby("ticker", sort=True):
        quintiles = pd.to_numeric(group["momentum_quintile"], errors="coerce")
        states = quintiles.map(lambda q: "weak" if q == 1 else ("strong" if q == 5 else "middle"))
        previous = states.shift(1)
        flips = ((states.eq("weak") & previous.eq("strong")) | (states.eq("strong") & previous.eq("weak")))
        rows.append({
            "ticker": ticker,
            "observations": int(len(group)),
            "weak_weeks": int(states.eq("weak").sum()),
            "strong_weeks": int(states.eq("strong").sum()),
            "direct_weak_strong_flips": int(flips.sum()),
        })
    detail = pd.DataFrame(rows)
    return pd.DataFrame([{
        "tickers": int(len(detail)),
        "observations": int(detail["observations"].sum()),
        "weak_weeks": int(detail["weak_weeks"].sum()),
        "strong_weeks": int(detail["strong_weeks"].sum()),
        "direct_weak_strong_flips": int(detail["direct_weak_strong_flips"].sum()),
        "flip_rate_per_observation": (
            float(detail["direct_weak_strong_flips"].sum() / detail["observations"].sum())
            if detail["observations"].sum()
            else np.nan
        ),
    }])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze #48 Momentum regime/persistence and Stability downside tails."
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
        default=Path("reports/v5/confirmation_risk/signal_diagnostics"),
    )
    args = parser.parse_args()

    import json
    config = json.loads(args.config.read_text(encoding="utf-8"))
    frame = pd.read_csv(args.dataset, low_memory=False)
    scored = add_cand003_rank(add_cand003_score(frame, config))
    scored = add_cross_sectional_quintiles(scored, "momentum")
    scored = add_cross_sectional_quintiles(scored, "stability")

    momentum_regime = momentum_regime_summary(scored)
    stability_tail = stability_tail_summary(scored)
    momentum_flips = momentum_flip_summary(scored)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    momentum_regime.to_csv(args.output_dir / "momentum_regime_summary.csv", index=False)
    stability_tail.to_csv(args.output_dir / "stability_tail_summary.csv", index=False)
    momentum_flips.to_csv(args.output_dir / "momentum_flip_summary.csv", index=False)

    print("V5 #48 SIGNAL DIAGNOSTICS")
    print("MOMENTUM Q5-Q1 BY REGIME")
    for row in momentum_regime.itertuples(index=False):
        print(
            "  {:9s} {:2d}w {:+.4%} q1_n={} q5_n={}".format(
                row.regime,
                int(row.horizon_weeks),
                row.q5_minus_q1_mean_excess,
                int(row.q1_rows),
                int(row.q5_rows),
            )
        )

    print("STABILITY TAIL: Q5 minus Q1")
    for horizon in (4, 13, 26, 52):
        h = stability_tail.loc[stability_tail["horizon_weeks"].eq(horizon)]
        q1 = h.loc[h["stability_quintile"].eq(1)].iloc[0]
        q5 = h.loc[h["stability_quintile"].eq(5)].iloc[0]
        print(
            "  {:2d}w mean={:+.4%} p10={:+.4%} neg_rate={:+.2%}".format(
                horizon,
                q5.mean_excess_return - q1.mean_excess_return,
                q5.p10_excess_return - q1.p10_excess_return,
                q5.negative_excess_rate - q1.negative_excess_rate,
            )
        )

    row = momentum_flips.iloc[0]
    print("MOMENTUM DIRECT Q1<->Q5 FLIPS")
    print(
        "  flips={} observations={} rate={:.4%}".format(
            int(row["direct_weak_strong_flips"]),
            int(row["observations"]),
            float(row["flip_rate_per_observation"]),
        )
    )
    print("Output:                     " + str(args.output_dir))


if __name__ == "__main__":
    main()
