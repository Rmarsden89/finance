from __future__ import annotations

from datetime import date
from typing import Iterable

from .models import SecurityRecord


class SecurityMaster:
    """Resolve time-bounded tickers to stable SEC CIK identifiers."""

    def __init__(self, records: Iterable[SecurityRecord]) -> None:
        self._records = tuple(records)

    def resolve_ticker(self, ticker: str, as_of: date) -> SecurityRecord | None:
        ticker = ticker.upper()
        matches = [
            record
            for record in self._records
            if record.ticker.upper() == ticker
            and (record.ticker_valid_from is None or record.ticker_valid_from <= as_of)
            and (record.ticker_valid_to is None or as_of < record.ticker_valid_to)
        ]

        if len(matches) > 1:
            raise ValueError(f"Ambiguous ticker {ticker} as of {as_of}")
        return matches[0] if matches else None

    def records_for_cik(self, cik: int) -> list[SecurityRecord]:
        return sorted(
            (record for record in self._records if record.cik == cik),
            key=lambda record: record.ticker_valid_from or date.min,
        )
