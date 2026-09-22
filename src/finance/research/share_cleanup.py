from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class ResidualSharesSummary:
    universe_rows: int
    positive_shares: int
    residual_rows: int
    blank_shares: int
    nonpositive_shares: int
    targeted_sec_refresh: int
    investigate_invalid_value: int
    candidate_not_selected: int
    documented_no_supported_fact: int


def _ticker_values(frame: pd.DataFrame, column: str) -> dict[str, str]:
    if frame.empty or "ticker" not in frame.columns or column not in frame.columns:
        return {}
    result: dict[str, str] = {}
    for ticker, group in frame.groupby(frame["ticker"].astype(str).str.upper()):
        values = sorted(
            {
                str(value).strip()
                for value in group[column].tolist()
                if str(value).strip() and str(value).strip().lower() != "nan"
            }
        )
        result[ticker] = "|".join(values)
    return result


def classify_residual_shares(
    *,
    snapshot: pd.DataFrame,
    discovery: pd.DataFrame,
    candidate_audit: pd.DataFrame,
    candidates: pd.DataFrame,
    cache_dir: Path,
) -> tuple[pd.DataFrame, ResidualSharesSummary]:
    """Classify every nonpositive current shares value and its next action."""

    required = {"ticker", "cik", "shares_outstanding"}
    missing = sorted(required - set(snapshot.columns))
    if missing:
        raise ValueError("Snapshot missing required columns: " + ", ".join(missing))

    current = snapshot.copy()
    current["ticker"] = current["ticker"].astype(str).str.upper()
    current["shares_numeric"] = pd.to_numeric(
        current["shares_outstanding"], errors="coerce"
    )
    residual = current.loc[~current["shares_numeric"].gt(0)].copy()

    discovery_status = _ticker_values(discovery, "status")
    discovery_accession = _ticker_values(discovery, "accession")
    audit_status = _ticker_values(candidate_audit, "status")
    share_candidates: set[str] = set()
    if not candidates.empty and {"ticker", "concept"}.issubset(candidates.columns):
        share_candidates = set(
            candidates.loc[
                candidates["concept"].astype(str).eq("shares_outstanding"),
                "ticker",
            ].astype(str).str.upper()
        )

    rows: list[dict[str, object]] = []
    for row in residual.itertuples(index=False):
        ticker = str(row.ticker).upper()
        cik = int(float(row.cik))
        raw_shares = getattr(row, "shares_outstanding")
        numeric = getattr(row, "shares_numeric")
        companyfacts = cache_dir / "companyfacts" / f"CIK{cik:010d}.json"
        statuses = discovery_status.get(ticker, "")
        accessions = discovery_accession.get(ticker, "")
        cache_present = companyfacts.exists()
        has_share_candidate = ticker in share_candidates

        if pd.notna(numeric) and float(numeric) <= 0:
            classification = "nonpositive_canonical_value"
            action = "investigate_invalid_value"
        elif not statuses:
            classification = "no_current_discovery"
            action = "targeted_sec_refresh"
        elif "new_filing_partial" in statuses.split("|") or (
            "submissions_error" in statuses.split("|")
        ):
            classification = "current_discovery_incomplete"
            action = "targeted_sec_refresh"
        elif "new_filing_cached" not in statuses.split("|"):
            classification = "no_new_supported_filing"
            action = "documented_no_supported_fact"
        elif not accessions:
            classification = "cached_filing_missing_accession"
            action = "targeted_sec_refresh"
        elif not cache_present:
            classification = "missing_companyfacts_cache"
            action = "targeted_sec_refresh"
        elif has_share_candidate:
            classification = "share_candidate_not_selected"
            action = "candidate_not_selected"
        else:
            classification = "no_supported_current_share_fact"
            action = "documented_no_supported_fact"

        rows.append(
            {
                "ticker": ticker,
                "cik": cik,
                "company_name": getattr(row, "company_name", ""),
                "shares_outstanding": raw_shares,
                "classification": classification,
                "recommended_action": action,
                "discovery_statuses": statuses,
                "discovery_accessions": accessions,
                "companyfacts_cache_present": cache_present,
                "candidate_audit_statuses": audit_status.get(ticker, ""),
                "current_share_candidate_present": has_share_candidate,
                "companyfacts_path": str(companyfacts),
            }
        )

    detail = pd.DataFrame(rows).sort_values(
        ["recommended_action", "ticker"], kind="stable"
    ).reset_index(drop=True)
    actions = detail["recommended_action"].value_counts().to_dict()
    summary = ResidualSharesSummary(
        universe_rows=len(current),
        positive_shares=int(current["shares_numeric"].gt(0).sum()),
        residual_rows=len(detail),
        blank_shares=int(current["shares_numeric"].isna().sum()),
        nonpositive_shares=int(
            (current["shares_numeric"].notna() & current["shares_numeric"].le(0)).sum()
        ),
        targeted_sec_refresh=int(actions.get("targeted_sec_refresh", 0)),
        investigate_invalid_value=int(actions.get("investigate_invalid_value", 0)),
        candidate_not_selected=int(actions.get("candidate_not_selected", 0)),
        documented_no_supported_fact=int(
            actions.get("documented_no_supported_fact", 0)
        ),
    )
    return detail, summary


def summary_payload(summary: ResidualSharesSummary) -> dict[str, int]:
    return asdict(summary)
