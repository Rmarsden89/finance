from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class HistoricalMarketTickerOverride:
    pit_ticker: str
    market_ticker: str
    valid_from: date
    valid_to: date | None
    company_name: str | None = None
    evidence: str | None = None

    def contains(self, as_of: date) -> bool:
        return self.valid_from <= as_of and (
            self.valid_to is None or as_of < self.valid_to
        )


def load_historical_market_ticker_overrides(
    path: str | Path,
) -> list[HistoricalMarketTickerOverride]:
    rows: list[HistoricalMarketTickerOverride] = []

    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                HistoricalMarketTickerOverride(
                    pit_ticker=row["pit_ticker"].strip().upper(),
                    market_ticker=row["market_ticker"].strip().upper(),
                    valid_from=date.fromisoformat(row["valid_from"]),
                    valid_to=(
                        date.fromisoformat(row["valid_to"])
                        if row.get("valid_to")
                        else None
                    ),
                    company_name=(row.get("company_name") or "").strip() or None,
                    evidence=(row.get("evidence") or "").strip() or None,
                )
            )

    return rows


def market_ticker_as_of(
    overrides: list[HistoricalMarketTickerOverride],
    *,
    pit_ticker: str,
    as_of: date,
) -> str:
    matches = [
        row.market_ticker
        for row in overrides
        if row.pit_ticker == pit_ticker.upper() and row.contains(as_of)
    ]

    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous market ticker override for {pit_ticker} as of {as_of}"
        )

    return matches[0] if matches else pit_ticker.upper()
