from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from .membership import MembershipStore
from .sec_snapshot import (
    SecWinnerFactCursor,
    annual_duration_facts,
    pivot_annual_snapshot,
    pivot_snapshot,
)


def trailing_friday_timestamps(
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[datetime]:
    fridays = pd.date_range(
        start=start.date(),
        end=end.date(),
        freq="W-FRI",
    )
    return [
        datetime.combine(day.date(), datetime.min.time()).replace(
            hour=16,
            minute=0,
            second=0,
            microsecond=0,
        )
        for day in fridays
    ]


def build_sec_only_weekly_extension(
    intervals,
    winner_facts: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """Build weekly SEC-only rows through the current period.

    These rows deliberately contain no market price. They exist so frozen
    trailing fundamental calculations, especially the 52-observed-week Growth
    family, retain the same cadence used by the historical panel.
    """

    facts = winner_facts.copy()
    facts["accepted_at"] = pd.to_datetime(
        facts["accepted_at"],
        errors="coerce",
    )
    for column in ("period_date", "filed_date", "ddate_date"):
        if column in facts.columns:
            facts[column] = pd.to_datetime(
                facts[column],
                errors="coerce",
            ).dt.date

    fact_cursor = SecWinnerFactCursor(facts)
    annual_cursor = SecWinnerFactCursor(annual_duration_facts(facts))
    memberships = MembershipStore(intervals)

    frames: list[pd.DataFrame] = []
    timestamps = trailing_friday_timestamps(start=start, end=end)

    for position, as_of in enumerate(timestamps, start=1):
        members = memberships.members_as_of(as_of.date())
        universe = pd.DataFrame(
            [
                {
                    "decision_date": as_of.date(),
                    "as_of": as_of,
                    "ticker": row.ticker,
                    "cik": row.cik,
                    "company_name": row.company_name,
                    "identity_resolved": row.cik is not None,
                }
                for row in members
            ]
        )

        latest = fact_cursor.as_of(as_of)
        annual_latest = annual_cursor.as_of(as_of)

        fundamentals = pivot_snapshot(latest)
        annual = pivot_annual_snapshot(annual_latest)

        panel = universe.merge(fundamentals, on="cik", how="left")
        if not annual.empty:
            panel = panel.merge(annual, on="cik", how="left")

        panel["close"] = pd.NA
        panel["adjusted_close"] = pd.NA
        panel["return_price"] = pd.NA
        panel["return_price_basis"] = pd.NA
        panel["price_date"] = pd.NaT
        panel["price_source"] = pd.NA
        panel["price_available"] = False

        fundamental_columns = [
            column
            for column in (
                "revenue",
                "net_income",
                "operating_income",
                "total_assets",
                "total_liabilities",
                "shareholders_equity",
                "cash",
                "operating_cash_flow",
                "capital_expenditures",
                "shares_outstanding",
            )
            if column in panel.columns
        ]
        panel["fundamentals_available"] = (
            panel[fundamental_columns].notna().any(axis=1)
            if fundamental_columns
            else False
        )
        panel["research_ready"] = False

        frames.append(panel)

        if position == 1 or position % 10 == 0 or position == len(timestamps):
            print(
                f"SEC extension {position}/{len(timestamps)} "
                f"date={as_of.date()} rows={len(panel):,} "
                f"fundamentals={int(panel['fundamentals_available'].sum()):,}",
                flush=True,
            )

    return (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame()
    )


def build_current_shadow_row(
    intervals,
    winner_facts: pd.DataFrame,
    market_snapshot: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    """Build the current universe row set from SEC shadow + Robinhood prices."""

    facts = winner_facts.copy()
    facts["accepted_at"] = pd.to_datetime(
        facts["accepted_at"],
        errors="coerce",
    )
    for column in ("period_date", "filed_date", "ddate_date"):
        if column in facts.columns:
            facts[column] = pd.to_datetime(
                facts[column],
                errors="coerce",
            ).dt.date

    cutoff = pd.Timestamp(as_of)
    latest = facts.loc[
        facts["accepted_at"].notna()
        & facts["accepted_at"].le(cutoff.tz_localize(None) if cutoff.tzinfo else cutoff)
    ].copy()
    latest = (
        latest.sort_values(
            ["cik", "concept", "ddate_date", "accepted_at"],
            kind="stable",
        )
        .groupby(["cik", "concept"], as_index=False, sort=False)
        .tail(1)
    )

    annual_facts = annual_duration_facts(facts)
    annual_latest = annual_facts.loc[
        annual_facts["accepted_at"].notna()
        & annual_facts["accepted_at"].le(
            cutoff.tz_localize(None) if cutoff.tzinfo else cutoff
        )
    ].copy()
    annual_latest = (
        annual_latest.sort_values(
            ["cik", "concept", "ddate_date", "accepted_at"],
            kind="stable",
        )
        .groupby(["cik", "concept"], as_index=False, sort=False)
        .tail(1)
    )

    members = MembershipStore(intervals).members_as_of(cutoff.date())
    universe = pd.DataFrame(
        [
            {
                "decision_date": cutoff.date(),
                "as_of": cutoff,
                "ticker": row.ticker,
                "cik": row.cik,
                "company_name": row.company_name,
                "identity_resolved": row.cik is not None,
            }
            for row in members
        ]
    )

    panel = universe.merge(pivot_snapshot(latest), on="cik", how="left")
    annual = pivot_annual_snapshot(annual_latest)
    if not annual.empty:
        panel = panel.merge(annual, on="cik", how="left")

    market = market_snapshot.copy()
    market["ticker"] = market["ticker"].astype(str).str.upper()
    market["price_timestamp"] = pd.to_datetime(
        market["price_timestamp"],
        errors="coerce",
        utc=True,
    )

    market_columns = [
        column
        for column in (
            "ticker",
            "close",
            "price_timestamp",
            "price_age_minutes",
            "price_valid",
            "price_source",
            "instrument_id",
            "instrument_state",
            "quote_status",
            "instrument_match_status",
            "tradability",
            "tradability_status",
            "market_state",
            "market_state_status",
        )
        if column in market.columns
    ]
    panel = panel.merge(
        market[market_columns],
        on="ticker",
        how="left",
        validate="one_to_one",
    )

    panel["price_available"] = panel["price_valid"].fillna(False).astype(bool)
    panel["price_date"] = panel["price_timestamp"].dt.date
    panel["adjusted_close"] = pd.NA
    panel["return_price"] = pd.NA
    panel["return_price_basis"] = pd.NA

    fundamental_columns = [
        column
        for column in (
            "revenue",
            "net_income",
            "operating_income",
            "total_assets",
            "total_liabilities",
            "shareholders_equity",
            "cash",
            "operating_cash_flow",
            "capital_expenditures",
            "shares_outstanding",
        )
        if column in panel.columns
    ]
    panel["fundamentals_available"] = (
        panel[fundamental_columns].notna().any(axis=1)
        if fundamental_columns
        else False
    )
    panel["research_ready"] = (
        panel["identity_resolved"]
        & panel["price_available"]
        & panel["fundamentals_available"]
    )

    return panel.sort_values("ticker").reset_index(drop=True)


def _as_naive_eastern(values: pd.Series) -> pd.Series:
    """Normalize mixed naive/aware decision timestamps to naive Eastern wall time.

    Historical research timestamps are intentionally timezone-naive 16:00
    decision times. Current shadow runs may receive an explicit Eastern offset.
    Converting aware values to America/New_York and then dropping the timezone
    keeps both representations on the same local decision-time basis.
    """

    def normalize(value):
        if pd.isna(value):
            return pd.NaT
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("America/New_York").tz_localize(None)
        return timestamp

    return values.map(normalize)


def assemble_current_scoring_panel(
    historical_panel: pd.DataFrame,
    sec_extension: pd.DataFrame,
    current_rows: pd.DataFrame,
    *,
    lookback_days: int = 390,
) -> pd.DataFrame:
    current = current_rows.copy()
    current["as_of"] = _as_naive_eastern(current["as_of"])
    current_as_of = current["as_of"].max()
    cutoff = current_as_of - timedelta(days=lookback_days)

    historical = historical_panel.copy()
    historical["as_of"] = _as_naive_eastern(historical["as_of"])
    historical = historical.loc[historical["as_of"].ge(cutoff)].copy()

    extension = sec_extension.copy()
    extension["as_of"] = _as_naive_eastern(extension["as_of"])

    combined = pd.concat(
        [historical, extension, current],
        ignore_index=True,
        sort=False,
    )
    combined = combined.sort_values(
        ["ticker", "as_of"],
        kind="stable",
    ).reset_index(drop=True)
    return combined
