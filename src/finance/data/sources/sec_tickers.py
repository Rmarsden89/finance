from __future__ import annotations

import json
from pathlib import Path

from ..models import SecurityRecord


def load_sec_company_tickers(path: str | Path) -> list[SecurityRecord]:
    """Load SEC company_tickers.json into canonical security records.

    SEC publishes this as a periodically updated CIK/ticker/company-name
    association. It is current-state data and does not establish historical
    ticker-validity dates on its own.
    """

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    records: list[SecurityRecord] = []

    for entry in payload.values():
        records.append(
            SecurityRecord(
                cik=int(entry["cik_str"]),
                ticker=entry["ticker"].strip().upper(),
                company_name=entry.get("title"),
                source="sec_company_tickers",
            )
        )

    return records


def sec_cik_map(path: str | Path) -> dict[str, int]:
    """Return current SEC ticker -> CIK associations for conservative enrichment."""

    return {
        record.ticker.upper(): record.cik
        for record in load_sec_company_tickers(path)
        if record.cik is not None
    }
