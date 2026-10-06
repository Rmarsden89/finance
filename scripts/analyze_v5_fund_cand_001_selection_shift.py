from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd



def add_candidate_score(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    result = frame.copy()
    weights = config["family_weights"]

    if abs(sum(float(value) for value in weights.values()) - 1.0) > 1e-12:
        raise ValueError("Candidate family weights must sum to 1.0")

    weighted = pd.Series(0.0, index=result.index, dtype="float64")
    available_weight = pd.Series(0.0, index=result.index, dtype="float64")
    available_count = pd.Series(0, index=result.index, dtype="int64")

    for family, weight in weights.items():
        column = family + "_score"
        if column not in result.columns:
            raise ValueError("Missing family score column: " + column)

        values = pd.to_numeric(result[column], errors="coerce")
        available = values.notna()
        weighted.loc[available] += values.loc[available] * float(weight)
        available_weight.loc[available] += float(weight)
        available_count.loc[available] += 1

    eligible = (
        (available_count >= int(config["minimum_families"]))
        & available_weight.gt(0)
    )

    score = pd.Series(float("nan"), index=result.index, dtype="float64")
    score.loc[eligible] = (
        weighted.loc[eligible] / available_weight.loc[eligible]
    )

    result["v5_candidate_score"] = score
    result["v5_candidate_family_count"] = available_count
    result["v5_candidate_weight_coverage"] = available_weight
    result["v5_candidate_eligible"] = eligible

    if bool(config["top_conviction_requires_full_family_coverage"]):
        result["v5_candidate_top_conviction_eligible"] = (
            score.notna() & available_count.eq(len(weights))
        )
    else:
        result["v5_candidate_top_conviction_eligible"] = (
            score.notna() & eligible
        )

    return result


def rank_frame(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["decision_date"] = pd.to_datetime(work["decision_date"], errors="raise")
    work["ticker"] = work["ticker"].astype(str).str.upper()

    rows = []
    for decision_date, group in work.groupby("decision_date", sort=True):
        g = group.copy()

        v1 = g.loc[
            g["top_conviction_eligible"].fillna(False).astype(bool)
            & pd.to_numeric(g["long_growth_v1_score"], errors="coerce").notna()
        ].copy()
        v1["long_growth_v1_score"] = pd.to_numeric(
            v1["long_growth_v1_score"], errors="coerce"
        )
        v1 = v1.sort_values(
            ["long_growth_v1_score", "ticker"],
            ascending=[False, True],
            kind="mergesort",
        )
        v1_rank = {
            ticker: rank
            for rank, ticker in enumerate(v1["ticker"].tolist(), start=1)
        }

        cand = g.loc[
            g["v5_candidate_top_conviction_eligible"].fillna(False).astype(bool)
            & pd.to_numeric(g["v5_candidate_score"], errors="coerce").notna()
        ].copy()
        cand["v5_candidate_score"] = pd.to_numeric(
            cand["v5_candidate_score"], errors="coerce"
        )
        cand = cand.sort_values(
            ["v5_candidate_score", "ticker"],
            ascending=[False, True],
            kind="mergesort",
        )
        cand_rank = {
            ticker: rank
            for rank, ticker in enumerate(cand["ticker"].tolist(), start=1)
        }

        for row in g.itertuples(index=False):
            ticker = str(row.ticker).upper()
            growth = pd.to_numeric(pd.Series([getattr(row, "growth_score")]), errors="coerce").iloc[0]
            valuation = pd.to_numeric(pd.Series([getattr(row, "valuation_score")]), errors="coerce").iloc[0]
            v1_score = pd.to_numeric(pd.Series([getattr(row, "long_growth_v1_score")]), errors="coerce").iloc[0]
            cand_score = pd.to_numeric(pd.Series([getattr(row, "v5_candidate_score")]), errors="coerce").iloc[0]
            rows.append({
                "decision_date": decision_date.date().isoformat(),
                "ticker": ticker,
                "v1_rank": v1_rank.get(ticker),
                "candidate_rank": cand_rank.get(ticker),
                "v1_top10": ticker in set(v1.head(10)["ticker"]),
                "candidate_top10": ticker in set(cand.head(10)["ticker"]),
                "growth_score": growth,
                "valuation_score": valuation,
                "growth_minus_valuation": (
                    float(growth - valuation)
                    if pd.notna(growth) and pd.notna(valuation)
                    else float("nan")
                ),
                "v1_score": v1_score,
                "candidate_score": cand_score,
                "candidate_minus_v1_score": (
                    float(cand_score - v1_score)
                    if pd.notna(cand_score) and pd.notna(v1_score)
                    else float("nan")
                ),
            })

    return pd.DataFrame(rows)


def summarize_tickers(ranked: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ticker, group in ranked.groupby("ticker", sort=True):
        v1_weeks = int(group["v1_top10"].sum())
        candidate_weeks = int(group["candidate_top10"].sum())
        candidate_only = group.loc[
            group["candidate_top10"] & ~group["v1_top10"]
        ]
        v1_only = group.loc[
            group["v1_top10"] & ~group["candidate_top10"]
        ]
        rows.append({
            "ticker": ticker,
            "v1_top10_weeks": v1_weeks,
            "candidate_top10_weeks": candidate_weeks,
            "top10_week_delta": candidate_weeks - v1_weeks,
            "candidate_only_weeks": int(len(candidate_only)),
            "v1_only_weeks": int(len(v1_only)),
            "mean_growth_minus_valuation_all_dates": float(
                pd.to_numeric(
                    group["growth_minus_valuation"], errors="coerce"
                ).mean()
            ),
            "mean_score_boost_all_dates": float(
                pd.to_numeric(
                    group["candidate_minus_v1_score"], errors="coerce"
                ).mean()
            ),
            "candidate_only_mean_growth_minus_valuation": float(
                pd.to_numeric(
                    candidate_only["growth_minus_valuation"], errors="coerce"
                ).mean()
            ) if not candidate_only.empty else float("nan"),
            "candidate_only_mean_v1_rank": float(
                pd.to_numeric(candidate_only["v1_rank"], errors="coerce").mean()
            ) if not candidate_only.empty else float("nan"),
            "candidate_only_mean_candidate_rank": float(
                pd.to_numeric(
                    candidate_only["candidate_rank"], errors="coerce"
                ).mean()
            ) if not candidate_only.empty else float("nan"),
        })

    return pd.DataFrame(rows).sort_values(
        ["top10_week_delta", "candidate_only_weeks", "ticker"],
        ascending=[False, False, True],
    ).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose selection-frequency shifts for V5-FUND-CAND-001."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/v5_fund_cand_001.json"),
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
        "--output-dir",
        type=Path,
        default=Path(
            "reports/v5/fundamental/V5-FUND-CAND-001/"
            "selection_shift"
        ),
    )
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    frame = pd.read_csv(args.dataset, low_memory=False)
    scored = add_candidate_score(frame, config)
    ranked = rank_frame(scored)
    summary = summarize_tickers(ranked)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ranked.to_csv(args.output_dir / "ticker_date_rank_detail.csv", index=False)
    summary.to_csv(args.output_dir / "ticker_selection_shift_summary.csv", index=False)

    nvda = summary.loc[summary["ticker"].eq("NVDA")]
    print("V5 #47 CAND-001 SELECTION-SHIFT DIAGNOSTIC")
    print("Research only:              YES")
    if not nvda.empty:
        row = nvda.iloc[0]
        print("NVDA Top-10 weeks V1/Cand:  {}/{}".format(
            int(row["v1_top10_weeks"]),
            int(row["candidate_top10_weeks"]),
        ))
        print("NVDA candidate-only weeks:  {}".format(
            int(row["candidate_only_weeks"])
        ))
        print("NVDA V1-only weeks:         {}".format(
            int(row["v1_only_weeks"])
        ))
        print("NVDA mean Growth-Valuation: {:+.2f}".format(
            row["mean_growth_minus_valuation_all_dates"]
        ))
        print("NVDA mean score boost:      {:+.2f}".format(
            row["mean_score_boost_all_dates"]
        ))
        print("NVDA cand-only G-V spread:  {:+.2f}".format(
            row["candidate_only_mean_growth_minus_valuation"]
        ))
        print("NVDA cand-only rank V1/Cand:{:.2f}/{:.2f}".format(
            row["candidate_only_mean_v1_rank"],
            row["candidate_only_mean_candidate_rank"],
        ))

    print("TOP POSITIVE SELECTION SHIFTS")
    for row in summary.head(10).itertuples(index=False):
        print("  {:6s} weeks={:+4d} cand_only={:3d} G-V={:+7.2f} boost={:+6.2f}".format(
            row.ticker,
            int(row.top10_week_delta),
            int(row.candidate_only_weeks),
            row.mean_growth_minus_valuation_all_dates,
            row.mean_score_boost_all_dates,
        ))
    print("Output:                     " + str(args.output_dir))


if __name__ == "__main__":
    main()
