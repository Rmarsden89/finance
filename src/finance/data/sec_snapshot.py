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
