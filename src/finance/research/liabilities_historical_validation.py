from __future__ import annotations

from pathlib import Path

import pandas as pd

from finance.data.sources.sec_financial_statements import (
    load_sec_financial_statement_zip,
)
from finance.research.pit_reconciliation import eastern_timestamp


SUPPORTED_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}
TAGS = {"Liabilities", "LiabilitiesCurrent", "LiabilitiesNoncurrent"}


def _clean(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def build_quarter_liabilities_comparisons(
    zip_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build strict same-filing historical liabilities comparisons.

    Only positive, undimensioned, non-coreg, USD instant facts whose fact date
    matches the filing period are eligible. Direct Liabilities is retained as
    the control target; Current + Noncurrent is the candidate construction.
    """
    quarter = load_sec_financial_statement_zip(zip_path)
    num = quarter.numeric_facts.copy()
    sub = quarter.submissions.copy()

    tag = _clean(num["tag"])
    facts = num.loc[tag.isin(TAGS)].copy()
    if facts.empty:
        return pd.DataFrame(), pd.DataFrame()

    facts["tag"] = _clean(facts["tag"])
    facts["uom"] = _clean(facts["uom"]).str.upper()
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
    ).dt.date

    if "name" not in sub.columns:
        sub = sub.copy()
        sub["name"] = ""

    keep_sub = sub[
        [
            "adsh",
            "cik",
            "name",
            "form",
            "period_date",
            "filed_date",
            "accepted_at",
        ]
    ].copy()
    facts = facts.merge(
        keep_sub,
        on="adsh",
        how="inner",
        validate="many_to_one",
    )
    facts["period_date"] = pd.to_datetime(
        facts["period_date"], errors="coerce"
    ).dt.date
    facts["filed_date"] = pd.to_datetime(
        facts["filed_date"], errors="coerce"
    ).dt.date
    facts["accepted_at"] = pd.to_datetime(
        facts["accepted_at"], errors="coerce"
    )
    facts["form"] = _clean(facts["form"])

    eligible = facts.loc[
        facts["form"].isin(SUPPORTED_FORMS)
        & facts["uom"].eq("USD")
        & facts["qtrs_num"].eq(0)
        & facts["segments_clean"].eq("")
        & facts["coreg_clean"].eq("")
        & facts["value_num"].notna()
        & facts["value_num"].gt(0)
        & facts["ddate_date"].notna()
        & facts["period_date"].notna()
        & facts["ddate_date"].eq(facts["period_date"])
        & facts["accepted_at"].notna()
        & facts["filed_date"].notna()
    ].copy()

    if eligible.empty:
        return pd.DataFrame(), pd.DataFrame()

    key = [
        "adsh",
        "cik",
        "name",
        "form",
        "period_date",
        "filed_date",
        "accepted_at",
        "ddate_date",
    ]

    grouped = (
        eligible.groupby(key + ["tag"], dropna=False)["value_num"]
        .agg(
            value_count="nunique",
            value_min="min",
            value_max="max",
            fact_rows="size",
        )
        .reset_index()
    )

    unique = grouped.loc[grouped["value_count"].eq(1)].copy()
    wide = unique.pivot_table(
        index=key,
        columns="tag",
        values="value_min",
        aggfunc="first",
    ).reset_index()
    wide.columns.name = None

    for tag_name in TAGS:
        if tag_name not in wide.columns:
            wide[tag_name] = pd.NA

    wide["has_direct"] = pd.to_numeric(
        wide["Liabilities"], errors="coerce"
    ).gt(0)
    wide["has_current"] = pd.to_numeric(
        wide["LiabilitiesCurrent"], errors="coerce"
    ).gt(0)
    wide["has_noncurrent"] = pd.to_numeric(
        wide["LiabilitiesNoncurrent"], errors="coerce"
    ).gt(0)
    wide["has_construction"] = wide["has_current"] & wide["has_noncurrent"]

    construction = (
        pd.to_numeric(wide["LiabilitiesCurrent"], errors="coerce")
        + pd.to_numeric(wide["LiabilitiesNoncurrent"], errors="coerce")
    )
    wide["constructed_liabilities"] = construction.where(
        wide["has_construction"]
    )

    comparable = wide.loc[
        wide["has_direct"] & wide["has_construction"]
    ].copy()
    if not comparable.empty:
        comparable["difference"] = (
            comparable["constructed_liabilities"]
            - pd.to_numeric(comparable["Liabilities"], errors="coerce")
        )
        comparable["absolute_difference"] = comparable["difference"].abs()
        comparable["absolute_relative_error"] = (
            comparable["absolute_difference"]
            / pd.to_numeric(comparable["Liabilities"], errors="coerce")
        )
        comparable["validation_band"] = "material_difference"
        comparable.loc[
            comparable["absolute_relative_error"].le(0.01),
            "validation_band",
        ] = "within_1_pct"
        comparable.loc[
            comparable["absolute_relative_error"].le(0.001),
            "validation_band",
        ] = "within_0_1_pct"
        comparable.loc[
            comparable["absolute_relative_error"].le(0.0001),
            "validation_band",
        ] = "within_0_01_pct"
        comparable.loc[
            comparable["absolute_difference"].eq(0),
            "validation_band",
        ] = "exact_match"

    wide["source_zip"] = zip_path.name
    if not comparable.empty:
        comparable["source_zip"] = zip_path.name
    return wide.reset_index(drop=True), comparable.reset_index(drop=True)


def availability_timestamp(
    accepted_at: object,
    filed_date: object,
) -> pd.Timestamp:
    accepted = eastern_timestamp(accepted_at)
    filed = eastern_timestamp(filed_date).normalize() + pd.Timedelta(hours=6)
    return max(accepted, filed)
