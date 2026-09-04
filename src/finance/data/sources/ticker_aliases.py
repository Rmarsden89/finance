from __future__ import annotations

import csv
from pathlib import Path


_UNSAFE_MARKERS = (
    "merger",
    "merged",
    "acquired",
    "acquisition",
    "restructuring",
    "spin-off",
    "spinoff",
    "forming",
    "combined company",
)


def load_safe_ticker_aliases(
    path: str | Path,
    *,
    cik_by_ticker: dict[str, int],
) -> dict[str, int]:
    """Resolve old tickers through conservative same-company rename evidence.

    Only rename rows whose reason looks like a plain rename/ticker change are
    eligible. Merger, acquisition, restructuring, and spin-off rows are
    intentionally excluded because the successor ticker may represent a
    different SEC registrant/CIK.
    """

    aliases: dict[str, int] = {}

    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            old_ticker = row["old_ticker"].strip().upper()
            new_ticker = row["new_ticker"].strip().upper()
            reason = (row.get("reason") or "").strip().lower()

            if not _looks_like_safe_rename(reason):
                continue

            cik = cik_by_ticker.get(new_ticker)
            if cik is not None:
                aliases[old_ticker] = cik

    return aliases


def _looks_like_safe_rename(reason: str) -> bool:
    if any(marker in reason for marker in _UNSAFE_MARKERS):
        return False

    return (
        "renamed" in reason
        or "ticker change" in reason
        or "ticker changes" in reason
    )
