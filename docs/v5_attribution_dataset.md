# V5 historical factor/family attribution dataset

Issue: #45  
Parent program: #43

## Purpose

This dataset is a research input for V5 hypothesis testing. It is not a new
model, a challenger score, or a live-trading input.

The builder consumes the already-scored historical `long_growth_v1` panel so
factor validation, weekly cross-sectional normalization, family scoring, and V1
eligibility are inherited from the existing validated pipeline rather than
reimplemented here.

## Ranking

For each decision date:

1. restrict ranking to `top_conviction_eligible=true` rows with a nonmissing
   `long_growth_v1_score`;
2. sort by score descending;
3. break score ties by ticker ascending;
4. assign `v1_rank`;
5. add `v1_top10`, `v1_top25`, and `v1_rank_band`.

Unranked rows are retained. This is important for missingness, eligibility, and
coverage analysis.

## Forward-return convention

Default horizons are 1, 4, 13, 26, and 52 weeks.

Entry valuation uses the canonical historical price store's last valid mark
price on or before the decision date. For each horizon, the exit uses the first
valid canonical price on or after the exact maturity date, with at most seven
calendar days of delay.

SPY uses the same timing convention but is read from the repository's separate
historical benchmark file, `data/market/benchmark_spy.csv`. SPY is intentionally
not expected to exist in the membership-filtered canonical universe price file.
The dataset records:

- security forward return;
- security exit date;
- SPY forward return;
- SPY exit date;
- security-minus-SPY excess return;
- explicit maturity/missing-price status.

Missing entry or exit prices remain missing. Prices are never interpolated or
invented.

These are research price returns, not broker fills and not portfolio returns.

## Reproducibility

The build summary records SHA-256 fingerprints for:

- the historical scored V1 input;
- the canonical historical universe price file;
- the separate historical SPY benchmark price file.

Duplicate `decision_date/ticker` keys fail closed.

## Command

```powershell
py scripts\build_v5_attribution_dataset.py
```

Default output:

```text
reports/v5/attribution_dataset/
  v5_historical_attribution_dataset.csv
  summary.json
```

## Current scope

The first implementation intentionally preserves every existing column from the
scored V1 history, including normalized factor scores, family scores, coverage,
eligibility, and available provenance/diagnostic fields.

Sector/industry/size fields are not synthesized. They should be added only from
a defensible PIT source.

## Safety

- research-only;
- no broker/order capability;
- no V5 model logic;
- no future-return value is used as a scoring input;
- no imputation of missing factor or return observations;
- no changes to V1/V2/V3/V4 definitions.
