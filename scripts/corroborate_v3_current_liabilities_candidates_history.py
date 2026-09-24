from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import time

import pandas as pd

from finance.data.sources.sec_financial_statements import (
    load_sec_financial_statement_zip,
)


CONSTRUCTION_TAGS = {
    "LiabilitiesCurrent",
    "LiabilitiesNoncurrent",
}
TOTAL_TAGS = {
    "Assets",
    "LiabilitiesAndStockholdersEquity",
}
EQUITY_TAGS = {
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
}
TAGS = CONSTRUCTION_TAGS | TOTAL_TAGS | EQUITY_TAGS
SUPPORTED_FORMS = {
    "10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Corroborate the 32 current SEC-native liabilities recovery candidates "
            "historically using same-filing balance-sheet identities. Identity "
            "facts are validation evidence only and never recovery inputs."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("zip_dir", type=Path)
    parser.add_argument("--pattern", default="*.zip")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def clean(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def load_current_candidates(base: Path) -> pd.DataFrame:
    paths = [
        base / "liabilities_identity_raw_strict" / "raw_sec_detail.csv",
        base / "liabilities_no_supported_current" / "raw_sec_detail.csv",
        base / "liabilities_alternate_tags" / "alternate_tag_detail.csv",
    ]
    frames = []
    for path in paths:
        if not path.exists():
            raise SystemExit(f"Missing current recovery detail: {path}")
        frame = pd.read_csv(path, low_memory=False)
        frames.append(frame)
    current = pd.concat(frames, ignore_index=True)
    current["ticker"] = current["ticker"].astype(str).str.upper().str.strip()
    current["cik"] = pd.to_numeric(current["cik"], errors="coerce").astype("Int64")
    current = current.loc[
        current["status"].astype(str).eq("current_plus_noncurrent_candidate")
        & current["cik"].notna()
    ].copy()
    return current.drop_duplicates(["ticker", "cik"], keep="first")


def unique_positive_value(group: pd.DataFrame, tag: str) -> float | None:
    values = pd.to_numeric(
        group.loc[group["tag"].eq(tag), "value_num"], errors="coerce"
    ).dropna()
    values = sorted({float(v) for v in values if float(v) > 0})
    return values[0] if len(values) == 1 else None


def classify_error(candidate: float, corroborated: float) -> tuple[float, str]:
    error = abs(corroborated - candidate) / candidate
    if corroborated == candidate:
        return error, "exact_match"
    if error <= 0.0001:
        return error, "within_0_01_pct"
    if error <= 0.001:
        return error, "within_0_1_pct"
    if error <= 0.01:
        return error, "within_1_pct"
    return error, "material_difference"


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    base = root / "reports" / "v3" / "data_sources" / args.as_of.isoformat()
    current = load_current_candidates(base)
    target_ciks = set(current["cik"].astype(int))
    ticker_by_cik = {
        int(row.cik): row.ticker for row in current.itertuples(index=False)
    }

    paths = sorted(args.zip_dir.resolve().glob(args.pattern))
    if not paths:
        raise SystemExit(
            f"No SEC ZIPs found in {args.zip_dir} matching {args.pattern!r}"
        )

    rows: list[dict[str, object]] = []
    started = time.monotonic()
    print("V3 CURRENT-CANDIDATE HISTORICAL IDENTITY CORROBORATION", flush=True)
    print(f"As of:                       {args.as_of.isoformat()}", flush=True)
    print(f"Current candidates:          {len(current)}", flush=True)
    print(f"SEC ZIPs:                    {len(paths)}", flush=True)

    for index, path in enumerate(paths, start=1):
        print(
            f"[{index}/{len(paths)}] {path.name} "
            f"elapsed={(time.monotonic() - started)/60:.1f}m",
            flush=True,
        )
        quarter = load_sec_financial_statement_zip(path)
        sub = quarter.submissions.copy()
        num = quarter.numeric_facts.copy()
        cik_map = sub.set_index("adsh")["cik"].to_dict()
        num["cik"] = pd.to_numeric(
            num["adsh"].map(cik_map), errors="coerce"
        )
        num["tag"] = clean(num["tag"])
        facts = num.loc[
            num["cik"].isin(target_ciks)
            & num["tag"].isin(TAGS)
        ].copy()
        if facts.empty:
            continue

        facts["value_num"] = pd.to_numeric(facts["value"], errors="coerce")
        facts["qtrs_num"] = pd.to_numeric(facts["qtrs"], errors="coerce")
        facts["uom_clean"] = clean(facts["uom"]).str.upper()
        facts["segments_clean"] = clean(
            facts.get("segments", pd.Series("", index=facts.index))
        )
        facts["coreg_clean"] = clean(
            facts.get("coreg", pd.Series("", index=facts.index))
        )
        facts["ddate_date"] = pd.to_datetime(
            facts["ddate_date"], errors="coerce"
        ).dt.date

        sub_keep = sub[
            ["adsh", "cik", "form", "period_date", "filed_date", "accepted_at"]
        ].copy()
        facts = facts.merge(
            sub_keep,
            on="adsh",
            how="inner",
            suffixes=("", "_submission"),
            validate="many_to_one",
        )
        facts["form"] = clean(facts["form"])
        facts["period_date"] = pd.to_datetime(
            facts["period_date"], errors="coerce"
        ).dt.date
        facts = facts.loc[
            facts["form"].isin(SUPPORTED_FORMS)
            & facts["uom_clean"].eq("USD")
            & facts["qtrs_num"].eq(0)
            & facts["segments_clean"].eq("")
            & facts["coreg_clean"].eq("")
            & facts["value_num"].notna()
            & facts["value_num"].gt(0)
            & facts["ddate_date"].notna()
            & facts["period_date"].notna()
            & facts["ddate_date"].eq(facts["period_date"])
        ].copy()
        if facts.empty:
            continue

        keys = [
            "adsh", "cik", "form", "period_date", "filed_date",
            "accepted_at", "ddate_date"
        ]
        for key, group in facts.groupby(keys, dropna=False):
            values = dict(zip(keys, key))
            cik = int(values["cik"])
            current_value = unique_positive_value(group, "LiabilitiesCurrent")
            noncurrent_value = unique_positive_value(
                group, "LiabilitiesNoncurrent"
            )
            if current_value is None or noncurrent_value is None:
                continue
            candidate = current_value + noncurrent_value
            assets = unique_positive_value(group, "Assets")
            total_like = unique_positive_value(
                group, "LiabilitiesAndStockholdersEquity"
            )
            for equity_tag in sorted(EQUITY_TAGS):
                equity = unique_positive_value(group, equity_tag)
                if equity is None:
                    continue
                identities: list[tuple[str, float]] = []
                if assets is not None and assets > equity:
                    identities.append(("assets_minus_equity", assets - equity))
                if total_like is not None and total_like > equity:
                    identities.append(
                        ("total_like_minus_equity", total_like - equity)
                    )
                for identity_type, corroborated in identities:
                    error, band = classify_error(candidate, corroborated)
                    rows.append(
                        {
                            "ticker": ticker_by_cik.get(cik, ""),
                            "cik": cik,
                            **values,
                            "source_zip": path.name,
                            "equity_tag": equity_tag,
                            "identity_type": identity_type,
                            "constructed_liabilities": candidate,
                            "corroborated_liabilities": corroborated,
                            "absolute_relative_error": error,
                            "validation_band": band,
                        }
                    )

    detail = pd.DataFrame(rows)
    output_dir = base / "liabilities_historical_validation"
    detail_path = output_dir / "current_candidate_identity_corroboration.csv"
    issuer_path = output_dir / "current_candidate_identity_by_ticker.csv"
    summary_path = output_dir / "current_candidate_identity_summary.csv"

    if detail.empty:
        detail.to_csv(detail_path, index=False)
        pd.DataFrame().to_csv(issuer_path, index=False)
        summary = pd.DataFrame([{
            "current_candidates": len(current),
            "candidates_with_corroboration": 0,
            "corroboration_rows": 0,
            "exact_or_within_0_1_pct_rows": 0,
            "material_rows": 0,
        }])
        summary.to_csv(summary_path, index=False)
    else:
        detail.to_csv(detail_path, index=False)
        per_ticker = (
            detail.groupby(["ticker", "cik"], as_index=False)
            .agg(
                corroboration_rows=("validation_band", "size"),
                exact_matches=(
                    "validation_band",
                    lambda s: int(s.eq("exact_match").sum()),
                ),
                within_0_01_pct=(
                    "validation_band",
                    lambda s: int(s.eq("within_0_01_pct").sum()),
                ),
                within_0_1_pct=(
                    "validation_band",
                    lambda s: int(s.eq("within_0_1_pct").sum()),
                ),
                within_1_pct=(
                    "validation_band",
                    lambda s: int(s.eq("within_1_pct").sum()),
                ),
                material_rows=(
                    "validation_band",
                    lambda s: int(s.eq("material_difference").sum()),
                ),
                max_absolute_relative_error=(
                    "absolute_relative_error", "max"
                ),
            )
        )
        all_candidates = current[["ticker", "cik"]].merge(
            per_ticker,
            on=["ticker", "cik"],
            how="left",
        )
        all_candidates.to_csv(issuer_path, index=False)
        safe_rows = detail["validation_band"].isin(
            {"exact_match", "within_0_01_pct", "within_0_1_pct"}
        )
        summary = pd.DataFrame([{
            "current_candidates": len(current),
            "candidates_with_corroboration": int(
                detail["ticker"].nunique()
            ),
            "corroboration_rows": len(detail),
            "exact_or_within_0_1_pct_rows": int(safe_rows.sum()),
            "material_rows": int(
                detail["validation_band"].eq(
                    "material_difference"
                ).sum()
            ),
            "candidates_with_any_material_corroboration": int(
                per_ticker["material_rows"].gt(0).sum()
            ),
        }])
        summary.to_csv(summary_path, index=False)

    row = summary.iloc[0]
    print()
    print("V3 CURRENT-CANDIDATE IDENTITY CORROBORATION COMPLETE")
    print(f"Current candidates:          {int(row['current_candidates'])}")
    print(
        f"With corroboration:          "
        f"{int(row['candidates_with_corroboration'])}"
    )
    print(f"Corroboration rows:          {int(row['corroboration_rows'])}")
    print(
        f"Exact/within 0.1% rows:      "
        f"{int(row['exact_or_within_0_1_pct_rows'])}"
    )
    print(f"Material rows:               {int(row['material_rows'])}")
    if "candidates_with_any_material_corroboration" in row:
        print(
            f"Candidates with material:    "
            f"{int(row['candidates_with_any_material_corroboration'])}"
        )
    print(f"Detail:                      {detail_path}")
    print(f"By ticker:                   {issuer_path}")
    print(f"Summary:                     {summary_path}")
    print("IDENTITY FACTS WERE USED FOR VALIDATION ONLY, NOT RECOVERY.")
    print("NO MODEL INPUTS WERE MODIFIED.")


if __name__ == "__main__":
    main()
