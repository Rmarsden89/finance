from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from finance.data.sources.sec_financial_statements import (
    load_sec_financial_statement_zip,
)
from finance.research.liabilities_historical_validation import (
    SUPPORTED_FORMS,
    availability_timestamp,
)


SHARE_TAG = "EntityCommonStockSharesOutstanding"
_ALLOWED_MEMBER_TERMS = (
    "commonstockmember",
    "commonstockclass",
    "commonclass",
    "nonvotingcommonstockmember",
    "preferredstockmember",
)


def _clean(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def _segment_members(value: object) -> list[str] | None:
    """Return explicit member values from SEC num.txt segments, fail closed."""

    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return []

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    members: list[str] = []

    def walk(obj: object, *, key: str = "") -> None:
        if isinstance(obj, dict):
            for child_key, child_value in obj.items():
                walk(child_value, key=str(child_key))
        elif isinstance(obj, list):
            for child in obj:
                walk(child, key=key)
        elif isinstance(obj, str):
            low_key = key.lower()
            low_value = obj.lower()
            if "member" in low_key or "member" in low_value or "class" in low_value:
                members.append(obj)

    if parsed is not None:
        walk(parsed)
        if members:
            return members

    # Some historical SEC sets serialize segments as compact text rather than
    # JSON. Accept only clearly recognizable member-like tokens.
    tokens = [
        token.strip(" []{}()\"'")
        for token in text.replace(";", ",").split(",")
        if token.strip()
    ]
    member_tokens = [
        token
        for token in tokens
        if "member" in token.lower() or "class" in token.lower()
    ]
    return member_tokens or None


def _recognized_share_class_segments(value: object) -> bool:
    members = _segment_members(value)
    if members is None or not members:
        return False
    return all(
        any(term in member.lower() for term in _ALLOWED_MEMBER_TERMS)
        for member in members
    )


def build_quarter_share_candidates(zip_path: Path) -> pd.DataFrame:
    """Build one fail-closed raw-DEI share candidate per filing.

    SEC Financial Statement Data Set values are already numeric/scaled. This
    function mirrors the current raw-filing rule as closely as the archive
    schema allows: standard DEI tag only, share unit, positive instant values,
    undimensioned preferred, otherwise recognized share-class segments summed.
    """

    quarter = load_sec_financial_statement_zip(zip_path)
    num = quarter.numeric_facts.copy()
    sub = quarter.submissions.copy()

    facts = num.loc[_clean(num["tag"]).eq(SHARE_TAG)].copy()
    if facts.empty:
        return pd.DataFrame()

    facts["uom_clean"] = _clean(facts["uom"]).str.lower()
    facts["segments_clean"] = _clean(
        facts.get("segments", pd.Series("", index=facts.index))
    )
    facts["coreg_clean"] = _clean(
        facts.get("coreg", pd.Series("", index=facts.index))
    )
    facts["value_num"] = pd.to_numeric(facts["value"], errors="coerce")
    facts["qtrs_num"] = pd.to_numeric(facts["qtrs"], errors="coerce")
    facts["ddate_date"] = pd.to_datetime(
        facts["ddate_date"], errors="coerce"
    ).dt.normalize()

    if "name" not in sub.columns:
        sub["name"] = ""

    facts = facts.merge(
        sub[
            [
                "adsh",
                "cik",
                "name",
                "form",
                "period_date",
                "filed_date",
                "accepted_at",
            ]
        ],
        on="adsh",
        how="inner",
        validate="many_to_one",
    )
    facts["form"] = _clean(facts["form"])
    facts["accepted_at"] = pd.to_datetime(
        facts["accepted_at"], errors="coerce"
    )
    facts["filed_date"] = pd.to_datetime(
        facts["filed_date"], errors="coerce"
    )
    facts["period_date"] = pd.to_datetime(
        facts["period_date"], errors="coerce"
    ).dt.normalize()

    facts = facts.loc[
        facts["form"].isin(SUPPORTED_FORMS)
        & facts["uom_clean"].str.contains("share", na=False)
        & facts["qtrs_num"].eq(0)
        & facts["value_num"].notna()
        & facts["value_num"].gt(0)
        & facts["ddate_date"].notna()
        & facts["accepted_at"].notna()
        & facts["filed_date"].notna()
    ].copy()
    if facts.empty:
        return pd.DataFrame()

    # A cover share fact should not refer to an instant after the filing was
    # accepted. Reject those rows before constructing a filing-level candidate.
    accepted_date = facts["accepted_at"].dt.normalize()
    facts = facts.loc[facts["ddate_date"].le(accepted_date)].copy()
    if facts.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    filing_keys = [
        "adsh",
        "cik",
        "name",
        "form",
        "period_date",
        "filed_date",
        "accepted_at",
    ]
    for key, group in facts.groupby(filing_keys, dropna=False, sort=False):
        (
            adsh,
            cik,
            name,
            form,
            period_date,
            filed_date,
            accepted_at,
        ) = key

        latest_instant = group["ddate_date"].max()
        latest = group.loc[group["ddate_date"].eq(latest_instant)].copy()
        undim = latest.loc[
            latest["segments_clean"].eq("")
            & latest["coreg_clean"].eq("")
        ].copy()

        status = "candidate"
        selection_rule = ""
        candidate_value: float | None = None
        component_count = 0
        component_segments = ""
        reason = ""

        if not undim.empty:
            values = sorted(set(float(v) for v in undim["value_num"]))
            if len(values) == 1:
                candidate_value = values[0]
                selection_rule = "undimensioned_preferred"
                component_count = len(undim)
                reason = "Single unique undimensioned latest-instant DEI value."
            else:
                status = "conflicting_undimensioned_values"
                selection_rule = "undimensioned_preferred"
                component_count = len(undim)
                reason = "Multiple undimensioned values conflict."
        else:
            if not latest["coreg_clean"].eq("").all():
                status = "unsupported_coreg"
                selection_rule = "share_class_sum"
                reason = "Latest-instant share facts include coreg values."
            elif not latest["segments_clean"].map(
                _recognized_share_class_segments
            ).all():
                status = "unsupported_dimension"
                selection_rule = "share_class_sum"
                component_segments = "|".join(
                    sorted(set(latest["segments_clean"].astype(str)))
                )
                reason = "Latest-instant dimensions are not recognized share classes."
            else:
                grouped = (
                    latest.groupby("segments_clean", sort=True)["value_num"]
                    .agg(lambda values: sorted(set(float(v) for v in values)))
                )
                if grouped.map(len).gt(1).any():
                    status = "conflicting_class_values"
                    selection_rule = "share_class_sum"
                    reason = "A share class has conflicting latest-instant values."
                else:
                    components = [float(values[0]) for values in grouped]
                    total = sum(components)
                    if total > 0:
                        candidate_value = total
                        selection_rule = "share_class_sum"
                        component_count = len(components)
                        component_segments = "|".join(grouped.index.astype(str))
                        reason = "Recognized share-class values summed."
                    else:
                        status = "nonpositive_candidate"
                        selection_rule = "share_class_sum"
                        reason = "Summed share-class candidate is nonpositive."

        rows.append(
            {
                "adsh": adsh,
                "cik": cik,
                "name": name,
                "form": form,
                "period_date": period_date,
                "filed_date": filed_date,
                "accepted_at": accepted_at,
                "context_instant": latest_instant,
                "status": status,
                "candidate_shares": candidate_value,
                "selection_rule": selection_rule,
                "component_count": component_count,
                "component_segments": component_segments,
                "reason": reason,
                "source_zip": zip_path.name,
            }
        )

    result = pd.DataFrame(rows)
    if not result.empty:
        result["available_at"] = [
            availability_timestamp(accepted, filed)
            for accepted, filed in zip(
                result["accepted_at"], result["filed_date"]
            )
        ]
    return result


def select_pit_share_candidate(
    filings: pd.DataFrame,
    *,
    decision_cutoff: pd.Timestamp,
    decision_date: pd.Timestamp,
) -> pd.Series | None:
    """Select the latest eligible filing candidate as of one decision cutoff."""

    if filings.empty:
        return None

    eligible = filings.loc[
        filings["status"].eq("candidate")
        & pd.to_numeric(
            filings["candidate_shares"], errors="coerce"
        ).gt(0)
        & pd.to_datetime(
            filings["available_at"], errors="coerce", utc=True
        ).le(
            pd.Timestamp(decision_cutoff).tz_localize("UTC")
            if pd.Timestamp(decision_cutoff).tzinfo is None
            else pd.Timestamp(decision_cutoff).tz_convert("UTC")
        )
        & pd.to_datetime(
            filings["context_instant"], errors="coerce"
        ).le(pd.Timestamp(decision_date).tz_localize(None))
    ].copy()
    if eligible.empty:
        return None

    eligible["_available_sort"] = pd.to_datetime(
        eligible["available_at"], errors="coerce", utc=True
    )
    eligible["_instant_sort"] = pd.to_datetime(
        eligible["context_instant"], errors="coerce"
    )
    eligible = eligible.sort_values(
        ["_available_sort", "_instant_sort", "accepted_at", "adsh"],
        kind="stable",
    )
    return eligible.iloc[-1]
