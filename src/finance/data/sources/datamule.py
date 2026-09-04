from __future__ import annotations

import ast
import csv
import gzip
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from ..models import MembershipInterval


_CORP_WORDS = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "ltd",
    "limited",
    "plc",
    "llc",
    "lp",
    "holdings",
    "holding",
    "group",
}


def load_datamule_identity_map(
    *,
    metadata_path: str | Path,
    names_path: str | Path,
    unresolved: Iterable[MembershipInterval],
) -> dict[str, int]:
    """Resolve unresolved historical tickers from SEC-derived Datamule files.

    Resolution rules are intentionally conservative:
      1. unique exact ticker -> CIK match in listed filer metadata
      2. unique normalized company/former-name -> CIK match

    Ambiguous matches are skipped.
    """

    metadata_path = Path(metadata_path)
    names_path = Path(names_path)

    ticker_to_ciks: dict[str, set[int]] = defaultdict(set)
    name_to_ciks: dict[str, set[int]] = defaultdict(set)

    with gzip.open(metadata_path, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            cik = _parse_cik(row.get("cik"))
            if cik is None:
                continue

            for ticker in _parse_tickers(row.get("tickers")):
                ticker_to_ciks[ticker].add(cik)

            name = _normalize_name(row.get("name"))
            if name:
                name_to_ciks[name].add(cik)

    with gzip.open(names_path, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            cik = _parse_cik(row.get("cik"))
            name = _normalize_name(row.get("name"))
            if cik is not None and name:
                name_to_ciks[name].add(cik)

    resolved: dict[str, int] = {}

    for record in unresolved:
        ticker = record.ticker.upper()

        ticker_matches = ticker_to_ciks.get(ticker, set())
        if len(ticker_matches) == 1:
            resolved[ticker] = next(iter(ticker_matches))
            continue

        normalized_name = _normalize_name(record.company_name)
        if normalized_name:
            name_matches = name_to_ciks.get(normalized_name, set())
            if len(name_matches) == 1:
                resolved[ticker] = next(iter(name_matches))

    return resolved


def _parse_cik(value: str | None) -> int | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    try:
        return int(text)
    except ValueError:
        return None


def _parse_tickers(value: str | None) -> set[str]:
    if value is None:
        return set()

    text = str(value).strip()
    if not text:
        return set()

    # Datamule may serialize a ticker collection as a Python/JSON-like list.
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, (list, tuple, set)):
                return {
                    str(item).strip().upper()
                    for item in parsed
                    if str(item).strip()
                }
        except (ValueError, SyntaxError):
            pass

    return {
        token.strip().strip("'\"").upper()
        for token in re.split(r"[;,|\s]+", text)
        if token.strip().strip("'\"")
    }


def _normalize_name(value: str | None) -> str:
    if not value:
        return ""

    text = str(value).lower().replace("&", " and ")
    words = re.findall(r"[a-z0-9]+", text)
    filtered = [word for word in words if word not in _CORP_WORDS]
    return " ".join(filtered)
