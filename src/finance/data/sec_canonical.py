from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .sec_concepts import map_canonical_facts


_ALLOWED_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}


@dataclass(frozen=True)
class CanonicalFactAudit:
    rows_input: int
    rows_mapped: int
    rows_supported_forms: int
    rows_consolidated: int
    duplicate_groups: int


def build_canonical_facts(
    submissions: pd.DataFrame,
    numeric_facts: pd.DataFrame,
) -> tuple[pd.DataFrame, CanonicalFactAudit]:
    """Build conservative PIT-ready canonical SEC facts.

    Rules:
      - map only curated v0.1 concepts
      - join filing metadata/provenance
      - keep annual/quarterly financial statement forms
      - prefer consolidated facts by excluding non-empty coreg rows when present
      - preserve duplicates for audit rather than silently choosing a winner
    """

    mapped = map_canonical_facts(numeric_facts)

    sub_columns = [
        column
        for column in (
            "adsh",
            "cik",
            "name",
            "form",
            "fy",
            "fp",
            "period_date",
            "filed_date",
            "accepted_at",
        )
        if column in submissions.columns
    ]

    joined = mapped.merge(
        submissions[sub_columns],
        on="adsh",
        how="inner",
        validate="many_to_one",
    )

    supported = joined.loc[joined["form"].isin(_ALLOWED_FORMS)].copy()

    if "coreg" in supported.columns:
        coreg = supported["coreg"].fillna("").astype(str).str.strip()
        consolidated = supported.loc[coreg.eq("")].copy()
    else:
        consolidated = supported.copy()

    key_columns = [
        column
        for column in (
            "cik",
            "concept",
            "ddate_date",
            "qtrs",
            "uom",
            "accepted_at",
        )
        if column in consolidated.columns
    ]

    duplicate_groups = 0
    if key_columns:
        sizes = consolidated.groupby(key_columns, dropna=False).size()
        duplicate_groups = int((sizes > 1).sum())

    sort_columns = [
        column
        for column in ("cik", "accepted_at", "concept", "ddate_date", "qtrs")
        if column in consolidated.columns
    ]
    if sort_columns:
        consolidated = consolidated.sort_values(sort_columns).reset_index(drop=True)

    audit = CanonicalFactAudit(
        rows_input=len(numeric_facts),
        rows_mapped=len(mapped),
        rows_supported_forms=len(supported),
        rows_consolidated=len(consolidated),
        duplicate_groups=duplicate_groups,
    )

    return consolidated, audit


def facts_available_by(
    canonical_facts: pd.DataFrame,
    as_of: datetime,
) -> pd.DataFrame:
    """Return only canonical facts accepted by the SEC on/before as_of."""

    if "accepted_at" not in canonical_facts.columns:
        raise ValueError("canonical facts missing accepted_at")

    return canonical_facts.loc[
        canonical_facts["accepted_at"].notna()
        & (canonical_facts["accepted_at"] <= as_of)
    ].copy()
