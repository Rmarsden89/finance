from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Finalize V5 #44 hypothesis register and diagnostic report.')
    parser.add_argument('--root', type=Path, default=Path('reports/v5/v1_diagnostic'))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.root
    register_path = root / 'hypothesis_register.csv'
    register = pd.read_csv(register_path, low_memory=False)
    existing = set(register['hypothesis_id'].astype(str)) if not register.empty else set()

    additions = []
    def add(row: dict) -> None:
        if row['hypothesis_id'] not in existing:
            additions.append(row)

    add({
        'hypothesis_id':'H44-05',
        'status':'supported_for_historical_testing',
        'observation':'Growth is the only family with positive score-vs-excess-return Spearman at 4w, 13w, 26w, and 52w.',
        'hypothesis':'Growth is V1\'s most consistent upside-driving family and should remain a core return signal in V5.',
        'supporting_evidence':'Historical Top-10 family-pattern diagnostic across 5,220 selected rows.',
        'contradictory_evidence':'High Growth worsens the P10 tail at 13w, 26w, and especially 52w.',
        'historical_test_question':'Can Growth receive greater architectural influence without violating frozen drawdown/concentration gates?',
        'target_issue':'#47',
    })
    add({
        'hypothesis_id':'H44-06',
        'status':'supported_for_historical_testing',
        'observation':'Higher Valuation scores reduce mean excess return at 13w/26w/52w but improve P10 tail outcomes, with Growth×Valuation strongest in 2016-2019 and 2023-2025.',
        'hypothesis':'Valuation may be better used as price-discipline/risk control than as a linear positive return family.',
        'supporting_evidence':'Negative Valuation Spearman at all horizons, negative high-vs-low mean spreads, positive P10 protection, and repeated Growth×Valuation interaction.',
        'contradictory_evidence':'2020-2022 Growth×Valuation reverses slightly at 26w/52w, so the relationship is not universal.',
        'historical_test_question':'Does a bounded Valuation modifier or nonlinear Growth×Valuation interaction improve matched-cash-flow terminal value robustly across rolling windows?',
        'target_issue':'#47',
    })
    add({
        'hypothesis_id':'H44-07',
        'status':'supported_for_historical_testing',
        'observation':'Momentum improves mean excess return and P10 behavior at 13w/26w, but its Growth interaction changes sign by regime and its 52w relationship weakens.',
        'hypothesis':'Momentum is better suited to confirmation/timing than a permanent additive long-horizon family weight.',
        'supporting_evidence':'Positive 13w/26w family spreads and Growth×Momentum results in 2016-2019 and 2023-2025.',
        'contradictory_evidence':'2020-2022 strongly favors lower Momentum conditional on high Growth; 52w family signal is near-flat/slightly negative.',
        'historical_test_question':'Can a bounded Momentum confirmation rule improve selection without excessive turnover or regime dependence?',
        'target_issue':'#48',
    })
    add({
        'hypothesis_id':'H44-08',
        'status':'supported_for_historical_testing',
        'observation':'Stability sacrifices mean return and provides only medium-horizon tail protection; its interaction with Growth changes materially across regimes.',
        'hypothesis':'Stability should be tested as a bounded risk modifier rather than a return-seeking additive family.',
        'supporting_evidence':'Negative family-return association; positive P10 protection at 13w/26w; regime-dependent Growth×Stability interaction.',
        'contradictory_evidence':'At 52w higher Stability is worse on both mean return and P10 protection in the aggregate diagnostic.',
        'historical_test_question':'Can Stability reduce drawdown/tail risk while preserving enough terminal-value improvement to pass #46 economic gates?',
        'target_issue':'#48',
    })

    if additions:
        register = pd.concat([register, pd.DataFrame(additions)], ignore_index=True)
    register.to_csv(register_path, index=False)

    report = '''# V5 #44 — V1 strengths and weaknesses diagnostic

## Scope

This diagnostic is for hypothesis generation only. It combines 5 completed V1 live runs across 4 independent ISO weeks with the PIT-safe 2016-2025 attribution dataset. The 2026-09-21 and 2026-09-25 runs are treated as one independent weekly cycle. No V1 weights or rules are changed here.

## What V1 appears to do well

- The Top-10 cutoff is broadly effective: historical ranks 11-25 underperform the selected Top 10 in mean SPY-relative return at every tested horizon from 1 to 52 weeks.
- Growth is the most consistent upside-associated family inside V1 selections.
- V1 shows a persistent live core across the short prospective sample, suggesting the model is not dominated by random weekly churn.

## Where V1 may be structurally weak

- Valuation has a consistent negative association with mean excess return inside selected names, despite providing some lower-tail protection.
- The strongest interaction is Growth x Valuation: high-Growth / lower-Valuation-score names materially outperform high-Growth / high-Valuation-score names in two of three major time periods, but the effect weakens/reverses in 2020-2022.
- Stability appears costly to return and only inconsistently protective across horizons/regimes.
- Momentum looks useful at medium horizons but regime-dependent, which argues against treating it as a permanent linear additive family.

## Live challenger evidence

- V1-to-V2 differences are dominated by TTM score/rank changes rather than eligibility changes.
- V2-to-V3 live membership differences are small and indirect in the current sample, so recent selection differences are more about methodology/ranking than direct data-recovery entrants.

## Finite research questions

1. Should Growth have greater architectural influence while retaining risk guardrails? (#47)
2. Should Valuation become a nonlinear price-discipline/risk modifier rather than a 20% linear return family? (#47)
3. Does an explicit Growth x Valuation interaction improve terminal value across rolling windows without unacceptable downside? (#47)
4. Can Momentum improve entries/selections as a bounded confirmation signal without creating excessive turnover or regime dependence? (#48)
5. Can Stability reduce downside as a bounded risk modifier while still passing the frozen economic gates? (#48)
6. After controlling for V2/V3 data coverage and TTM methodology, which remaining errors are attributable to V1 scoring architecture? (#47)

## Rejected broad hypothesis

The aggregate hypothesis that V1 routinely leaves superior names just outside the Top 10 is rejected. Ranks 11-25 underperform Top-10 selections at every tested forward horizon.

## Governance

All candidate experiments must use the frozen #46 protocol. Historical improvement must primarily translate into higher matched-cash-flow terminal value and higher XIRR, with the frozen robustness/risk/concentration gates also satisfied. Historical success does not authorize live trading.
'''
    (root / 'diagnostic_report.md').write_text(report + '\n', encoding='utf-8')

    print('V5 #44 DIAGNOSTIC FINALIZATION')
    print('Hypotheses in register:      {}'.format(len(register)))
    print('Added family hypotheses:     {}'.format(len(additions)))
    print('Report:                      ' + str(root / 'diagnostic_report.md'))
    print('Register:                    ' + str(register_path))


if __name__ == '__main__':
    main()
