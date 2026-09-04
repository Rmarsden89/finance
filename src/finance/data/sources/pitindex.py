from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..models import MembershipInterval


@dataclass(frozen=True)
class PitIndexEvent:
    date: date
    action: str
    ticker: str
    company_name: str | None = None


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_pitindex_sp500(
    source_dir: str | Path,
    *,
    sec_cik_by_ticker: dict[str, int] | None = None,
) -> list[MembershipInterval]:
    """Convert pitindex's seed + event log into canonical membership intervals.

    The source directory must contain:
      - sp500_seed.csv
      - sp500_changes.csv
      - sp500_current.csv

    CIK enrichment is conservative. The bundled current roster provides CIKs
    only for current constituents. An optional SEC ticker map can fill more
    identities, but unresolved historical tickers remain explicitly unresolved.
    """

    source_dir = Path(source_dir)
    seed_rows = _read_csv(source_dir / "sp500_seed.csv")
    change_rows = _read_csv(source_dir / "sp500_changes.csv")
    current_rows = _read_csv(source_dir / "sp500_current.csv")

    if not seed_rows:
        raise ValueError("sp500_seed.csv is empty")

    current_cik = {
        row["ticker"].strip().upper(): int(row["cik"])
        for row in current_rows
        if row.get("ticker") and row.get("cik")
    }
    current_name = {
        row["ticker"].strip().upper(): row.get("name") or None
        for row in current_rows
        if row.get("ticker")
    }
    sec_cik_by_ticker = {
        ticker.upper(): cik for ticker, cik in (sec_cik_by_ticker or {}).items()
    }

    seed_date = date.fromisoformat(seed_rows[0]["effective_date"])
    open_intervals: dict[str, date] = {}
    names: dict[str, str | None] = {}

    for row in seed_rows:
        ticker = row["ticker"].strip().upper()
        open_intervals[ticker] = seed_date

    intervals: list[MembershipInterval] = []

    for row in sorted(change_rows, key=lambda r: (r["date"], r["action"], r["ticker"])):
        event_date = date.fromisoformat(row["date"])
        action = row["action"].strip().lower()
        ticker = row["ticker"].strip().upper()
        event_name = (row.get("name") or "").strip() or None
        if event_name:
            names[ticker] = event_name

        if action == "removed":
            start_date = open_intervals.pop(ticker, None)
            if start_date is None:
                # Some reconstructed event logs can contain correction events.
                # Do not invent an interval if we cannot prove the start.
                continue
            intervals.append(
                _interval(
                    ticker=ticker,
                    start_date=start_date,
                    end_date=event_date,
                    current_cik=current_cik,
                    sec_cik_by_ticker=sec_cik_by_ticker,
                    names=names,
                    current_name=current_name,
                )
            )
        elif action == "added":
            if ticker not in open_intervals:
                open_intervals[ticker] = event_date
        else:
            raise ValueError(f"Unknown pitindex action: {action}")

    for ticker, start_date in open_intervals.items():
        intervals.append(
            _interval(
                ticker=ticker,
                start_date=start_date,
                end_date=None,
                current_cik=current_cik,
                sec_cik_by_ticker=sec_cik_by_ticker,
                names=names,
                current_name=current_name,
            )
        )

    return sorted(intervals, key=lambda r: (r.ticker, r.start_date))


def _interval(
    *,
    ticker: str,
    start_date: date,
    end_date: date | None,
    current_cik: dict[str, int],
    sec_cik_by_ticker: dict[str, int],
    names: dict[str, str | None],
    current_name: dict[str, str | None],
) -> MembershipInterval:
    return MembershipInterval(
        index_name="sp500",
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        cik=sec_cik_by_ticker.get(ticker) or current_cik.get(ticker),
        company_name=names.get(ticker) or current_name.get(ticker),
        source="pitindex",
    )
