from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile


@dataclass(frozen=True)
class SecEntityEvent:
    cik: int
    accepted_at: datetime
    name: str


@dataclass(frozen=True)
class SecEntityEvidence:
    cik: int
    first_accepted_at: datetime
    last_accepted_at: datetime
    names: tuple[str, ...]


def load_sec_entity_events(
    zip_paths: list[str | Path],
    *,
    through: datetime | None = None,
) -> list[SecEntityEvent]:
    """Load SEC registrant/name events from quarterly sub.txt files once.

    Events are sorted by acceptance timestamp so callers can advance through
    history without rescanning every SEC ZIP for each research date.
    """

    events: list[SecEntityEvent] = []

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
                    if through is not None and accepted > through:
                        continue

                    events.append(
                        SecEntityEvent(
                            cik=cik,
                            accepted_at=accepted,
                            name=str(row.get("name") or "").strip(),
                        )
                    )

    return sorted(events, key=lambda row: row.accepted_at)


class SecEntityEvidenceCursor:
    """Incrementally materialize SEC entity evidence at increasing timestamps."""

    def __init__(self, events: list[SecEntityEvent]) -> None:
        self._events = events
        self._position = 0
        self._rows_by_cik: dict[int, list[SecEntityEvent]] = {}

    def as_of(self, timestamp: datetime) -> dict[int, SecEntityEvidence]:
        while (
            self._position < len(self._events)
            and self._events[self._position].accepted_at <= timestamp
        ):
            event = self._events[self._position]
            self._rows_by_cik.setdefault(event.cik, []).append(event)
            self._position += 1

        evidence: dict[int, SecEntityEvidence] = {}
        for cik, rows in self._rows_by_cik.items():
            names = tuple(sorted({row.name for row in rows if row.name}))
            evidence[cik] = SecEntityEvidence(
                cik=cik,
                first_accepted_at=rows[0].accepted_at,
                last_accepted_at=rows[-1].accepted_at,
                names=names,
            )

        return evidence


def load_sec_entity_evidence(
    zip_paths: list[str | Path],
    *,
    as_of: datetime | None = None,
) -> dict[int, SecEntityEvidence]:
    """Build historical SEC filer evidence from quarterly sub.txt files."""

    events = load_sec_entity_events(zip_paths, through=as_of)
    cursor = SecEntityEvidenceCursor(events)

    if not events:
        return {}

    cutoff = as_of or events[-1].accepted_at
    return cursor.as_of(cutoff)


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
