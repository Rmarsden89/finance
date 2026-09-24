from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.v2 import resolve_v2_sec_artifact_paths


TAGS = {
    "Liabilities",
    "LiabilitiesCurrent",
    "LiabilitiesNoncurrent",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Screen the existing SEC CompanyFacts cache for current controls "
            "whose same filing/period reports Liabilities, LiabilitiesCurrent, "
            "and LiabilitiesNoncurrent. Used only to build a higher-yield "
            "raw-filing validation cohort."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _eligible_discovery(discovery: pd.DataFrame, as_of: date) -> pd.DataFrame:
    frame = discovery.copy()
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    accepted = pd.to_datetime(frame["accepted_at"], errors="coerce", utc=True)
    cutoff = pd.Timestamp(as_of).tz_localize("UTC")
    frame = frame.loc[accepted.notna() & accepted.lt(cutoff)].copy()
    return frame


def _tag_observations(payload: dict, tag: str) -> list[dict]:
    fact = (((payload.get("facts") or {}).get("us-gaap") or {}).get(tag) or {})
    rows: list[dict] = []
    for unit, observations in (fact.get("units") or {}).items():
        if str(unit).upper() != "USD":
            continue
        for obs in observations or []:
            if obs.get("start"):
                continue
            value = pd.to_numeric(pd.Series([obs.get("val")]), errors="coerce").iloc[0]
            if pd.isna(value) or float(value) <= 0:
                continue
            rows.append(
                {
                    "accn": str(obs.get("accn") or ""),
                    "end": str(obs.get("end") or "")[:10],
                    "value": float(value),
                }
            )
    return rows


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    manifest_path = v2["manifest"]
    if not manifest_path.exists():
        raise SystemExit(f"Missing V2 manifest: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    input_artifacts = manifest.get("input_artifacts", {})
    discovery_path = Path(str(input_artifacts.get("discovery") or ""))
    cache_dir = Path(str(input_artifacts.get("SEC cache") or ""))
    snapshot_path = v2["current_snapshot"]

    required = {
        "discovery": discovery_path,
        "SEC cache": cache_dir,
        "current snapshot": snapshot_path,
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.exists()]
    if missing:
        raise SystemExit(
            "Missing current/noncurrent screen input(s):\n  "
            + "\n  ".join(missing)
        )

    discovery = _eligible_discovery(
        pd.read_csv(discovery_path, low_memory=False),
        args.as_of,
    )
    snapshot = pd.read_csv(snapshot_path, low_memory=False)
    snapshot["ticker"] = snapshot["ticker"].astype(str).str.upper().str.strip()
    liabilities = pd.to_numeric(snapshot["total_liabilities"], errors="coerce")
    controls = snapshot.loc[liabilities.gt(0)].copy()

    discovered = {
        ticker: set(group["accession"].astype(str).str.strip())
        for ticker, group in discovery.groupby("ticker")
    }

    rows: list[dict[str, object]] = []
    for row in controls.itertuples(index=False):
        ticker = str(row.ticker).upper()
        cik = int(float(row.cik))
        path = cache_dir / "companyfacts" / f"CIK{cik:010d}.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        tag_rows = {tag: _tag_observations(payload, tag) for tag in TAGS}
        accessions = discovered.get(ticker, set())
        by_key: dict[tuple[str, str], set[str]] = {}
        for tag, observations in tag_rows.items():
            for obs in observations:
                key = (obs["accn"], obs["end"])
                if obs["accn"] not in accessions:
                    continue
                by_key.setdefault(key, set()).add(tag)

        eligible_keys = [
            key for key, present in by_key.items() if TAGS.issubset(present)
        ]
        if not eligible_keys:
            continue

        accession, end = sorted(eligible_keys, key=lambda item: (item[1], item[0]), reverse=True)[0]
        rows.append(
            {
                "ticker": ticker,
                "cik": cik,
                "company_name": getattr(row, "company_name", ""),
                "sample_cohort": "liabilities_current_noncurrent_screened_control",
                "raw_share_validation_role": "control",
                "discovery_accessions": accession,
                "screen_accession": accession,
                "screen_period_end": end,
                "screen_rule": "companyfacts_same_accession_period_all_three_tags",
            }
        )

    cohort = pd.DataFrame(rows).sort_values("ticker", kind="stable").reset_index(drop=True)
    base = root / "reports" / "v3" / "data_sources" / args.as_of.isoformat()
    output_dir = base / "liabilities_current_noncurrent_screened"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "validation_cohort.csv"
    cohort.to_csv(output, index=False)

    print("V3 LIABILITIES CURRENT+NONCURRENT SCREENED CONTROLS")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Screened controls:           {len(cohort)}")
    print(f"Cohort:                      {output}")
    print("NO MODEL INPUTS WERE MODIFIED.")


if __name__ == "__main__":
    main()
