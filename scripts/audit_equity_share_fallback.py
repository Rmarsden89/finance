"""Compare opt-in equity-share selection with baseline using a lineage bundle.

Does not write caches or rebuild models. --output-dir saves comparison CSVs.
"""
from __future__ import annotations
import argparse
import json
import zipfile
from pathlib import Path
import pandas as pd
from finance.data.sec_canonical import build_canonical_facts
from finance.data.sec_winners import select_canonical_winners


def compare(bundle):
    with zipfile.ZipFile(bundle) as archive:
        def read(name):
            with archive.open(name) as stream:
                return pd.read_csv(stream, dtype=str, keep_default_na=False)
        sub, num, pre = (read(n) for n in ['raw_submissions.csv', 'raw_numeric_facts.csv', 'raw_presentation.csv'])
        cached = read('cached_winner_facts.csv')
        panel = read('weekly_panel_lineage.csv')
    sub = sub.drop_duplicates()
    sub['cik'] = pd.to_numeric(sub['cik'])
    sub['accepted_at'] = pd.to_datetime(sub['accepted'])
    for raw, target in [('period', 'period_date'), ('filed', 'filed_date')]:
        sub[target] = pd.to_datetime(sub[raw], format='%Y%m%d').dt.date
    num['ddate_date'] = pd.to_datetime(num['ddate'], format='%Y%m%d').dt.date
    num['qtrs'] = pd.to_numeric(num['qtrs'])
    num['value'] = pd.to_numeric(num['value'], errors='coerce')
    base, _ = build_canonical_facts(sub, num, pre)
    proposed, audit = build_canonical_facts(sub, num, pre, allow_equity_component_shares=True)
    base, _, _ = select_canonical_winners(base)
    proposed, _, _ = select_canonical_winners(proposed)
    def signatures(frame):
        return {(r.adsh, r.concept, str(r.ddate_date), float(r.value), r.source_tag)
                for r in frame.itertuples()}
    base_matches_cache = signatures(base) == signatures(cached)
    if not base_matches_cache:
        raise ValueError('Rebuilt baseline differs from supplied cache; resolve baseline drift before interpreting experiment')
    nonshare_unchanged = signatures(base.loc[base.concept.ne('shares_outstanding')]) == signatures(proposed.loc[proposed.concept.ne('shares_outstanding')])
    if not nonshare_unchanged:
        raise ValueError('Experiment unexpectedly changed non-share facts')
    new = proposed.loc[~proposed.apply(lambda r: (r.adsh, r.concept, str(r.ddate_date), float(r.value), r.source_tag) in signatures(base), axis=1)]
    weekly = []
    for r in panel.itertuples():
        cutoff = pd.Timestamp(r.as_of)
        eligible = proposed.loc[proposed.concept.eq('shares_outstanding')
                                & proposed.cik.eq(int(float(r.cik)))
                                & proposed.accepted_at.le(cutoff)]
        if eligible.empty:
            raise ValueError(f'No proposed shares available for {r.decision_date}')
        selected = eligible.sort_values(['ddate_date', 'accepted_at'], kind='stable').iloc[-1]
        old, current = float(r.shares_outstanding), float(selected.value)
        weekly.append({'decision_date': r.decision_date, 'baseline_shares': old,
                       'proposed_shares': current, 'changed': old != current,
                       'baseline_adsh': r.shares_outstanding_adsh, 'proposed_adsh': selected.adsh,
                       'proposed_accepted_at': str(selected.accepted_at),
                       'market_cap_relative_change': current / old - 1,
                       'valuation_yield_multiplier': old / current})
    summary = {'baseline_matches_cached_winners': base_matches_cache,
               'baseline_winner_count': len(base), 'proposed_winner_count': len(proposed),
               'nonshare_facts_unchanged': nonshare_unchanged,
               'candidate_rows_added': audit.rows_equity_component_added,
               'weekly_rows': len(weekly), 'weekly_share_changes': sum(r['changed'] for r in weekly),
               'status': 'EXPERIMENT ONLY: no multi-company validation or ranking/backtest impact measured'}
    return summary, new, pd.DataFrame(weekly)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    if args.output_dir and args.output_dir.exists():
        parser.error('Choose a new output directory; existing output is never overwritten')
    summary, added, weekly = compare(args.bundle)
    print(json.dumps(summary, indent=2))
    print(added[['adsh', 'concept', 'ddate_date', 'value', 'accepted_at']].to_string(index=False))
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=False)
        added.to_csv(args.output_dir / 'added_share_facts.csv', index=False)
        weekly.to_csv(args.output_dir / 'weekly_share_impact.csv', index=False)
        (args.output_dir / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
