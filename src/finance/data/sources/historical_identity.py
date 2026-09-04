from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HistoricalIdentityContext:
    ticker: str
    company_name: str | None
    removal_reason: str | None
    rename_successor: str | None
    category: str


def load_identity_context(
    *,
    changes_path: str | Path,
    rename_path: str | Path | None = None,
) -> dict[str, HistoricalIdentityContext]:
    """Build research context for unresolved historical tickers.

    This does not resolve CIKs. It only classifies the evidence already present
    in the PIT source so ambiguous identities can be researched deliberately.
    """

    contexts: dict[str, HistoricalIdentityContext] = {}

    rename_successors: dict[str, str] = {}
    if rename_path is not None and Path(rename_path).exists():
        with Path(rename_path).open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                rename_successors[row["old_ticker"].strip().upper()] = (
                    row["new_ticker"].strip().upper()
                )

    with Path(changes_path).open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        if row["action"].strip().lower() != "removed":
            continue

        ticker = row["ticker"].strip().upper()
        name = (row.get("name") or "").strip() or None
        reason = (row.get("reason") or "").strip() or None
        successor = rename_successors.get(ticker)
        category = classify_identity_case(
            reason=reason,
            has_rename_successor=successor is not None,
        )

        contexts[ticker] = HistoricalIdentityContext(
            ticker=ticker,
            company_name=name,
            removal_reason=reason,
            rename_successor=successor,
            category=category,
        )

    return contexts


def classify_identity_case(
    *,
    reason: str | None,
    has_rename_successor: bool,
) -> str:
    text = (reason or "").lower()

    if has_rename_successor:
        return "rename_or_successor_review"
    if any(word in text for word in ("bankrupt", "bankruptcy", "liquidat", "receivership")):
        return "bankruptcy_or_failure"
    if any(word in text for word in ("acquired", "acquisition", "buyout", "purchased")):
        return "acquisition"
    if any(word in text for word in ("merger", "merged", "combination")):
        return "merger"
    if any(word in text for word in ("delist", "removed", "no longer eligible")):
        return "delisting_or_removal"
    if any(word in text for word in ("rename", "ticker change", "changed ticker")):
        return "rename_review"
    return "historical_identity_research"
