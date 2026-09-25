# V3 Combined Data-Coverage Challenger

## Status

Frozen and explicitly approved on 2026-09-25 for prospective shadow testing.
Approval and merge of the V3 pull request into `main` is the final code gate
before counted weekly shadow observations begin.

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

## Shadow-entry decision

All declared V3 exit gates are complete:

1. constituent liabilities and shares rules frozen;
2. combined challenger contract frozen;
3. historical combined replay completed with zero PIT violations;
4. current same-input V2-vs-V3 comparison completed;
5. deterministic replay/fingerprint verification passed;
6. shadow execution isolation verified;
7. focused V3 regression suite passed (9 tests);
8. post-V3 residual gap inventory frozen as the V4 baseline;
9. explicit human governance approval granted on 2026-09-25.

The current 2026-09-15 same-input comparison produced:
- Financial Health eligibility: 364 -> 385;
- TTM Valuation eligibility: 435 -> 493;
- Top-Conviction eligibility: 302 -> 360;
- Top-10 overlap: 10/10;
- PIT violations: 0.

The V4 starting boundary is:
- shares gaps: 61 -> 1 after V3;
- liabilities gaps: 137 -> 116 after V3;
- 117 unique residual tickers;
- zero tickers missing both fields.

Weekly V3 shadow observations run after a valid V2 shadow observation and write
to the isolated V3 namespace. They do not participate in V1 execution.

The final code gate is pull-request approval and merge into `main`. Any
methodological change after this freeze requires a new model or rule version.
