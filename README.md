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


### Factor input validation

Raw factor values are preserved even when an observation is rejected for model
use. Validation creates three companion columns for every registered factor:

```text
<factor>_valid
<factor>_invalid_reason
<factor>_validated
```

The raw value is the audit record. Downstream normalization and scoring must use
the `_validated` value, never the raw factor directly.

V1 validation is intentionally conservative and targets internal inconsistency
rather than merely large economic values. Examples include balance-sheet fields
that differ from total-assets scale by more than two orders of magnitude and
growth comparisons whose prior value is microscopic relative to company scale.
Legitimate distressed observations may remain extreme and are handled later by
cross-sectional winsorization.

Validation rejection reasons are machine-readable and summarized by
`scripts/audit_raw_factors.py`.

## Core-business composite V1

The first versioned composite is `core_business_v1`.

Family weights:

- Quality: 45%
- Financial Health: 25%
- Growth: 30%

The composite requires at least two of the three family scores. Missing
families are not treated as zero; available family weights are proportionally
renormalized.

Governance flags are emitted with every score:

- `health_missing`
- `top_conviction_eligible`
- `evaluation_eligible`
- `model_id`

A row with missing Financial Health may receive a diagnostic composite score
but cannot qualify for the highest-conviction classification. Clean V1
evaluation begins in 2016 because 2015 is the Growth warm-up year.

The model definition is versioned in
`src/finance/models/core_business_v1.py`. Material changes to family weights,
minimum-family requirements, or eligibility rules require a new model version.

## Valuation-factor V1 contract

V1 valuation uses the latest PIT-available annual (`qtrs=4`) SEC duration
facts rather than the generic latest-period panel values. This avoids mixing
quarterly 10-Q values with annual 10-K values in price-to-fundamental ratios.

Current raw valuation factors are:

- annual earnings yield;
- annual sales yield;
- annual free-cash-flow yield;
- book-to-market.

Market capitalization is `close * shares_outstanding`. Raw `close` is used
for contemporaneous valuation; adjusted/return prices are reserved for return
and momentum calculations.

Annual valuation inputs retain their reported period and filing acceptance
timestamps. V1 rejects annual valuation observations when the relevant filing
is more than 550 days old or appears after the decision date.

Negative earnings and negative free cash flow remain valid negative valuation
signals rather than being discarded. Book-to-market requires positive equity.

TTM valuation is intentionally deferred. Building defensible TTM fundamentals
requires reconstructing discrete quarters, including Q4 from annual minus
year-to-date reported values, and will be evaluated as a later challenger.

### Valuation family V1

The Valuation family uses normalized, validated valuation factors with the
following fixed V1 component weights:

- annual earnings yield: 30%
- annual sales yield: 20%
- annual free-cash-flow yield: 30%
- book-to-market: 20%

At least two of the four component scores are required. Missing components are
not treated as zero; available component weights are proportionally
renormalized. The family remains separate from `core_business_v1` until its
coverage, distribution, and correlation with the existing families are
validated.

## Long-growth composite V1

`long_growth_v1` is the first four-family composite intended to represent the
project's long-term growth objective.

Family weights:

- Quality: 35%
- Financial Health: 20%
- Growth: 25%
- Valuation: 20%

At least three of the four family scores are required. If exactly one family
is missing, the available family weights are proportionally renormalized;
missing families are never treated as zero or neutral.

`top_conviction_eligible` requires all four family scores to be present. The
model also emits explicit `health_missing`, `growth_missing`,
`valuation_missing`, `full_family_coverage`, and `evaluation_eligible` flags.

Clean historical evaluation begins in 2016. `core_business_v1` remains
unchanged and serves as a prior benchmark model rather than being mutated.

Material changes to family weights, minimum-family requirements, or conviction
rules require a new model version.

## Stability-factor V1 contract

V1 Stability is derived from the weekly PIT `return_price` series and remains
outside the composite model until provider and distribution audits pass.

Raw Stability factors:

- 52-week annualized realized volatility;
- 52-week annualized downside deviation;
- 52-week maximum drawdown magnitude.

A trailing window uses up to 52 weekly returns and requires at least 40 valid
weekly returns for volatility/downside deviation. Maximum drawdown uses up to
53 weekly prices and requires at least 41 valid prices.

Return chains reset across gaps longer than 14 days and whenever the canonical
`price_source` or `return_price_basis` changes. This prevents synthetic returns
across membership gaps or provider/adjustment transitions.

Because Tiingo normally contributes adjusted close while Stooq V1 uses close
fallback, Stability must be audited by `price_source` and year before it is
eligible for family scoring. Provider differences are treated as a possible
hidden factor until validation shows otherwise.

### Stability family V1

The Stability family uses normalized, validated trailing price-risk factors:

- 52-week realized volatility: 35%
- 52-week downside deviation: 35%
- 52-week maximum drawdown: 30%

At least two of the three components are required. Missing components are
proportionally reweighted and are never treated as zero.

Stability remains outside `long_growth_v1`; adding it to a composite requires
a new model version after family-level coverage, correlation, and provider
audits are reviewed.

## Momentum-factor V1 contract

Momentum V1 is intentionally long-horizon and excludes approximately the most
recent month so the model does not chase very short-term moves.

Raw Momentum factors:

- 12-month momentum excluding the most recent month: 60% planned family weight;
- 6-month momentum excluding the most recent month: 40% planned family weight.

The raw signals use weekly PIT `return_price`:

- 12m ex-1m = price around t-4 weeks / price around t-52 weeks - 1;
- 6m ex-1m = price around t-4 weeks / price around t-26 weeks - 1.

The actual anchor ages are preserved and must fall within explicit calendar
windows. Momentum histories reset across gaps longer than 14 days and whenever
`price_source` or `return_price_basis` changes.

Like Stability, Momentum remains outside the composite until raw coverage,
provider distributions, normalization, and family correlations are validated.

### Momentum family V1

The Momentum family uses normalized, validated long-horizon trend factors:

- 12-month momentum excluding the most recent month: 60%
- 6-month momentum excluding the most recent month: 40%

At least one of the two components is required. This allows the 6-month signal
to contribute during periods where a valid 12-month history is not yet
available, while proportionally reweighting rather than treating the missing
component as neutral.

Momentum remains outside `long_growth_v1`; any composite that adds Momentum or
Stability must be created as a new model version after family-level audits.

## Full-growth composite V1 challenger

`full_growth_v1` is the first six-family challenger model. It does not mutate
`long_growth_v1` or `core_business_v1`.

Family weights:

- Quality: 30%
- Financial Health: 20%
- Growth: 20%
- Valuation: 15%
- Stability: 10%
- Momentum: 5%

Eligibility is semantic rather than a simple family-count threshold:

- Quality is required.
- Growth is required.
- At least two of Financial Health, Valuation, and Stability are required.
- Momentum may contribute when available, but cannot make an otherwise
  ineligible row eligible.

`top_conviction_eligible` requires Quality, Financial Health, Growth,
Valuation, and Stability. Momentum is optional for top conviction.

Available family weights are proportionally renormalized. Missing families are
never treated as zero or neutral. Clean historical evaluation begins in 2016.

The challenger must be audited side-by-side against `long_growth_v1` before
any backtest or promotion decision. Material changes require a new model
version.
