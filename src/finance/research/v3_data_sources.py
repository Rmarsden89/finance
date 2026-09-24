from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


STANDARD_COLUMNS = [
    "field",
    "ticker",
    "cik",
    "company_name",
    "current_value",
    "classification",
    "recommended_action",
]


@dataclass(frozen=True)
class GapInventorySummary:
    total_gap_rows: int
    unique_gap_tickers: int
    shares_gap_rows: int
    liabilities_gap_rows: int
    tickers_missing_both: int


def _normalize_gap_frame(
    frame: pd.DataFrame,
    *,
    field: str,
    value_column: str,
) -> pd.DataFrame:
    required = {"ticker", "cik", "classification", "recommended_action"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            f"{field} gap detail missing required columns: {', '.join(missing)}"
        )

    result = frame.copy()
    result["field"] = field
    result["ticker"] = result["ticker"].astype(str).str.upper().str.strip()
    result["company_name"] = result.get(
        "company_name", pd.Series("", index=result.index)
    ).fillna("")
    result["current_value"] = result.get(
        value_column, pd.Series(pd.NA, index=result.index)
    )

    preferred = STANDARD_COLUMNS + [
        column
        for column in result.columns
        if column not in STANDARD_COLUMNS and column != value_column
    ]
    return result[preferred]


def build_residual_gap_inventory(
    *,
    shares_detail: pd.DataFrame,
    liabilities_detail: pd.DataFrame,
) -> tuple[pd.DataFrame, GapInventorySummary]:
    """Combine already-classified V2 residual gaps without altering source data."""

    shares = _normalize_gap_frame(
        shares_detail,
        field="shares_outstanding",
        value_column="shares_outstanding",
    )
    liabilities = _normalize_gap_frame(
        liabilities_detail,
        field="total_liabilities",
        value_column="total_liabilities",
    )

    combined = pd.concat([shares, liabilities], ignore_index=True, sort=False)
    combined = combined.sort_values(
        ["ticker", "field", "recommended_action", "classification"],
        kind="stable",
    ).reset_index(drop=True)

    by_ticker = combined.groupby("ticker")["field"].nunique()
    summary = GapInventorySummary(
        total_gap_rows=len(combined),
        unique_gap_tickers=int(combined["ticker"].nunique()),
        shares_gap_rows=len(shares),
        liabilities_gap_rows=len(liabilities),
        tickers_missing_both=int(by_ticker.ge(2).sum()),
    )
    return combined, summary


def summarize_gap_inventory(inventory: pd.DataFrame) -> pd.DataFrame:
    """Return deterministic counts by field, action, and classification."""

    required = {"field", "recommended_action", "classification", "ticker"}
    missing = sorted(required - set(inventory.columns))
    if missing:
        raise ValueError(
            "Gap inventory missing required columns: " + ", ".join(missing)
        )

    return (
        inventory.groupby(
            ["field", "recommended_action", "classification"],
            dropna=False,
            as_index=False,
        )
        .agg(rows=("ticker", "size"), tickers=("ticker", "nunique"))
        .sort_values(
            ["field", "recommended_action", "classification"],
            kind="stable",
        )
        .reset_index(drop=True)
    )


def source_candidate_template() -> pd.DataFrame:
    """Frozen Issue #26 comparison fields for candidate-source research."""

    columns = [
        "source",
        "category",
        "target_fields",
        "historical_pit_support",
        "filing_or_publication_timestamp",
        "identity_mapping",
        "restatement_revision_handling",
        "methodology_documented",
        "access_cost",
        "licensing_notes",
        "sec_overlap_validation",
        "population_bias_risk",
        "fail_closed_provenance_fit",
        "research_status",
        "notes",
    ]
    return pd.DataFrame(columns=columns)
