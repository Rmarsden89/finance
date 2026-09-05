from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from zipfile import ZipFile


_CORP_WORDS = {
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "ltd", "limited", "plc", "llc", "lp", "holdings", "holding", "group",
}


@dataclass(frozen=True)
class IdentityCandidate:
    ticker: str
    company_name: str | None
    candidate_cik: int
    sec_name: str
    method: str
    evidence_count: int
    score: float
    first_seen: datetime
    last_seen: datetime


def generate_identity_candidates(
    unresolved_rows: list[dict[str, str]],
    *,
    sec_zip_paths: list[str | Path],
    as_of: datetime,
    fuzzy_threshold: float = 0.92,
    fuzzy_margin: float = 0.04,
) -> list[IdentityCandidate]:
    """Generate conservative SEC-backed candidates for unresolved tickers.

    Candidate evidence:
      - exact historical ticker token/prefix in SEC instance filename
      - exact normalized company-name match
      - high-confidence unique fuzzy company-name match

    This function only proposes candidates. It does not mutate identity maps.
    """

    target_tickers = {
        row["ticker"].strip().upper()
        for row in unresolved_rows
        if row.get("ticker")
    }

    instance_hits: dict[str, dict[int, list[tuple[datetime, str]]]] = {
        ticker: defaultdict(list) for ticker in target_tickers
    }
    names_by_cik: dict[int, set[str]] = defaultdict(set)
    accepted_by_cik: dict[int, list[datetime]] = defaultdict(list)

    for path in sorted(Path(item) for item in sec_zip_paths):
        with ZipFile(path) as archive:
            with archive.open("sub.txt") as handle:
                text = (line.decode("utf-8", errors="replace") for line in handle)
                reader = csv.DictReader(text, delimiter="\t")

                for row in reader:
                    accepted = _parse_accepted(row.get("accepted"))
                    if accepted is None or accepted > as_of:
                        continue

                    try:
                        cik = int(str(row.get("cik") or "").strip())
                    except ValueError:
                        continue

                    name = str(row.get("name") or "").strip()
                    instance = str(row.get("instance") or "").strip()

                    if name:
                        names_by_cik[cik].add(name)
                    accepted_by_cik[cik].append(accepted)

                    instance_token = _instance_leading_token(instance)
                    if instance_token and instance_token in target_tickers:
                        instance_hits[instance_token][cik].append((accepted, name))

    normalized_name_to_ciks: dict[str, set[int]] = defaultdict(set)
    normalized_names_by_cik: dict[int, set[str]] = defaultdict(set)

    for cik, names in names_by_cik.items():
        for name in names:
            normalized = _normalize_name(name)
            if normalized:
                normalized_name_to_ciks[normalized].add(cik)
                normalized_names_by_cik[cik].add(normalized)

    candidates: list[IdentityCandidate] = []

    for unresolved in unresolved_rows:
        ticker = unresolved.get("ticker", "").strip().upper()
        company_name = (unresolved.get("company_name") or "").strip() or None

        ticker_candidates = instance_hits.get(ticker, {})
        if ticker_candidates:
            ranked = sorted(
                ticker_candidates.items(),
                key=lambda item: (-len(item[1]), item[0]),
            )
            best_cik, evidence = ranked[0]
            if len(ranked) == 1 or len(evidence) > len(ranked[1][1]):
                sec_name = _most_common_name(evidence)
                candidates.append(
                    IdentityCandidate(
                        ticker=ticker,
                        company_name=company_name,
                        candidate_cik=best_cik,
                        sec_name=sec_name,
                        method="sec_instance_ticker",
                        evidence_count=len(evidence),
                        score=1.0,
                        first_seen=min(item[0] for item in evidence),
                        last_seen=max(item[0] for item in evidence),
                    )
                )
                continue

        normalized = _normalize_name(company_name)
        if not normalized:
            continue

        exact = normalized_name_to_ciks.get(normalized, set())
        if len(exact) == 1:
            cik = next(iter(exact))
            dates = accepted_by_cik[cik]
            candidates.append(
                IdentityCandidate(
                    ticker=ticker,
                    company_name=company_name,
                    candidate_cik=cik,
                    sec_name=_representative_name(names_by_cik[cik]),
                    method="sec_name_exact",
                    evidence_count=len(dates),
                    score=1.0,
                    first_seen=min(dates),
                    last_seen=max(dates),
                )
            )
            continue

        scored: list[tuple[float, int]] = []
        for cik, sec_names in normalized_names_by_cik.items():
            score = max(
                SequenceMatcher(None, normalized, sec_name).ratio()
                for sec_name in sec_names
            )
            if score >= fuzzy_threshold:
                scored.append((score, cik))

        scored.sort(reverse=True)
        if not scored:
            continue

        best_score, best_cik = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if best_score - second_score < fuzzy_margin:
            continue

        dates = accepted_by_cik[best_cik]
        candidates.append(
            IdentityCandidate(
                ticker=ticker,
                company_name=company_name,
                candidate_cik=best_cik,
                sec_name=_representative_name(names_by_cik[best_cik]),
                method="sec_name_fuzzy_candidate",
                evidence_count=len(dates),
                score=best_score,
                first_seen=min(dates),
                last_seen=max(dates),
            )
        )

    return sorted(candidates, key=lambda row: row.ticker)


def _instance_leading_token(instance: str) -> str:
    if not instance:
        return ""

    stem = Path(instance).name.rsplit(".", 1)[0].upper()
    match = re.match(r"([A-Z][A-Z0-9]{0,5})(?:[-_]|\d)", stem)
    if match:
        return match.group(1)

    token = re.split(r"[-_.]", stem, maxsplit=1)[0]
    if 1 <= len(token) <= 6 and token.isalnum():
        return token
    return ""


def _normalize_name(value: str | None) -> str:
    if not value:
        return ""
    text = value.lower().replace("&", " and ")
    words = re.findall(r"[a-z0-9]+", text)
    return " ".join(word for word in words if word not in _CORP_WORDS)


def _parse_accepted(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) >= 14:
        return datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
    if len(digits) == 8:
        return datetime.strptime(digits, "%Y%m%d")
    return None


def _most_common_name(evidence: list[tuple[datetime, str]]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for _, name in evidence:
        if name:
            counts[name] += 1
    if not counts:
        return ""
    return max(counts, key=counts.get)


def _representative_name(names: set[str]) -> str:
    if not names:
        return ""
    return sorted(names, key=lambda value: (len(value), value))[0]
