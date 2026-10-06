# V5 Challenger Evaluation Protocol v1

Issue: #46  
Parent: #43  
Status: **FROZEN BEFORE FINAL CANDIDATE EVALUATION**

## Objective hierarchy

The primary question for V5 is:

> **Did the challenger make more money than frozen V1 under the same cash flows and portfolio rules?**

The primary economic measure is matched-cash-flow **terminal portfolio value**. Because every candidate receives the same $10 weekly contribution schedule, terminal value is the most direct dollar answer. XIRR is the normalized return cross-check.

Risk, turnover, concentration, rank stability, and attribution matter because they tell us whether the extra money is credible, repeatable, and explainable. They do **not** create an alternate path where a model that makes less money is declared the winner because it is smoother.

## Frozen comparison contract

All serious V5 candidates use:

- champion: `long_growth_v1`;
- benchmark: SPY;
- historical evaluation: 2016-01-01 through 2025-12-31;
- canonical V5 attribution dataset SHA-256:
  `36a30bc1115c1496ee34efa0dfe2ba4e030b927e68e4d018c882d4b97831e0dc`;
- Top 10;
- $10 weekly contribution;
- equal-dollar allocation across buyable selected names;
- 10% appreciation-only add-on threshold;
- no discretionary selling;
- ticker ascending deterministic tie-break.

Portfolio construction remains fixed while ranking architecture is being evaluated. A later portfolio-construction experiment must be separately identified and attributed.

## Hard gates

A challenger must pass **all** hard gates to advance from historical research to a frozen prospective shadow challenger.

### 1. Integrity

- PIT violations: 0.
- Duplicate decision-date/ticker keys: 0.
- Deterministic rebuild required.
- Frozen V1 must remain unchanged.
- Research path must have no broker/order capability.

### 2. Primary economic gate

Both conditions must pass:

- terminal value must exceed V1 by at least **1.0%**;
- XIRR must exceed V1 by at least **0.10 percentage points**.

Why both: terminal value answers the dollar question; XIRR prevents a result from looking better only because of timing/accounting artifacts. The threshold prevents a trivial numerical edge from being treated as a new model.

A candidate that makes less money than V1 does **not** advance solely because it has lower drawdown.

### 3. Rolling robustness

For 3-year and 5-year windows, independently:

- challenger XIRR must beat V1 in at least **55%** of windows;
- median challenger-minus-V1 XIRR must be >= 0;
- challenger may not reduce the count of windows that beat SPY.

### 4. Risk guardrails

- full-period max drawdown may be no more than **2.5 percentage points worse** than V1;
- mean Top-10 replacement rate may increase by no more than **5 percentage points** versus V1.

These are guardrails, not optimization targets.

### 5. Concentration guardrails

Relative to V1:

- largest-position weight may increase by no more than **5 percentage points**;
- Top-5 position weight may increase by no more than **10 percentage points**.

A candidate whose apparent improvement comes from materially greater concentration fails unless it is separately versioned as an explicit portfolio-construction experiment.

## Required explanatory tracking

Every experiment must preserve the reason chain:

1. **Hypothesis** — what weakness or opportunity is being tested?
2. **Allowed change** — exactly what model behavior may differ?
3. **Expected mechanism** — why should that change improve selection?
4. **Observed selection effect** — which names/ranks changed and through what factor/family?
5. **Economic result reason** — where did the dollar/XIRR difference come from?
6. **Disposition reason** — why did the experiment advance or fail?

This is separate from merely recording whether the backtest went up or down. The goal is to know **what produced the money difference**.

Selection attribution should distinguish, where applicable:

- eligibility gain/loss;
- factor/family score change;
- rank displacement;
- Top-10 boundary entry/exit;
- confirmation/veto/risk modifier effect;
- indirect rerank caused by another ticker's change.

Negative and rejected experiments remain in the registry.

## Exploratory versus frozen experiments

### Exploratory

Exploratory work may:

- inspect factor/family relationships;
- generate hypotheses;
- test bounded candidate structures;
- identify thresholds worth freezing.

It may **not** be called a V5 winner and may not enter shadow.

### Frozen candidate

Before final evaluation, a candidate must have:

- unique experiment ID;
- explicit hypothesis and allowed-change scope;
- immutable configuration;
- configuration hash;
- code commit;
- canonical input fingerprints;
- declared status of `frozen_candidate`.

After final historical results are reviewed, changing weights, thresholds, family membership, eligibility, interaction logic, or signal usage requires a **new experiment ID**. Do not overwrite the prior result.

## Multiple-experiment discipline

Unrestricted grid search is prohibited.

A serious experiment should change one attributable concept or one tightly related bundle. Examples:

- family-weight redesign;
- factor-weight redesign within one family;
- eligibility architecture;
- Momentum confirmation;
- Stability risk modifier;
- one specified interaction structure.

If an exploratory sweep is used to understand a response surface, its results are exploratory only. A final candidate configuration must then be frozen before its formal evaluation.

## Required outputs for every frozen candidate

The evaluation package must include:

- terminal value and dollar delta vs V1;
- XIRR and XIRR delta vs V1;
- SPY-relative terminal value and XIRR;
- full-period max drawdown;
- 3-year and 5-year rolling results;
- turnover and replacement rate;
- rank stability;
- Top-10 overlap vs V1;
- concentration;
- winner-dependence / leave-out diagnostics;
- factor/family and selection attribution;
- family/factor coverage;
- explicit pass/fail for each frozen gate.

## Decision states

A frozen experiment ends in exactly one of:

- `rejected_integrity`
- `rejected_economic`
- `rejected_robustness`
- `rejected_risk`
- `rejected_concentration`
- `historical_pass_shadow_required`

No historical result authorizes live trading.

A historical winner must be frozen and complete at least **8 prospective weekly shadow cycles** before a separate human promotion review. Shadow performance does not silently modify the historical gates.

## Machine-readable source

The frozen machine-readable protocol is:

`config/v5_evaluation_protocol_v1.json`

Its frozen canonical JSON SHA-256 is:

`29ee0283a3850864f7731aac83204ef9726949cbc3ccfc9cf61d7e528b193b6a`

The governance validator checks this hash so an in-place protocol edit is detectable. Changing the protocol after formal candidate results have been reviewed requires a new protocol version, not an in-place edit.
