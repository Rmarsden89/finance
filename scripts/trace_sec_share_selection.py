from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from finance.data.sec_canonical import _ALLOWED_FORMS, _EXPECTED_STATEMENTS
from finance.data.sec_concepts import CANONICAL_TAGS
from finance.data.sources.sec_financial_statements import (
    load_sec_financial_statement_zip,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Trace one CIK's shares_outstanding facts through the historical "
            "canonical SEC filters. This is read-only and does not modify caches."
        )
    )
    parser.add_argument("zip_path", type=Path)
    parser.add_argument("--cik", type=int, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/sec_share_selection_trace.csv"),
    )
    return parser.parse_args()


def _clean(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def trace_share_selection(
    submissions: pd.DataFrame,
    numeric_facts: pd.DataFrame,
    presentation: pd.DataFrame,
    *,
    cik: int,
) -> pd.DataFrame:
    share_tags = set(CANONICAL_TAGS["shares_outstanding"])
    filing_keys = set(
        submissions.loc[
            pd.to_numeric(submissions["cik"], errors="coerce").eq(cik),
            "adsh",
        ].astype(str)
    )
    facts = numeric_facts.loc[
        numeric_facts["adsh"].astype(str).isin(filing_keys)
        & numeric_facts["tag"].isin(share_tags)
    ].copy()
    if facts.empty:
        return pd.DataFrame()

    sub_columns = [
        column
        for column in (
            "adsh",
            "cik",
            "name",
            "form",
            "period_date",
            "filed_date",
            "accepted_at",
        )
        if column in submissions.columns
    ]
    facts = facts.merge(
        submissions[sub_columns],
        on="adsh",
        how="left",
        validate="many_to_one",
    )

    pre = presentation.loc[
        presentation["tag"].isin(share_tags),
        ["adsh", "tag", "version", "stmt"],
    ].copy()
    statement_summary = (
        pre.groupby(["adsh", "tag", "version"], dropna=False)["stmt"]
        .agg(lambda values: "|".join(sorted(set(_clean(values)) - {""})))
        .rename("presentation_statements")
        .reset_index()
    )
    facts = facts.merge(
        statement_summary,
        on=["adsh", "tag", "version"],
        how="left",
        validate="many_to_one",
    )
    facts["presentation_statements"] = _clean(facts["presentation_statements"])

    facts["supported_form"] = facts["form"].isin(_ALLOWED_FORMS)
    facts["coreg_blank"] = _clean(
        facts.get("coreg", pd.Series("", index=facts.index))
    ).eq("")
    facts["non_dimensional"] = _clean(
        facts.get("segments", pd.Series("", index=facts.index))
    ).eq("")
    allowed_statements = _EXPECTED_STATEMENTS["shares_outstanding"]
    facts["statement_matched"] = facts["presentation_statements"].map(
        lambda value: bool(set(value.split("|")) & allowed_statements)
    )
    facts["instant_period"] = pd.to_numeric(
        facts["qtrs"], errors="coerce"
    ).eq(0)
    facts["current_period"] = pd.to_datetime(
        facts["ddate_date"], errors="coerce"
    ).eq(pd.to_datetime(facts["period_date"], errors="coerce"))
    facts["numeric_value"] = pd.to_numeric(
        facts["value"], errors="coerce"
    ).notna()

    checks = [
        "supported_form",
        "coreg_blank",
        "non_dimensional",
        "statement_matched",
        "instant_period",
        "current_period",
        "numeric_value",
    ]
    rejection_names = {
        "supported_form": "unsupported_form",
        "coreg_blank": "coreg",
        "non_dimensional": "dimensional_context",
        "statement_matched": "statement_placement",
        "instant_period": "period_shape",
        "current_period": "not_filing_current_period",
        "numeric_value": "non_numeric_value",
    }

    def first_rejection(row: pd.Series) -> str:
        for check in checks:
            if not bool(row[check]):
                return rejection_names[check]
        return "canonical_eligible"

    facts["first_rejection_stage"] = facts.apply(first_rejection, axis=1)
    facts["canonical_eligible"] = facts[checks].all(axis=1)

    output_columns = [
        "cik",
        "name",
        "adsh",
        "form",
        "period_date",
        "accepted_at",
        "tag",
        "version",
        "ddate_date",
        "qtrs",
        "uom",
        "segments",
        "coreg",
        "value",
        "presentation_statements",
        *checks,
        "first_rejection_stage",
        "canonical_eligible",
    ]
    return facts[[column for column in output_columns if column in facts.columns]].sort_values(
        ["ddate_date", "adsh", "tag"],
        kind="stable",
    ).reset_index(drop=True)


def main() -> None:
    args = parse_args()
    quarter = load_sec_financial_statement_zip(args.zip_path)
    trace = trace_share_selection(
        quarter.submissions,
        quarter.numeric_facts,
        quarter.presentation,
        cik=args.cik,
    )
    if trace.empty:
        raise SystemExit(
            f"No shares_outstanding facts found for CIK {args.cik} in {args.zip_path}."
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    trace.to_csv(args.output, index=False)
    current = trace.loc[trace["current_period"]].copy()

    print("SEC SHARES CANONICAL-SELECTION TRACE")
    print(f"CIK:                       {args.cik}")
    print(f"ZIP:                       {args.zip_path}")
    print(f"Share fact rows:           {len(trace):,}")
    print(f"Current-period rows:       {len(current):,}")
    print(f"Canonical-eligible rows:   {int(trace['canonical_eligible'].sum()):,}")
    print("Current-period outcomes:")
    for stage, count in current["first_rejection_stage"].value_counts().items():
        print(f"  {stage}: {count:,}")
    print(f"Output:                    {args.output}")
    print("Canonical winners/cache were NOT modified.")


if __name__ == "__main__":
    main()
