from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from finance.data.sources.sec_financial_statements import (
    load_sec_financial_statement_zip,
)


COMMON_STOCK_TAGS = {
    "CommonStockSharesOutstanding",
    "EntityCommonStockSharesOutstanding",
}
EXACT_COMMON_STOCK_SEGMENT = "EquityComponents=CommonStock;"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit SEC dimensional common-stock share facts across quarterly "
            "Financial Statement Data Set ZIPs without changing canonical selection."
        )
    )
    parser.add_argument(
        "zip_dir",
        type=Path,
        help="Directory containing SEC quarterly ZIP files.",
    )
    parser.add_argument(
        "--pattern",
        default="*.zip",
        help="Glob pattern inside zip_dir. Default: *.zip",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/sec_common_stock_dimension_audit.csv"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("reports/sec_common_stock_dimension_summary.csv"),
    )
    parser.add_argument(
        "--conflict-summary-output",
        type=Path,
        default=Path("reports/sec_common_stock_conflict_summary.csv"),
    )
    return parser.parse_args()


def clean_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def main() -> None:
    args = parse_args()
    zip_paths = sorted(args.zip_dir.glob(args.pattern))
    if not zip_paths:
        raise SystemExit(
            f"No SEC ZIPs found in {args.zip_dir} matching {args.pattern!r}"
        )

    detail_parts: list[pd.DataFrame] = []

    for index, path in enumerate(zip_paths, start=1):
        print(f"[{index}/{len(zip_paths)}] {path.name}", flush=True)
        quarter = load_sec_financial_statement_zip(path)

        num = quarter.numeric_facts.copy()
        tag = clean_text(num["tag"])
        qtrs = pd.to_numeric(num["qtrs"], errors="coerce")
        uom = clean_text(num["uom"]).str.lower()
        segments = (
            clean_text(num["segments"])
            if "segments" in num.columns
            else pd.Series("", index=num.index)
        )
        coreg = (
            clean_text(num["coreg"])
            if "coreg" in num.columns
            else pd.Series("", index=num.index)
        )

        candidate = num.loc[
            tag.isin(COMMON_STOCK_TAGS)
            & qtrs.eq(0)
            & uom.eq("shares")
            & coreg.eq("")
        ].copy()

        if candidate.empty:
            continue

        candidate = candidate.merge(
            quarter.submissions[
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
            how="left",
            validate="many_to_one",
        )

        candidate["segments_clean"] = clean_text(candidate.get(
            "segments", pd.Series("", index=candidate.index)
        ))
        candidate["segment_class"] = "other_dimensional"
        candidate.loc[
            candidate["segments_clean"].eq(""),
            "segment_class",
        ] = "non_dimensional"
        candidate.loc[
            candidate["segments_clean"].eq(EXACT_COMMON_STOCK_SEGMENT),
            "segment_class",
        ] = "exact_common_stock"

        candidate["source_zip"] = path.name
        detail_parts.append(candidate)

    if not detail_parts:
        raise SystemExit("No common-stock share candidates found.")

    detail = pd.concat(detail_parts, ignore_index=True)
    detail["value"] = pd.to_numeric(detail["value"], errors="coerce")
    detail["ddate_date"] = pd.to_datetime(
        detail["ddate_date"], errors="coerce"
    ).dt.date

    keys = ["cik", "adsh", "tag", "ddate_date"]

    comparisons: list[dict] = []
    for key, group in detail.groupby(keys, dropna=False):
        blank = group.loc[group["segment_class"].eq("non_dimensional"), "value"].dropna()
        exact = group.loc[group["segment_class"].eq("exact_common_stock"), "value"].dropna()
        other = group.loc[group["segment_class"].eq("other_dimensional")]

        blank_values = sorted(set(float(v) for v in blank))
        exact_values = sorted(set(float(v) for v in exact))

        if exact_values and blank_values:
            if exact_values == blank_values:
                parity = "agree"
            else:
                parity = "conflict"
        elif exact_values:
            parity = "exact_only"
        elif blank_values:
            parity = "blank_only"
        else:
            parity = "no_numeric_value"

        company_names = sorted(
            set(clean_text(group.get("name", pd.Series("", index=group.index))))
            - {""}
        )
        forms = sorted(
            set(clean_text(group.get("form", pd.Series("", index=group.index))))
            - {""}
        )
        source_zips = sorted(
            set(clean_text(group.get("source_zip", pd.Series("", index=group.index))))
            - {""}
        )
        other_segments = sorted(
            set(clean_text(other.get("segments_clean", pd.Series("", index=other.index))))
            - {""}
        )

        singleton_pair = len(blank_values) == 1 and len(exact_values) == 1
        absolute_difference = None
        symmetric_relative_difference = None
        conflict_bucket = ""

        if singleton_pair:
            blank_value = blank_values[0]
            exact_value = exact_values[0]
            absolute_difference = abs(exact_value - blank_value)
            scale = max(abs(exact_value), abs(blank_value))
            symmetric_relative_difference = (
                0.0 if scale == 0 else absolute_difference / scale
            )

            if parity == "conflict":
                pct = symmetric_relative_difference
                if pct <= 0.0001:
                    conflict_bucket = "<=0.01%"
                elif pct <= 0.001:
                    conflict_bucket = "<=0.1%"
                elif pct <= 0.01:
                    conflict_bucket = "<=1%"
                elif pct <= 0.05:
                    conflict_bucket = "<=5%"
                elif pct <= 0.20:
                    conflict_bucket = "<=20%"
                else:
                    conflict_bucket = ">20%"
        elif parity == "conflict":
            conflict_bucket = "multi_value_ambiguous"

        comparisons.append(
            {
                "cik": key[0],
                "company_name": "|".join(company_names),
                "adsh": key[1],
                "form": "|".join(forms),
                "source_zip": "|".join(source_zips),
                "tag": key[2],
                "ddate_date": key[3],
                "exact_common_stock_values": "|".join(
                    f"{v:.12g}" for v in exact_values
                ),
                "non_dimensional_values": "|".join(
                    f"{v:.12g}" for v in blank_values
                ),
                "other_dimensional_count": int(len(other)),
                "other_dimensional_segments": "|".join(other_segments),
                "parity": parity,
                "singleton_pair": singleton_pair,
                "absolute_difference": absolute_difference,
                "symmetric_relative_difference": symmetric_relative_difference,
                "conflict_bucket": conflict_bucket,
            }
        )

    comparison = pd.DataFrame(comparisons)

    summary = (
        comparison.groupby("parity", dropna=False)
        .agg(
            fact_groups=("parity", "size"),
            unique_ciks=("cik", "nunique"),
        )
        .reset_index()
        .sort_values("parity")
    )

    conflicts = comparison.loc[comparison["parity"].eq("conflict")].copy()
    if conflicts.empty:
        conflict_summary = pd.DataFrame(
            columns=["conflict_bucket", "fact_groups", "unique_ciks"]
        )
    else:
        conflict_summary = (
            conflicts.groupby("conflict_bucket", dropna=False)
            .agg(
                fact_groups=("conflict_bucket", "size"),
                unique_ciks=("cik", "nunique"),
            )
            .reset_index()
            .sort_values("conflict_bucket")
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(args.output, index=False)
    summary.to_csv(args.summary_output, index=False)
    conflict_summary.to_csv(args.conflict_summary_output, index=False)

    print()
    print("SEC COMMON-STOCK DIMENSION AUDIT")
    print(f"ZIPs scanned:              {len(zip_paths):,}")
    print(f"Candidate fact rows:       {len(detail):,}")
    print(f"Compared fact groups:      {len(comparison):,}")
    print(f"Unique CIKs:               {comparison['cik'].nunique():,}")
    print()
    for row in summary.itertuples(index=False):
        print(
            f"{row.parity:22s} "
            f"groups={int(row.fact_groups):6,d} "
            f"ciks={int(row.unique_ciks):5,d}"
        )
    if not conflict_summary.empty:
        print()
        print("CONFLICT MAGNITUDE")
        for row in conflict_summary.itertuples(index=False):
            print(
                f"{str(row.conflict_bucket):22s} "
                f"groups={int(row.fact_groups):6,d} "
                f"ciks={int(row.unique_ciks):5,d}"
            )

    print()
    print(f"Detail:                    {args.output}")
    print(f"Summary:                   {args.summary_output}")
    print(f"Conflict summary:          {args.conflict_summary_output}")


if __name__ == "__main__":
    main()
