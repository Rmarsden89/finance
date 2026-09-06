from __future__ import annotations

import csv
import gzip
from bisect import bisect_right
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .historical_identity import resolve_memberships_as_of
from .historical_identity_overrides import HistoricalIdentityOverride
from .historical_market_tickers import HistoricalMarketTickerOverride
from .membership import MembershipStore
from .sec_entity_history import SecEntityEvidence
from .sec_snapshot import latest_facts_as_of, pivot_snapshot


class CanonicalPriceStore:
    """Load canonical PIT daily prices once and answer as-of lookups efficiently.

    The canonical dataset is keyed by PIT ticker and already encodes provider
    precedence, historical market-ticker mapping, membership-window filtering,
    and provider provenance. Research code must not reach back into raw provider
    caches.
    """

    def __init__(self, prices_path: str | Path) -> None:
        self._rows: dict[str, list[dict]] = {}
        self._dates: dict[str, list[date]] = {}

        path = Path(prices_path)
        opener = gzip.open if path.suffix.lower() == ".gz" else open

        with opener(path, "rt", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                pit_ticker = (row.get("pit_ticker") or "").strip().upper()
                if not pit_ticker:
                    continue

                try:
                    row_date = date.fromisoformat(row["date"])
                except (KeyError, ValueError):
                    continue

                self._rows.setdefault(pit_ticker, []).append(
                    {
                        "date": row_date,
                        "market_ticker": (
                            (row.get("market_ticker") or "").strip().upper()
                            or pit_ticker
                        ),
                        "close": _float_or_none(row.get("close")),
                        "adjusted_close": _float_or_none(
                            row.get("adjusted_close")
                        ),
                        "source": (row.get("source") or "").strip() or None,
                    }
                )

        for ticker, rows in self._rows.items():
            deduped = {row["date"]: row for row in rows}
            ordered = [
                deduped[row_date]
                for row_date in sorted(deduped)
            ]
            self._rows[ticker] = ordered
            self._dates[ticker] = [row["date"] for row in ordered]

    def latest_as_of(self, pit_ticker: str, as_of: date) -> dict | None:
        symbol = pit_ticker.upper()
        dates = self._dates.get(symbol)
        if not dates:
            return None

        position = bisect_right(dates, as_of) - 1
        if position < 0:
            return None

        return self._rows[symbol][position]


def build_research_snapshot(
    intervals,
    *,
    winner_facts: pd.DataFrame | None,
    canonical_prices: str | Path,
    as_of: datetime,
    sec_entity_evidence: dict[int, SecEntityEvidence] | None = None,
    identity_overrides: list[HistoricalIdentityOverride] | None = None,
    market_ticker_overrides: list[HistoricalMarketTickerOverride] | None = None,
    latest_facts: pd.DataFrame | None = None,
    price_store: CanonicalPriceStore | None = None,
) -> pd.DataFrame:
    """Build one point-in-time research snapshot for S&P 500 members.

    If SEC historical entity evidence is supplied, identities are validated and
    repaired as-of the simulated timestamp before fundamentals are joined.
    Missing identities/prices/fundamentals remain visible.

    Prices come only from the canonical market dataset. Provider selection and
    historical market-ticker reconciliation happen upstream in the canonical
    market-data build, not inside the research panel.

    For repeated historical snapshots, callers may supply pre-advanced latest
    facts and a reusable CanonicalPriceStore to avoid rescanning histories.
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

    if latest_facts is None:
        if winner_facts is None:
            raise ValueError(
                "winner_facts is required when latest_facts is not supplied"
            )
        latest = latest_facts_as_of(winner_facts, as_of)
    else:
        latest = latest_facts

    fundamentals = pivot_snapshot(latest)
    panel = universe.merge(fundamentals, on="cik", how="left")

    store = price_store or CanonicalPriceStore(canonical_prices)

    prices = []
    for _, universe_row in universe.iterrows():
        ticker = universe_row["ticker"]
        quote = store.latest_as_of(ticker, as_of_date)
        adjusted_close = quote["adjusted_close"] if quote else None
        close = quote["close"] if quote else None
        if adjusted_close is not None:
            return_price = adjusted_close
            return_price_basis = "adjusted_close"
        elif close is not None:
            return_price = close
            return_price_basis = "close_fallback"
        else:
            return_price = None
            return_price_basis = None

        prices.append(
            {
                "ticker": ticker,
                "market_ticker_used": (
                    quote["market_ticker"] if quote else None
                ),
                "price_source": quote["source"] if quote else None,
                "price_date": quote["date"] if quote else None,
                "close": close,
                "adjusted_close": adjusted_close,
                "return_price": return_price,
                "return_price_basis": return_price_basis,
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


def _float_or_none(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None
