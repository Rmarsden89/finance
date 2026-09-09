from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .sec_concepts import CANONICAL_TAGS
from .sec_winners import select_canonical_winners


FACT_KEY = [
    "cik",
    "adsh",
    "concept",
    "ddate_date",
    "qtrs",
    "uom",
]


@dataclass(frozen=True)
class ShadowMergeAudit:
    historical_rows: int
    current_rows_input: int
    current_rows_valid: int
    current_rows_added: int
    rejected_invalid_acceptance: int
    rejected_unsupported_concept: int
    rejected_existing_fact_group: int
    rejected_ambiguous_value_group: int
    rejected_unresolved_winner_group: int
    merged_rows: int


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "cik" in result.columns:
        result["cik"] = pd.to_numeric(result["cik"], errors="coerce")
    if "qtrs" in result.columns:
        result["qtrs"] = pd.to_numeric(result["qtrs"], errors="coerce")
    if "accepted_at" in result.columns:
        result["accepted_at"] = pd.to_datetime(
            result["accepted_at"], errors="coerce"
        )
    for column in ("ddate_date", "period_date", "filed_date"):
        if column in result.columns:
            result[column] = pd.to_datetime(
                result[column], errors="coerce"
            ).dt.date
    if "uom" in result.columns:
        result["uom"] = result["uom"].fillna("").astype(str)
    if "adsh" in result.columns:
        result["adsh"] = result["adsh"].fillna("").astype(str)
    return result


def _key_tuples(frame: pd.DataFrame) -> set[tuple]:
    if frame.empty:
        return set()
    return set(frame[FACT_KEY].itertuples(index=False, name=None))


def merge_current_sec_shadow(
    historical_winners: pd.DataFrame,
    current_candidates: pd.DataFrame,
    *,
    as_of: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, ShadowMergeAudit]:
    """Merge review-only current SEC facts into a separate shadow history.

    Historical quarterly-ZIP winners always take precedence. Current rows must:
      - have a valid acceptance timestamp
      - use a supported canonical concept
      - not duplicate an archived accession/fact group
      - not contain competing numeric values for one fact group
      - resolve deterministically through the existing winner selector

    The function never mutates either input frame.
    """

    historical = _normalize(historical_winners)
    current = _normalize(current_candidates)

    required = set(FACT_KEY) | {
        "accepted_at",
        "value",
        "source_tag",
    }
    missing_hist = required - set(historical.columns)
    missing_current = required - set(current.columns)
    if missing_hist:
        raise ValueError(
            "Historical winners missing required columns: "
            + ", ".join(sorted(missing_hist))
        )
    if missing_current:
        raise ValueError(
            "Current candidates missing required columns: "
            + ", ".join(sorted(missing_current))
        )

    audit_rows: list[dict] = []
    accepted_mask = current["accepted_at"].notna()
    if as_of is not None:
        cutoff = pd.Timestamp(as_of)
        accepted_mask &= current["accepted_at"].le(cutoff)

    for index in current.index[~accepted_mask]:
        audit_rows.append(
            {
                "row_index": index,
                "status": "rejected_invalid_acceptance",
                "reason": "missing accepted_at or accepted after shadow as_of",
            }
        )

    supported_concepts = set(CANONICAL_TAGS)
    supported_mask = current["concept"].isin(supported_concepts)
    for index in current.index[accepted_mask & ~supported_mask]:
        audit_rows.append(
            {
                "row_index": index,
                "status": "rejected_unsupported_concept",
                "reason": str(current.at[index, "concept"]),
            }
        )

    valid = current.loc[accepted_mask & supported_mask].copy()

    historical_keys = _key_tuples(historical)
    if valid.empty:
        existing_mask = pd.Series(False, index=valid.index, dtype=bool)
    else:
        existing_mask = pd.Series(
            [
                key in historical_keys
                for key in valid[FACT_KEY].itertuples(index=False, name=None)
            ],
            index=valid.index,
        )

    for index in valid.index[existing_mask]:
        audit_rows.append(
            {
                "row_index": index,
                "status": "rejected_existing_fact_group",
                "reason": "quarterly archive already contains this accession/fact group",
            }
        )

    novel = valid.loc[~existing_mask].copy()

    ambiguous_indices: set[int] = set()
    if not novel.empty:
        for _, group in novel.groupby(FACT_KEY, dropna=False, sort=False):
            if group["value"].astype(str).nunique(dropna=False) > 1:
                ambiguous_indices.update(int(index) for index in group.index)

    for index in sorted(ambiguous_indices):
        audit_rows.append(
            {
                "row_index": index,
                "status": "rejected_ambiguous_value_group",
                "reason": "multiple values compete for the same accession/fact group",
            }
        )

    resolvable = novel.loc[~novel.index.isin(ambiguous_indices)].copy()

    if resolvable.empty:
        current_winners = resolvable
        unresolved_groups = 0
    else:
        current_winners, winner_detail, winner_audit = select_canonical_winners(
            resolvable
        )
        unresolved_groups = winner_audit.unresolved_groups
        if not winner_detail.empty:
            unresolved = winner_detail.loc[
                winner_detail["resolution"].eq("unresolved_priority_tie")
            ]
            unresolved_keys = set(
                unresolved[
                    ["cik", "concept", "ddate_date", "qtrs", "uom", "accepted_at"]
                ].itertuples(index=False, name=None)
            )
            for index, row in resolvable.iterrows():
                key = (
                    row["cik"],
                    row["concept"],
                    row["ddate_date"],
                    row["qtrs"],
                    row["uom"],
                    row["accepted_at"],
                )
                if key in unresolved_keys:
                    audit_rows.append(
                        {
                            "row_index": index,
                            "status": "rejected_unresolved_winner_group",
                            "reason": "existing canonical tag priority could not resolve group",
                        }
                    )

    historical_out = historical.copy()
    if "source_system" not in historical_out.columns:
        historical_out["source_system"] = "sec_quarterly_zip"
    else:
        historical_out["source_system"] = historical_out["source_system"].fillna(
            "sec_quarterly_zip"
        )

    if not current_winners.empty:
        current_winners = current_winners.copy()
        current_winners["source_system"] = "sec_companyfacts_current"

    merged = pd.concat(
        [historical_out, current_winners],
        ignore_index=True,
        sort=False,
    )

    sort_columns = [
        column
        for column in ("cik", "accepted_at", "concept", "ddate_date", "adsh")
        if column in merged.columns
    ]
    if sort_columns:
        merged = merged.sort_values(sort_columns, kind="stable").reset_index(
            drop=True
        )

    winner_signatures: set[tuple] = set()
    if not current_winners.empty:
        signature_columns = [
            *FACT_KEY,
            "accepted_at",
            "value",
            "source_tag",
        ]
        winner_signatures = set(
            current_winners[signature_columns].itertuples(index=False, name=None)
        )

    for index, row in resolvable.iterrows():
        signature = tuple(
            row[column]
            for column in [*FACT_KEY, "accepted_at", "value", "source_tag"]
        )
        if signature in winner_signatures:
            audit_rows.append(
                {
                    "row_index": index,
                    "status": "added_current_winner",
                    "reason": "",
                }
            )
        elif not any(item["row_index"] == index for item in audit_rows):
            audit_rows.append(
                {
                    "row_index": index,
                    "status": "resolved_duplicate_candidate",
                    "reason": "same-value/tag-priority duplicate not selected as winner",
                }
            )

    audit_frame = pd.DataFrame(audit_rows)
    if not audit_frame.empty:
        provenance = current.reset_index().rename(columns={"index": "row_index"})
        keep = [
            column
            for column in (
                "row_index", "ticker", "cik", "adsh", "concept", "source_tag",
                "value", "uom", "qtrs", "ddate_date", "accepted_at",
            )
            if column in provenance.columns
        ]
        audit_frame = audit_frame.merge(
            provenance[keep],
            on="row_index",
            how="left",
            validate="many_to_one",
        ).sort_values(["status", "ticker", "concept"], kind="stable")

    status_counts = (
        audit_frame["status"].value_counts().to_dict()
        if not audit_frame.empty
        else {}
    )

    summary = ShadowMergeAudit(
        historical_rows=len(historical),
        current_rows_input=len(current),
        current_rows_valid=len(valid),
        current_rows_added=len(current_winners),
        rejected_invalid_acceptance=status_counts.get(
            "rejected_invalid_acceptance", 0
        ),
        rejected_unsupported_concept=status_counts.get(
            "rejected_unsupported_concept", 0
        ),
        rejected_existing_fact_group=status_counts.get(
            "rejected_existing_fact_group", 0
        ),
        rejected_ambiguous_value_group=status_counts.get(
            "rejected_ambiguous_value_group", 0
        ),
        rejected_unresolved_winner_group=status_counts.get(
            "rejected_unresolved_winner_group", 0
        ),
        merged_rows=len(merged),
    )

    return merged, audit_frame.reset_index(drop=True), summary
