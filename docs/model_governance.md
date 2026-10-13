# Model Governance

## Champion/challenger model

The live system has one frozen **champion** model version. New model versions are **challengers** until they satisfy their predeclared validation and prospective-observation requirements.

Current champion:

`long_growth_v1`

Current challengers on `main`:

- `long_growth_v2_ttm_valuation_v1` — research-only TTM challenger;
- `long_growth_v3_data_coverage_v1` — shadow-only data-coverage challenger;
- `long_growth_v5_mom_add_v1` — frozen prospective long-growth challenger.

V4 is completed data-quality research, not a live or shadow model promotion.

## Promotion rule

A challenger is not promoted because of one successful live recommendation, one backtest, or one short prospective period.

Promotion requires, as applicable:

1. a frozen immutable model/configuration;
2. point-in-time and no-look-ahead validation;
3. walk-forward and rolling-window evidence;
4. benchmark-relative return/risk analysis;
5. turnover, concentration, rank-stability, and selection-effect review;
6. deterministic reproducibility and immutable input/code fingerprints;
7. an execution-inert prospective shadow period when required;
8. a separate explicit human promotion decision.

Code integration onto `main` is not model promotion.

## Live observations

Low-dollar live results are useful for learning about operational behavior, execution, drawdowns, confidence, and risk tolerance. They may generate hypotheses.

They do **not** directly retrain or retune any frozen model.

The research loop is:

1. Observe live/shadow behavior.
2. Form a testable hypothesis.
3. Test the hypothesis using point-in-time historical data and walk-forward validation.
4. Accept or reject the proposed model change using predeclared criteria.
5. Create a new immutable model version if warranted.
6. Run any required prospective shadow period.
7. Consider promotion only through a separate explicit decision.

## Live capital constraint

Maximum new live capital allocation remains **$10 per week**.

The live workflow requires explicit human approval. No challenger may silently inherit V1's broker/order capabilities.

## Runtime isolation on main

Model versions are isolated by:

- immutable model identifiers;
- versioned configuration;
- artifact namespaces;
- decision hashes and input fingerprints;
- runtime capability boundaries;
- registry-driven shadow orchestration.

The intended steady state is:

- `long_growth_v1` remains the only live champion;
- V2/V3/V5 may coexist on `main` as execution-inert challengers;
- only V1 may reach broker review, order-intent generation, order placement, modification, or cancellation;
- challenger runners may consume the same saved weekly point-in-time evidence but must not mutate V1 decisions or broker state;
- challenger failures may be non-blocking for V1, but failed observations do not count toward challenger governance requirements.

The generic shadow registry is:

`config/live_shadow_modes.json`

Current registered research/shadow modes are:

- `v2_ttm`;
- `v3_data_coverage` — dependent on a valid V2 observation;
- `v5_long_growth` — independent of V2/V3.

Future challengers should enter the same registry-driven infrastructure instead of adding model-specific control flow to the V1 coordinator.

## Prospective requirements

### V2

V2 requires at least eight valid weekly research-only shadow cycles before it becomes eligible for a separate promotion review.

Issue #18 tracks that requirement.

### V5

V5 requires at least eight **distinct valid prospective shadow weeks** before any future promotion review.

Historical success cannot reduce this requirement, and week 8 does not automatically promote V5.

Issue #49 tracks that requirement.

## No automatic promotion

No test result, completed shadow count, Git merge, or generated report can automatically authorize live use of a challenger.

Any future live model change requires a separate explicit human decision after the applicable evidence is reviewed.
