from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Iterable

from .models import MembershipInterval


class MembershipStore:
    """In-memory point-in-time membership view over canonical intervals."""

    def __init__(self, intervals: Iterable[MembershipInterval]) -> None:
        self._intervals = tuple(intervals)
        self._validate()

    def _validate(self) -> None:
        for interval in self._intervals:
            if interval.end_date is not None and interval.end_date <= interval.start_date:
                raise ValueError(
                    f"Membership interval for {interval.ticker} has end_date on or before start_date"
                )

        groups: dict[tuple[str, str], list[MembershipInterval]] = {}
        for interval in self._intervals:
            groups.setdefault((interval.index_name, interval.ticker), []).append(interval)

        for key, records in groups.items():
            ordered = sorted(records, key=lambda record: record.start_date)
            for previous, current in zip(ordered, ordered[1:]):
                if previous.end_date is None or current.start_date < previous.end_date:
                    raise ValueError(f"Overlapping membership intervals for {key}")

    def members_as_of(
        self,
        as_of: date,
        *,
        index_name: str = "sp500",
        require_cik: bool = False,
    ) -> list[MembershipInterval]:
        members = [
            interval
            for interval in self._intervals
            if interval.index_name == index_name and interval.contains(as_of)
        ]
        if require_cik:
            members = [member for member in members if member.cik is not None]
        return sorted(members, key=lambda member: member.ticker)

    def coverage(self, as_of: date, *, index_name: str = "sp500") -> dict[str, float | int]:
        members = self.members_as_of(as_of, index_name=index_name)
        resolved = sum(member.cik is not None for member in members)
        total = len(members)
        return {
            "members": total,
            "cik_resolved": resolved,
            "cik_unresolved": total - resolved,
            "cik_coverage": (resolved / total) if total else 0.0,
        }

    def duplicate_ciks(self, as_of: date, *, index_name: str = "sp500") -> list[int]:
        counts = Counter(
            member.cik
            for member in self.members_as_of(as_of, index_name=index_name)
            if member.cik is not None
        )
        return sorted(cik for cik, count in counts.items() if count > 1)
