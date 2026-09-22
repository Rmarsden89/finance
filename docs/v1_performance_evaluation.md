# V1 live performance evaluation

This workflow evaluates completed live `long_growth_v1` runs. It is intentionally separate from the V1 model and execution path.

It does **not** modify:

- factors or raw-factor validation;
- family scores or family weights;
- `long_growth_v1` weights, eligibility, ranking, or Top-10 construction;
- portfolio decision logic;
- order intents, reviews, submission, or reconciliation;
- any broker order.

The evaluator only reads saved live-run artifacts plus a SPY benchmark-price file and writes separate files under `reports/v1_evaluation`.

## Canonical benchmark

SPY is the primary V1 benchmark.

Each completed V1 live run contributes the amount that was actually deployed according to `post_fill_reconciliation.json`. The synthetic benchmark invests that same dollar amount in SPY on the same run date.

This prevents later weekly contributions from being mistaken for investment gains and gives a matched-cash-flow comparison.

The current benchmark price basis is:

1. `adjusted_close` when present and positive;
2. otherwise `close`.

Exact benchmark dates are required. The evaluator fails closed rather than silently using a different trading day.

The benchmark is synthetic and read-only. No SPY benchmark order is ever previewed, reviewed, or placed.

## Prepare SPY benchmark prices

The repository already contains the historical benchmark fetch command. Fetch SPY into the evaluation input file:

```powershell
$env:TIINGO_API_TOKEN="<your existing Tiingo token>"

py scripts\fetch_benchmark_prices.py `
  --symbol SPY `
  --start 2026-09-15 `
  --end YYYY-MM-DD `
  --output data\market\benchmark_spy.csv
```

Use an end date at least through the latest completed live-run date.

Do not commit API tokens.

## Run the evaluator

```powershell
py scripts\evaluate_v1_performance.py `
  --benchmark-prices data\market\benchmark_spy.csv
```

Default inputs:

```text
reports/shadow/
data/market/benchmark_spy.csv
```

Default outputs:

```text
reports/v1_evaluation/performance_summary.json
reports/v1_evaluation/performance_summary.csv
reports/v1_evaluation/weekly_portfolio_history.csv
reports/v1_evaluation/benchmark_history.csv
reports/v1_evaluation/selection_cohorts.csv
reports/v1_evaluation/selection_forward_returns.csv
```

## What is measured

The top-level summary reports:

- completed live-run count;
- cumulative dollars actually deployed;
- current V1 position market value from the latest completed run;
- V1 P/L and deployed-capital return;
- synthetic matched-cash-flow SPY value and return;
- V1 excess value versus SPY;
- V1 excess return versus SPY;
- distinct selected tickers;
- total selection events;
- current V1 position count.

`selection_cohorts.csv` preserves each live buy-selection event separately, including repeated selections. It records the selection date, decision hash, rank, score, allocation, and entry-price provenance.

## Forward-selection evaluation

Each selection event is tracked independently at:

- 1 week;
- 4 weeks;
- 13 weeks;
- 26 weeks;
- 52 weeks.

The entry price prefers the broker-reconciled average fill price. If the broker artifact does not contain a usable fill price, the evaluator falls back to the Robinhood market snapshot saved for that live run.

The forward observation uses the first completed weekly live-run market snapshot on or after the target horizon, up to seven days after the target.

A horizon that has not matured is reported as `pending`. It is never filled with a partial-period estimate.

Once mature, the output includes:

- stock forward return;
- SPY forward return over the same selection/observation dates;
- stock excess return versus SPY.

## Interpretation

Weekly reports are monitoring evidence, not an automatic model-promotion signal.

Use these checkpoints:

- 13 weeks: early monitoring checkpoint;
- about 26 weeks: first substantive live review;
- 52 weeks: first meaningful annual live review.

The effective sample remains smaller than the number of weekly purchases because many Top-10 names repeat across weeks.

Live observations may generate hypotheses, but any future model change still requires separate historical/walk-forward validation and a new model/version decision. This evaluator itself never changes V1.


## Live-run evaluation package

Every future completed V1 live run now builds:

```text
reports/shadow/YYYY-MM-DD/evaluation_package.json
```

The package is the stable evaluation contract for scripts today and a future dashboard later. It contains the run identity, decision hash, deployed contribution, post-fill marked position value, reconciled selections/fill metadata, benchmark capture, and SHA-256 provenance for source artifacts.

The package is evaluation-only and explicitly records `broker_order_capability: false`.

### Intraday SPY benchmark capture

On approved live submission, the pipeline captures a read-only SPY quote immediately before any V1 orders are placed. The capture is saved as:

```text
reports/shadow/YYYY-MM-DD/benchmark_spy_capture.json
```

The artifact records:

- selected SPY trade price;
- Robinhood venue quote timestamp;
- capture timestamp;
- quote age;
- selected price field;
- bid/ask when returned;
- market-session context.

A missing or stale SPY quote blocks the approved submission **before any order placement**. This preserves a benchmark timestamp without creating order ambiguity.

The performance evaluator prefers this intraday run capture for both contribution entry and later weekly observation points. For older runs created before this feature existed, it falls back to the same-date SPY daily adjusted close and labels that source explicitly.

This means future comparisons use the actual time each weekly pipeline was run rather than assuming a fixed daily close.
