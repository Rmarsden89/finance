from __future__ import annotations

import numpy as np
import pandas as pd


def characterize_q4_material_differences(
    q4_reconciliation: pd.DataFrame,
) -> pd.DataFrame:
    """Characterize >1% Q4 TTM-vs-annual differences for review only."""

    required = {
        "cik",
        "concept",
        "ttm_end_fy",
        "ttm_value",
        "reported_annual_value",
        "relative_difference_abs",
        "material_difference_gt_1pct",
        "quarter_derivations",
        "source_tags",
        "source_forms",
        "source_adshs",
        "reported_annual_source_tag",
    }
    missing = sorted(required - set(q4_reconciliation.columns))
    if missing:
        raise ValueError(
            "Q4 reconciliation missing diagnostic columns: "
            + ", ".join(missing)
        )

    detail = q4_reconciliation.loc[
        q4_reconciliation["material_difference_gt_1pct"].fillna(False)
    ].copy()
    if detail.empty:
        return detail

    detail["relative_difference_abs"] = pd.to_numeric(
        detail["relative_difference_abs"], errors="coerce"
    )
    detail["difference_magnitude_band"] = pd.cut(
        detail["relative_difference_abs"],
        bins=[0.01, 0.05, 0.20, np.inf],
        labels=["1-5pct", "5-20pct", "gt20pct"],
        include_lowest=False,
        right=True,
    ).astype("object")

    def derivation_pattern(value: object) -> str:
        parts = str(value or "").split("|")
        if len(parts) >= 4:
            q2 = parts[1]
            q3 = parts[2]
            if q2 == "direct_qtrs_1" and q3 == "direct_qtrs_1":
                return "q2_q3_both_direct"
            if q2 == "direct_qtrs_1" or q3 == "direct_qtrs_1":
                return "q2_or_q3_direct"
            return "q2_q3_both_derived"
        return "unparseable"

    detail["derivation_pattern"] = detail["quarter_derivations"].map(
        derivation_pattern
    )

    def atomic_tags(value: object) -> list[str]:
        text = str(value or "")
        tags: list[str] = []
        for quarter_part in text.split("||"):
            for tag in quarter_part.split("|"):
                cleaned = tag.strip()
                if cleaned and cleaned.lower() != "nan":
                    tags.append(cleaned)
        return tags

    tag_lists = detail["source_tags"].map(atomic_tags)
    detail["source_tag_count"] = tag_lists.map(len)
    detail["unique_source_tag_count"] = tag_lists.map(lambda x: len(set(x)))
    detail["source_tag_continuity"] = np.where(
        detail["unique_source_tag_count"].le(1),
        "single_tag",
        "multiple_tags",
    )
    detail["reported_annual_tag_matches_any_quarter_tag"] = [
        str(reported).strip() in set(tags)
        for reported, tags in zip(
            detail["reported_annual_source_tag"], tag_lists
        )
    ]

    form_text = detail["source_forms"].fillna("").astype(str)
    detail["amendment_present"] = form_text.str.contains(
        r"(?:10-Q/A|10-K/A|20-F/A|40-F/A)",
        regex=True,
    )

    adsh_lists = detail["source_adshs"].fillna("").astype(str).map(
        lambda value: [
            part.strip()
            for quarter_part in value.split("||")
            for part in quarter_part.split("|")
            if part.strip() and part.strip().lower() != "nan"
        ]
    )
    detail["unique_source_adsh_count"] = adsh_lists.map(
        lambda x: len(set(x))
    )

    detail["review_category"] = np.select(
        [
            detail["amendment_present"],
            detail["source_tag_continuity"].eq("multiple_tags"),
            detail["derivation_pattern"].eq("q2_q3_both_direct"),
        ],
        [
            "amendment_present",
            "source_tag_change",
            "direct_q2_q3_representation",
        ],
        default="other",
    )

    return detail.sort_values(
        ["concept", "relative_difference_abs", "cik", "ttm_end_fy"],
        ascending=[True, False, True, True],
        kind="stable",
    ).reset_index(drop=True)


def summarize_q4_difference_dimension(
    detail: pd.DataFrame,
    dimension: str,
) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame(
            columns=["concept", dimension, "rows", "unique_ciks"]
        )
    return (
        detail.groupby(["concept", dimension], as_index=False, dropna=False)
        .agg(
            rows=("cik", "size"),
            unique_ciks=("cik", "nunique"),
            median_relative_difference_abs=(
                "relative_difference_abs", "median"
            ),
            max_relative_difference_abs=(
                "relative_difference_abs", "max"
            ),
        )
        .sort_values(
            ["concept", "rows"],
            ascending=[True, False],
            kind="stable",
        )
        .reset_index(drop=True)
    )


def repeated_q4_difference_ciks(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame(
            columns=[
                "concept",
                "cik",
                "rows",
                "fiscal_years",
                "max_relative_difference_abs",
            ]
        )
    rows = (
        detail.groupby(["concept", "cik"], as_index=False)
        .agg(
            rows=("ttm_end_fy", "size"),
            fiscal_years=(
                "ttm_end_fy",
                lambda values: "|".join(
                    str(int(value))
                    for value in sorted(set(values.dropna()))
                ),
            ),
            max_relative_difference_abs=(
                "relative_difference_abs", "max"
            ),
            median_relative_difference_abs=(
                "relative_difference_abs", "median"
            ),
        )
    )
    return rows.loc[rows["rows"].gt(1)].sort_values(
        ["rows", "max_relative_difference_abs", "concept", "cik"],
        ascending=[False, False, True, True],
        kind="stable",
    ).reset_index(drop=True)
