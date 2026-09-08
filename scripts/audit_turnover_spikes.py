from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit high-turnover Top-N weeks to distinguish genuine "
            "information refresh from factor/eligibility artifacts."
        )
    )
    parser.add_argument("--long-growth", type=Path, required=True)
    parser.add_argument("--family-scores", type=Path, required=True)
    parser.add_argument("--weekly-panel", type=Path, required=False)
    parser.add_argument("--weekly-turnover", type=Path, required=True)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--replacement-threshold", type=float, default=0.30)
    parser.add_argument("--start-year", type=int, default=2016)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/turnover_spike_audit_v1"),
    )
    return parser.parse_args()


def normalize_dates(frame: pd.DataFrame, column: str = "decision_date") -> pd.DataFrame:
    out = frame.copy()
    out[column] = pd.to_datetime(out[column], errors="coerce")
    return out


def available_family_columns(frame: pd.DataFrame) -> list[str]:
    preferred = [
        "quality_score",
        "financial_health_score",
        "growth_score",
        "valuation_score",
        "stability_score",
        "momentum_score",
    ]
    return [column for column in preferred if column in frame.columns]


def main() -> None:
    args = parse_args()

    long_growth = normalize_dates(
        pd.read_csv(args.long_growth, low_memory=False)
    )
    family = normalize_dates(
        pd.read_csv(args.family_scores, low_memory=False)
    )
    turnover = normalize_dates(
        pd.read_csv(args.weekly_turnover, low_memory=False)
    )

    panel = None
    if args.weekly_panel is not None and args.weekly_panel.exists():
        panel = normalize_dates(
            pd.read_csv(args.weekly_panel, low_memory=False)
        )

    long_growth = long_growth.loc[
        long_growth["decision_date"].notna()
        & long_growth["decision_date"].dt.year.between(
            args.start_year,
            args.end_year,
        )
    ].copy()

    turnover["replacement_rate"] = pd.to_numeric(
        turnover["replacement_rate"],
        errors="coerce",
    )

    spikes = turnover.loc[
        turnover["replacement_rate"].ge(args.replacement_threshold)
    ].copy()

    print(
        "Found "
        + str(len(spikes))
        + " turnover-spike weeks at replacement rate >= "
        + format(args.replacement_threshold, ".0%"),
        flush=True,
    )

    if spikes.empty:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        spikes.to_csv(
            args.output_dir / "turnover_spike_weeks.csv",
            index=False,
        )
        print("No spike weeks found.", flush=True)
        return

    family_columns = available_family_columns(family)

    rank_rows = []
    transition_rows = []
    summary_rows = []

    dates = sorted(long_growth["decision_date"].dropna().unique())
    date_to_previous = {
        dates[i]: dates[i - 1]
        for i in range(1, len(dates))
    }

    for number, spike in enumerate(
        spikes.itertuples(index=False),
        start=1,
    ):
        day = pd.Timestamp(spike.decision_date)
        prior_day = date_to_previous.get(day)

        if prior_day is None:
            continue

        print(
            "["
            + str(number)
            + "/"
            + str(len(spikes))
            + "] Auditing "
            + day.date().isoformat()
            + " replacement="
            + format(float(spike.replacement_rate), ".0%"),
            flush=True,
        )

        current = long_growth.loc[
            long_growth["decision_date"].eq(day)
        ].copy()
        prior = long_growth.loc[
            long_growth["decision_date"].eq(prior_day)
        ].copy()

        for frame in (current, prior):
            frame["long_growth_v1_score"] = pd.to_numeric(
                frame["long_growth_v1_score"],
                errors="coerce",
            )

        current_ranked = current.loc[
            current["top_conviction_eligible"].fillna(False).astype(bool)
            & current["long_growth_v1_score"].notna()
        ].sort_values(
            "long_growth_v1_score",
            ascending=False,
        ).head(args.top_n)

        prior_ranked = prior.loc[
            prior["top_conviction_eligible"].fillna(False).astype(bool)
            & prior["long_growth_v1_score"].notna()
        ].sort_values(
            "long_growth_v1_score",
            ascending=False,
        ).head(args.top_n)

        current_tickers = (
            current_ranked["ticker"].astype(str).str.upper().tolist()
        )
        prior_tickers = (
            prior_ranked["ticker"].astype(str).str.upper().tolist()
        )

        entrants = sorted(set(current_tickers) - set(prior_tickers))
        exits = sorted(set(prior_tickers) - set(current_tickers))
        overlap = sorted(set(current_tickers) & set(prior_tickers))

        current_rank_lookup = {
            ticker: rank
            for rank, ticker in enumerate(current_tickers, start=1)
        }
        prior_rank_lookup = {
            ticker: rank
            for rank, ticker in enumerate(prior_tickers, start=1)
        }

        impacted = sorted(set(entrants) | set(exits) | set(overlap))

        prior_family = family.loc[
            family["decision_date"].eq(prior_day)
            & family["ticker"].astype(str).str.upper().isin(impacted)
        ].copy()
        current_family = family.loc[
            family["decision_date"].eq(day)
            & family["ticker"].astype(str).str.upper().isin(impacted)
        ].copy()

        family_prior_lookup = {
            str(row["ticker"]).upper(): row
            for _, row in prior_family.iterrows()
        }
        family_current_lookup = {
            str(row["ticker"]).upper(): row
            for _, row in current_family.iterrows()
        }

        eligibility_change_count = 0
        family_availability_change_count = 0
        score_jump_count = 0
        filing_refresh_count = 0

        for ticker in impacted:
            prior_row = prior.loc[
                prior["ticker"].astype(str).str.upper().eq(ticker)
            ]
            current_row = current.loc[
                current["ticker"].astype(str).str.upper().eq(ticker)
            ]

            prior_score = (
                float(prior_row["long_growth_v1_score"].iloc[0])
                if not prior_row.empty
                and pd.notna(prior_row["long_growth_v1_score"].iloc[0])
                else float("nan")
            )
            current_score = (
                float(current_row["long_growth_v1_score"].iloc[0])
                if not current_row.empty
                and pd.notna(current_row["long_growth_v1_score"].iloc[0])
                else float("nan")
            )

            prior_eligible = (
                bool(prior_row["top_conviction_eligible"].iloc[0])
                if not prior_row.empty
                else False
            )
            current_eligible = (
                bool(current_row["top_conviction_eligible"].iloc[0])
                if not current_row.empty
                else False
            )

            if prior_eligible != current_eligible:
                eligibility_change_count += 1

            prior_f = family_prior_lookup.get(ticker)
            current_f = family_current_lookup.get(ticker)

            family_changes = {}
            prior_available = 0
            current_available = 0

            for column in family_columns:
                prior_value = (
                    pd.to_numeric(
                        pd.Series([prior_f[column]]),
                        errors="coerce",
                    ).iloc[0]
                    if prior_f is not None and column in prior_f
                    else float("nan")
                )
                current_value = (
                    pd.to_numeric(
                        pd.Series([current_f[column]]),
                        errors="coerce",
                    ).iloc[0]
                    if current_f is not None and column in current_f
                    else float("nan")
                )

                if pd.notna(prior_value):
                    prior_available += 1
                if pd.notna(current_value):
                    current_available += 1

                family_changes[column + "_prior"] = prior_value
                family_changes[column + "_current"] = current_value
                family_changes[column + "_delta"] = (
                    current_value - prior_value
                    if pd.notna(prior_value) and pd.notna(current_value)
                    else float("nan")
                )

            availability_changed = prior_available != current_available
            if availability_changed:
                family_availability_change_count += 1

            score_delta = (
                current_score - prior_score
                if pd.notna(prior_score) and pd.notna(current_score)
                else float("nan")
            )
            if pd.notna(score_delta) and abs(score_delta) >= 5.0:
                score_jump_count += 1

            filing_refresh = False
            filing_fields = {}

            if panel is not None:
                panel_prior = panel.loc[
                    panel["decision_date"].eq(prior_day)
                    & panel["ticker"].astype(str).str.upper().eq(ticker)
                ]
                panel_current = panel.loc[
                    panel["decision_date"].eq(day)
                    & panel["ticker"].astype(str).str.upper().eq(ticker)
                ]

                candidate_fields = [
                    field
                    for field in panel.columns
                    if (
                        field.endswith("_accepted_at")
                        or field.endswith("_filed_date")
                        or field in {
                            "annual_accepted_at",
                            "annual_filing_date",
                            "latest_annual_accepted_at",
                            "latest_filing_accepted_at",
                            "sec_accepted_at",
                        }
                    )
                ]

                for field in candidate_fields:
                    if (
                        field in panel.columns
                        and not panel_prior.empty
                        and not panel_current.empty
                    ):
                        prior_value = panel_prior[field].iloc[0]
                        current_value = panel_current[field].iloc[0]
                        filing_fields[field + "_prior"] = prior_value
                        filing_fields[field + "_current"] = current_value

                        if (
                            pd.notna(prior_value)
                            and pd.notna(current_value)
                            and str(prior_value) != str(current_value)
                        ):
                            filing_refresh = True

            if filing_refresh:
                filing_refresh_count += 1

            status = (
                "entrant"
                if ticker in entrants
                else "exit"
                if ticker in exits
                else "overlap"
            )

            transition_row = {
                "decision_date": day,
                "prior_decision_date": prior_day,
                "ticker": ticker,
                "status": status,
                "prior_rank": prior_rank_lookup.get(ticker),
                "current_rank": current_rank_lookup.get(ticker),
                "prior_score": prior_score,
                "current_score": current_score,
                "score_delta": score_delta,
                "prior_eligible": prior_eligible,
                "current_eligible": current_eligible,
                "prior_family_count": prior_available,
                "current_family_count": current_available,
                "family_availability_changed": availability_changed,
                "filing_refresh_detected": filing_refresh,
            }
            transition_row.update(family_changes)
            transition_row.update(filing_fields)
            transition_rows.append(transition_row)

        for rank, ticker in enumerate(prior_tickers, start=1):
            rank_rows.append({
                "decision_date": day,
                "snapshot": "prior",
                "snapshot_date": prior_day,
                "rank": rank,
                "ticker": ticker,
            })

        for rank, ticker in enumerate(current_tickers, start=1):
            rank_rows.append({
                "decision_date": day,
                "snapshot": "current",
                "snapshot_date": day,
                "rank": rank,
                "ticker": ticker,
            })

        likely_driver = "score_reordering"
        if eligibility_change_count >= max(2, len(entrants) // 2):
            likely_driver = "eligibility_or_coverage_shift"
        elif family_availability_change_count >= max(2, len(entrants) // 2):
            likely_driver = "family_availability_shift"
        elif filing_refresh_count >= max(2, len(entrants) // 2):
            likely_driver = "filing_information_refresh"
        elif score_jump_count >= max(2, len(entrants) // 2):
            likely_driver = "large_score_reordering"

        summary_rows.append({
            "decision_date": day,
            "prior_decision_date": prior_day,
            "replacement_rate": float(spike.replacement_rate),
            "entrants_count": len(entrants),
            "exits_count": len(exits),
            "overlap_count": len(overlap),
            "entrants": "|".join(entrants),
            "exits": "|".join(exits),
            "eligibility_change_count": eligibility_change_count,
            "family_availability_change_count": (
                family_availability_change_count
            ),
            "large_score_jump_count": score_jump_count,
            "filing_refresh_count": filing_refresh_count,
            "likely_driver": likely_driver,
        })

    spike_summary = pd.DataFrame(summary_rows)
    transitions = pd.DataFrame(transition_rows)
    ranks = pd.DataFrame(rank_rows)

    if not spike_summary.empty:
        month_summary = (
            spike_summary.assign(
                month=spike_summary["decision_date"].dt.month
            )
            .groupby("month", as_index=False)
            .agg(
                spike_weeks=("decision_date", "count"),
                mean_replacement_rate=("replacement_rate", "mean"),
                mean_eligibility_changes=(
                    "eligibility_change_count",
                    "mean",
                ),
                mean_family_availability_changes=(
                    "family_availability_change_count",
                    "mean",
                ),
                mean_large_score_jumps=(
                    "large_score_jump_count",
                    "mean",
                ),
                mean_filing_refreshes=(
                    "filing_refresh_count",
                    "mean",
                ),
            )
        )
    else:
        month_summary = pd.DataFrame()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    spikes.to_csv(
        args.output_dir / "turnover_spike_weeks.csv",
        index=False,
    )
    spike_summary.to_csv(
        args.output_dir / "turnover_spike_summary.csv",
        index=False,
    )
    transitions.to_csv(
        args.output_dir / "turnover_spike_transitions.csv",
        index=False,
    )
    ranks.to_csv(
        args.output_dir / "turnover_spike_rank_snapshots.csv",
        index=False,
    )
    month_summary.to_csv(
        args.output_dir / "turnover_spike_month_summary.csv",
        index=False,
    )

    print("Turnover-spike audit complete.", flush=True)
    print("Reports: " + str(args.output_dir), flush=True)


if __name__ == "__main__":
    main()
