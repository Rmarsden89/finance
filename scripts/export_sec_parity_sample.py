"""Export existing SEC winners for source parity; no network or broker access."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path


def export_sample(source: Path, output: Path, cik: int, start: date, end: date) -> dict:
    if end < start:
        raise ValueError("--until must be on or after --since")
    if not source.is_file():
        raise ValueError(f"Winner-fact file not found: {source}. Supply --input with its actual path.")
    if output.exists():
        raise ValueError(f"Output directory already exists: {output}. Choose a new --output-dir.")

    # Hash the exact input bytes and check again after extraction for concurrent changes.
    def digest() -> str:
        result = hashlib.sha256()
        with source.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                result.update(block)
        return result.hexdigest()

    source_hash = digest()
    selected = []
    scanned = 0
    target_rows = 0
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        required = {"cik", "adsh", "concept", "value", "source_tag", "accepted_at", "ddate_date", "qtrs", "uom", "form"}
        missing = sorted(required - set(fields))
        if missing:
            raise ValueError("Missing required winner-fact columns: " + ", ".join(missing))
        for row in reader:
            scanned += 1
            if scanned % 250000 == 0:
                print(f"Scanned {scanned:,} source rows", flush=True)
            raw_cik = row["cik"].strip()
            if raw_cik.endswith(".0"):
                raw_cik = raw_cik[:-2]
            if not raw_cik.isdigit() or int(raw_cik) != cik:
                continue
            target_rows += 1
            try:
                accepted = datetime.fromisoformat(row["accepted_at"].strip())
            except ValueError as exc:
                raise ValueError(f"Invalid acceptance timestamp for target CIK, accession {row['adsh']!r}") from exc
            # Filter by source calendar date without assigning or changing a timezone.
            # This is a filing sample, NOT a historical decision-time snapshot.
            if start <= accepted.date() <= end:
                selected.append(row)
    if not selected:
        raise ValueError(f"No records for CIK {cik} accepted between {start} and {end}; {target_rows} target rows exist in source.")
    if digest() != source_hash:
        raise ValueError("Source changed during extraction. Rerun after its writer has finished.")
    selected.sort(key=lambda r: (r["accepted_at"], r["adsh"], r["concept"], r["ddate_date"], r["source_tag"]))

    filings = {}
    for row in selected:
        key = row["adsh"]
        item = filings.setdefault(key, {
            "adsh": key, "form": row["form"], "accepted_at": row["accepted_at"],
            "filed_date": row.get("filed_date", ""), "period_date": row.get("period_date", ""),
            "row_count": 0, "concepts": set(),
        })
        item["row_count"] += 1
        item["concepts"].add(row["concept"])
    summaries = []
    for item in filings.values():
        summaries.append({**item, "concept_count": len(item["concepts"]), "concepts": "|".join(sorted(item["concepts"]))})
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    manifest = {
        "purpose": "Existing winner-fact export for source parity; not a backtest or PIT snapshot",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "cik": cik, "since_acceptance_date": start.isoformat(), "until_acceptance_date": end.isoformat(),
        "source_filename": source.name, "source_sha256": source_hash,
        "checkout_commit": revision,
        "provenance_note": "Checkout commit is not proof of the commit that originally generated the cache.",
        "source_rows_scanned": scanned, "exported_rows": len(selected), "filing_count": len(filings),
        "forms": sorted({r["form"] for r in selected}),
        "columns": fields,
        "limitations": ["Original winner values and metadata retained without cleaning or deduplication.",
                        "Rejected candidates and unresolved duplicates are not present in a winner cache.",
                        "No timezone interpretation or historical eligibility determination is performed."],
    }
    output.mkdir(parents=True, exist_ok=False)
    for filename, rows, columns in [
        ("sec_parity_facts.csv", selected, fields),
        ("sec_parity_filings.csv", summaries, list(summaries[0])),
    ]:
        with (output / filename).open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
    (output / "sec_parity_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/cache/sec/sec_winner_facts_all.csv"))
    parser.add_argument("--cik", type=int, default=6951, help="Default 6951 = Applied Materials (AMAT).")
    parser.add_argument("--since", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--until", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    output = args.output_dir or Path("reports/parity") / (f"cik_{args.cik}_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    print(f"Reading {args.input}; exporting CIK {args.cik}", flush=True)
    try:
        manifest = export_sample(args.input, output, args.cik, args.since, args.until)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Export failed: {exc}\n")
    print(f"Exported {manifest['exported_rows']} facts across {manifest['filing_count']} filings.")
    print(f"Forms: {', '.join(manifest['forms'])}")
    print(f"Output: {output.resolve()}")
    print("Upload sec_parity_facts.csv, sec_parity_filings.csv, and sec_parity_manifest.json.")


if __name__ == "__main__":
    main()
