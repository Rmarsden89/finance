from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .historical_identity import resolve_memberships_as_of
from .historical_identity_overrides import HistoricalIdentityOverride
from .membership import MembershipStore
from .sec_entity_history import SecEntityEvidence
from .sec_snapshot import latest_facts_as_of, pivot_snapshot


def build_research_snapshot(
    intervals,
    *,
    winner_facts: pd.DataFrame,
    tiingo_cache_dir: str | Path,
    as_of: datetime,
    sec_entity_evidence: dict[int, SecEntityEvidence] | None = None,
    identity_overrides: list[HistoricalIdentityOverride] | None = None,
) -> pd.DataFrame:
    """Build one point-in-time research snapshot for S&P 500 members.

    If SEC historical entity evidence is supplied, identities are validated and
    repaired as-of the simulated timestamp before fundamentals are joined.
    Missing identities/prices/fundamentals remain visible.
    """

    as_of_date = as_of.date()
    members = MembershipStore(intervals).members_as_of(as_of_date)

    if sec_entity_evidence is not None:
        resolutions = resolve_memberships_as_of(
            members,
            evidence_by_cik=sec_entity_evidence,
            overrides=identity_overrides,
            as_of_date=as_of_date,
        )
    else:
        resolutions = None

    universe_rows = []
    for index, row in enumerate(members):
        if resolutions is None:
            resolved_cik = row.cik
            method = "not_validated"
        else:
            resolution = resolutions[index]
            resolved_cik = resolution.resolved_cik
            method = resolution.method

        universe_rows.append(
            {
                "ticker": row.ticker,
                "original_cik": row.cik,
                "cik": resolved_cik,
                "company_name": row.company_name,
                "membership_start": row.start_date,
                "membership_end": row.end_date,
                "identity_resolution_method": method,
                "identity_resolved": resolved_cik is not None,
            }
        )

    universe = pd.DataFrame(universe_rows)

    latest = latest_facts_as_of(winner_facts, as_of)
    fundamentals = pivot_snapshot(latest)

    panel = universe.merge(
        fundamentals,
        on="cik",
        how="left",
    )

    prices = []
    for ticker in universe["ticker"]:
        quote = _latest_cached_price(
            Path(tiingo_cache_dir),
            ticker,
            as_of_date,
        )
        prices.append(
            {
                "ticker": ticker,
                "price_date": quote["date"] if quote else None,
                "close": quote["close"] if quote else None,
                "adjusted_close": quote["adjusted_close"] if quote else None,
                "price_available": quote is not None,
            }
        )

    price_frame = pd.DataFrame(prices)
    panel = panel.merge(price_frame, on="ticker", how="left")

    fundamental_columns = [
        column
        for column in (
            "revenue",
            "net_income",
            "operating_income",
            "total_assets",
            "total_liabilities",
            "shareholders_equity",
            "cash",
            "operating_cash_flow",
            "capital_expenditures",
            "shares_outstanding",
        )
        if column in panel.columns
    ]

    if fundamental_columns:
        panel["fundamentals_available"] = panel[
            fundamental_columns
        ].notna().any(axis=1)
    else:
        panel["fundamentals_available"] = False

    return panel.sort_values("ticker").reset_index(drop=True)


def _latest_cached_price(
    cache_dir: Path,
    ticker: str,
    as_of: date,
) -> dict | None:
    candidates: list[dict] = []

    for path in cache_dir.glob(f"{ticker.upper()}_*.csv"):
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    row_date = date.fromisoformat(row["date"])
                except (KeyError, ValueError):
                    continue
                if row_date > as_of:
                    continue

                candidates.append(
                    {
                        "date": row_date,
                        "close": _float_or_none(row.get("close")),
                        "adjusted_close": _float_or_none(row.get("adjusted_close")),
                    }
                )

    if not candidates:
        return None

    return max(candidates, key=lambda row: row["date"])


def _float_or_none(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None
