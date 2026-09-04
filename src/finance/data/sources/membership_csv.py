from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from ..models import MembershipInterval


def _parse_date(value: str) -> date | None:
    value = value.strip()
    return date.fromisoformat(value) if value else None


def load_membership_intervals(
    path: str | Path,
    *,
    index_name: str = "sp500",
    source: str,
) -> list[MembershipInterval]:
    """Load normalized point-in-time intervals from a CSV file.

    Required columns: ticker, start_date, end_date.
    Optional columns: cik, company_name.
    """

    records: list[MembershipInterval] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"ticker", "start_date", "end_date"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Membership CSV missing columns: {sorted(missing)}")

        for row in reader:
            cik_raw = (row.get("cik") or "").strip()
            records.append(
                MembershipInterval(
                    index_name=index_name,
                    ticker=row["ticker"].strip().upper(),
                    start_date=date.fromisoformat(row["start_date"].strip()),
                    end_date=_parse_date(row["end_date"]),
                    cik=int(cik_raw) if cik_raw else None,
                    company_name=(row.get("company_name") or "").strip() or None,
                    source=source,
                )
            )
    return records
