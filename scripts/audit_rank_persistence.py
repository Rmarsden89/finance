from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit Top-N rank persistence, turnover, and buy-set durability "
            "for frozen long_growth_v1."
        )
    )
    parser.add_argument("--long-growth", type=Path, required=True)
    parser.add_argument(
        "--trades",
        type=Path,
        required=False,
        help=(
            "Optional appreciation-cap trade log. If supplied, purchase "
            "activity is joined to rank persistence."
        ),
    )
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--start-year", type=int, default=2016)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--selection-flag",
        default="top_conviction_eligible",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/rank_persistence_v1"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    frame = pd.read_csv(args.long_growth, low_memory=False)
    frame["decision_date"] = pd.to_datetime(
        frame["decision_date"],
        errors="coerce",
    )
    frame["long_growth_v1_score"] = pd.to_numeric(
        frame["long_growth_v1_score"],
        errors="coerce",
    )

    if args.selection_flag not in frame.columns:
        raise ValueError(
            "Missing selection flag: " + args.selection_flag
        )

    frame = frame.loc[
        frame["decision_date"].notna()
        & frame["decision_date"].dt.year.between(
            args.start_year,
            args.end_year,
        )
        & frame[args.selection_flag].fillna(False).astype(bool)
        & frame["long_growth_v1_score"].notna()
    ].copy()

    print("Building weekly Top-" + str(args.top_n) + " sets...", flush=True)

    weekly_rank_rows = []
    weekly_sets: dict[pd.Timestamp, list[str]] = {}

    for decision_date, group in frame.groupby("decision_date", sort=True):
        ranked = group.sort_values(
            "long_growth_v1_score",
            ascending=False,
        ).head(args.top_n)

        tickers = ranked["ticker"].astype(str).str.upper().tolist()
        weekly_sets[decision_date] = tickers

        for rank, row in enumerate(
            ranked.itertuples(index=False),
            start=1,
        ):
            weekly_rank_rows.append({
                "decision_date": decision_date,
                "ticker": str(row.ticker).upper(),
                "rank": rank,
                "score": float(row.long_growth_v1_score),
            })

    weekly_ranks = pd.DataFrame(weekly_rank_rows)
    dates = sorted(weekly_sets)

    print(
        "Calculating week-to-week turnover across "
        + str(len(dates))
        + " decision weeks...",
        flush=True,
    )

    turnover_rows = []

    for index, day in enumerate(dates):
        current = weekly_sets[day]
        current_set = set(current)

        if index == 0:
            turnover_rows.append({
                "decision_date": day,
                "prior_decision_date": pd.NaT,
                "overlap_count": pd.NA,
                "overlap_pct": pd.NA,
                "entrants_count": pd.NA,
                "exits_count": pd.NA,
                "replacement_rate": pd.NA,
                "rank_weighted_turnover": pd.NA,
            })
            continue

        prior_day = dates[index - 1]
        prior = weekly_sets[prior_day]
        prior_set = set(prior)

        overlap = current_set & prior_set
        entrants = current_set - prior_set
        exits = prior_set - current_set

        prior_rank = {ticker: rank for rank, ticker in enumerate(prior, start=1)}
        current_rank = {
            ticker: rank for rank, ticker in enumerate(current, start=1)
        }

        rank_movement = []
        for ticker in overlap:
            rank_movement.append(
                abs(current_rank[ticker] - prior_rank[ticker])
            )

        turnover_rows.append({
            "decision_date": day,
            "prior_decision_date": prior_day,
            "overlap_count": len(overlap),
            "overlap_pct": len(overlap) / args.top_n,
            "entrants_count": len(entrants),
            "exits_count": len(exits),
            "replacement_rate": len(entrants) / args.top_n,
            "rank_weighted_turnover": (
                sum(rank_movement) / len(rank_movement)
                if rank_movement
                else 0.0
            ),
        })

    turnover = pd.DataFrame(turnover_rows)

    print("Calculating continuous Top-N spells...", flush=True)

    appearances = defaultdict(list)
    for index, day in enumerate(dates):
        for ticker in weekly_sets[day]:
            appearances[ticker].append(index)

    spell_rows = []
    ticker_summary_rows = []

    for ticker, indices in appearances.items():
        runs = []
        run_start = indices[0]
        prior = indices[0]

        for idx in indices[1:]:
            if idx == prior + 1:
                prior = idx
                continue

            runs.append((run_start, prior))
            run_start = idx
            prior = idx

        runs.append((run_start, prior))

        for spell_number, (start_idx, end_idx) in enumerate(runs, start=1):
            spell_rows.append({
                "ticker": ticker,
                "spell_number": spell_number,
                "start_date": dates[start_idx],
                "end_date": dates[end_idx],
                "consecutive_weeks": end_idx - start_idx + 1,
            })

        ticker_summary_rows.append({
            "ticker": ticker,
            "total_topn_weeks": len(indices),
            "appearance_share": len(indices) / len(dates),
            "spell_count": len(runs),
            "reentry_count": max(0, len(runs) - 1),
            "longest_spell_weeks": max(
                end_idx - start_idx + 1
                for start_idx, end_idx in runs
            ),
            "median_spell_weeks": pd.Series(
                [
                    end_idx - start_idx + 1
                    for start_idx, end_idx in runs
                ]
            ).median(),
        })

    spells = pd.DataFrame(spell_rows)
    ticker_summary = pd.DataFrame(ticker_summary_rows)

    print("Calculating persistence by rank band...", flush=True)

    rank_band_rows = []

    weekly_ranks["rank_band"] = pd.cut(
        weekly_ranks["rank"],
        bins=[0, 3, 5, 10, float("inf")],
        labels=["1-3", "4-5", "6-10", "11+"],
        right=True,
    )

    next_week_lookup = {
        dates[i]: dates[i + 1]
        for i in range(len(dates) - 1)
    }

    persistence_records = []
    rank_lookup = {
        (row.decision_date, row.ticker): row.rank
        for row in weekly_ranks.itertuples(index=False)
    }

    for row in weekly_ranks.itertuples(index=False):
        next_day = next_week_lookup.get(row.decision_date)
        if next_day is None:
            continue

        next_rank = rank_lookup.get((next_day, row.ticker))

        persistence_records.append({
            "rank_band": str(row.rank_band),
            "rank": row.rank,
            "stays_next_week": next_rank is not None,
            "next_rank": next_rank,
        })

    persistence = pd.DataFrame(persistence_records)

    if not persistence.empty:
        for band, group in persistence.groupby("rank_band", sort=False):
            rank_band_rows.append({
                "rank_band": band,
                "observations": len(group),
                "next_week_persistence_rate": group[
                    "stays_next_week"
                ].mean(),
                "median_current_rank": group["rank"].median(),
                "median_next_rank_if_persistent": pd.to_numeric(
                    group.loc[
                        group["stays_next_week"],
                        "next_rank",
                    ],
                    errors="coerce",
                ).median(),
            })

    rank_band_summary = pd.DataFrame(rank_band_rows)

    trade_summary = pd.DataFrame()
    trade_join = pd.DataFrame()

    if args.trades is not None and args.trades.exists():
        print("Joining actual purchase activity...", flush=True)

        trades = pd.read_csv(args.trades, low_memory=False)
        trades["decision_date"] = pd.to_datetime(
            trades["decision_date"],
            errors="coerce",
        )
        trades["ticker"] = trades["ticker"].astype(str).str.upper()

        if "strategy_id" in trades.columns:
            preferred = trades.loc[
                trades["strategy_id"].eq("top10_addon_cap_0_1")
            ].copy()
            if not preferred.empty:
                trades = preferred

        buys = trades.loc[trades["side"].eq("buy")].copy()

        trade_join = buys.merge(
            ticker_summary,
            on="ticker",
            how="left",
        )

        if not trade_join.empty:
            trade_summary = pd.DataFrame([{
                "buy_count": len(trade_join),
                "unique_bought_tickers": trade_join["ticker"].nunique(),
                "buy_dollars": pd.to_numeric(
                    trade_join["dollars"],
                    errors="coerce",
                ).sum(),
                "pct_buy_dollars_to_tickers_with_26plus_topn_weeks": (
                    pd.to_numeric(
                        trade_join.loc[
                            trade_join["total_topn_weeks"] >= 26,
                            "dollars",
                        ],
                        errors="coerce",
                    ).sum()
                    / pd.to_numeric(
                        trade_join["dollars"],
                        errors="coerce",
                    ).sum()
                ),
                "pct_buy_dollars_to_tickers_with_52plus_topn_weeks": (
                    pd.to_numeric(
                        trade_join.loc[
                            trade_join["total_topn_weeks"] >= 52,
                            "dollars",
                        ],
                        errors="coerce",
                    ).sum()
                    / pd.to_numeric(
                        trade_join["dollars"],
                        errors="coerce",
                    ).sum()
                ),
            }])

    summary = pd.DataFrame([{
        "decision_weeks": len(dates),
        "top_n": args.top_n,
        "mean_weekly_overlap_pct": pd.to_numeric(
            turnover["overlap_pct"],
            errors="coerce",
        ).mean(),
        "median_weekly_overlap_pct": pd.to_numeric(
            turnover["overlap_pct"],
            errors="coerce",
        ).median(),
        "mean_weekly_replacement_rate": pd.to_numeric(
            turnover["replacement_rate"],
            errors="coerce",
        ).mean(),
        "median_weekly_replacement_rate": pd.to_numeric(
            turnover["replacement_rate"],
            errors="coerce",
        ).median(),
        "weeks_with_zero_replacements": int(
            (pd.to_numeric(
                turnover["entrants_count"],
                errors="coerce",
            ) == 0).sum()
        ),
        "weeks_with_3plus_replacements": int(
            (pd.to_numeric(
                turnover["entrants_count"],
                errors="coerce",
            ) >= 3).sum()
        ),
        "unique_topn_tickers": ticker_summary["ticker"].nunique(),
        "median_total_topn_weeks_per_ticker": ticker_summary[
            "total_topn_weeks"
        ].median(),
        "median_longest_spell_weeks": ticker_summary[
            "longest_spell_weeks"
        ].median(),
        "median_spell_weeks_all_spells": spells[
            "consecutive_weeks"
        ].median(),
        "mean_spell_weeks_all_spells": spells[
            "consecutive_weeks"
        ].mean(),
        "pct_spells_1_week": (
            (spells["consecutive_weeks"] == 1).mean()
        ),
        "pct_spells_4plus_weeks": (
            (spells["consecutive_weeks"] >= 4).mean()
        ),
        "pct_spells_13plus_weeks": (
            (spells["consecutive_weeks"] >= 13).mean()
        ),
        "pct_spells_26plus_weeks": (
            (spells["consecutive_weeks"] >= 26).mean()
        ),
    }])

    args.output_dir.mkdir(parents=True, exist_ok=True)

    summary.to_csv(
        args.output_dir / "rank_persistence_summary.csv",
        index=False,
    )
    turnover.to_csv(
        args.output_dir / "weekly_topn_turnover.csv",
        index=False,
    )
    weekly_ranks.to_csv(
        args.output_dir / "weekly_topn_ranks.csv",
        index=False,
    )
    spells.to_csv(
        args.output_dir / "topn_spells.csv",
        index=False,
    )
    ticker_summary.sort_values(
        ["total_topn_weeks", "longest_spell_weeks"],
        ascending=False,
    ).to_csv(
        args.output_dir / "ticker_persistence_summary.csv",
        index=False,
    )
    rank_band_summary.to_csv(
        args.output_dir / "rank_band_persistence.csv",
        index=False,
    )

    if not trade_join.empty:
        trade_join.to_csv(
            args.output_dir / "buy_activity_with_persistence.csv",
            index=False,
        )
        trade_summary.to_csv(
            args.output_dir / "buy_persistence_summary.csv",
            index=False,
        )

    print("Rank persistence audit complete.", flush=True)
    print("Reports: " + str(args.output_dir), flush=True)


if __name__ == "__main__":
    main()
