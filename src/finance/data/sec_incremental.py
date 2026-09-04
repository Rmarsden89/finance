from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .sec_snapshot import build_winner_facts
from .sources.sec_financial_statements import load_sec_financial_statement_zip


@dataclass(frozen=True)
class IncrementalSecAudit:
    zip_count: int
    processed_quarters: int
    reused_quarters: int
    combined_rows_before_dedup: int
    combined_rows_after_dedup: int
    duplicate_rows_removed: int
    unique_ciks: int


def materialize_sec_quarters_incrementally(
    zip_paths: list[str | Path],
    *,
    quarter_cache_dir: str | Path,
    force: bool = False,
) -> tuple[pd.DataFrame, IncrementalSecAudit]:
    """Materialize quarterly SEC winner facts once, then reuse cached outputs.

    Each source ZIP is cached to one CSV named after the ZIP stem. Existing
    caches are reused unless force=True. The combined history is deduplicated
    conservatively across quarterly caches.
    """

    quarter_cache_dir = Path(quarter_cache_dir)
    quarter_cache_dir.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []
    processed = 0
    reused = 0

    for zip_path in sorted(Path(path) for path in zip_paths):
        cache_path = quarter_cache_dir / f"{zip_path.stem}_winner_facts.csv"

        if cache_path.exists() and not force:
            frame = pd.read_csv(
                cache_path,
                parse_dates=["accepted_at"],
                low_memory=False,
            )
            frame = _restore_date_columns(frame)
            reused += 1
        else:
            quarter = load_sec_financial_statement_zip(zip_path)
            frame = build_winner_facts(
                quarter.submissions,
                quarter.numeric_facts,
                quarter.presentation,
            ).copy()
            frame["source_zip"] = zip_path.name
            frame.to_csv(cache_path, index=False)
            processed += 1

        if "source_zip" not in frame.columns:
            frame["source_zip"] = zip_path.name

        frames.append(frame)

    if not frames:
        empty = pd.DataFrame()
        return empty, IncrementalSecAudit(0, 0, 0, 0, 0, 0, 0)

    combined = pd.concat(frames, ignore_index=True)
    before = len(combined)

    dedup_columns = [
        column
        for column in (
            "adsh",
            "cik",
            "concept",
            "ddate_date",
            "qtrs",
            "uom",
            "accepted_at",
            "value",
            "source_tag",
        )
        if column in combined.columns
    ]

    combined = (
        combined.drop_duplicates(subset=dedup_columns, keep="first")
        .sort_values(
            [
                column
                for column in ("cik", "accepted_at", "concept", "ddate_date")
                if column in combined.columns
            ],
            kind="stable",
        )
        .reset_index(drop=True)
    )

    after = len(combined)

    audit = IncrementalSecAudit(
        zip_count=len(frames),
        processed_quarters=processed,
        reused_quarters=reused,
        combined_rows_before_dedup=before,
        combined_rows_after_dedup=after,
        duplicate_rows_removed=before - after,
        unique_ciks=int(combined["cik"].nunique(dropna=True)),
    )

    return combined, audit


def _restore_date_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in ("period_date", "filed_date", "ddate_date"):
        if column in result.columns:
            result[column] = pd.to_datetime(
                result[column],
                errors="coerce",
            ).dt.date
    return result
