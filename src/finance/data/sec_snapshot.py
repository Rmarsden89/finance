from __future__ import annotations

from datetime import datetime

import pandas as pd

from .sec_canonical import build_canonical_facts
from .sec_winners import select_canonical_winners


def build_winner_facts(
    submissions: pd.DataFrame,
    numeric_facts: pd.DataFrame,
    presentation: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Return deterministic canonical SEC winner facts with PIT provenance."""

    canonical, _ = build_canonical_facts(
        submissions,
        numeric_facts,
        presentation,
    )
    winners, _, _ = select_canonical_winners(canonical)
    return winners


class SecWinnerFactCursor:
    """Incrementally maintain latest canonical facts at increasing timestamps.

    Winner facts are sorted once by acceptance time. As the cursor advances,
    each newly available fact can replace the current CIK/concept value only
    when its reported period is newer, or when it is a later filing for the
    same reported period.
    """

    def __init__(self, winner_facts: pd.DataFrame) -> None:
        required = {
            "cik",
            "concept",
            "ddate_date",
            "accepted_at",
            "value",
            "source_tag",
        }
        missing = required - set(winner_facts.columns)
        if missing:
            raise ValueError(
                "Winner facts missing required columns: "
                + ", ".join(sorted(missing))
            )

        eligible = winner_facts.loc[
            winner_facts["accepted_at"].notna()
        ].copy()
        self._facts = eligible.sort_values(
            ["accepted_at", "cik", "concept", "ddate_date"],
            kind="stable",
        ).reset_index(drop=True)
        self._position = 0
        self._latest: dict[tuple[int, str], pd.Series] = {}
        self._last_as_of: datetime | None = None

    def as_of(self, as_of: datetime) -> pd.DataFrame:
        if self._last_as_of is not None and as_of < self._last_as_of:
            raise ValueError("SecWinnerFactCursor timestamps must be nondecreasing")

        while self._position < len(self._facts):
            row = self._facts.iloc[self._position]
            accepted_at = row["accepted_at"]
            if accepted_at > as_of:
                break

            key = (int(row["cik"]), str(row["concept"]))
            current = self._latest.get(key)

            if current is None or _fact_rank(row) >= _fact_rank(current):
                self._latest[key] = row

            self._position += 1

        self._last_as_of = as_of

        if not self._latest:
            return self._facts.iloc[0:0].copy()

        return pd.DataFrame(
            [row.to_dict() for row in self._latest.values()]
        ).reset_index(drop=True)


def _fact_rank(row: pd.Series) -> tuple:
    period = row["ddate_date"]
    accepted = row["accepted_at"]
    return (period, accepted)


def latest_facts_as_of(
    winner_facts: pd.DataFrame,
    as_of: datetime,
) -> pd.DataFrame:
    """Return latest known canonical fact per CIK/concept as of a timestamp.

    Selection is strictly point-in-time:
      - only filings accepted on/before as_of are eligible
      - later amendments replace earlier filings only after their acceptance time
      - within eligible facts, prefer the most recent reported period, then the
        most recently accepted filing for that period
    """

    required = {
        "cik",
        "concept",
        "ddate_date",
        "accepted_at",
        "value",
        "source_tag",
    }
    missing = required - set(winner_facts.columns)
    if missing:
        raise ValueError(
            "Winner facts missing required columns: "
            + ", ".join(sorted(missing))
        )

    eligible = winner_facts.loc[
        winner_facts["accepted_at"].notna()
        & (winner_facts["accepted_at"] <= as_of)
    ].copy()

    if eligible.empty:
        return eligible

    eligible = eligible.sort_values(
        ["cik", "concept", "ddate_date", "accepted_at"],
        ascending=[True, True, True, True],
        kind="stable",
    )

    latest = (
        eligible.groupby(["cik", "concept"], as_index=False, sort=False)
        .tail(1)
        .reset_index(drop=True)
    )
    return latest


def pivot_snapshot(latest_facts: pd.DataFrame) -> pd.DataFrame:
    """Pivot latest canonical facts into one row per CIK for model features."""

    if latest_facts.empty:
        return pd.DataFrame()

    values = latest_facts.pivot(
        index="cik",
        columns="concept",
        values="value",
    ).reset_index()
    values.columns.name = None
    return values


ANNUAL_DURATION_CONCEPTS = {
    "revenue",
    "net_income",
    "operating_income",
    "operating_cash_flow",
    "capital_expenditures",
}


def annual_duration_facts(winner_facts: pd.DataFrame) -> pd.DataFrame:
    """Return annual qtrs=4 duration facts suitable for comparable valuation inputs."""

    required = {"concept", "qtrs"}
    missing = required - set(winner_facts.columns)
    if missing:
        raise ValueError(
            "Winner facts missing annual-filter columns: "
            + ", ".join(sorted(missing))
        )

    qtrs = pd.to_numeric(winner_facts["qtrs"], errors="coerce")
    return winner_facts.loc[
        winner_facts["concept"].isin(ANNUAL_DURATION_CONCEPTS)
        & qtrs.eq(4)
    ].copy()


def pivot_annual_snapshot(latest_annual_facts: pd.DataFrame) -> pd.DataFrame:
    """Pivot latest annual PIT facts using annual_ column prefixes."""

    if latest_annual_facts.empty:
        return pd.DataFrame()

    values = latest_annual_facts.pivot(
        index="cik",
        columns="concept",
        values="value",
    ).reset_index()
    values.columns.name = None
    values = values.rename(
        columns={
            column: f"annual_{column}"
            for column in values.columns
            if column != "cik"
        }
    )
    return values
