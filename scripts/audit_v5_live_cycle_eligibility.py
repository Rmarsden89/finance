from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Audit completed V1 live runs and distinct weekly cycles for V5 #44.'
    )
    parser.add_argument('--shadow-root', type=Path, default=Path('reports/shadow'))
    parser.add_argument('--minimum-weeks', type=int, default=4)
    parser.add_argument('--target-weeks', type=int, default=5)
    parser.add_argument(
        '--output',
        type=Path,
        default=Path('reports/v5/v1_diagnostic/live_cycle_eligibility.json'),
    )
    return parser.parse_args()


def load_complete_v1_runs(shadow_root: Path) -> list[date]:
    runs: list[date] = []
    if not shadow_root.exists():
        return runs
    for child in shadow_root.iterdir():
        if not child.is_dir():
            continue
        try:
            run_date = date.fromisoformat(child.name)
        except ValueError:
            continue
        state_path = child / 'workflow_state.json'
        if not state_path.exists():
            continue
        payload = json.loads(state_path.read_text(encoding='utf-8'))
        if payload.get('status') != 'COMPLETE':
            continue
        if str(payload.get('model') or '') != 'long_growth_v1':
            continue
        runs.append(run_date)
    return sorted(runs)


def summarize_runs(run_dates: list[date], minimum_weeks: int, target_weeks: int) -> dict:
    by_week: dict[str, list[str]] = defaultdict(list)
    for run_date in sorted(run_dates):
        iso = run_date.isocalendar()
        key = '{}-W{:02d}'.format(iso.year, iso.week)
        by_week[key].append(run_date.isoformat())

    duplicate_weeks = {key: values for key, values in by_week.items() if len(values) > 1}
    distinct_weeks = len(by_week)
    return {
        'schema_version': 1,
        'completed_runs': len(run_dates),
        'distinct_iso_weeks': distinct_weeks,
        'minimum_weeks_required': minimum_weeks,
        'target_weeks': target_weeks,
        'entry_condition_satisfied': distinct_weeks >= minimum_weeks,
        'target_reached': distinct_weeks >= target_weeks,
        'run_dates': [value.isoformat() for value in sorted(run_dates)],
        'runs_by_iso_week': dict(sorted(by_week.items())),
        'duplicate_iso_weeks': duplicate_weeks,
        'counting_rule': 'multiple completed runs in one ISO week count as one prospective weekly cycle',
    }


def main() -> None:
    args = parse_args()
    runs = load_complete_v1_runs(args.shadow_root)
    result = summarize_runs(runs, args.minimum_weeks, args.target_weeks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    print('V5 #44 LIVE-CYCLE ELIGIBILITY')
    print('Completed V1 runs:         {:,}'.format(result['completed_runs']))
    print('Distinct ISO weeks:        {:,}'.format(result['distinct_iso_weeks']))
    print('Minimum required:          {:,}'.format(result['minimum_weeks_required']))
    print('Entry condition:           ' + ('PASS' if result['entry_condition_satisfied'] else 'WAIT'))
    print('5-week target reached:     ' + ('YES' if result['target_reached'] else 'NO'))
    print('Run dates:                 ' + ', '.join(result['run_dates']))
    if result['duplicate_iso_weeks']:
        print('Duplicate weeks:')
        for key, values in result['duplicate_iso_weeks'].items():
            print('  {}: {}'.format(key, ', '.join(values)))
    else:
        print('Duplicate weeks:           none')
    print('Output:                    ' + str(args.output))


if __name__ == '__main__':
    main()
