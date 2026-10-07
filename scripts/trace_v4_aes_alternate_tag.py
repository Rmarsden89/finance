from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.v2 import resolve_v2_liabilities_audit_paths


TARGET = "AES"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Trace AES alternate-liability classification back to V2 liabilities "
            "audit and cached CompanyFacts evidence."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    audit_paths = resolve_v2_liabilities_audit_paths(root, args.as_of)
    audit_path = audit_paths["detail"]
    if not audit_path.exists():
        raise SystemExit(f"Missing V2 liabilities audit detail: {audit_path}")

    audit = pd.read_csv(audit_path, low_memory=False)
    audit["ticker"] = audit["ticker"].astype(str).str.upper().str.strip()
    aes = audit.loc[audit["ticker"].eq(TARGET)].copy()
    if aes.empty:
        raise SystemExit("AES not found in V2 liabilities audit")

    row = aes.iloc[0]
    companyfacts_path = Path(str(row.get("companyfacts_path", "")).strip())
    if not companyfacts_path.is_absolute():
        companyfacts_path = root / companyfacts_path

    tags: list[dict[str, object]] = []
    if companyfacts_path.exists():
        payload = json.loads(companyfacts_path.read_text(encoding="utf-8"))
        facts = (payload.get("facts") or {}).get("us-gaap") or {}
        accessions = {
            value.strip()
            for value in str(row.get("discovery_accessions", "")).split("|")
            if value.strip()
        }
        for tag, fact in facts.items():
            if "Liabilit" not in str(tag):
                continue
            observations = []
            for unit, obs_list in (fact.get("units") or {}).items():
                for obs in obs_list or []:
                    accn = str(obs.get("accn") or "")
                    if accessions and accn not in accessions:
                        continue
                    observations.append(
                        {
                            "tag": tag,
                            "label": fact.get("label", ""),
                            "description": fact.get("description", ""),
                            "unit": unit,
                            "accn": accn,
                            "fy": obs.get("fy"),
                            "fp": obs.get("fp"),
                            "form": obs.get("form"),
                            "filed": obs.get("filed"),
                            "start": obs.get("start"),
                            "end": obs.get("end"),
                            "val": obs.get("val"),
                            "frame": obs.get("frame"),
                        }
                    )
            if observations:
                tags.extend(observations)

    detail = pd.DataFrame(tags)
    if not detail.empty:
        detail["end"] = pd.to_datetime(detail["end"], errors="coerce")
        detail["filed"] = pd.to_datetime(detail["filed"], errors="coerce")
        detail["val"] = pd.to_numeric(detail["val"], errors="coerce")
        detail = detail.sort_values(
            ["end", "tag", "filed"],
            ascending=[False, True, False],
            kind="stable",
        ).reset_index(drop=True)

    summary = {
        "schema_version": 1,
        "status": "V4_AES_ALTERNATE_TAG_TRACE_COMPLETE",
        "as_of": as_of,
        "ticker": TARGET,
        "cik": (
            int(float(row.get("cik")))
            if pd.notna(row.get("cik"))
            else None
        ),
        "v2_classification": str(row.get("classification", "")),
        "v2_recommended_action": str(row.get("recommended_action", "")),
        "discovery_statuses": str(row.get("discovery_statuses", "")),
        "discovery_accessions": str(row.get("discovery_accessions", "")),
        "audit_liability_tags": str(row.get("current_filing_liability_tags", "")),
        "audit_total_like_tags": str(row.get("current_filing_total_like_tags", "")),
        "companyfacts_path": str(companyfacts_path),
        "matching_companyfacts_rows": int(len(detail)),
        "matching_companyfacts_tags": (
            sorted(detail["tag"].astype(str).unique().tolist())
            if not detail.empty
            else []
        ),
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
        "v3_modified": False,
    }

    output_dir = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "aes_alternate_tag_trace"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "companyfacts_liability_evidence.csv"
    summary_path = output_dir / "summary.json"
    detail.to_csv(detail_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 AES ALTERNATE-TAG TRACE")
    print(f"As of:                       {as_of}")
    print(f"V2 classification:           {summary['v2_classification']}")
    print(f"V2 recommended action:       {summary['v2_recommended_action']}")
    print(f"Discovery statuses:          {summary['discovery_statuses'] or '-'}")
    print(f"Discovery accessions:        {summary['discovery_accessions'] or '-'}")
    print(f"Audit liability tags:        {summary['audit_liability_tags'] or '-'}")
    print(f"Audit total-like tags:       {summary['audit_total_like_tags'] or '-'}")
    print(f"Matching CompanyFacts rows:  {summary['matching_companyfacts_rows']}")
    print(
        "Matching CompanyFacts tags:  "
        + (
            "|".join(summary["matching_companyfacts_tags"])
            if summary["matching_companyfacts_tags"]
            else "-"
        )
    )
    print(f"Detail:                      {detail_path}")
    print(f"Summary:                     {summary_path}")
    print("V1/V2/V3 INPUTS AND RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
