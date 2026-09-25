# V3 Combined Data-Coverage Challenger

## Status

Frozen for prospective shadow testing once the remaining V3 exit gates are
completed.

- Model ID: `long_growth_v3_data_coverage_v1`
- Evidence as of: 2026-09-15
- Mode: shadow-only
- Foundation: `long_growth_v2_ttm_valuation_v1`
- V1 modified: no
- Frozen V2 modified: no
- Broker/order capability: no

## Composition

The challenger is a thin composition layer over two already-frozen V3 data
rules:

1. `v3_liabilities_current_plus_noncurrent_v1`
2. `v3_raw_sec_entity_common_shares_v1`

The combined contract stores both constituent rule IDs and their configuration
hashes. Any constituent rule change therefore invalidates the combined freeze
and requires a new V3 version.

## What changes

Only input availability may change through the two frozen recovery rules.

The challenger does not change:

- factor definitions;
- family weights;
- family minimums;
- Top-Conviction eligibility rules;
- ranking/tie rules;
- portfolio construction;
- allocation;
- live execution behavior.

## Historical combined evidence

Across 288,655 historical rows and 574 decision dates:

- liabilities recovery rows: 10,598;
- shares recovery rows: 674;
- rows receiving both recoveries: 0;
- positive liabilities: 192,870 -> 203,468;
- positive shares: 168,026 -> 168,700;
- Financial Health eligibility gained/lost: +10,598 / 0;
- Valuation eligibility gained/lost: +574 / 0;
- Top-Conviction eligibility gained/lost: +6,165 / 0;
- mean/latest Top-10 overlap: 9.6092 / 9;
- mean weekly replacement rate: 12.1881% -> 12.7063%;
- median/max absolute rank displacement: 6 / 17;
- PIT violations: 0.

The one-week historical Top-10 return comparison is diagnostic only and is not
part of the freeze decision or a forecast.

## Shadow-only boundary

This challenger must remain execution-inert.

It may not:

- access broker account state for decision execution;
- create order intents;
- request broker order reviews;
- place, modify, or cancel orders;
- mutate V1 live artifacts;
- mutate frozen V2 research/shadow artifacts.

Shadow artifacts must use a distinct V3 namespace and carry:

- combined model ID;
- combined configuration hash;
- constituent rule IDs and hashes;
- input artifact fingerprints;
- decision timestamp / as-of date;
- deterministic selected ranks and Top 10.

## Remaining exit gates before shadow starts

The combined contract freeze satisfies the constituent-rule and composition
parts of the V3 exit criteria. Before prospective V3 shadow observation begins,
the project must still complete:

1. current-state V2-vs-V3 comparison using identical PIT inputs;
2. deterministic replay/fingerprint verification;
3. shadow-path isolation and regression tests;
4. post-V3 residual-gap inventory handoff to V4;
5. explicit human decision to begin V3 shadow.

Any methodological change after this freeze requires a new model or rule version.
