from __future__ import annotations

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


def load_sec_historical_name_map(
    path: str | Path,
    *,
    unresolved: Iterable[MembershipInterval],
) -> dict[str, int]:
    """Resolve historical tickers through SEC's cumulative CIK/name lookup.

    Expected SEC format is one record per line with company name and CIK
    separated by a colon. Only unique normalized company-name matches resolve.
    Ambiguous names are intentionally skipped.
    """

    name_to_ciks: dict[str, set[int]] = defaultdict(set)

    for raw_line in Path(path).read_text(
        encoding="latin-1",
        errors="ignore",
    ).splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue

        name, cik_text = line.rsplit(":", 1)
        name_key = _normalize_name(name)

        try:
            cik = int(cik_text.strip())
        except ValueError:
            continue

        if name_key:
            name_to_ciks[name_key].add(cik)

    resolved: dict[str, int] = {}

    for row in unresolved:
        key = _normalize_name(row.company_name)
        if not key:
            continue

        matches = name_to_ciks.get(key, set())
        if len(matches) == 1:
            resolved[row.ticker.upper()] = next(iter(matches))

    return resolved


def _normalize_name(value: str | None) -> str:
    if not value:
        return ""

    text = str(value).lower().replace("&", " and ")
    words = re.findall(r"[a-z0-9]+", text)
    filtered = [word for word in words if word not in _CORP_WORDS]
    return " ".join(filtered)
