from __future__ import annotations

from dataclasses import dataclass
import hashlib
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


SAMPLE_QUOTAS = {
    "dual_gap": 4,
    "liabilities_alternate_tag": 3,
    "liabilities_identity_candidate": 3,
    "shares_only_gap": 3,
    "sec_supported_control": 2,
}


def _stable_sample_rank(*, as_of: str, cohort: str, ticker: str) -> str:
    payload = f"{as_of}|{cohort}|{ticker.upper()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _pick_rows(
    frame: pd.DataFrame,
    *,
    as_of: str,
    cohort: str,
    count: int,
    excluded: set[str],
) -> pd.DataFrame:
    if frame.empty or count <= 0:
        return frame.iloc[0:0].copy()

    candidates = frame.copy()
    candidates["ticker"] = candidates["ticker"].astype(str).str.upper().str.strip()
    candidates = candidates.loc[~candidates["ticker"].isin(excluded)].copy()
    candidates["_sample_rank"] = candidates["ticker"].map(
        lambda ticker: _stable_sample_rank(
            as_of=as_of,
            cohort=cohort,
            ticker=ticker,
        )
    )
    candidates = (
        candidates.sort_values(["_sample_rank", "ticker"], kind="stable")
        .drop_duplicates("ticker", keep="first")
        .head(count)
        .copy()
    )
    candidates["sample_cohort"] = cohort
    candidates["sample_rank"] = candidates["_sample_rank"]
    return candidates.drop(columns=["_sample_rank"])


def build_overlap_validation_sample(
    *,
    inventory: pd.DataFrame,
    current_snapshot: pd.DataFrame,
    as_of: str,
    quotas: dict[str, int] | None = None,
) -> pd.DataFrame:
    """Build a deterministic, stratified Issue #26 validation cohort."""

    required_inventory = {
        "field",
        "ticker",
        "classification",
        "recommended_action",
    }
    missing = sorted(required_inventory - set(inventory.columns))
    if missing:
        raise ValueError(
            "Gap inventory missing required columns: " + ", ".join(missing)
        )

    required_snapshot = {
        "ticker",
        "cik",
        "shares_outstanding",
        "total_liabilities",
    }
    missing_snapshot = sorted(required_snapshot - set(current_snapshot.columns))
    if missing_snapshot:
        raise ValueError(
            "Current snapshot missing required columns: "
            + ", ".join(missing_snapshot)
        )

    quotas = dict(SAMPLE_QUOTAS if quotas is None else quotas)
    inv = inventory.copy()
    inv["ticker"] = inv["ticker"].astype(str).str.upper().str.strip()
    snap = current_snapshot.copy()
    snap["ticker"] = snap["ticker"].astype(str).str.upper().str.strip()

    fields_by_ticker = inv.groupby("ticker")["field"].agg(set)
    dual_tickers = set(
        fields_by_ticker.loc[
            fields_by_ticker.map(
                lambda value: {
                    "shares_outstanding",
                    "total_liabilities",
                }.issubset(value)
            )
        ].index
    )

    selected: list[pd.DataFrame] = []
    excluded: set[str] = set()

    # Represent one row per dual-gap company while retaining both source reasons.
    dual_rows: list[dict[str, Any]] = []
    for ticker in sorted(dual_tickers):
        group = inv.loc[inv["ticker"].eq(ticker)]
        first = group.iloc[0].to_dict()
        first["field"] = "shares_outstanding|total_liabilities"
        first["classification"] = "|".join(
            sorted(set(group["classification"].astype(str)))
        )
        first["recommended_action"] = "|".join(
            sorted(set(group["recommended_action"].astype(str)))
        )
        dual_rows.append(first)
    dual_frame = pd.DataFrame(dual_rows)
    pick = _pick_rows(
        dual_frame,
        as_of=as_of,
        cohort="dual_gap",
        count=quotas.get("dual_gap", 0),
        excluded=excluded,
    )
    selected.append(pick)
    excluded.update(pick.get("ticker", pd.Series(dtype=str)).tolist())

    alt = inv.loc[
        inv["field"].eq("total_liabilities")
        & inv["recommended_action"].eq("research_alternate_tag")
    ]
    pick = _pick_rows(
        alt,
        as_of=as_of,
        cohort="liabilities_alternate_tag",
        count=quotas.get("liabilities_alternate_tag", 0),
        excluded=excluded,
    )
    selected.append(pick)
    excluded.update(pick.get("ticker", pd.Series(dtype=str)).tolist())

    identity = inv.loc[
        inv["field"].eq("total_liabilities")
        & inv["recommended_action"].eq("research_identity_candidate")
    ]
    pick = _pick_rows(
        identity,
        as_of=as_of,
        cohort="liabilities_identity_candidate",
        count=quotas.get("liabilities_identity_candidate", 0),
        excluded=excluded,
    )
    selected.append(pick)
    excluded.update(pick.get("ticker", pd.Series(dtype=str)).tolist())

    shares_only = inv.loc[
        inv["field"].eq("shares_outstanding")
        & ~inv["ticker"].isin(dual_tickers)
        & inv["classification"].eq("no_supported_current_share_fact")
    ]
    pick = _pick_rows(
        shares_only,
        as_of=as_of,
        cohort="shares_only_gap",
        count=quotas.get("shares_only_gap", 0),
        excluded=excluded,
    )
    selected.append(pick)
    excluded.update(pick.get("ticker", pd.Series(dtype=str)).tolist())

    share_values = pd.to_numeric(snap["shares_outstanding"], errors="coerce")
    liability_values = pd.to_numeric(snap["total_liabilities"], errors="coerce")
    controls = snap.loc[share_values.gt(0) & liability_values.gt(0)].copy()
    controls["field"] = "shares_outstanding|total_liabilities"
    controls["classification"] = "sec_supported_control"
    controls["recommended_action"] = "validate_overlap"
    controls["current_value"] = pd.NA
    pick = _pick_rows(
        controls,
        as_of=as_of,
        cohort="sec_supported_control",
        count=quotas.get("sec_supported_control", 0),
        excluded=excluded,
    )
    selected.append(pick)

    result = pd.concat(selected, ignore_index=True, sort=False)
    result["as_of"] = as_of
    desired = [
        "as_of",
        "sample_cohort",
        "sample_rank",
        "ticker",
        "cik",
        "company_name",
        "field",
        "classification",
        "recommended_action",
    ]
    extra = [column for column in result.columns if column not in desired]
    return result[desired + extra].reset_index(drop=True)
