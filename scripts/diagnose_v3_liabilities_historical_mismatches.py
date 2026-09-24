from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose historical material differences between direct SEC "
            "Liabilities and LiabilitiesCurrent + LiabilitiesNoncurrent."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    base = (
        root
        / "reports"
        / "v3"
        / "data_sources"
        / args.as_of.isoformat()
        / "liabilities_historical_validation"
    )
    overlap_path = base / "filing_overlap_validation.csv"
    if not overlap_path.exists():
        raise SystemExit(f"Missing historical overlap detail: {overlap_path}")

    frame = pd.read_csv(overlap_path, low_memory=False)
    required = {
        "cik",
        "name",
        "form",
        "adsh",
        "ddate_date",
        "accepted_at",
        "Liabilities",
        "LiabilitiesCurrent",
        "LiabilitiesNoncurrent",
        "constructed_liabilities",
        "absolute_difference",
        "absolute_relative_error",
        "validation_band",
        "source_zip",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit(
            "Historical overlap detail missing required columns: "
            + ", ".join(missing)
        )

    frame["absolute_relative_error"] = pd.to_numeric(
        frame["absolute_relative_error"], errors="coerce"
    )
    frame["absolute_difference"] = pd.to_numeric(
        frame["absolute_difference"], errors="coerce"
    )
    frame["Liabilities"] = pd.to_numeric(
        frame["Liabilities"], errors="coerce"
    )
    frame["LiabilitiesCurrent"] = pd.to_numeric(
        frame["LiabilitiesCurrent"], errors="coerce"
    )
    frame["LiabilitiesNoncurrent"] = pd.to_numeric(
        frame["LiabilitiesNoncurrent"], errors="coerce"
    )
    frame["constructed_liabilities"] = pd.to_numeric(
        frame["constructed_liabilities"], errors="coerce"
    )
    frame["year"] = pd.to_datetime(
        frame["ddate_date"], errors="coerce"
    ).dt.year

    material = frame.loc[
        frame["validation_band"].astype(str).eq("material_difference")
    ].copy()
    material["signed_difference"] = (
        material["constructed_liabilities"] - material["Liabilities"]
    )
    material["construction_above_direct"] = material["signed_difference"].gt(0)
    material["relative_error_pct"] = (
        material["absolute_relative_error"] * 100
    )

    by_year = (
        frame.groupby("year", dropna=False)
        .agg(
            comparison_rows=("cik", "size"),
            material_rows=(
                "validation_band",
                lambda s: int(s.astype(str).eq("material_difference").sum()),
            ),
            comparison_ciks=("cik", "nunique"),
        )
        .reset_index()
    )
    by_year["material_rate"] = (
        by_year["material_rows"] / by_year["comparison_rows"]
    )

    by_form = (
        frame.groupby("form", dropna=False)
        .agg(
            comparison_rows=("cik", "size"),
            material_rows=(
                "validation_band",
                lambda s: int(s.astype(str).eq("material_difference").sum()),
            ),
            comparison_ciks=("cik", "nunique"),
        )
        .reset_index()
    )
    by_form["material_rate"] = (
        by_form["material_rows"] / by_form["comparison_rows"]
    )
    by_form = by_form.sort_values(
        ["material_rate", "material_rows", "form"],
        ascending=[False, False, True],
        kind="stable",
    )

    if material.empty:
        by_cik = pd.DataFrame()
    else:
        by_cik = (
            material.groupby(["cik", "name"], dropna=False)
            .agg(
                material_rows=("adsh", "size"),
                first_year=("year", "min"),
                last_year=("year", "max"),
                forms=("form", lambda s: "|".join(sorted(set(s.astype(str))))),
                median_relative_error=(
                    "absolute_relative_error",
                    "median",
                ),
                max_relative_error=("absolute_relative_error", "max"),
                construction_above_direct_rows=(
                    "construction_above_direct",
                    "sum",
                ),
            )
            .reset_index()
            .sort_values(
                ["material_rows", "max_relative_error", "cik"],
                ascending=[False, False, True],
                kind="stable",
            )
        )

    bins = [
        -float("inf"),
        0.01,
        0.02,
        0.05,
        0.10,
        0.25,
        0.50,
        1.00,
        float("inf"),
    ]
    labels = [
        "1-2%",
        "2-5%",
        "5-10%",
        "10-25%",
        "25-50%",
        "50-100%",
        ">100%",
        "invalid",
    ]
    # Material rows are already >1%; cut explicitly for readable severity bands.
    if not material.empty:
        material["severity_band"] = pd.cut(
            material["absolute_relative_error"],
            bins=[0.01, 0.02, 0.05, 0.10, 0.25, 0.50, 1.00, float("inf")],
            labels=[
                "1-2%",
                "2-5%",
                "5-10%",
                "10-25%",
                "25-50%",
                "50-100%",
                ">100%",
            ],
            right=True,
            include_lowest=False,
        )
        severity = (
            material.groupby("severity_band", observed=False, dropna=False)
            .agg(
                material_rows=("cik", "size"),
                material_ciks=("cik", "nunique"),
            )
            .reset_index()
        )
    else:
        severity = pd.DataFrame(
            columns=["severity_band", "material_rows", "material_ciks"]
        )

    repeat_material_ciks = (
        int((by_cik["material_rows"] > 1).sum())
        if not by_cik.empty
        else 0
    )
    summary = {
        "as_of": args.as_of.isoformat(),
        "comparison_rows": int(len(frame)),
        "material_rows": int(len(material)),
        "material_rate": (
            float(len(material) / len(frame)) if len(frame) else None
        ),
        "material_ciks": (
            int(material["cik"].nunique()) if not material.empty else 0
        ),
        "repeat_material_ciks": repeat_material_ciks,
        "construction_above_direct_rows": (
            int(material["construction_above_direct"].sum())
            if not material.empty
            else 0
        ),
        "construction_below_direct_rows": (
            int((~material["construction_above_direct"]).sum())
            if not material.empty
            else 0
        ),
        "median_material_relative_error": (
            float(material["absolute_relative_error"].median())
            if not material.empty
            else None
        ),
        "max_material_relative_error": (
            float(material["absolute_relative_error"].max())
            if not material.empty
            else None
        ),
        "research_only": True,
        "model_inputs_modified": False,
        "paid_vendor_used": False,
    }

    material_path = base / "material_difference_detail.csv"
    year_path = base / "material_difference_by_year.csv"
    form_path = base / "material_difference_by_form.csv"
    cik_path = base / "material_difference_by_cik.csv"
    severity_path = base / "material_difference_severity.csv"
    summary_path = base / "material_difference_summary.json"

    material.sort_values(
        ["absolute_relative_error", "year", "cik"],
        ascending=[False, True, True],
        kind="stable",
    ).to_csv(material_path, index=False)
    by_year.to_csv(year_path, index=False)
    by_form.to_csv(form_path, index=False)
    by_cik.to_csv(cik_path, index=False)
    severity.to_csv(severity_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V3 HISTORICAL LIABILITIES MATERIAL-DIFFERENCE DIAGNOSTIC")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Comparison rows:             {summary['comparison_rows']}")
    print(f"Material rows:               {summary['material_rows']}")
    print(f"Material CIKs:               {summary['material_ciks']}")
    print(f"Repeat material CIKs:        {summary['repeat_material_ciks']}")
    print(
        f"Material rate:               "
        f"{summary['material_rate']:.4%}"
        if summary["material_rate"] is not None
        else "Material rate:               n/a"
    )
    print(
        f"Median material error:       "
        f"{summary['median_material_relative_error']:.4%}"
        if summary["median_material_relative_error"] is not None
        else "Median material error:       n/a"
    )
    print(
        f"Max material error:          "
        f"{summary['max_material_relative_error']:.4%}"
        if summary["max_material_relative_error"] is not None
        else "Max material error:          n/a"
    )
    print(
        "Construction above/below:   "
        f"{summary['construction_above_direct_rows']}/"
        f"{summary['construction_below_direct_rows']}"
    )
    print(f"Detail:                      {material_path}")
    print(f"By year:                     {year_path}")
    print(f"By form:                     {form_path}")
    print(f"By CIK:                      {cik_path}")
    print(f"Severity:                    {severity_path}")
    print(f"Summary:                     {summary_path}")
    print("NO MODEL INPUTS WERE MODIFIED.")


if __name__ == "__main__":
    main()
