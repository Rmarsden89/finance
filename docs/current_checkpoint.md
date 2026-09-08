# Current Research Checkpoint

Last updated: 2026-09-07

This file is the handoff point for continuing the project in a new chat or work session.

## Current champion candidate

Frozen model:

- `long_growth_v1`
- Family weights:
  - Quality 35%
  - Financial Health 20%
  - Growth 25%
  - Valuation 20%
- Missing available family weights are proportionally reweighted.
- `top_conviction_eligible` requires all four families.
- Model definition remains immutable; recent work has only tested portfolio construction and data quality.

Current leading portfolio construction:

- Top 10 `top_conviction_eligible` names by `long_growth_v1_score`
- $10 weekly contribution
- Equal-dollar allocation across currently buyable names
- 10% appreciation-only add-on threshold:
  - if current position weight is >= 10%, do not add new money
  - never sell merely because a winner appreciates above 10%
  - if the position later falls below 10%, it becomes buyable again automatically
- No Stability guardrail
- No discretionary selling
- Forced exits only when the PIT investable-universe/data boundary requires them

## Portfolio validation completed

### Initial full-period accumulation backtest, 2016-2025

Top-5 `long_growth_v1`:

- contributions: $5,220
- ending value: about $18,523
- XIRR: about 24.1%
- annualized TWR: about 20.6%
- max drawdown: about 35.8%

Top-5 `full_growth_v1` challenger:

- ending value: about $17,402
- XIRR: about 23.0%
- annualized TWR: about 20.3%
- max drawdown: about 37.8%

VOO:

- ending value: about $11,782
- XIRR: about 15.7%
- annualized TWR: about 14.9%
- max drawdown: about 33.7%

Conclusion: keep `long_growth_v1` as champion; Stability + Momentum did not earn positive composite weights.

### Top-N sensitivity

`long_growth_v1` full-period results:

- Top 1: about $16,065, 21.5% XIRR
- Top 3: about $18,877, 24.5% XIRR
- Top 5: about $18,523, 24.1% XIRR
- Top 10: about $19,472, 25.1% XIRR

Top 10 became the leading portfolio breadth.

### Concentration robustness

Top-10 baseline:

- ending value: about $19,472
- XIRR: about 25.1%

Profit concentration was meaningful but capital allocation was not dominated by one stock.

Approximate gain shares:

- NVDA: 37.8%
- top 3 winners: 58.3%
- top 5 winners: 70.0%

Historical concentration:

- median largest position: about 9.7%
- maximum largest position: about 33%
- ending largest position: about 29%
- ending top-3 concentration: about 45.5%
- ending top-5 concentration: about 56.1%

Leave-winner-out tests degraded gradually rather than collapsing:

- exclude NVDA: about $14,295, 19.3% XIRR
- exclude top 3 historical winners: about 15.8% XIRR
- exclude top 5 historical winners: about 14.3% XIRR

Interpretation: the ranking signal is broader than one lucky pick, but exceptional winners materially amplify excess return.

### 10% appreciation-only add-on threshold

The engine now distinguishes:

- `max_position_weight`: earlier allocation-cap experiment
- `max_addon_position_weight`: current appreciation-only rule

Re-entry is explicitly tracked with trade reason:

`position_cap_reentry`

10% threshold full-period result was nearly identical to uncapped Top 10:

- uncapped: about $19,471.73, 25.05% XIRR
- 10% add-on threshold: about $19,442.91, 25.03% XIRR
- 153 blocked purchases
- 19 re-entry purchases

Conclusion: 10% add-on threshold is the current leading concentration control.

### Rolling-window robustness

Fresh-start rolling windows:

- 3-year windows: 2016-2018 through 2023-2025
- 5-year windows: 2016-2020 through 2021-2025

Against VOO:

- 3-year windows: Long Growth beat VOO in 6 of 8
- 5-year windows: Long Growth beat VOO in 5 of 6

Average XIRR edge versus VOO:

- 3-year: roughly +2.8 percentage points
- 5-year: roughly +3.8 percentage points

The 10% add-on threshold remained effectively costless across rolling windows.

### Rank persistence / turnover

Across 522 decision weeks:

- average week-to-week Top-10 overlap: 87.6%
- median overlap: 90%
- average replacement rate: 12.4%
- 183 weeks had zero replacements

Next-week persistence:

- ranks 1-3: about 93.2%
- ranks 4-5: about 93.6%
- ranks 6-10: about 81.9%

Examples of durable Top-10 membership:

- TROW: 400 weeks
- VRTX: 255
- NVDA: 254
- AMAT: 219
- LRCX: 211

Actual purchase persistence:

- 86.9% of buy dollars went to tickers with at least 26 Top-10 weeks
- 77.0% went to tickers with at least 52 Top-10 weeks

Conclusion: ranking behavior is persistent enough for a long-term accumulation strategy.

## Turnover-spike investigation

Large Top-10 changes clustered around filing-season periods, especially May.

Root-cause audit found:

- no evidence of family-coverage collapse
- no meaningful eligibility-flip artifact
- no random score instability
- Growth was the largest family mover, followed by Quality
- batches of companies received new underlying fundamental values, causing legitimate cross-sectional percentile reordering

The prior weekly panel did not carry exact accepted/filed lineage for generic current fundamentals, so exact filing-to-factor traceability was incomplete.

## SEC lineage patch

Completed in `src/finance/data/sec_snapshot.py`.

`pivot_snapshot()` now preserves per-concept PIT provenance alongside existing value columns.

For a concept such as revenue, the weekly panel can now carry:

- `revenue`
- `revenue_period_date`
- `revenue_filing_period_date`
- `revenue_filed_date`
- `revenue_accepted_at`
- `revenue_form`
- `revenue_fy`
- `revenue_fp`
- `revenue_qtrs`
- `revenue_source_tag`
- `revenue_adsh`

Equivalent provenance is carried for other current SEC concepts when source columns are available.

This is an auditability-only patch. It does not alter winner selection, factor formulas, normalization, model weights, or eligibility.

The turnover-spike audit was also updated to detect concept-level `*_accepted_at` and `*_filed_date` fields.

## Refreshed Tiingo / canonical market baseline

Priority Tiingo acquisition is complete.

Canonical market validation after rebuild:

- coverage rows: 754
- materialized price rows: 1,312,685
- tickers with price rows: 685
- Tiingo selected: 685
- Stooq selected: 0
- unresolved: 69
- structural validation: PASS, all checks zero

Previous baseline was roughly:

- Tiingo: 493
- Stooq: 188
- unresolved: 73
- covered: 681

The priority work mostly replaced Stooq fallback / partial coverage with full Tiingo coverage rather than dramatically increasing the number of covered tickers.

Important implication: overall coverage changed only slightly, but return-basis consistency changed materially because former Stooq names now use Tiingo adjusted-close data.

Known market-data limitation still worth remembering: earlier V1 work identified a PARA adjusted-return quality issue for review. Do not assume all adjusted-price history is perfect merely because structural validation passes.

## Refreshed weekly panel

The weekly PIT panel has already been rebuilt after both:

1. Tiingo/canonical refresh
2. SEC current-fact lineage patch

Current output:

`reports/weekly_research_panel_2015_2025.csv`

Audit:

- decision weeks: 574
- panel rows: 288,655
- average members/week: 502.9
- identity-resolved rows: 288,655
- price-available rows: 272,407
- fundamentals-available rows: 288,004
- research-ready rows: 272,165

Approximate panel rates:

- price available: 94.37%
- research ready: 94.29%

## Current task: rebuild frozen factor/model outputs

The weekly panel is rebuilt. The next step is to regenerate the frozen V1 pipeline from the new panel.

Correct build order:

```text
weekly panel
-> raw factors
-> normalized factors
-> family scores
-> long_growth_v1
```

Commands:

```powershell
cd C:\Repos\finance

py scripts\build_raw_factors.py `
  --panel "reports\weekly_research_panel_2015_2025.csv" `
  --output "reports\raw_factors_v1.csv"

py scripts\build_normalized_factors.py `
  --factors "reports\raw_factors_v1.csv" `
  --output "reports\normalized_factors_v1.csv"

py scripts\build_family_scores.py `
  --normalized "reports\normalized_factors_v1.csv" `
  --output "reports\family_scores_v1.csv"

py scripts\build_long_growth_v1.py `
  --family-scores "reports\family_scores_v1.csv" `
  --output "reports\long_growth_v1.csv"
```

Do not skip raw-factor or normalization stages. `build_family_scores.py` requires `--normalized`, not the weekly panel directly.

## Immediate next validation after the rebuild

After the four builds complete:

1. Compare raw-factor coverage and rejection counts with the prior frozen baseline.
2. Compare normalized/family/model coverage with prior results.
3. Rerun the targeted May SEC provenance audit using the rebuilt panel to confirm exact accepted/filed lineage behind the major turnover events:
   - 2020-05-08
   - 2022-05-06
   - 2023-05-05
   - 2025-05-09
4. If the frozen factor/model outputs remain structurally consistent, rerun the current champion portfolio:
   - `long_growth_v1`
   - Top 10
   - $10/week
   - 10% `max_addon_position_weight`
5. Compare refreshed results to the earlier champion baseline rather than retuning weights.

## Governance reminder

Do not tune factor weights, thresholds, or model definitions in response to this data refresh.

The current exercise is a frozen-model validation against:

- improved Tiingo return-price consistency
- slightly improved market coverage
- improved SEC provenance/auditability

Any material performance change should first be explained as a data effect before proposing a new model version.
