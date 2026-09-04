from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SecurityRecord:
    """Canonical company/security identity used across research datasets.

    CIK is the stable company identifier. A ticker is a time-bounded attribute,
    not a primary key. Raw historical records may remain unresolved until a CIK
    can be supported by source evidence.
    """

    cik: int | None
    ticker: str
    company_name: str | None = None
    exchange: str | None = None
    ticker_valid_from: date | None = None
    ticker_valid_to: date | None = None
    source: str | None = None

    @property
    def resolved(self) -> bool:
        return self.cik is not None


@dataclass(frozen=True)
class MembershipInterval:
    """One point-in-time index membership interval; end_date is exclusive."""

    index_name: str
    ticker: str
    start_date: date
    end_date: date | None
    cik: int | None = None
    company_name: str | None = None
    source: str | None = None

    def contains(self, as_of: date) -> bool:
        return self.start_date <= as_of and (
            self.end_date is None or as_of < self.end_date
        )
