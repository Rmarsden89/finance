# Current Research / Live Pilot Checkpoint

Last updated: 2026-10-07

This file is the current handoff point for continuing the project in a new chat or work session.

## Executive status

`long_growth_v1` remains the frozen live champion in the dedicated Robinhood Agentic account.

Current live operating contract:

- weekly contribution hard cap: $10;
- Top 10 `top_conviction_eligible` selections;
- deterministic ticker-ascending score tiebreak;
- equal-dollar contribution across currently buyable selected names;
- 10% appreciation-only add-on threshold;
- no discretionary selling;
- explicit human approval required before live submission;
- NYSE regular-session and package-freshness gates remain authoritative;
- matched-cash-flow SPY evaluation remains separate from model/execution logic.

The normal weekly workflow is now the single resumable coordinator:

```powershell
cd C:\Repos\finance
git pull

$env:SEC_USER_AGENT="Reece Marsden rmarsden89@gmail.com"

py scripts\run_v1_live_pipeline.py --as-of YYYY-MM-DD
```

The coordinator performs V1 preparation, all registered research-only shadow modes, fresh pre-submit review, dry-run validation, the human approval checkpoint, submission/reconciliation, post-fill verification, and read-only comparison reporting.

Canonical operational runbook:

`docs/long_growth_v1_live_runbook.md`

## Current model/version state

### V1 — live champion

Model ID:

`long_growth_v1`

Frozen family weights:

- Quality: 35%
- Financial Health: 20%
- Growth: 25%
- Valuation: 20%

V1 remains the only model permitted to reach broker review, order-intent generation, placement, modification, or cancellation capability.

### V2 — research-only TTM challenger

Model ID:

`long_growth_v2_ttm_valuation_v1`

V2 passed its predeclared historical gates and remains in prospective research-only shadow observation.

Issue #18 tracks the required minimum eight valid weekly shadow observations. Historical success plus eight observations does not automatically authorize promotion.

### V3 — shadow-only data-coverage challenger

Model ID:

`long_growth_v3_data_coverage_v1`

V3 layers the frozen liabilities and shares recovery rules on the V2 foundation. It remains execution-inert and depends on a valid V2 shadow observation for the same weekly decision.

V3 is integrated into the generic shadow registry and comparison infrastructure.

### V4 — completed data-quality research

V4 residual-gap research is complete and merged to `main`.

The accepted DECK liabilities rule is documented in:

`docs/v4_deck_liabilities_rule_v1.md`

V4 did not replace the live model. Its work remains a versioned research/data-quality result.

### V5 — frozen prospective challenger

Final V5 shadow model ID:

`long_growth_v5_mom_add_v1`

Source candidate:

`V5-MOM-ADD-CAND-001`

Frozen structure:

- 95% `V5-FUND-CAND-003` score;
- 5% Momentum;
- Quality / Financial Health / Growth / Valuation all required for Top Conviction;
- Momentum required;
- Top 10 selected by score with ticker ascending as deterministic tiebreak.

Historical evaluation versus V1:

- terminal value: +7.39%;
- XIRR: +1.3278 percentage points;
- max-drawdown change: +1.56 percentage points;
- 3-year rolling XIRR win rate: 62.5%;
- 5-year rolling XIRR win rate: 66.67%;
- all frozen V5 hard gates: PASS.

Important caveat: versus the simpler `V5-FUND-CAND-003`, the 5% Momentum version had mixed incremental 3-year evidence. That complexity tradeoff must remain part of any future promotion review.

V5 is integrated into `config/live_shadow_modes.json` as `v5_long_growth`. It is non-blocking, execution-inert, and bound to the exact saved V1 decision hash.

Prospective requirement:

- minimum 8 distinct valid V5 shadow weeks;
- historical performance cannot shorten the requirement;
- no automatic promotion occurs after week 8;
- any future live use requires a separate explicit human decision.

Issue #49 tracks the V5 monitoring period.

## Current registered shadow workflow

The weekly coordinator reads:

`config/live_shadow_modes.json`

Current modes:

1. `v2_ttm`
2. `v3_data_coverage` — depends on `v2_ttm`
3. `v5_long_growth` — independent of V2/V3

All registered shadows are research-only. Their failures are non-blocking with respect to an otherwise valid V1 live workflow, but failed observations do not count toward their own prospective requirements.

The generic comparison/reporting infrastructure automatically consumes registered decision artifacts for:

- Top-10 comparison;
- pairwise overlap;
- qualifying-week counts;
- selection attribution where supported;
- prospective execution-price capture;
- matched-cash-flow shadow portfolios;
- SPY-relative evaluation.

## Current open-program state

There is no active model-development backlog at this checkpoint.

The open GitHub issues are monitoring/governance issues:

- #9 — V2 program plan / promotion governance;
- #18 — V2 eight-week shadow observation requirement;
- #43 — V5 prospective challenger monitoring parent;
- #49 — V5 eight-week prospective shadow observation period.

The active work is therefore operational observation rather than additional tuning:

1. run the normal weekly pipeline;
2. verify V1 completes safely;
3. verify V2/V3/V5 shadow artifacts complete and remain PIT-valid;
4. preserve decision hashes, entrants/exits, comparison evidence, and execution-price captures;
5. allow forward-return and matched-cash-flow portfolio evidence to mature;
6. do not retune challengers from a few prospective weeks;
7. perform explicit post-shadow review only after the required observation count is satisfied.

## Governance boundary

A code merge is not model promotion.

V1 remains frozen and live until a separate explicit promotion decision is made.

Do not change V1 factor definitions, family weights, family minimums, Top-N breadth, allocation logic, add-on threshold, or execution behavior as a reaction to short-term performance.

Any model-rule change requires a new immutable challenger/version, predeclared evaluation criteria, point-in-time validation, and its own governance decision.
