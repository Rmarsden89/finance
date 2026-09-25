from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.raw_share_validation import build_raw_share_candidate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit every current shares_outstanding residual using the frozen "
            "research-only raw SEC DEI candidate rule."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--raw-sec-subdir",
        default="raw_sec_share_full_residual",
    )
    return parser.parse_args()


def _quantiles(series: pd.Series) -> dict[str, float | None]:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return {
            "min": None,
            "p25": None,
            "median": None,
            "p75": None,
            "max": None,
        }
    return {
        "min": float(numeric.min()),
        "p25": float(numeric.quantile(0.25)),
        "median": float(numeric.median()),
        "p75": float(numeric.quantile(0.75)),
        "max": float(numeric.max()),
    }


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    base = root / "reports" / "v3" / "data_sources" / args.as_of.isoformat()

    cohort_path = base / "raw_share_full_residual" / "validation_cohort.csv"
    facts_path = base / args.raw_sec_subdir / "inline_xbrl_candidate_facts.csv"
    manifest_path = base / args.raw_sec_subdir / "filing_manifest.csv"

    required = {
        "residual cohort": cohort_path,
        "raw SEC candidate facts": facts_path,
        "filing manifest": manifest_path,
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.exists()]
    if missing:
        raise SystemExit(
            "Missing full residual raw-share input(s):\n  "
            + "\n  ".join(missing)
        )

    cohort = pd.read_csv(cohort_path, low_memory=False)
    facts = pd.read_csv(facts_path, low_memory=False)
    manifest = pd.read_csv(manifest_path, low_memory=False)

    cohort["ticker"] = cohort["ticker"].astype(str).str.upper().str.strip()
    facts["ticker"] = facts["ticker"].astype(str).str.upper().str.strip()
    manifest["ticker"] = manifest["ticker"].astype(str).str.upper().str.strip()

    manifest_fields = [
        column
        for column in (
            "ticker",
            "accession",
            "selection_method",
            "accepted_at",
            "primary_document",
            "candidate_fact_count",
            "context_count",
            "document_sha256",
            "header_sha256",
        )
        if column in manifest.columns
    ]
    manifest_lookup = (
        manifest[manifest_fields]
        .drop_duplicates("ticker", keep="first")
        .set_index("ticker")
        .to_dict(orient="index")
    )

    rows: list[dict[str, object]] = []
    for row in cohort.itertuples(index=False):
        ticker = str(row.ticker).upper()
        candidate = build_raw_share_candidate(
            facts.loc[facts["ticker"].eq(ticker)].copy(),
            ticker=ticker,
            as_of=args.as_of,
        )
        output = {
            "ticker": ticker,
            "cik": getattr(row, "cik", ""),
            "company_name": getattr(row, "company_name", ""),
            "original_classification": getattr(row, "classification", ""),
            "original_recommended_action": getattr(
                row, "recommended_action", ""
            ),
            "status": candidate.status,
            "candidate_value": candidate.value,
            "candidate_accession": candidate.accession,
            "candidate_accepted_at": candidate.accepted_at,
            "context_instant": candidate.context_instant,
            "selection_rule": candidate.selection_rule,
            "component_count": candidate.component_count,
            "component_dimensions": candidate.component_dimensions,
            "component_values": candidate.component_values,
            "reason": candidate.reason,
        }
        for key, value in manifest_lookup.get(ticker, {}).items():
            if key == "ticker":
                continue
            output[f"manifest_{key}"] = value
        rows.append(output)

    detail = pd.DataFrame(rows)

    context_date = pd.to_datetime(detail["context_instant"], errors="coerce")
    detail["context_age_days"] = (
        pd.Timestamp(args.as_of) - context_date
    ).dt.days

    accepted_at = pd.to_datetime(
        detail["candidate_accepted_at"], errors="coerce", utc=True
    )
    cutoff = pd.Timestamp(args.as_of).tz_localize("UTC")
    detail["acceptance_age_days"] = (
        cutoff - accepted_at
    ).dt.total_seconds() / 86400.0

    detail = detail.sort_values(
        ["status", "selection_rule", "ticker"],
        kind="stable",
    ).reset_index(drop=True)

    status_summary = (
        detail.groupby("status", dropna=False, as_index=False)
        .agg(rows=("ticker", "size"), tickers=("ticker", "nunique"))
        .sort_values(["rows", "status"], ascending=[False, True], kind="stable")
    )
    rule_summary = (
        detail.groupby(["status", "selection_rule"], dropna=False, as_index=False)
        .agg(rows=("ticker", "size"), tickers=("ticker", "nunique"))
        .sort_values(["status", "selection_rule"], kind="stable")
    )
    filing_selection_summary = (
        detail.groupby("manifest_selection_method", dropna=False, as_index=False)
        .agg(rows=("ticker", "size"), tickers=("ticker", "nunique"))
        .sort_values(["rows", "manifest_selection_method"], ascending=[False, True], kind="stable")
        if "manifest_selection_method" in detail.columns
        else pd.DataFrame(columns=["manifest_selection_method", "rows", "tickers"])
    )

    candidates = detail.loc[detail["status"].eq("candidate")].copy()
    recovery_rate = (
        float(len(candidates) / len(detail))
        if len(detail)
        else None
    )

    summary = {
        "as_of": args.as_of.isoformat(),
        "residual_rows": int(len(detail)),
        "candidate_rows": int(len(candidates)),
        "recovery_rate": recovery_rate,
        "undimensioned_candidates": int(
            candidates["selection_rule"].eq("undimensioned_preferred").sum()
        ),
        "share_class_sum_candidates": int(
            candidates["selection_rule"].eq("share_class_sum").sum()
        ),
        "context_age_days": _quantiles(candidates["context_age_days"]),
        "acceptance_age_days": _quantiles(candidates["acceptance_age_days"]),
        "research_only": True,
        "model_inputs_modified": False,
        "paid_vendor_used": False,
    }

    output_dir = base / "raw_share_full_residual"
    detail_path = output_dir / "candidate_detail.csv"
    status_path = output_dir / "status_summary.csv"
    rules_path = output_dir / "selection_rule_summary.csv"
    filing_path = output_dir / "filing_selection_summary.csv"
    summary_path = output_dir / "summary.json"

    detail.to_csv(detail_path, index=False)
    status_summary.to_csv(status_path, index=False)
    rule_summary.to_csv(rules_path, index=False)
    filing_selection_summary.to_csv(filing_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V3 FULL CURRENT RAW SEC SHARE AUDIT")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Residual rows:               {summary['residual_rows']}")
    print(f"Recovered candidates:        {summary['candidate_rows']}")
    print(
        f"Recovery rate:               "
        f"{summary['recovery_rate']:.2%}"
        if summary["recovery_rate"] is not None
        else "Recovery rate:               n/a"
    )
    print(
        f"Undimensioned candidates:    "
        f"{summary['undimensioned_candidates']}"
    )
    print(
        f"Share-class-sum candidates:  "
        f"{summary['share_class_sum_candidates']}"
    )
    print(f"Detail:                      {detail_path}")
    print(f"Status summary:              {status_path}")
    print(f"Selection rules:             {rules_path}")
    print(f"Filing selection:            {filing_path}")
    print(f"Summary:                     {summary_path}")
    print("NO PAID VENDOR WAS USED.")
    print("NO RAW SEC CANDIDATES WERE PROMOTED INTO V1 OR V2.")


if __name__ == "__main__":
    main()
