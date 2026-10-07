from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import time

import pandas as pd

from finance.data.sources.sec_financial_statements import (
    load_sec_financial_statement_zip,
)


TARGET_TICKER = "AES"
TARGET_CIK = 874761
SUPPORTED_FORMS = {
    "10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"
}
ASSET_TAG = "Assets"
DIRECT_TAG = "Liabilities"
CURRENT_TAG = "LiabilitiesCurrent"
NONCURRENT_TAG = "LiabilitiesNoncurrent"
EQUITY_TAGS = {
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
}
TAGS = {
    ASSET_TAG,
    DIRECT_TAG,
    CURRENT_TAG,
    NONCURRENT_TAG,
    *EQUITY_TAGS,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Targeted AES historical validation of same-filing Assets - Equity "
            "as a V4 liabilities research candidate. Identity values are never "
            "written into model inputs by this command."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("zip_dir", type=Path)
    parser.add_argument("--pattern", default="*.zip")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _clean(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def _unique_positive(group: pd.DataFrame, tag: str) -> float | None:
    values = pd.to_numeric(
        group.loc[group["tag"].eq(tag), "value_num"],
        errors="coerce",
    ).dropna()
    values = sorted({float(value) for value in values if float(value) > 0})
    return values[0] if len(values) == 1 else None


def _classify(candidate: float, control: float) -> tuple[float, str]:
    error = abs(candidate - control) / control
    if candidate == control:
        return error, "exact_match"
    if error <= 0.0001:
        return error, "within_0_01_pct"
    if error <= 0.001:
        return error, "within_0_1_pct"
    if error <= 0.01:
        return error, "within_1_pct"
    return error, "material_difference"


def _pick_equity(group: pd.DataFrame) -> tuple[str | None, float | None]:
    nci = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
    plain = "StockholdersEquity"
    nci_value = _unique_positive(group, nci)
    if nci_value is not None:
        return nci, nci_value
    plain_value = _unique_positive(group, plain)
    if plain_value is not None:
        return plain, plain_value
    return None, None


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    zip_dir = args.zip_dir.resolve()
    paths = sorted(zip_dir.glob(args.pattern))
    if not paths:
        raise SystemExit(
            f"No SEC ZIPs found in {zip_dir} matching {args.pattern!r}"
        )

    rows: list[dict[str, object]] = []
    started = time.monotonic()

    print("V4 AES HISTORICAL IDENTITY VALIDATION", flush=True)
    print(f"As of:                       {args.as_of.isoformat()}", flush=True)
    print(f"Target:                      {TARGET_TICKER}/{TARGET_CIK}", flush=True)
    print(f"SEC ZIP directory:           {zip_dir}", flush=True)
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

        sub["cik"] = pd.to_numeric(sub["cik"], errors="coerce")
        target_sub = sub.loc[sub["cik"].eq(TARGET_CIK)].copy()
        if target_sub.empty:
            continue

        target_adsh = set(target_sub["adsh"].astype(str))
        num["adsh"] = num["adsh"].astype(str)
        num["tag"] = _clean(num["tag"])
        facts = num.loc[
            num["adsh"].isin(target_adsh)
            & num["tag"].isin(TAGS)
        ].copy()
        if facts.empty:
            continue

        facts["value_num"] = pd.to_numeric(facts["value"], errors="coerce")
        facts["qtrs_num"] = pd.to_numeric(facts["qtrs"], errors="coerce")
        facts["uom_clean"] = _clean(facts["uom"]).str.upper()
        facts["segments_clean"] = _clean(
            facts.get("segments", pd.Series("", index=facts.index))
        )
        facts["coreg_clean"] = _clean(
            facts.get("coreg", pd.Series("", index=facts.index))
        )
        facts["ddate_date"] = pd.to_datetime(
            facts["ddate_date"], errors="coerce"
        ).dt.date

        keep_sub = target_sub[
            ["adsh", "cik", "name", "form", "period_date", "filed_date", "accepted_at"]
        ].copy()
        facts = facts.merge(
            keep_sub,
            on="adsh",
            how="inner",
            validate="many_to_one",
        )
        facts["form"] = _clean(facts["form"])
        facts["period_date"] = pd.to_datetime(
            facts["period_date"], errors="coerce"
        ).dt.date
        facts["filed_date"] = pd.to_datetime(
            facts["filed_date"], errors="coerce"
        ).dt.date
        facts["accepted_at"] = pd.to_datetime(
            facts["accepted_at"], errors="coerce"
        )

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
            "adsh", "cik", "name", "form", "period_date",
            "filed_date", "accepted_at", "ddate_date"
        ]
        for key, group in facts.groupby(keys, dropna=False):
            values = dict(zip(keys, key))
            assets = _unique_positive(group, ASSET_TAG)
            equity_tag, equity = _pick_equity(group)
            if assets is None or equity is None or assets <= equity:
                continue

            derived = assets - equity
            direct = _unique_positive(group, DIRECT_TAG)
            current = _unique_positive(group, CURRENT_TAG)
            noncurrent = _unique_positive(group, NONCURRENT_TAG)
            constructed = (
                current + noncurrent
                if current is not None and noncurrent is not None
                else None
            )

            control_type = ""
            control_value = None
            if direct is not None:
                control_type = "direct_liabilities"
                control_value = direct
            elif constructed is not None:
                control_type = "current_plus_noncurrent"
                control_value = constructed

            error = None
            band = "no_independent_control"
            if control_value is not None:
                error, band = _classify(derived, control_value)

            rows.append(
                {
                    "ticker": TARGET_TICKER,
                    "cik": TARGET_CIK,
                    **values,
                    "source_zip": path.name,
                    "assets": assets,
                    "equity_tag": equity_tag,
                    "equity": equity,
                    "derived_liabilities": derived,
                    "direct_liabilities": direct,
                    "current_liabilities": current,
                    "noncurrent_liabilities": noncurrent,
                    "current_plus_noncurrent": constructed,
                    "control_type": control_type,
                    "control_value": control_value,
                    "absolute_relative_error": error,
                    "validation_band": band,
                }
            )

    detail = pd.DataFrame(rows)
    if detail.empty:
        raise SystemExit("No strict historical AES identity rows found")

    detail = detail.sort_values(
        ["ddate_date", "accepted_at", "adsh"],
        kind="stable",
    ).drop_duplicates(
        ["adsh", "ddate_date"],
        keep="last",
    ).reset_index(drop=True)

    bands = detail["validation_band"].astype(str)
    controlled = ~bands.eq("no_independent_control")
    material = bands.eq("material_difference")
    clean = bands.isin(
        {"exact_match", "within_0_01_pct", "within_0_1_pct", "within_1_pct"}
    )

    detail["year"] = pd.to_datetime(
        detail["ddate_date"], errors="coerce"
    ).dt.year
    by_year = (
        detail.groupby("year", as_index=False)
        .agg(
            filing_rows=("ticker", "size"),
            controlled_rows=(
                "validation_band",
                lambda s: int(~pd.Series(s).astype(str).eq("no_independent_control").sum()),
            ),
            clean_rows=(
                "validation_band",
                lambda s: int(pd.Series(s).astype(str).isin(
                    {"exact_match", "within_0_01_pct", "within_0_1_pct", "within_1_pct"}
                ).sum()),
            ),
            material_rows=(
                "validation_band",
                lambda s: int(pd.Series(s).astype(str).eq("material_difference").sum()),
            ),
        )
        .sort_values("year", kind="stable")
    )

    material_periods = detail.loc[
        material, "ddate_date"
    ].astype(str).tolist()

    payload = {
        "schema_version": 1,
        "status": "V4_AES_HISTORICAL_IDENTITY_VALIDATION_COMPLETE",
        "as_of": args.as_of.isoformat(),
        "ticker": TARGET_TICKER,
        "cik": TARGET_CIK,
        "filing_rows": int(len(detail)),
        "controlled_rows": int(controlled.sum()),
        "direct_control_rows": int(
            detail["control_type"].astype(str).eq("direct_liabilities").sum()
        ),
        "current_plus_noncurrent_control_rows": int(
            detail["control_type"].astype(str).eq("current_plus_noncurrent").sum()
        ),
        "clean_controlled_rows": int(clean.sum()),
        "material_rows": int(material.sum()),
        "uncontrolled_rows": int((~controlled).sum()),
        "material_periods": material_periods,
        "max_relative_error": (
            float(detail.loc[material, "absolute_relative_error"].max())
            if material.any()
            else None
        ),
        "research_only": True,
        "assets_minus_equity_used_for_recovery": False,
        "v1_modified": False,
        "v2_modified": False,
        "v3_modified": False,
    }

    output_dir = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / args.as_of.isoformat()
        / "aes_targeted_historical_identity"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "historical_identity_detail.csv"
    year_path = output_dir / "historical_identity_by_year.csv"
    summary_path = output_dir / "summary.json"

    detail.to_csv(detail_path, index=False)
    by_year.to_csv(year_path, index=False)
    summary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V4 AES HISTORICAL IDENTITY VALIDATION COMPLETE")
    print(f"Filing rows:                 {payload['filing_rows']}")
    print(f"Controlled rows:             {payload['controlled_rows']}")
    print(f"Direct control rows:         {payload['direct_control_rows']}")
    print(
        f"Current+noncurrent controls: "
        f"{payload['current_plus_noncurrent_control_rows']}"
    )
    print(f"Clean controlled rows:       {payload['clean_controlled_rows']}")
    print(f"Material rows:               {payload['material_rows']}")
    print(f"Uncontrolled rows:           {payload['uncontrolled_rows']}")
    print(
        "Material periods:            "
        + ("|".join(material_periods) if material_periods else "-")
    )
    print(f"Max relative error:          {payload['max_relative_error']}")
    print(f"Detail:                      {detail_path}")
    print(f"By year:                     {year_path}")
    print(f"Summary:                     {summary_path}")
    print("ASSETS - EQUITY WAS VALIDATION-ONLY; NO RECOVERY WAS APPLIED.")
    print("V1/V2/V3 INPUTS AND RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
