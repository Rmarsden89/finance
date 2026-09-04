from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .sec_snapshot import build_winner_facts
from .sources.sec_financial_statements import load_sec_financial_statement_zip


@dataclass(frozen=True)
class MultiQuarterAudit:
    zip_count: int
    winner_rows_before_dedup: int
    winner_rows_after_dedup: int
    duplicate_rows_removed: int
    unique_ciks: int


def build_multi_quarter_winner_facts(
    zip_paths: list[str | Path],
) -> tuple[pd.DataFrame, MultiQuarterAudit]:
    """Build one continuous winner-fact history across SEC quarterly ZIPs.

    SEC quarterly datasets can repeat the same filing/fact in adjacent quarters.
    Exact fact duplicates are removed conservatively using accession + concept +
    period + unit + acceptance time + value + source tag.
    """

    frames: list[pd.DataFrame] = []

    for path in sorted(Path(p) for p in zip_paths):
        quarter = load_sec_financial_statement_zip(path)
        winners = build_winner_facts(
            quarter.submissions,
            quarter.numeric_facts,
            quarter.presentation,
        ).copy()
        winners["source_zip"] = path.name
        frames.append(winners)

    if not frames:
        empty = pd.DataFrame()
        return empty, MultiQuarterAudit(0, 0, 0, 0, 0)

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
            [c for c in ("cik", "accepted_at", "concept", "ddate_date") if c in combined.columns],
            kind="stable",
        )
        .reset_index(drop=True)
    )

    after = len(combined)

    audit = MultiQuarterAudit(
        zip_count=len(frames),
        winner_rows_before_dedup=before,
        winner_rows_after_dedup=after,
        duplicate_rows_removed=before - after,
        unique_ciks=int(combined["cik"].nunique(dropna=True)),
    )

    return combined, audit
