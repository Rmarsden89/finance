# V2 TTM Valuation Challenger — Predeclared Promotion Criteria

Declared before generation or review of the final full-model challenger backtest.

This document freezes the first V2 model challenger for Issue #6 and the pass/fail criteria that will be applied to its eventual full-model historical and shadow evaluation.

## Challenger identity

Model ID:

`long_growth_v2_ttm_valuation_v1`

The challenger changes **only the Valuation family numerator convention** relative to `long_growth_v1`.

Unchanged from V1:

- Quality family definitions and weights;
- Financial Health family definitions and weights;
- Growth family definitions and weights;
- family-level normalization conventions;
- family minimums;
- composite family weights:
  - Quality 35%;
  - Financial Health 20%;
  - Growth 25%;
  - Valuation 20%;
- minimum three families for a composite score;
- Top-Conviction requirement for all four core families;
- evaluation start: 2016-01-01;
- deterministic ticker-ascending score-tie rule;
- Top 10 breadth;
- $10 weekly contribution;
- equal-dollar contribution across buyable selected names;
- 10% appreciation-only add-on threshold;
- no discretionary selling.

TTM Valuation family:

- TTM earnings yield: 30%;
- TTM sales yield: 20%;
- TTM free-cash-flow yield: 30%;
- existing frozen V1 book-to-market: 20%;
- minimum two available factors;
- available factor weights renormalize proportionally exactly as in V1.

TTM construction remains YTD-preferred for compatible revenue/net-income Q2/Q3 observations, Q4 remains annual minus compatible Q3 YTD, and free cash flow uses the latest common TTM OCF/capex endpoint.

## Why criteria are being declared now

Issue #9 requires promotion criteria to be declared before final challenger results are reviewed.

Mechanics, coverage, PIT integrity, and valuation-family diagnostics have already been used to determine whether the challenger is valid enough to evaluate. They are not final full-model performance results.

No full `long_growth_v2_ttm_valuation_v1` composite return, rolling-window return, drawdown, or benchmark-relative result has been generated or reviewed at the time of this declaration.

## Hard promotion gates

All hard gates must pass. A strong aggregate return does not override a failed integrity or robustness gate.

### 1. Safety and reproducibility

- Historical and current TTM PIT violations: **0**.
- Frozen V1 regression outputs must remain unchanged.
- V2 artifacts remain independently fingerprinted and isolated.
- No broker, order-review, order-intent, or placement capability may exist in the challenger research path.

### 2. Valuation-family coverage continuity

Measured over the frozen V1 evaluation period beginning 2016-01-01:

- overall TTM Valuation-family eligible observations must be at least **95%** of annual V1 Valuation-family eligible observations;
- each complete calendar year must retain at least **90%** of annual V1 Valuation-family eligibility.

These are coverage-integrity gates, not performance gates.

### 3. Valuation-family structural continuity

Across historical weekly decision dates:

- mean weekly annual-vs-TTM Valuation-family Spearman correlation must be at least **0.85**;
- median weekly Valuation-family Top-10 overlap must be at least **6 of 10**;
- median weekly absolute Valuation-family rank shift must be no more than **25 ranks**.

These limits allow a materially fresher signal while rejecting a challenger that behaves like an unrelated factor family.

### 4. Full-model rank and turnover continuity

After the frozen V1 composite is rebuilt with only the TTM Valuation family substituted:

- median weekly V1-vs-V2 full-model Top-10 overlap must be at least **7 of 10**;
- mean weekly Top-10 replacement rate may increase by no more than **2.5 percentage points** versus V1.

### 5. Full-period performance and drawdown

Using the same historical dates, price data, contribution schedule, portfolio construction, and benchmark inputs for both models:

- challenger full-period XIRR must be **at least equal to V1**;
- challenger maximum drawdown may be no more than **2 percentage points worse** than V1.

A better XIRR does not compensate for a drawdown deterioration beyond this limit.

### 6. Rolling-window robustness

Use the existing V1 convention:

- 3-year windows;
- 5-year windows;
- start year 2016;
- end year 2025.

For **each** window length independently:

- challenger XIRR must be at least V1 in **50% or more** of windows;
- median challenger-minus-V1 XIRR must be **>= 0**;
- the number of windows in which the challenger beats the common benchmark must be **no lower than V1**.

No single favorable market regime may satisfy this gate by itself.

### 7. Prospective shadow requirement

Even if all historical gates pass:

- V2 must complete at least **8 weekly shadow decision cycles**;
- shadow runs remain research-only and disconnected from order generation/placement;
- data/PIT/reproducibility failures remain fail-closed;
- a separate explicit human decision is required before any future live integration.

Historical outperformance does not waive the shadow requirement.

## Required report-only comparisons

The final evaluation package must also report, even though these do not have separately optimized hard thresholds:

- benchmark-relative terminal value and XIRR;
- drawdown by rolling window;
- Top-10 concentration and position concentration;
- rank persistence;
- Top-10 turnover and turnover spikes;
- valuation-factor and family coverage by year;
- missingness / eligibility effects;
- current-state V1-vs-V2 rank and Top-10 differences;
- distribution and correlation of annual versus TTM valuation factors.

Any material anomaly in these diagnostics must be explained before promotion even if the numerical hard gates pass.

## Decision rule

The outcome is one of:

1. **Fails promotion gates** — retain V1 and document the failed gates.
2. **Passes historical gates, enters shadow** — challenger remains research/shadow-only.
3. **Passes historical gates and required shadow period** — eligible for a separate explicit promotion decision.

There is no automatic live promotion.

## Immutable implementation source

Machine-readable constants are defined in:

`src/finance/research/ttm_promotion_criteria.py`

Changing these thresholds after final challenger results are generated constitutes a new evaluation protocol and must be versioned and justified rather than silently replacing this declaration.
