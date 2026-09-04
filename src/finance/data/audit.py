from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from .membership import MembershipStore
from .models import MembershipInterval


@dataclass(frozen=True)
class UniverseAuditRow:
    as_of: date
    members: int
    cik_resolved: int
    cik_unresolved: int
    cik_coverage: float


def audit_dates(
    intervals: Iterable[MembershipInterval],
    dates: Iterable[date],
) -> list[UniverseAuditRow]:
    store = MembershipStore(intervals)
    rows: list[UniverseAuditRow] = []

    for as_of in dates:
        coverage = store.coverage(as_of)
        rows.append(
            UniverseAuditRow(
                as_of=as_of,
                members=int(coverage["members"]),
                cik_resolved=int(coverage["cik_resolved"]),
                cik_unresolved=int(coverage["cik_unresolved"]),
                cik_coverage=float(coverage["cik_coverage"]),
            )
        )

    return rows


def unique_constituents(
    intervals: Iterable[MembershipInterval],
    *,
    start_date: date,
) -> list[MembershipInterval]:
    """Return one representative interval per ticker active at/after start_date."""

    representatives: dict[str, MembershipInterval] = {}
    for interval in intervals:
        if interval.end_date is not None and interval.end_date <= start_date:
            continue
        representatives.setdefault(interval.ticker, interval)

    return sorted(representatives.values(), key=lambda row: row.ticker)
