from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a weekly PIT research panel and summarize data-quality gaps."
    )
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/panel_validation"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    panel = pd.read_csv(args.panel, low_memory=False)
    panel["decision_date"] = pd.to_datetime(panel["decision_date"])
    panel["price_date"] = pd.to_datetime(panel["price_date"], errors="coerce")
    panel["year"] = panel["decision_date"].dt.year

    required = {
        "decision_date",
        "ticker",
        "identity_resolved",
        "fundamentals_available",
        "price_available",
        "research_ready",
    }
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(
            "Panel missing required columns: " + ", ".join(sorted(missing))
        )

    duplicate_rows = int(
        panel.duplicated(["decision_date", "ticker"]).sum()
    )

    weekly = (
        panel.groupby("decision_date")
        .agg(
            members=("ticker", "size"),
            identity_resolved_pct=("identity_resolved", "mean"),
            fundamentals_available_pct=("fundamentals_available", "mean"),
            price_available_pct=("price_available", "mean"),
            research_ready_pct=("research_ready", "mean"),
        )
        .reset_index()
    )

    yearly = (
        panel.groupby("year")
        .agg(
            weeks=("decision_date", "nunique"),
            rows=("ticker", "size"),
            identity_resolved_pct=("identity_resolved", "mean"),
            fundamentals_available_pct=("fundamentals_available", "mean"),
            price_available_pct=("price_available", "mean"),
            research_ready_pct=("research_ready", "mean"),
        )
        .reset_index()
    )
    yearly["average_members"] = (
        yearly["rows"] / yearly["weeks"]
    )

    unresolved = panel.loc[
        ~panel["identity_resolved"].astype(bool)
    ].copy()

    if unresolved.empty:
        unresolved_summary = pd.DataFrame(
            columns=[
                "year",
                "ticker",
                "unresolved_weeks",
                "first_unresolved",
                "last_unresolved",
                "company_name",
                "original_cik",
            ]
        )
    else:
        unresolved_summary = (
            unresolved.groupby(["year", "ticker"], dropna=False)
            .agg(
                unresolved_weeks=("decision_date", "size"),
                first_unresolved=("decision_date", "min"),
                last_unresolved=("decision_date", "max"),
                company_name=("company_name", "first"),
                original_cik=("original_cik", "first"),
            )
            .reset_index()
            .sort_values(
                ["year", "unresolved_weeks", "ticker"],
                ascending=[True, False, True],
            )
        )

    missing_fundamentals = panel.loc[
        panel["identity_resolved"].astype(bool)
        & ~panel["fundamentals_available"].astype(bool)
    ].copy()

    if missing_fundamentals.empty:
        fundamentals_summary = pd.DataFrame(
            columns=[
                "year",
                "ticker",
                "missing_weeks",
                "first_missing",
                "last_missing",
                "company_name",
                "cik",
            ]
        )
    else:
        fundamentals_summary = (
            missing_fundamentals.groupby(["year", "ticker"], dropna=False)
            .agg(
                missing_weeks=("decision_date", "size"),
                first_missing=("decision_date", "min"),
                last_missing=("decision_date", "max"),
                company_name=("company_name", "first"),
                cik=("cik", "first"),
            )
            .reset_index()
            .sort_values(
                ["year", "missing_weeks", "ticker"],
                ascending=[True, False, True],
            )
        )

    price_rows = panel.loc[
        panel["price_available"].astype(bool)
    ].copy()

    negative_price_age = 0
    stale_price_rows = 0
    if "price_age_days" in panel.columns:
        price_age = pd.to_numeric(
            price_rows["price_age_days"],
            errors="coerce",
        )
        negative_price_age = int((price_age < 0).sum())
        stale_price_rows = int((price_age > 4).sum())

    args.output_dir.mkdir(parents=True, exist_ok=True)
    weekly.to_csv(args.output_dir / "weekly_quality.csv", index=False)
    yearly.to_csv(args.output_dir / "yearly_quality.csv", index=False)
    unresolved_summary.to_csv(
        args.output_dir / "unresolved_identity_intervals.csv",
        index=False,
    )
    fundamentals_summary.to_csv(
        args.output_dir / "missing_fundamentals_intervals.csv",
        index=False,
    )

    print("WEEKLY PANEL VALIDATION")
    print(f"Rows:                          {len(panel):,}")
    print(f"Decision weeks:                {panel['decision_date'].nunique():,}")
    print(
        f"Average members/week:          "
        f"{len(panel) / panel['decision_date'].nunique():.1f}"
    )
    print(f"Min members/week:              {weekly['members'].min():,}")
    print(f"Max members/week:              {weekly['members'].max():,}")
    print(f"Duplicate date+ticker rows:    {duplicate_rows:,}")
    print(
        f"Identity-resolved rows:        "
        f"{panel['identity_resolved'].mean():.2%}"
    )
    print(
        f"Fundamentals-available rows:   "
        f"{panel['fundamentals_available'].mean():.2%}"
    )
    print(
        f"Price-available rows:          "
        f"{panel['price_available'].mean():.2%}"
    )
    print(
        f"Research-ready rows:           "
        f"{panel['research_ready'].mean():.2%}"
    )
    print(f"Negative price-age rows:       {negative_price_age:,}")
    print(f"Price age > 4 day rows:        {stale_price_rows:,}")
    print(f"Validation reports:            {args.output_dir}")

    print()
    print("YEARLY QUALITY")
    for row in yearly.itertuples(index=False):
        print(
            f"{row.year}: "
            f"identity={row.identity_resolved_pct:.1%} "
            f"fundamentals={row.fundamentals_available_pct:.1%} "
            f"price={row.price_available_pct:.1%} "
            f"ready={row.research_ready_pct:.1%}"
        )

    if not unresolved_summary.empty:
        print()
        print("UNRESOLVED IDENTITY")
        by_year = unresolved_summary.groupby("year").agg(
            unresolved_rows=("unresolved_weeks", "sum"),
            tickers=("ticker", "nunique"),
        )
        for year, row in by_year.iterrows():
            print(
                f"{year}: {int(row.unresolved_rows):,} rows / "
                f"{int(row.tickers)} tickers"
            )


if __name__ == "__main__":
    main()
