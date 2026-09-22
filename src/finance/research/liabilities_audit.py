from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class LiabilitiesGapSummary:
    universe_rows: int
    positive_liabilities: int
    residual_rows: int
    blank_liabilities: int
    nonpositive_liabilities: int
    health_eligible: int
    health_factor_count_0: int
    health_factor_count_1: int
    health_factor_count_2: int
    health_factor_count_3: int
    targeted_sec_refresh: int
    investigate_invalid_value: int
    candidate_not_selected: int
    research_identity_candidate: int
    research_alternate_tag: int
    documented_no_supported_fact: int


def normalize_v2_nonpositive_liability_winners(
    winners: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convert nonpositive V2 liabilities winners to missing with an audit."""

    required = {"concept", "value"}
    missing = sorted(required - set(winners.columns))
    if missing:
        raise ValueError(
            "Winner facts missing required columns: " + ", ".join(missing)
        )

    normalized = winners.copy()
    values = pd.to_numeric(normalized["value"], errors="coerce")
    mask = (
        normalized["concept"].astype(str).eq("total_liabilities")
        & values.notna()
        & values.le(0)
    )
    audit = normalized.loc[mask].copy()
    audit["original_value"] = values.loc[mask]
    audit["adjusted_value"] = pd.NA
    audit["quality_adjustment"] = (
        "nonpositive_total_liabilities_normalized_to_missing"
    )
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


def _accepted_naive(frame: pd.DataFrame) -> pd.Series:
    source = (
        frame["accepted_at"]
        if "accepted_at" in frame.columns
        else pd.Series(pd.NaT, index=frame.index)
    )
    values = pd.to_datetime(source, errors="coerce", utc=True)
    return values.dt.tz_convert(None)


def _candidate_signals(
    candidates: pd.DataFrame,
    *,
    as_of: date | pd.Timestamp,
) -> tuple[set[str], set[str], dict[str, str], pd.DataFrame]:
    if candidates.empty or not {"ticker", "concept"}.issubset(candidates.columns):
        return set(), set(), {}, pd.DataFrame()

    frame = candidates.copy()
    frame["ticker"] = frame["ticker"].astype(str).str.upper()
    frame["_accepted_at"] = _accepted_naive(frame)
    # Match sec_shadow_merge exactly: a date-only as_of is midnight. Facts
    # accepted later on the decision date are not available to that decision.
    cutoff = pd.Timestamp(as_of).normalize()
    liabilities = frame.loc[frame["concept"].astype(str).eq("total_liabilities")]
    present = set(liabilities["ticker"])
    eligible = liabilities["_accepted_at"].notna() & liabilities["_accepted_at"].le(
        cutoff
    )
    eligible_tickers = set(liabilities.loc[eligible, "ticker"])
    acceptance: dict[str, str] = {}
    for ticker, group in liabilities.groupby("ticker"):
        values = group["_accepted_at"].dropna()
        if values.empty:
            acceptance[ticker] = "missing_acceptance"
        elif values.le(cutoff).any():
            acceptance[ticker] = "pit_eligible"
        else:
            acceptance[ticker] = "accepted_after_decision_date"

    identity = _same_context_identity_candidates(frame, cutoff=cutoff)
    return present, eligible_tickers, acceptance, identity


def _same_context_identity_candidates(
    candidates: pd.DataFrame,
    *,
    cutoff: pd.Timestamp,
) -> pd.DataFrame:
    """Find assets - equity candidates with identical filing and period context."""

    required = {
        "ticker",
        "concept",
        "value",
        "adsh",
        "ddate_date",
        "uom",
        "_accepted_at",
    }
    if not required.issubset(candidates.columns):
        return pd.DataFrame()

    frame = candidates.loc[
        candidates["concept"].astype(str).isin(
            {"total_assets", "shareholders_equity"}
        )
    ].copy()
    frame["_value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.loc[
        frame["_value"].gt(0)
        & frame["_accepted_at"].notna()
        & frame["_accepted_at"].le(cutoff)
        & frame["uom"].astype(str).str.upper().eq("USD")
    ]
    keys = ["ticker", "adsh", "ddate_date", "uom", "_accepted_at"]
    if "qtrs" in frame.columns:
        keys.append("qtrs")
    assets = frame.loc[
        frame["concept"].eq("total_assets"), keys + ["_value", "source_tag"]
    ].rename(
        columns={"_value": "assets_value", "source_tag": "assets_source_tag"}
    )
    equity = frame.loc[
        frame["concept"].eq("shareholders_equity"),
        keys + ["_value", "source_tag"],
    ].rename(columns={"_value": "equity_value", "source_tag": "equity_source_tag"})
    if assets.empty or equity.empty:
        return pd.DataFrame()
    merged = assets.merge(equity, on=keys, how="inner", validate="many_to_many")
    merged["derived_liabilities"] = (
        merged["assets_value"] - merged["equity_value"]
    )
    merged = merged.loc[merged["derived_liabilities"].gt(0)].copy()
    if merged.empty:
        return merged
    return (
        merged.sort_values(
            ["ticker", "adsh", "ddate_date", "derived_liabilities"], kind="stable"
        )
        .drop_duplicates(
            subset=["ticker", "adsh", "ddate_date", "derived_liabilities"],
            keep="first",
        )
        .reset_index(drop=True)
    )


def build_same_context_liabilities_identities(
    candidates: pd.DataFrame,
    *,
    as_of: date | pd.Timestamp,
) -> pd.DataFrame:
    """Build PIT-eligible Assets - Equity research candidates.

    The operands must share ticker, accession, period date, unit, acceptance
    timestamp, and (when present) quarterly-duration context. No value is
    promoted into the model by this function.
    """

    if candidates.empty or not {"ticker", "concept"}.issubset(candidates.columns):
        return pd.DataFrame()
    frame = candidates.copy()
    frame["ticker"] = frame["ticker"].astype(str).str.upper()
    frame["_accepted_at"] = _accepted_naive(frame)
    return _same_context_identity_candidates(
        frame,
        cutoff=pd.Timestamp(as_of).normalize(),
    )


def validate_liabilities_identity(
    candidates: pd.DataFrame,
    *,
    as_of: date | pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compare same-context Assets - Equity with reported Liabilities."""

    identities = build_same_context_liabilities_identities(candidates, as_of=as_of)
    summary_columns = [
        "equity_source_tag",
        "comparison_rows",
        "tickers",
        "exact_matches",
        "within_0_01_pct",
        "within_0_1_pct",
        "material_differences",
        "median_absolute_relative_error",
        "max_absolute_relative_error",
    ]
    if identities.empty:
        return pd.DataFrame(), pd.DataFrame(columns=summary_columns)

    frame = candidates.copy()
    frame["ticker"] = frame["ticker"].astype(str).str.upper()
    frame["_accepted_at"] = _accepted_naive(frame)
    cutoff = pd.Timestamp(as_of).normalize()
    liabilities = frame.loc[
        frame["concept"].astype(str).eq("total_liabilities")
        & frame["_accepted_at"].notna()
        & frame["_accepted_at"].le(cutoff)
        & frame["uom"].astype(str).str.upper().eq("USD")
    ].copy()
    liabilities["direct_liabilities"] = pd.to_numeric(
        liabilities["value"], errors="coerce"
    )
    liabilities = liabilities.loc[liabilities["direct_liabilities"].gt(0)]
    keys = ["ticker", "adsh", "ddate_date", "uom", "_accepted_at"]
    if "qtrs" in identities.columns and "qtrs" in liabilities.columns:
        keys.append("qtrs")
    direct = liabilities[
        keys + ["direct_liabilities", "source_tag"]
    ].rename(columns={"source_tag": "liabilities_source_tag"})
    if direct.empty:
        return pd.DataFrame(), pd.DataFrame(columns=summary_columns)

    comparison = identities.merge(
        direct,
        on=keys,
        how="inner",
        validate="many_to_many",
    )
    if comparison.empty:
        return comparison, pd.DataFrame(columns=summary_columns)
    comparison["difference"] = (
        comparison["derived_liabilities"] - comparison["direct_liabilities"]
    )
    comparison["absolute_difference"] = comparison["difference"].abs()
    comparison["absolute_relative_error"] = (
        comparison["absolute_difference"] / comparison["direct_liabilities"]
    )
    comparison["validation_band"] = "material_difference"
    comparison.loc[
        comparison["absolute_relative_error"].le(0.001), "validation_band"
    ] = "within_0_1_pct"
    comparison.loc[
        comparison["absolute_relative_error"].le(0.0001), "validation_band"
    ] = "within_0_01_pct"
    comparison.loc[comparison["absolute_difference"].eq(0), "validation_band"] = (
        "exact_match"
    )
    comparison = comparison.sort_values(
        ["ticker", "adsh", "ddate_date", "equity_source_tag"], kind="stable"
    ).reset_index(drop=True)

    rows: list[dict[str, object]] = []
    for tag, group in comparison.groupby("equity_source_tag", dropna=False):
        bands = group["validation_band"].value_counts()
        rows.append(
            {
                "equity_source_tag": tag,
                "comparison_rows": len(group),
                "tickers": int(group["ticker"].nunique()),
                "exact_matches": int(bands.get("exact_match", 0)),
                "within_0_01_pct": int(bands.get("within_0_01_pct", 0)),
                "within_0_1_pct": int(bands.get("within_0_1_pct", 0)),
                "material_differences": int(bands.get("material_difference", 0)),
                "median_absolute_relative_error": float(
                    group["absolute_relative_error"].median()
                ),
                "max_absolute_relative_error": float(
                    group["absolute_relative_error"].max()
                ),
            }
        )
    return comparison, pd.DataFrame(rows, columns=summary_columns)


def _current_accessions(
    discovery: pd.DataFrame,
    *,
    as_of: date | pd.Timestamp,
) -> dict[str, dict[str, str]]:
    required = {"ticker", "accession", "report_date", "accepted_at"}
    if discovery.empty or not required.issubset(discovery.columns):
        return {}
    frame = discovery.copy()
    frame["_accepted_at"] = pd.to_datetime(
        frame["accepted_at"], errors="coerce", utc=True
    ).dt.tz_convert(None)
    frame = frame.loc[
        frame["_accepted_at"].notna()
        & frame["_accepted_at"].le(pd.Timestamp(as_of).normalize())
    ]
    result: dict[str, dict[str, str]] = {}
    for ticker, group in frame.groupby(frame["ticker"].astype(str).str.upper()):
        result[ticker] = {
            str(row.accession).strip(): str(row.report_date)[:10]
            for row in group.itertuples(index=False)
            if str(row.accession).strip()
            and str(row.accession).strip().lower() != "nan"
            and str(row.report_date).strip()
            and str(row.report_date).strip().lower() != "nan"
        }
    return result


def _alternate_liability_tags(
    path: Path,
    accessions: dict[str, str],
) -> tuple[str, str]:
    """Return exact-filing liability tags for audit only, never as winners."""

    if not path.exists() or not accessions:
        return "", ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", ""
    tags: set[str] = set()
    total_like: set[str] = set()
    facts = (payload.get("facts") or {}).get("us-gaap") or {}
    for tag, fact in facts.items():
        if "Liabilit" not in str(tag):
            continue
        for observations in (fact.get("units") or {}).values():
            for observation in observations or []:
                if str(observation.get("accn") or "") not in accessions:
                    continue
                accession = str(observation.get("accn") or "")
                if str(observation.get("end") or "")[:10] != accessions[accession]:
                    continue
                if observation.get("start"):
                    continue
                value = pd.to_numeric(
                    pd.Series([observation.get("val")]), errors="coerce"
                ).iloc[0]
                if pd.isna(value) or value <= 0:
                    continue
                if str(tag) != "Liabilities":
                    tags.add(str(tag))
                if str(tag) in {
                    "LiabilitiesAndStockholdersEquity",
                    "LiabilitiesAndPartnersCapital",
                }:
                    total_like.add(str(tag))
    return "|".join(sorted(tags)), "|".join(sorted(total_like))


def classify_liabilities_gaps(
    *,
    snapshot: pd.DataFrame,
    discovery: pd.DataFrame,
    candidate_audit: pd.DataFrame,
    candidates: pd.DataFrame,
    merge_audit: pd.DataFrame,
    scored: pd.DataFrame,
    cache_dir: Path,
    as_of: date | pd.Timestamp,
) -> tuple[pd.DataFrame, LiabilitiesGapSummary]:
    """Classify every current liabilities gap without changing model data."""

    required = {"ticker", "cik", "total_liabilities"}
    missing = sorted(required - set(snapshot.columns))
    if missing:
        raise ValueError("Snapshot missing required columns: " + ", ".join(missing))

    current = snapshot.copy()
    current["ticker"] = current["ticker"].astype(str).str.upper()
    current["liabilities_numeric"] = pd.to_numeric(
        current["total_liabilities"], errors="coerce"
    )

    current_scores = scored.copy()
    if "decision_date" in current_scores.columns:
        dates = pd.to_datetime(current_scores["decision_date"], errors="coerce")
        current_scores = current_scores.loc[dates.dt.normalize().eq(pd.Timestamp(as_of))]
    if not current_scores.empty and "ticker" in current_scores.columns:
        current_scores["ticker"] = current_scores["ticker"].astype(str).str.upper()
        score_columns = [
            column
            for column in (
                "ticker",
                "financial_health_factor_count",
                "financial_health_eligible",
                "financial_health_score",
                "top_conviction_eligible",
            )
            if column in current_scores.columns
        ]
        current = current.merge(
            current_scores[score_columns].drop_duplicates("ticker"),
            on="ticker",
            how="left",
            validate="one_to_one",
        )

    residual = current.loc[~current["liabilities_numeric"].gt(0)].copy()
    discovery_status = _ticker_values(discovery, "status")
    discovery_accession = _ticker_values(discovery, "accession")
    candidate_audit_status = _ticker_values(candidate_audit, "status")
    merge_status = _ticker_values(
        merge_audit.loc[
            merge_audit.get("concept", pd.Series(index=merge_audit.index)).astype(str).eq(
                "total_liabilities"
            )
        ]
        if not merge_audit.empty
        else merge_audit,
        "status",
    )
    accessions = _current_accessions(discovery, as_of=as_of)
    (
        liability_candidates,
        eligible_candidates,
        candidate_acceptance,
        identity_candidates,
    ) = _candidate_signals(candidates, as_of=as_of)
    identity_by_ticker = {
        ticker: group
        for ticker, group in identity_candidates.groupby("ticker")
    } if not identity_candidates.empty else {}

    rows: list[dict[str, object]] = []
    for row in residual.itertuples(index=False):
        ticker = str(row.ticker).upper()
        cik = int(float(row.cik))
        numeric = getattr(row, "liabilities_numeric")
        companyfacts = cache_dir / "companyfacts" / f"CIK{cik:010d}.json"
        statuses = discovery_status.get(ticker, "")
        accession_text = discovery_accession.get(ticker, "")
        cache_present = companyfacts.exists()
        alternate_tags, total_like_tags = _alternate_liability_tags(
            companyfacts, accessions.get(ticker, {})
        )
        identity = identity_by_ticker.get(ticker, pd.DataFrame())
        identity_present = not identity.empty

        if pd.notna(numeric) and float(numeric) <= 0:
            classification = "nonpositive_canonical_value"
            action = "investigate_invalid_value"
        elif not statuses:
            classification = "no_current_discovery"
            action = "targeted_sec_refresh"
        elif {"new_filing_partial", "submissions_error"}.intersection(
            statuses.split("|")
        ):
            classification = "current_discovery_incomplete"
            action = "targeted_sec_refresh"
        elif "new_filing_cached" not in statuses.split("|"):
            classification = "no_new_supported_filing"
            action = "documented_no_supported_fact"
        elif not accession_text:
            classification = "cached_filing_missing_accession"
            action = "targeted_sec_refresh"
        elif not cache_present:
            classification = "missing_companyfacts_cache"
            action = "targeted_sec_refresh"
        elif ticker in eligible_candidates:
            classification = "liabilities_candidate_not_selected"
            action = "candidate_not_selected"
        elif ticker in liability_candidates:
            classification = "liabilities_candidate_not_pit_eligible"
            action = "documented_no_supported_fact"
        elif identity_present:
            classification = "same_context_assets_minus_equity_candidate"
            action = "research_identity_candidate"
        elif total_like_tags or alternate_tags:
            classification = "unsupported_alternate_liability_tag_present"
            action = "research_alternate_tag"
        else:
            classification = "no_supported_current_liabilities_fact"
            action = "documented_no_supported_fact"

        rows.append(
            {
                "ticker": ticker,
                "cik": cik,
                "company_name": getattr(row, "company_name", ""),
                "total_liabilities": getattr(row, "total_liabilities"),
                "classification": classification,
                "recommended_action": action,
                "financial_health_factor_count": getattr(
                    row, "financial_health_factor_count", pd.NA
                ),
                "financial_health_eligible": getattr(
                    row, "financial_health_eligible", pd.NA
                ),
                "financial_health_score": getattr(
                    row, "financial_health_score", pd.NA
                ),
                "top_conviction_eligible": getattr(
                    row, "top_conviction_eligible", pd.NA
                ),
                "discovery_statuses": statuses,
                "discovery_accessions": accession_text,
                "companyfacts_cache_present": cache_present,
                "candidate_audit_statuses": candidate_audit_status.get(ticker, ""),
                "liabilities_merge_statuses": merge_status.get(ticker, ""),
                "current_liabilities_candidate_present": ticker in liability_candidates,
                "pit_eligible_liabilities_candidate_present": ticker in eligible_candidates,
                "liabilities_candidate_acceptance_status": candidate_acceptance.get(
                    ticker, ""
                ),
                "same_context_identity_candidate_present": identity_present,
                "same_context_identity_candidate_count": len(identity),
                "identity_derived_liabilities_values": "|".join(
                    str(int(value) if float(value).is_integer() else float(value))
                    for value in sorted(
                        set(
                            pd.to_numeric(
                                identity.get(
                                    "derived_liabilities",
                                    pd.Series(dtype="float64"),
                                ),
                                errors="coerce",
                            ).dropna()
                        )
                    )
                ),
                "current_filing_liability_tags": alternate_tags,
                "current_filing_total_like_tags": total_like_tags,
                "companyfacts_path": str(companyfacts),
            }
        )

    detail = pd.DataFrame(rows).sort_values(
        ["recommended_action", "ticker"], kind="stable"
    ).reset_index(drop=True)
    actions = detail["recommended_action"].value_counts().to_dict()
    factor_counts = pd.to_numeric(
        current.get(
            "financial_health_factor_count",
            pd.Series(pd.NA, index=current.index),
        ),
        errors="coerce",
    )
    health_eligible = current.get(
        "financial_health_eligible", pd.Series(False, index=current.index)
    ).fillna(False).astype(bool)
    summary = LiabilitiesGapSummary(
        universe_rows=len(current),
        positive_liabilities=int(current["liabilities_numeric"].gt(0).sum()),
        residual_rows=len(detail),
        blank_liabilities=int(current["liabilities_numeric"].isna().sum()),
        nonpositive_liabilities=int(
            (
                current["liabilities_numeric"].notna()
                & current["liabilities_numeric"].le(0)
            ).sum()
        ),
        health_eligible=int(health_eligible.sum()),
        health_factor_count_0=int(factor_counts.eq(0).sum()),
        health_factor_count_1=int(factor_counts.eq(1).sum()),
        health_factor_count_2=int(factor_counts.eq(2).sum()),
        health_factor_count_3=int(factor_counts.eq(3).sum()),
        targeted_sec_refresh=int(actions.get("targeted_sec_refresh", 0)),
        investigate_invalid_value=int(actions.get("investigate_invalid_value", 0)),
        candidate_not_selected=int(actions.get("candidate_not_selected", 0)),
        research_identity_candidate=int(
            actions.get("research_identity_candidate", 0)
        ),
        research_alternate_tag=int(actions.get("research_alternate_tag", 0)),
        documented_no_supported_fact=int(
            actions.get("documented_no_supported_fact", 0)
        ),
    )
    return detail, summary


def liabilities_coverage_by_year(scored: pd.DataFrame) -> pd.DataFrame:
    """Summarize liability and Health coverage for every available panel year."""

    if "decision_date" not in scored.columns:
        raise ValueError("Scored panel is missing decision_date")
    frame = scored.copy()
    frame["decision_date"] = pd.to_datetime(frame["decision_date"], errors="coerce")
    frame = frame.loc[frame["decision_date"].notna()].copy()
    frame["year"] = frame["decision_date"].dt.year
    liabilities = pd.to_numeric(frame.get("total_liabilities"), errors="coerce")
    frame["positive_liabilities"] = liabilities.gt(0)
    frame["health_eligible"] = frame.get(
        "financial_health_eligible", pd.Series(False, index=frame.index)
    ).fillna(False).astype(bool)
    return (
        frame.groupby("year", as_index=False)
        .agg(
            rows=("ticker", "size"),
            tickers=("ticker", "nunique"),
            positive_liabilities=("positive_liabilities", "sum"),
            financial_health_eligible=("health_eligible", "sum"),
        )
        .assign(
            liabilities_coverage_pct=lambda value: (
                value["positive_liabilities"] / value["rows"]
            ),
            financial_health_coverage_pct=lambda value: (
                value["financial_health_eligible"] / value["rows"]
            ),
        )
    )


def summary_payload(summary: LiabilitiesGapSummary) -> dict[str, int]:
    return asdict(summary)
