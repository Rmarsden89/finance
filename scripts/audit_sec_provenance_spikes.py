from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_DATES = (
    "2020-05-08",
    "2022-05-06",
    "2023-05-05",
    "2025-05-09",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Trace major turnover-spike entrants/exits to the exact SEC "
            "provenance fields available in the weekly research panel."
        )
    )
    parser.add_argument("--long-growth", type=Path, required=True)
    parser.add_argument("--family-scores", type=Path, required=True)
    parser.add_argument("--weekly-panel", type=Path, required=True)
    parser.add_argument(
        "--spike-summary",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--dates",
        nargs="*",
        default=list(DEFAULT_DATES),
    )
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/sec_provenance_spike_audit_v1"),
    )
    return parser.parse_args()


def to_dates(frame: pd.DataFrame, column: str = "decision_date") -> pd.DataFrame:
    out = frame.copy()
    out[column] = pd.to_datetime(out[column], errors="coerce")
    return out


def provenance_columns(panel: pd.DataFrame) -> list[str]:
    tokens = (
        "accepted",
        "filing",
        "filed",
        "period",
        "fy",
        "fp",
        "qtrs",
        "form",
        "annual",
        "revenue",
        "net_income",
        "operating_income",
        "operating_cash",
        "ocf",
        "capex",
        "assets",
        "liabilities",
        "equity",
    )

    result = []
    for column in panel.columns:
        lower = column.lower()
        if any(token in lower for token in tokens):
            result.append(column)

    return result


def value_changed(prior, current) -> bool:
    if pd.isna(prior) and pd.isna(current):
        return False
    if pd.isna(prior) != pd.isna(current):
        return True

    prior_num = pd.to_numeric(pd.Series([prior]), errors="coerce").iloc[0]
    current_num = pd.to_numeric(pd.Series([current]), errors="coerce").iloc[0]

    if pd.notna(prior_num) and pd.notna(current_num):
        return abs(float(current_num) - float(prior_num)) > 1e-12

    return str(prior) != str(current)


def main() -> None:
    args = parse_args()

    long_growth = to_dates(pd.read_csv(args.long_growth, low_memory=False))
    family = to_dates(pd.read_csv(args.family_scores, low_memory=False))
    panel = to_dates(pd.read_csv(args.weekly_panel, low_memory=False))
    spike_summary = to_dates(
        pd.read_csv(args.spike_summary, low_memory=False)
    )

    requested_dates = {
        pd.Timestamp(value).normalize()
        for value in args.dates
    }

    prov_columns = provenance_columns(panel)

    print(
        "Detected "
        + str(len(prov_columns))
        + " candidate SEC/fundamental provenance columns.",
        flush=True,
    )
    print(
        "Target spike weeks: "
        + ", ".join(sorted(day.date().isoformat() for day in requested_dates)),
        flush=True,
    )

    available_dates = sorted(long_growth["decision_date"].dropna().unique())
    previous_date = {
        pd.Timestamp(available_dates[index]): pd.Timestamp(available_dates[index - 1])
        for index in range(1, len(available_dates))
    }

    family_columns = [
        column
        for column in (
            "quality_score",
            "financial_health_score",
            "growth_score",
            "valuation_score",
            "stability_score",
            "momentum_score",
        )
        if column in family.columns
    ]

    detail_rows = []
    changed_field_rows = []
    spike_rows = []

    for number, day in enumerate(sorted(requested_dates), start=1):
        prior_day = previous_date.get(day)
        if prior_day is None:
            print(
                "[" + str(number) + "/" + str(len(requested_dates)) + "] "
                + day.date().isoformat()
                + " has no prior weekly snapshot; skipping.",
                flush=True,
            )
            continue

        print(
            "[" + str(number) + "/" + str(len(requested_dates)) + "] "
            + day.date().isoformat()
            + " vs "
            + prior_day.date().isoformat(),
            flush=True,
        )

        summary_match = spike_summary.loc[
            spike_summary["decision_date"].eq(day)
        ]

        if not summary_match.empty:
            entrants = {
                value
                for value in str(
                    summary_match.iloc[0].get("entrants", "")
                ).split("|")
                if value
            }
            exits = {
                value
                for value in str(
                    summary_match.iloc[0].get("exits", "")
                ).split("|")
                if value
            }
        else:
            current_ranked = long_growth.loc[
                long_growth["decision_date"].eq(day)
                & long_growth["top_conviction_eligible"].fillna(False).astype(bool)
            ].sort_values(
                "long_growth_v1_score",
                ascending=False,
            ).head(args.top_n)

            prior_ranked = long_growth.loc[
                long_growth["decision_date"].eq(prior_day)
                & long_growth["top_conviction_eligible"].fillna(False).astype(bool)
            ].sort_values(
                "long_growth_v1_score",
                ascending=False,
            ).head(args.top_n)

            current_set = set(
                current_ranked["ticker"].astype(str).str.upper()
            )
            prior_set = set(
                prior_ranked["ticker"].astype(str).str.upper()
            )
            entrants = current_set - prior_set
            exits = prior_set - current_set

        impacted = sorted(entrants | exits)

        changed_ticker_count = 0
        accepted_field_change_count = 0
        period_field_change_count = 0
        fundamental_value_change_count = 0

        for ticker in impacted:
            status = "entrant" if ticker in entrants else "exit"

            prior_panel = panel.loc[
                panel["decision_date"].eq(prior_day)
                & panel["ticker"].astype(str).str.upper().eq(ticker)
            ]
            current_panel = panel.loc[
                panel["decision_date"].eq(day)
                & panel["ticker"].astype(str).str.upper().eq(ticker)
            ]

            prior_family = family.loc[
                family["decision_date"].eq(prior_day)
                & family["ticker"].astype(str).str.upper().eq(ticker)
            ]
            current_family = family.loc[
                family["decision_date"].eq(day)
                & family["ticker"].astype(str).str.upper().eq(ticker)
            ]

            prior_model = long_growth.loc[
                long_growth["decision_date"].eq(prior_day)
                & long_growth["ticker"].astype(str).str.upper().eq(ticker)
            ]
            current_model = long_growth.loc[
                long_growth["decision_date"].eq(day)
                & long_growth["ticker"].astype(str).str.upper().eq(ticker)
            ]

            changed_fields = []
            ticker_accepted_change = False
            ticker_period_change = False
            ticker_fundamental_change = False

            if not prior_panel.empty or not current_panel.empty:
                prior_row = (
                    prior_panel.iloc[0]
                    if not prior_panel.empty
                    else pd.Series(dtype=object)
                )
                current_row = (
                    current_panel.iloc[0]
                    if not current_panel.empty
                    else pd.Series(dtype=object)
                )

                for column in prov_columns:
                    prior_value = prior_row.get(column, pd.NA)
                    current_value = current_row.get(column, pd.NA)

                    if not value_changed(prior_value, current_value):
                        continue

                    lower = column.lower()
                    category = "fundamental_or_other"
                    if "accept" in lower or "filing" in lower or "filed" in lower:
                        category = "filing_provenance"
                        ticker_accepted_change = True
                    elif (
                        "period" in lower
                        or "qtrs" in lower
                        or lower.endswith("_fy")
                        or lower.endswith("_fp")
                        or "_fy_" in lower
                        or "_fp_" in lower
                    ):
                        category = "period_provenance"
                        ticker_period_change = True
                    else:
                        ticker_fundamental_change = True

                    changed_fields.append(column)
                    changed_field_rows.append({
                        "decision_date": day,
                        "prior_decision_date": prior_day,
                        "ticker": ticker,
                        "status": status,
                        "field": column,
                        "category": category,
                        "prior_value": prior_value,
                        "current_value": current_value,
                    })

            if changed_fields:
                changed_ticker_count += 1
            if ticker_accepted_change:
                accepted_field_change_count += 1
            if ticker_period_change:
                period_field_change_count += 1
            if ticker_fundamental_change:
                fundamental_value_change_count += 1

            detail = {
                "decision_date": day,
                "prior_decision_date": prior_day,
                "ticker": ticker,
                "status": status,
                "changed_provenance_field_count": len(changed_fields),
                "changed_provenance_fields": "|".join(changed_fields),
                "accepted_or_filing_changed": ticker_accepted_change,
                "period_provenance_changed": ticker_period_change,
                "fundamental_value_changed": ticker_fundamental_change,
            }

            for column in family_columns:
                prior_value = (
                    prior_family.iloc[0].get(column, pd.NA)
                    if not prior_family.empty
                    else pd.NA
                )
                current_value = (
                    current_family.iloc[0].get(column, pd.NA)
                    if not current_family.empty
                    else pd.NA
                )

                prior_num = pd.to_numeric(
                    pd.Series([prior_value]),
                    errors="coerce",
                ).iloc[0]
                current_num = pd.to_numeric(
                    pd.Series([current_value]),
                    errors="coerce",
                ).iloc[0]

                detail[column + "_prior"] = prior_num
                detail[column + "_current"] = current_num
                detail[column + "_delta"] = (
                    current_num - prior_num
                    if pd.notna(prior_num) and pd.notna(current_num)
                    else pd.NA
                )

            for label, source in (
                ("prior", prior_model),
                ("current", current_model),
            ):
                if not source.empty:
                    detail[label + "_model_score"] = pd.to_numeric(
                        pd.Series([
                            source.iloc[0].get(
                                "long_growth_v1_score",
                                pd.NA,
                            )
                        ]),
                        errors="coerce",
                    ).iloc[0]
                    detail[label + "_eligible"] = source.iloc[0].get(
                        "top_conviction_eligible",
                        pd.NA,
                    )

            detail_rows.append(detail)

        spike_rows.append({
            "decision_date": day,
            "prior_decision_date": prior_day,
            "entrant_count": len(entrants),
            "exit_count": len(exits),
            "impacted_ticker_count": len(impacted),
            "tickers_with_any_provenance_change": changed_ticker_count,
            "tickers_with_accepted_or_filing_change": (
                accepted_field_change_count
            ),
            "tickers_with_period_provenance_change": (
                period_field_change_count
            ),
            "tickers_with_fundamental_value_change": (
                fundamental_value_change_count
            ),
            "entrants": "|".join(sorted(entrants)),
            "exits": "|".join(sorted(exits)),
        })

    detail = pd.DataFrame(detail_rows)
    changed_fields = pd.DataFrame(changed_field_rows)
    spike_level = pd.DataFrame(spike_rows)

    if not changed_fields.empty:
        field_frequency = (
            changed_fields.groupby(
                ["field", "category"],
                as_index=False,
            )
            .agg(
                changed_count=("ticker", "count"),
                unique_tickers=("ticker", "nunique"),
                spike_weeks=("decision_date", "nunique"),
            )
            .sort_values(
                ["spike_weeks", "changed_count"],
                ascending=False,
            )
        )
    else:
        field_frequency = pd.DataFrame(
            columns=[
                "field",
                "category",
                "changed_count",
                "unique_tickers",
                "spike_weeks",
            ]
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    spike_level.to_csv(
        args.output_dir / "sec_provenance_spike_summary.csv",
        index=False,
    )
    detail.to_csv(
        args.output_dir / "sec_provenance_ticker_detail.csv",
        index=False,
    )
    changed_fields.to_csv(
        args.output_dir / "sec_provenance_changed_fields.csv",
        index=False,
    )
    field_frequency.to_csv(
        args.output_dir / "sec_provenance_field_frequency.csv",
        index=False,
    )

    pd.DataFrame({"detected_provenance_column": prov_columns}).to_csv(
        args.output_dir / "sec_provenance_detected_columns.csv",
        index=False,
    )

    print("SEC provenance spike audit complete.", flush=True)
    print("Reports: " + str(args.output_dir), flush=True)


if __name__ == "__main__":
    main()
