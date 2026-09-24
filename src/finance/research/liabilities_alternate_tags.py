from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import math

import pandas as pd


DIRECT_LIABILITIES = "us-gaap:Liabilities"
CURRENT_LIABILITIES = "us-gaap:LiabilitiesCurrent"
NONCURRENT_LIABILITIES = "us-gaap:LiabilitiesNoncurrent"
TOTAL_LIKE = {
    "us-gaap:LiabilitiesAndStockholdersEquity",
    "us-gaap:LiabilitiesAndPartnersCapital",
}


@dataclass(frozen=True)
class AlternateLiabilitiesEvidence:
    ticker: str
    status: str
    context_instant: str
    direct_value: float | None
    current_value: float | None
    noncurrent_value: float | None
    current_plus_noncurrent: float | None
    total_like_tags: str
    other_liability_tags: str
    reason: str


def parse_inline_numeric(
    value_text: object,
    *,
    scale: object = "",
    sign: object = "",
) -> float | None:
    text = str(value_text or "").strip()
    if not text or text.lower() == "nan":
        return None
    negative_parentheses = text.startswith("(") and text.endswith(")")
    if negative_parentheses:
        text = text[1:-1]
    text = text.replace(",", "").replace("$", "").strip()
    try:
        value = float(text)
    except ValueError:
        return None

    scale_text = str(scale or "").strip()
    if scale_text and scale_text.lower() != "nan":
        try:
            value *= 10 ** int(float(scale_text))
        except (TypeError, ValueError, OverflowError):
            return None

    sign_text = str(sign or "").strip()
    if sign_text == "-" or negative_parentheses:
        value = -abs(value)
    elif sign_text == "+":
        value = abs(value)

    return value if math.isfinite(value) else None


def _unique_positive_value(frame: pd.DataFrame) -> float | None:
    if frame.empty:
        return None
    values = {
        parse_inline_numeric(
            row.value_text,
            scale=getattr(row, "scale", ""),
            sign=getattr(row, "sign", ""),
        )
        for row in frame.itertuples(index=False)
    }
    values = {value for value in values if value is not None and value > 0}
    if len(values) != 1:
        return None
    return float(next(iter(values)))


def classify_alternate_liabilities_evidence(
    facts: pd.DataFrame,
    *,
    ticker: str,
    as_of: date | pd.Timestamp,
) -> AlternateLiabilitiesEvidence:
    required = {
        "fact_name",
        "accepted_at",
        "context_instant",
        "context_dimensions",
        "unit_ref",
        "scale",
        "sign",
        "value_text",
    }
    missing = sorted(required - set(facts.columns))
    if missing:
        raise ValueError(
            "Raw SEC evidence missing required columns: " + ", ".join(missing)
        )

    ticker = ticker.upper()
    frame = facts.copy()
    accepted = pd.to_datetime(frame["accepted_at"], errors="coerce", utc=True)
    cutoff = pd.Timestamp(as_of)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    else:
        cutoff = cutoff.tz_convert("UTC")
    frame = frame.loc[accepted.notna() & accepted.lt(cutoff)].copy()

    instants = pd.to_datetime(frame["context_instant"], errors="coerce")
    frame = frame.loc[instants.notna()].copy()
    frame["_instant"] = pd.to_datetime(frame["context_instant"], errors="coerce")
    frame = frame.loc[frame["_instant"].le(pd.Timestamp(as_of))].copy()

    dims = frame["context_dimensions"].fillna("").astype(str).str.strip()
    frame = frame.loc[dims.eq("") | dims.str.lower().eq("nan")].copy()
    frame = frame.loc[
        frame["unit_ref"].astype(str).str.upper().str.contains("USD")
    ].copy()

    if frame.empty:
        return AlternateLiabilitiesEvidence(
            ticker=ticker,
            status="no_eligible_undimensioned_liability_evidence",
            context_instant="",
            direct_value=None,
            current_value=None,
            noncurrent_value=None,
            current_plus_noncurrent=None,
            total_like_tags="",
            other_liability_tags="",
            reason=(
                "No PIT-eligible undimensioned USD liability-related facts "
                "were available."
            ),
        )

    latest = frame["_instant"].max()
    latest_frame = frame.loc[frame["_instant"].eq(latest)].copy()
    instant_text = latest.date().isoformat()

    direct = _unique_positive_value(
        latest_frame.loc[latest_frame["fact_name"].eq(DIRECT_LIABILITIES)]
    )
    current = _unique_positive_value(
        latest_frame.loc[latest_frame["fact_name"].eq(CURRENT_LIABILITIES)]
    )
    noncurrent = _unique_positive_value(
        latest_frame.loc[latest_frame["fact_name"].eq(NONCURRENT_LIABILITIES)]
    )

    names = set(latest_frame["fact_name"].astype(str))
    total_like = sorted(names.intersection(TOTAL_LIKE))
    other = sorted(
        name
        for name in names
        if "liabilit" in name.lower()
        and name not in {
            DIRECT_LIABILITIES,
            CURRENT_LIABILITIES,
            NONCURRENT_LIABILITIES,
            *TOTAL_LIKE,
        }
    )

    if direct is not None:
        status = "raw_direct_liabilities_candidate"
        reason = (
            "Raw filing contains one positive undimensioned us-gaap:Liabilities "
            "value at the latest eligible instant."
        )
    elif current is not None and noncurrent is not None:
        status = "current_plus_noncurrent_candidate"
        reason = (
            "Raw filing contains positive undimensioned current and noncurrent "
            "liabilities at the same latest eligible instant; construction "
            "requires separate control validation before use."
        )
    elif total_like:
        status = "total_like_only"
        reason = (
            "A liabilities-plus-equity total is present, but it is not total "
            "liabilities by itself."
        )
    else:
        status = "alternate_tags_only"
        reason = (
            "Liability-related tags are present, but no direct or current-plus-"
            "noncurrent construction is available under the strict rule."
        )

    return AlternateLiabilitiesEvidence(
        ticker=ticker,
        status=status,
        context_instant=instant_text,
        direct_value=direct,
        current_value=current,
        noncurrent_value=noncurrent,
        current_plus_noncurrent=(
            current + noncurrent
            if current is not None and noncurrent is not None
            else None
        ),
        total_like_tags="|".join(total_like),
        other_liability_tags="|".join(other),
        reason=reason,
    )
