from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class HistoricalIdentityOverride:
    ticker: str
    cik: int
    valid_from: date
    valid_to: date | None
    company_name: str | None
    evidence: str | None

    def contains(self, as_of: date) -> bool:
        return self.valid_from <= as_of and (
            self.valid_to is None or as_of < self.valid_to
        )


def load_historical_identity_overrides(
    path: str | Path,
) -> list[HistoricalIdentityOverride]:
    rows: list[HistoricalIdentityOverride] = []

    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                HistoricalIdentityOverride(
                    ticker=row["ticker"].strip().upper(),
                    cik=int(row["cik"]),
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


def override_cik_as_of(
    overrides: list[HistoricalIdentityOverride],
    *,
    ticker: str,
    as_of: date,
) -> HistoricalIdentityOverride | None:
    matches = [
        row
        for row in overrides
        if row.ticker == ticker.upper() and row.contains(as_of)
    ]

    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous historical identity override for {ticker} as of {as_of}"
        )

    return matches[0] if matches else None
