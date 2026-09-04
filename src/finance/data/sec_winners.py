from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .sec_concepts import CANONICAL_TAGS


@dataclass(frozen=True)
class WinnerAudit:
    rows_input: int
    duplicate_groups_input: int
    rows_output: int
    groups_resolved_by_same_value: int
    groups_resolved_by_tag_priority: int
    unresolved_groups: int


def _priority_map() -> dict[tuple[str, str], int]:
    priorities: dict[tuple[str, str], int] = {}
    for concept, tags in CANONICAL_TAGS.items():
        for rank, tag in enumerate(tags):
            priorities[(concept, tag)] = rank
    return priorities


def select_canonical_winners(
    canonical_facts: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, WinnerAudit]:
    """Collapse duplicate canonical SEC fact candidates deterministically.

    Duplicate group key:
      company + concept + fact end date + qtrs + unit + filing acceptance time

    Resolution:
      1. If all candidate rows have the same numeric value, keep the highest-
         priority source tag while recording all candidates.
      2. If values conflict, use curated source-tag priority from CANONICAL_TAGS.
      3. If the highest priority is tied across different values, leave the
         group unresolved for manual/rule review rather than guessing.
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
    priorities = _priority_map()

    winners: list[pd.Series] = []
    audit_rows: list[dict] = []
    duplicate_groups = 0
    same_value_resolved = 0
    priority_resolved = 0
    unresolved = 0

    for group_key, group in canonical_facts.groupby(keys, dropna=False, sort=False):
        if len(group) == 1:
            winners.append(group.iloc[0])
            continue

        duplicate_groups += 1
        ranked = group.copy()
        ranked["_priority"] = [
            priorities.get((concept, tag), 999)
            for concept, tag in zip(
                ranked["concept"],
                ranked["source_tag"],
            )
        ]

        unique_values = ranked["value"].nunique(dropna=False)
        resolution = ""
        winner = None

        if unique_values == 1:
            winner = ranked.sort_values(
                ["_priority", "source_tag"],
                kind="stable",
            ).iloc[0]
            same_value_resolved += 1
            resolution = "same_value"
        else:
            best_priority = ranked["_priority"].min()
            best = ranked.loc[ranked["_priority"].eq(best_priority)]

            if best["value"].nunique(dropna=False) == 1:
                winner = best.sort_values(
                    ["source_tag"],
                    kind="stable",
                ).iloc[0]
                priority_resolved += 1
                resolution = "tag_priority"
            else:
                unresolved += 1
                resolution = "unresolved_priority_tie"

        audit_rows.append(
            {
                **dict(zip(keys, group_key if isinstance(group_key, tuple) else (group_key,))),
                "candidate_count": len(group),
                "unique_values": unique_values,
                "candidate_tags": "|".join(
                    sorted(set(group["source_tag"].astype(str)))
                ),
                "candidate_values": "|".join(
                    sorted(set(group["value"].astype(str)))
                ),
                "resolution": resolution,
                "winner_tag": "" if winner is None else winner["source_tag"],
                "winner_value": "" if winner is None else winner["value"],
            }
        )

        if winner is not None:
            winners.append(winner.drop(labels=["_priority"]))

    winner_frame = pd.DataFrame(winners)
    if not winner_frame.empty:
        winner_frame = winner_frame.reset_index(drop=True)

    audit_frame = pd.DataFrame(audit_rows)

    summary = WinnerAudit(
        rows_input=len(canonical_facts),
        duplicate_groups_input=duplicate_groups,
        rows_output=len(winner_frame),
        groups_resolved_by_same_value=same_value_resolved,
        groups_resolved_by_tag_priority=priority_resolved,
        unresolved_groups=unresolved,
    )

    return winner_frame, audit_frame, summary
