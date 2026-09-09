"""Read-only evidence export for SEC dates, share lineage, and weekly availability.

Run from the finance repository. Uses only Python's standard library; no API/auth.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def same_cik(value, cik):
    return str(value or '').strip().removesuffix('.0').lstrip('0') == str(cik)


def csv_rows(path, required):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f'{path}: missing columns {sorted(missing)}')
        yield from reader


def in_years(value, first, last):
    text = str(value or '').strip()
    return len(text) >= 4 and text[:4].isdigit() and first <= int(text[:4]) <= last


def raw_table(archive, name, required):
    matches = [n for n in archive.namelist() if Path(n).name.lower() == name]
    if len(matches) != 1:
        raise ValueError(f'{archive.filename}: expected exactly one {name}')
    with archive.open(matches[0]) as binary:
        with io.TextIOWrapper(binary, encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream, delimiter='\t')
            if not set(required) <= set(reader.fieldnames or []):
                raise ValueError(f'{archive.filename}: missing required columns in {name}')
            yield from reader


def export(args):
    if args.start_year > args.end_year:
        raise ValueError('start-year must not exceed end-year')
    if args.output_dir.exists():
        raise ValueError('Output directory already exists; use a new --output-dir')
    for path in (args.panel, args.winners):
        if not path.is_file():
            raise ValueError(f'Input not found: {path}')

    evidence = []
    def record(path):
        item = {'filename': path.name, 'bytes': path.stat().st_size, 'sha256': sha256(path)}
        evidence.append((path, item))

    record(args.panel)
    record(args.winners)
    print('Reading target weekly rows and all referenced fact accessions...', flush=True)
    panel = [r for r in csv_rows(args.panel, ['ticker', 'cik', 'decision_date'])
             if (same_cik(r['cik'], args.cik) or r['ticker'].strip().upper() == args.ticker)
             and in_years(r['decision_date'], args.start_year, args.end_year)]
    if not panel:
        raise ValueError('No target weekly rows found in requested years')
    referenced = {v for r in panel for k, v in r.items() if k.endswith('_adsh') and v}
    first_raw_year = args.start_year - 1
    winners = [r for r in csv_rows(args.winners, ['cik', 'adsh', 'accepted_at', 'source_zip'])
               if same_cik(r['cik'], args.cik) and
               (r['adsh'] in referenced or in_years(r['accepted_at'], first_raw_year, args.end_year))]
    required_zip_names = {r['source_zip'] for r in winners if r['source_zip']}
    print(f'Found {len(panel)} weekly rows and {len(winners)} winner rows. Locating SEC ZIPs...', flush=True)
    candidates = sorted(args.zip_dir.rglob('*.zip'))
    paths = []
    for path in candidates:
        match = re.fullmatch(r'(\d{4})q[1-4]', path.stem, re.IGNORECASE)
        if path.name in required_zip_names or (match and first_raw_year <= int(match[1]) <= args.end_year):
            paths.append(path)
    if not paths:
        raise ValueError(f'No matching SEC quarterly ZIPs under {args.zip_dir}; pass --zip-dir with the source directory')
    names = [p.name for p in paths]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate SEC ZIP filenames found; use a narrower --zip-dir to identify the intended copies')

    submissions, numeric, presentation = [], [], []
    for number, path in enumerate(paths, 1):
        print(f'[{number}/{len(paths)}] Reading {path.name}', flush=True)
        record(path)
        with zipfile.ZipFile(path) as archive:
            selected = [r for r in raw_table(archive, 'sub.txt', ['adsh', 'cik', 'period', 'accepted'])
                        if same_cik(r['cik'], args.cik) and
                        (r['adsh'] in referenced or in_years(r['accepted'], first_raw_year, args.end_year))]
            accessions = {r['adsh'] for r in selected}
            submissions.extend(dict(r, evidence_source_zip=path.name) for r in selected)
            # Keep ALL tags, dimensions, units, periods, and presentation rows for
            # these filings, including candidates the production pipeline rejects.
            for table, target in [('num.txt', numeric), ('pre.txt', presentation)]:
                for r in raw_table(archive, table, ['adsh', 'tag']):
                    if r['adsh'] in accessions:
                        target.append(dict(r, evidence_source_zip=path.name))

    if not submissions or not numeric:
        raise ValueError('No target raw submissions/numeric facts were found')
    print('Verifying source files stayed unchanged...', flush=True)
    for path, item in evidence:
        if sha256(path) != item['sha256']:
            raise ValueError(f'Source changed during export: {path}')

    found = {r['adsh'] for r in submissions}
    requested = referenced | {r['adsh'] for r in winners}
    warnings = []
    missing_accessions = sorted(requested - found)
    missing_zips = sorted(required_zip_names - set(names))
    if missing_accessions:
        warnings.append('Some referenced/winner accessions are absent from raw submissions')
    if missing_zips:
        warnings.append('Some source ZIPs named by winner rows were not located')
    expected = ['shares_outstanding', 'shares_outstanding_adsh', 'shares_outstanding_accepted_at',
                'shares_outstanding_period_date', 'shares_outstanding_source_tag',
                'close', 'adjusted_close', 'return_price_basis', 'price_date', 'as_of']
    missing_columns = sorted(set(expected) - set(panel[0]))
    if missing_columns:
        warnings.append('Weekly panel lacks some expected lineage/price columns; inspect missing_panel_columns')
    missing_shares = sum(not r.get('shares_outstanding', '').strip() for r in panel)
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None

    args.output_dir.mkdir(parents=True, exist_ok=False)
    datasets = {'raw_submissions.csv': submissions, 'raw_numeric_facts.csv': numeric,
                'raw_presentation.csv': presentation, 'cached_winner_facts.csv': winners,
                'weekly_panel_lineage.csv': panel}
    for filename, rows in datasets.items():
        fields = list(dict.fromkeys(k for row in rows for k in row)) or ['adsh']
        with (args.output_dir / filename).open('x', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    manifest = {'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'purpose': 'Evidence extraction only; no validation pass or source equivalence is claimed',
                'ticker': args.ticker, 'cik': args.cik, 'panel_years': [args.start_year, args.end_year],
                'raw_years': [first_raw_year, args.end_year], 'checkout_commit': commit,
                'source_files': [item for _, item in evidence],
                'row_counts': {k: len(v) for k, v in datasets.items()},
                'warnings': warnings, 'missing_raw_accessions': missing_accessions,
                'missing_source_zips': missing_zips, 'missing_panel_columns': missing_columns,
                'weekly_rows_without_shares': missing_shares,
                'limitations': ['Checkout commit does not identify the original cache generation revision.',
                                'Raw field strings are preserved; dates/timezones are not interpreted or normalized.',
                                'One prior calendar year plus available referenced source ZIPs is included.',
                                'Split events are not fetched; price/share split compatibility is not proven.',
                                'CSV exports preserve field values, not byte-for-byte source TSV formatting.']}
    (args.output_dir / 'lineage_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    archive_path = args.output_dir / 'sec_lineage_bundle.zip'
    with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(args.output_dir.iterdir()):
            if path.name != archive_path.name:
                archive.write(path, arcname=path.name)
    print(f'Export complete: {archive_path.resolve()}')
    for warning in warnings:
        print(f'INCOMPLETE EVIDENCE: {warning}')
    print('Upload sec_lineage_bundle.zip. This is evidence for validation, not a validation result.')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip-dir', type=Path, default=Path('.'), help='Recursively search for original SEC quarterly ZIPs')
    parser.add_argument('--panel', type=Path, default=Path('reports/weekly_research_panel_2015_2025.csv'))
    parser.add_argument('--winners', type=Path, default=Path('data/cache/sec/sec_winner_facts_all.csv'))
    parser.add_argument('--ticker', default='AMAT')
    parser.add_argument('--cik', type=int, default=6951)
    parser.add_argument('--start-year', type=int, default=2025)
    parser.add_argument('--end-year', type=int, default=2025)
    parser.add_argument('--output-dir', type=Path, default=Path('reports/lineage') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    args = parser.parse_args()
    args.ticker = args.ticker.strip().upper()
    try:
        export(args)
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        parser.exit(1, f'Export failed: {exc}\n')


if __name__ == '__main__':
    main()
