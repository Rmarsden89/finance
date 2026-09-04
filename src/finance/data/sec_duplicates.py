from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class DuplicateAudit:
    duplicate_groups: int
    same_value_groups: int
    conflicting_value_groups: int


def audit_duplicate_groups(canonical_facts: pd.DataFrame) -> tuple[pd.DataFrame, DuplicateAudit]:
    """Audit remaining canonical SEC duplicate groups.

    A duplicate group is defined at the company/concept/period/unit/filing level.
    Groups are classified as:
      - same_value: multiple candidate rows, one unique numeric value
      - conflicting_value: multiple candidate rows, >1 unique numeric value
    """

    required = {
        "cik",
        "concept",
        "ddate_date",
        "qtrs",
        "uom",
        "accepted_at",
        "value",
        "source_tag",
    }
    missing = required - set(canonical_facts.columns)
    if missing:
        raise ValueError(
            "Canonical facts missing required columns: "
            + ", ".join(sorted(missing))
        )

    keys = ["cik", "concept", "ddate_date", "qtrs", "uom", "accepted_at"]

    grouped = canonical_facts.groupby(keys, dropna=False)
    sizes = grouped.size().rename("row_count")
    duplicates = sizes.loc[sizes > 1]

    if duplicates.empty:
        empty = pd.DataFrame(
            columns=keys
            + [
                "row_count",
                "unique_values",
                "classification",
                "source_tags",
                "values",
            ]
        )
        return empty, DuplicateAudit(0, 0, 0)

    value_counts = grouped["value"].nunique(dropna=False).rename("unique_values")

    audit = (
        duplicates.to_frame()
        .join(value_counts)
        .reset_index()
    )
    audit["classification"] = audit["unique_values"].map(
        lambda count: "same_value" if count == 1 else "conflicting_value"
    )

    tag_lists = grouped["source_tag"].agg(
        lambda values: "|".join(sorted(set(str(value) for value in values)))
    ).rename("source_tags")

    value_lists = grouped["value"].agg(
        lambda values: "|".join(
            sorted(set(str(value) for value in values))
        )
    ).rename("values")

    audit = (
        audit.set_index(keys)
        .join(tag_lists)
        .join(value_lists)
        .reset_index()
    )

    same_value = int((audit["classification"] == "same_value").sum())
    conflicting = int((audit["classification"] == "conflicting_value").sum())

    summary = DuplicateAudit(
        duplicate_groups=len(audit),
        same_value_groups=same_value,
        conflicting_value_groups=conflicting,
    )

    return audit, summary
