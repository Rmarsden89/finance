from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
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


def normalize_v2_nonpositive_share_winners(
    winners: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convert nonpositive V2 share winners to missing and retain an audit."""

    required = {"concept", "value"}
    missing = sorted(required - set(winners.columns))
    if missing:
        raise ValueError(
            "Winner facts missing required columns: " + ", ".join(missing)
        )

    normalized = winners.copy()
    values = pd.to_numeric(normalized["value"], errors="coerce")
    mask = (
        normalized["concept"].astype(str).eq("shares_outstanding")
        & values.notna()
        & values.le(0)
    )
    audit = normalized.loc[mask].copy()
    audit["original_value"] = values.loc[mask]
    audit["adjusted_value"] = pd.NA
    audit["quality_adjustment"] = "nonpositive_shares_normalized_to_missing"
    normalized.loc[mask, "value"] = pd.NA
    return normalized, audit.reset_index(drop=True)


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
    as_of: date | pd.Timestamp,
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
    eligible_share_candidates: set[str] = set()
    candidate_acceptance: dict[str, str] = {}
    if not candidates.empty and {"ticker", "concept"}.issubset(candidates.columns):
        shares = candidates.loc[
            candidates["concept"].astype(str).eq("shares_outstanding")
        ].copy()
        shares["ticker"] = shares["ticker"].astype(str).str.upper()
        share_candidates = set(shares["ticker"])
        accepted_source = (
            shares["accepted_at"]
            if "accepted_at" in shares.columns
            else pd.Series(pd.NaT, index=shares.index)
        )
        accepted = pd.to_datetime(accepted_source, errors="coerce")
        cutoff = pd.Timestamp(as_of)
        if cutoff.tzinfo is not None:
            cutoff = cutoff.tz_localize(None)
        accepted_naive = accepted.map(
            lambda value: (
                value.tz_convert(None) if pd.notna(value) and value.tzinfo else value
            )
        )
        eligible = accepted_naive.notna() & accepted_naive.le(cutoff)
        eligible_share_candidates = set(shares.loc[eligible, "ticker"])
        for ticker, group in shares.assign(
            _accepted_at=accepted_naive
        ).groupby("ticker"):
            values = group["_accepted_at"].dropna().sort_values()
            if values.empty:
                candidate_acceptance[ticker] = "missing_acceptance"
            elif values.le(cutoff).any():
                candidate_acceptance[ticker] = "pit_eligible"
            else:
                candidate_acceptance[ticker] = "accepted_after_decision_date"

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
        has_eligible_share_candidate = ticker in eligible_share_candidates

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
        elif has_eligible_share_candidate:
            classification = "share_candidate_not_selected"
            action = "candidate_not_selected"
        elif has_share_candidate:
            classification = "share_candidate_not_pit_eligible"
            action = "documented_no_supported_fact"
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
                "pit_eligible_share_candidate_present": (
                    has_eligible_share_candidate
                ),
                "share_candidate_acceptance_status": candidate_acceptance.get(
                    ticker, ""
                ),
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
