# Finance Research

A versioned research project for developing and validating a long-term investing model before and alongside limited live deployment.

## Current focus

1. Point-in-time historical data
2. Walk-forward validation
3. Versioned scoring models
4. Benchmark comparison
5. Risk and allocation rules
6. Live shadow / low-dollar validation
7. Brokerage execution only after the research path is stable

## Core principle

Live results may generate hypotheses, but model changes must be validated against historical walk-forward data before promotion.

## Initial operating constraints

- Long-term investing, not day trading
- Weekly decision cadence
- Initial live capital capped at $10/week
- No leverage, margin, options, or forced trades
- Broad-market benchmark
- Model versions are immutable after evaluation
- Brokerage integration is isolated from the research engine

## Planned layout

```text
src/finance/
  config.py
  models/
  data/
  backtest/
  portfolio/
  risk/

tests/
docs/
```

## Development stages

### Stage 1 — Research framework
Build deterministic scoring, portfolio accounting, and walk-forward evaluation.

### Stage 2 — Historical validation
Evaluate performance, drawdown, volatility, calibration, and benchmark-relative results across multiple market regimes.

### Stage 3 — Shadow mode
Generate weekly recommendations without trading.

### Stage 4 — Low-dollar live validation
Use a frozen promoted model with a maximum of $10/week while continuing to develop challenger models separately.

### Stage 5 — Execution integration
Integrate brokerage execution only after the controls, audit trail, and promotion process are established.


## Market-data V1 contract

The research layer consumes only the canonical PIT market dataset:

```text
data/market/price_coverage.csv
data/market/daily_prices.csv.gz
```

Raw provider caches are inputs to the canonical market-data build only. Research
and model code must not read Tiingo, Stooq, Twelve Data, or any future provider
cache directly.

Current provider precedence for V1 is:

```text
Tiingo
  ↓
Stooq bulk
  ↓
Unresolved
```

A new provider may be evaluated as a fallback, but it is not promoted into the
canonical dataset until it passes the market-data validation gates.

### Required validation gate before changing canonical market data

1. Rebuild the canonical market dataset.
2. Run `scripts/validate_canonical_market_data.py` and require `RESULT: PASS`.
3. Run `scripts/audit_market_price_quality.py` and review every issue.
4. Run `scripts/audit_market_coverage_risk_full.py` and compare coverage/risk
   with the previous baseline.
5. Review any new historical ticker mappings, provider substitutions, or
   suspicious price series before accepting them.

Known limitations and the current frozen baseline are documented in
[`docs/known_limitations.md`](docs/known_limitations.md).

## Research-panel price source

`src/finance/data/research_panel.py` and
`src/finance/data/weekly_research_panel.py` read
`data/market/daily_prices.csv.gz` through `CanonicalPriceStore`.

The panel records both `market_ticker_used` and `price_source` so provider
provenance remains visible after the canonical layer is materialized.


## Factor-layer contract

The V1 factor framework lives under `src/finance/factors/`.

Raw factors are deterministic measurements derived from the PIT research panel.
They do **not** contain percentile normalization, family weights, composite-model
weights, portfolio rules, or buy/sell decisions.

Current V1 raw families:

- Quality
- Financial health
- Growth

The factor registry in `src/finance/factors/registry.py` records each factor's
family, direction, required inputs, lookback, description, and introduction
version.

The intended dependency is:

```text
PIT research panel
        ↓
raw factor calculation
        ↓
factor validation
        ↓
normalization / cross-sectional ranking
        ↓
family scores
        ↓
versioned composite model
        ↓
portfolio / backtest rules
```

Factor formulas should remain stable after a model version has been evaluated.
A changed formula should be treated as a new factor or model version rather
than silently changing historical results.

V1 growth factors use the PIT-visible value from roughly 52 weekly observations
earlier. Signed measures such as net income use absolute prior value as the
scale so changes through zero remain interpretable. Rows without an appropriate
roughly-one-year PIT lookback remain missing rather than being imputed.
