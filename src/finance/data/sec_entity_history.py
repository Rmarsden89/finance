from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zipfile import ZipFile


@dataclass(frozen=True)
class SecEntityEvidence:
    cik: int
    first_accepted_at: datetime
    last_accepted_at: datetime
    names: tuple[str, ...]


def load_sec_entity_evidence(
    zip_paths: list[str | Path],
    *,
    as_of: datetime | None = None,
) -> dict[int, SecEntityEvidence]:
    """Build historical SEC filer evidence from quarterly sub.txt files."""

    rows_by_cik: dict[int, list[tuple[datetime, str]]] = {}

    for path in sorted(Path(item) for item in zip_paths):
        with ZipFile(path) as archive:
            with archive.open("sub.txt") as handle:
                text = (line.decode("utf-8", errors="replace") for line in handle)
                reader = csv.DictReader(text, delimiter="\t")

                for row in reader:
                    try:
                        cik = int(str(row.get("cik") or "").strip())
                    except ValueError:
                        continue

                    accepted = _parse_accepted(row.get("accepted"))
                    if accepted is None:
                        continue
                    if as_of is not None and accepted > as_of:
                        continue

                    name = str(row.get("name") or "").strip()
                    rows_by_cik.setdefault(cik, []).append((accepted, name))

    evidence: dict[int, SecEntityEvidence] = {}
    for cik, rows in rows_by_cik.items():
        ordered = sorted(rows, key=lambda item: item[0])
        names = tuple(sorted({name for _, name in ordered if name}))
        evidence[cik] = SecEntityEvidence(
            cik=cik,
            first_accepted_at=ordered[0][0],
            last_accepted_at=ordered[-1][0],
            names=names,
        )

    return evidence


def _parse_accepted(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) >= 14:
        return datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
    if len(digits) == 8:
        return datetime.strptime(digits, "%Y%m%d")
    return None
