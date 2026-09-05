from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

import pandas as pd

from .historical_identity_overrides import HistoricalIdentityOverride
from .historical_market_tickers import HistoricalMarketTickerOverride
from .research_panel import CachedPriceStore, build_research_snapshot
from .sec_entity_history import SecEntityEvidenceCursor, SecEntityEvent
from .sec_snapshot import SecWinnerFactCursor


@dataclass(frozen=True)
class WeeklyPanelAudit:
    decision_weeks: int
    panel_rows: int
    average_members: float
    identity_resolved_rows: int
    price_available_rows: int
    fundamentals_available_rows: int
    research_ready_rows: int


def weekly_decision_timestamps(
    *,
    start: date,
    end: date,
    decision_time: time = time(16, 0),
) -> list[datetime]:
    """Return Friday decision timestamps between start and end, inclusive."""

    if end < start:
        raise ValueError("end must be on or after start")

    fridays = pd.date_range(start=start, end=end, freq="W-FRI")
    return [
        datetime.combine(timestamp.date(), decision_time)
        for timestamp in fridays
    ]


def build_weekly_research_panel(
    intervals,
    *,
    winner_facts: pd.DataFrame,
    sec_entity_events: list[SecEntityEvent],
    tiingo_cache_dir,
    start: date,
    end: date,
    identity_overrides: list[HistoricalIdentityOverride] | None = None,
    market_ticker_overrides: list[HistoricalMarketTickerOverride] | None = None,
) -> tuple[pd.DataFrame, WeeklyPanelAudit]:
    """Build weekly PIT research snapshots without future information leakage."""

    timestamps = weekly_decision_timestamps(start=start, end=end)
    if not timestamps:
        empty = pd.DataFrame()
        return empty, WeeklyPanelAudit(0, 0, 0.0, 0, 0, 0, 0)

    fact_cursor = SecWinnerFactCursor(winner_facts)
    entity_cursor = SecEntityEvidenceCursor(sec_entity_events)
    price_store = CachedPriceStore(tiingo_cache_dir)

    frames: list[pd.DataFrame] = []

    for as_of in timestamps:
        latest_facts = fact_cursor.as_of(as_of)
        entity_evidence = entity_cursor.as_of(as_of)

        snapshot = build_research_snapshot(
            intervals,
            winner_facts=None,
            tiingo_cache_dir=tiingo_cache_dir,
            as_of=as_of,
            sec_entity_evidence=entity_evidence,
            identity_overrides=identity_overrides,
            market_ticker_overrides=market_ticker_overrides,
            latest_facts=latest_facts,
            price_store=price_store,
        ).copy()

        snapshot.insert(0, "decision_date", as_of.date())
        snapshot.insert(1, "as_of", as_of)

        snapshot["price_age_days"] = snapshot["price_date"].apply(
            lambda value: (
                (as_of.date() - value).days
                if pd.notna(value)
                else None
            )
        )
        snapshot["research_ready"] = (
            snapshot["identity_resolved"]
            & snapshot["price_available"]
            & snapshot["fundamentals_available"]
        )

        frames.append(snapshot)

    panel = pd.concat(frames, ignore_index=True)

    audit = WeeklyPanelAudit(
        decision_weeks=len(timestamps),
        panel_rows=len(panel),
        average_members=(
            float(len(panel)) / float(len(timestamps))
            if timestamps
            else 0.0
        ),
        identity_resolved_rows=int(panel["identity_resolved"].sum()),
        price_available_rows=int(panel["price_available"].sum()),
        fundamentals_available_rows=int(panel["fundamentals_available"].sum()),
        research_ready_rows=int(panel["research_ready"].sum()),
    )

    return panel, audit
