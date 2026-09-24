from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import math
import re

import pandas as pd


SHARE_FACT_NAME = "dei:EntityCommonStockSharesOutstanding"


@dataclass(frozen=True)
class RawShareCandidate:
    ticker: str
    status: str
    value: float | None
    context_instant: str
    selection_rule: str
    component_count: int
    component_dimensions: str
    component_values: str
    reason: str


def _parse_number(
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

    if not math.isfinite(value):
        return None
    return value


def _is_share_class_dimension(value: object) -> bool:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return False

    # The raw collector currently stores explicit-member values joined by "|".
    # For the initial V3 rule, every member must look like an equity/share-class
    # member. Other entity/subsidiary/segment dimensions fail closed.
    members = [item.strip() for item in text.split("|") if item.strip()]
    if not members:
        return False

    allowed_terms = (
        "commonstockmember",
        "commonclass",
        "nonvotingcommonstockmember",
        "preferredstockmember",
    )
    return all(
        any(term in member.lower() for term in allowed_terms)
        for member in members
    )


def build_raw_share_candidate(
    facts: pd.DataFrame,
    *,
    ticker: str,
    as_of: date | pd.Timestamp,
) -> RawShareCandidate:
    """Build one research-only shares candidate from raw Inline XBRL evidence."""

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
    frame = facts.loc[facts["fact_name"].astype(str).eq(SHARE_FACT_NAME)].copy()
    if frame.empty:
        return RawShareCandidate(
            ticker=ticker,
            status="no_share_fact",
            value=None,
            context_instant="",
            selection_rule="",
            component_count=0,
            component_dimensions="",
            component_values="",
            reason="No raw DEI EntityCommonStockSharesOutstanding fact.",
        )

    accepted = pd.to_datetime(frame["accepted_at"], errors="coerce", utc=True)
    cutoff = pd.Timestamp(as_of)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    else:
        cutoff = cutoff.tz_convert("UTC")
    frame = frame.loc[accepted.notna() & accepted.lt(cutoff)].copy()
    if frame.empty:
        return RawShareCandidate(
            ticker=ticker,
            status="not_pit_eligible",
            value=None,
            context_instant="",
            selection_rule="",
            component_count=0,
            component_dimensions="",
            component_values="",
            reason="Raw share facts were not accepted before the decision cutoff.",
        )

    instants = pd.to_datetime(frame["context_instant"], errors="coerce")
    frame = frame.loc[instants.notna()].copy()
    frame["_instant"] = pd.to_datetime(frame["context_instant"], errors="coerce")
    frame = frame.loc[frame["_instant"].le(pd.Timestamp(as_of))].copy()
    if frame.empty:
        return RawShareCandidate(
            ticker=ticker,
            status="no_eligible_instant",
            value=None,
            context_instant="",
            selection_rule="",
            component_count=0,
            component_dimensions="",
            component_values="",
            reason="No PIT-eligible instant context was present.",
        )

    frame["_value"] = [
        _parse_number(value, scale=scale, sign=sign)
        for value, scale, sign in zip(
            frame["value_text"], frame["scale"], frame["sign"]
        )
    ]
    frame = frame.loc[
        pd.Series(frame["_value"], index=frame.index).notna()
    ].copy()
    if frame.empty:
        return RawShareCandidate(
            ticker=ticker,
            status="unparseable_share_fact",
            value=None,
            context_instant="",
            selection_rule="",
            component_count=0,
            component_dimensions="",
            component_values="",
            reason="Share facts existed but no numeric value could be parsed.",
        )

    latest = frame["_instant"].max()
    frame = frame.loc[frame["_instant"].eq(latest)].copy()
    instant_text = latest.date().isoformat()

    unit_ok = frame["unit_ref"].astype(str).str.lower().str.contains("share")
    frame = frame.loc[unit_ok].copy()
    if frame.empty:
        return RawShareCandidate(
            ticker=ticker,
            status="unsupported_share_unit",
            value=None,
            context_instant=instant_text,
            selection_rule="",
            component_count=0,
            component_dimensions="",
            component_values="",
            reason="Latest raw share facts did not use a shares-like unit.",
        )

    dims = frame["context_dimensions"].fillna("").astype(str).str.strip()
    undimensioned = frame.loc[dims.eq("") | dims.str.lower().eq("nan")].copy()

    if not undimensioned.empty:
        unique_values = sorted(set(float(value) for value in undimensioned["_value"]))
        if len(unique_values) != 1:
            return RawShareCandidate(
                ticker=ticker,
                status="conflicting_undimensioned_values",
                value=None,
                context_instant=instant_text,
                selection_rule="undimensioned_preferred",
                component_count=len(undimensioned),
                component_dimensions="",
                component_values="|".join(str(value) for value in unique_values),
                reason="Multiple conflicting undimensioned share facts exist.",
            )
        value = unique_values[0]
        if value <= 0:
            return RawShareCandidate(
                ticker=ticker,
                status="nonpositive_candidate",
                value=None,
                context_instant=instant_text,
                selection_rule="undimensioned_preferred",
                component_count=len(undimensioned),
                component_dimensions="",
                component_values=str(value),
                reason="Undimensioned raw share candidate is nonpositive.",
            )
        return RawShareCandidate(
            ticker=ticker,
            status="candidate",
            value=value,
            context_instant=instant_text,
            selection_rule="undimensioned_preferred",
            component_count=len(undimensioned),
            component_dimensions="",
            component_values=str(value),
            reason="Single unique undimensioned latest-instant share value.",
        )

    dimensioned = frame.copy()
    if not dimensioned["context_dimensions"].map(_is_share_class_dimension).all():
        rejected = sorted(
            set(
                str(value)
                for value in dimensioned.loc[
                    ~dimensioned["context_dimensions"].map(
                        _is_share_class_dimension
                    ),
                    "context_dimensions",
                ]
            )
        )
        return RawShareCandidate(
            ticker=ticker,
            status="unsupported_dimension",
            value=None,
            context_instant=instant_text,
            selection_rule="share_class_sum",
            component_count=len(dimensioned),
            component_dimensions="|".join(rejected),
            component_values="",
            reason="At least one latest-instant dimension is not a recognized share class.",
        )

    by_dimension = dimensioned.groupby(
        dimensioned["context_dimensions"].astype(str),
        sort=True,
    )["_value"].agg(lambda values: sorted(set(float(value) for value in values)))
    conflicts = by_dimension.loc[by_dimension.map(len).gt(1)]
    if not conflicts.empty:
        return RawShareCandidate(
            ticker=ticker,
            status="conflicting_class_values",
            value=None,
            context_instant=instant_text,
            selection_rule="share_class_sum",
            component_count=len(dimensioned),
            component_dimensions="|".join(conflicts.index.astype(str)),
            component_values="",
            reason="A share class has multiple conflicting latest-instant values.",
        )

    components = [
        (dimension, float(values[0]))
        for dimension, values in by_dimension.items()
    ]
    total = sum(value for _, value in components)
    if total <= 0:
        return RawShareCandidate(
            ticker=ticker,
            status="nonpositive_candidate",
            value=None,
            context_instant=instant_text,
            selection_rule="share_class_sum",
            component_count=len(components),
            component_dimensions="|".join(dimension for dimension, _ in components),
            component_values="|".join(str(value) for _, value in components),
            reason="Summed share-class candidate is nonpositive.",
        )

    return RawShareCandidate(
        ticker=ticker,
        status="candidate",
        value=total,
        context_instant=instant_text,
        selection_rule="share_class_sum",
        component_count=len(components),
        component_dimensions="|".join(dimension for dimension, _ in components),
        component_values="|".join(str(value) for _, value in components),
        reason="Latest-instant share-class facts summed after dimension validation.",
    )


def raw_share_validation_row(
    *,
    candidate: RawShareCandidate,
    canonical_value: object,
) -> dict[str, object]:
    canonical = pd.to_numeric(pd.Series([canonical_value]), errors="coerce").iloc[0]
    candidate_value = candidate.value
    row = asdict(candidate)
    row["canonical_value"] = (
        float(canonical) if pd.notna(canonical) and float(canonical) > 0 else None
    )

    if candidate_value is None or row["canonical_value"] is None:
        row["absolute_difference"] = None
        row["absolute_relative_error"] = None
        row["validation_band"] = "not_comparable"
        return row

    difference = candidate_value - row["canonical_value"]
    relative = abs(difference) / row["canonical_value"]
    row["absolute_difference"] = abs(difference)
    row["absolute_relative_error"] = relative
    if difference == 0:
        band = "exact_match"
    elif relative <= 0.0001:
        band = "within_0_01_pct"
    elif relative <= 0.001:
        band = "within_0_1_pct"
    elif relative <= 0.01:
        band = "within_1_pct"
    else:
        band = "material_difference"
    row["validation_band"] = band
    return row
