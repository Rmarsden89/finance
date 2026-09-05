from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from .historical_identity_overrides import (
    HistoricalIdentityOverride,
    override_cik_as_of,
)
from .models import MembershipInterval
from .sec_entity_history import SecEntityEvidence


_CORP_WORDS = {
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "ltd", "limited", "plc", "llc", "lp", "holdings", "holding", "group",
}


@dataclass(frozen=True)
class HistoricalIdentityResolution:
    ticker: str
    original_cik: int | None
    resolved_cik: int | None
    method: str
    company_name: str | None


def resolve_membership_cik_as_of(
    member: MembershipInterval,
    *,
    evidence_by_cik: dict[int, SecEntityEvidence],
    overrides: list[HistoricalIdentityOverride] | None = None,
    as_of_date=None,
) -> HistoricalIdentityResolution:
    """Resolve one PIT membership identity using SEC evidence available as-of.

    Priority:
      1. Keep the membership CIK if it exists in historical SEC evidence.
      2. Otherwise, use a unique exact normalized company-name match.
      3. Otherwise leave unresolved rather than guess.
    """

    if member.cik is not None and member.cik in evidence_by_cik:
        return HistoricalIdentityResolution(
            ticker=member.ticker,
            original_cik=member.cik,
            resolved_cik=member.cik,
            method="existing_cik_verified",
            company_name=member.company_name,
        )

    if overrides is not None and as_of_date is not None:
        override = override_cik_as_of(
            overrides,
            ticker=member.ticker,
            as_of=as_of_date,
        )
        if override is not None:
            return HistoricalIdentityResolution(
                ticker=member.ticker,
                original_cik=member.cik,
                resolved_cik=override.cik,
                method="curated_sec_override",
                company_name=member.company_name or override.company_name,
            )

    normalized = _normalize_name(member.company_name)
    if normalized:
        reverse = _name_index(evidence_by_cik)
        matches = reverse.get(normalized, set())
        if len(matches) == 1:
            cik = next(iter(matches))
            return HistoricalIdentityResolution(
                ticker=member.ticker,
                original_cik=member.cik,
                resolved_cik=cik,
                method="sec_name_as_of",
                company_name=member.company_name,
            )

    return HistoricalIdentityResolution(
        ticker=member.ticker,
        original_cik=member.cik,
        resolved_cik=None,
        method="unresolved",
        company_name=member.company_name,
    )


def resolve_memberships_as_of(
    members: list[MembershipInterval],
    *,
    evidence_by_cik: dict[int, SecEntityEvidence],
    overrides: list[HistoricalIdentityOverride] | None = None,
    as_of_date=None,
) -> list[HistoricalIdentityResolution]:
    return [
        resolve_membership_cik_as_of(
            member,
            evidence_by_cik=evidence_by_cik,
            overrides=overrides,
            as_of_date=as_of_date,
        )
        for member in members
    ]


def _name_index(
    evidence_by_cik: dict[int, SecEntityEvidence],
) -> dict[str, set[int]]:
    result: dict[str, set[int]] = defaultdict(set)
    for cik, evidence in evidence_by_cik.items():
        for name in evidence.names:
            normalized = _normalize_name(name)
            if normalized:
                result[normalized].add(cik)
    return result


def _normalize_name(value: str | None) -> str:
    if not value:
        return ""

    text = value.lower().replace("&", " and ")
    words = re.findall(r"[a-z0-9]+", text)
    filtered = [word for word in words if word not in _CORP_WORDS]
    return " ".join(filtered)
