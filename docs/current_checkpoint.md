# Current Research / Live Pilot Checkpoint

Last updated: 2026-09-21

This file is the handoff point for continuing the project in a new chat or work session.

## Executive status

`long_growth_v1` has moved from research/shadow readiness into a successful micro-stakes live pilot on the dedicated Robinhood Agentic account.

The first live weekly cycle completed on 2026-09-15 after two receipt-shape bugs were identified and patched during reconciliation. The trades appeared correctly in Robinhood and the final workflow reconciled to the broker account.

Current state:

- model: `long_growth_v1`
- model definition: frozen
- weekly contribution hard cap: $10
- portfolio breadth: Top 10 `top_conviction_eligible`
- allocation: equal-dollar across currently buyable names
- concentration rule: 10% appreciation-only add-on threshold
- discretionary selling: disabled
- broker: Robinhood dedicated Agentic account
- execution mode: deterministic Python with explicit human `--approve`
- first live cycle: completed successfully
- second live cycle: completed on the happy path with no recovery/errors
- live evaluation: matched-cash-flow SPY benchmark, with intraday SPY capture for future runs
- evaluation package: generated per completed run for reproducible analysis/dashboarding
- canonical operational runbook: `docs/long_growth_v1_live_runbook.md`

The current priority is **observe and validate V1 over additional weekly live cycles**, while any V2 work remains a separate research track and must not silently modify V1.

## Frozen V1 model

Family weights:

- Quality: 35%
- Financial Health: 20%
- Growth: 25%
- Valuation: 20%

Rules:

- missing available family weights are proportionally reweighted;
- `top_conviction_eligible` requires all four core families;
- Stability and Momentum are not part of the champion composite;
- factor definitions, family minimums, normalization rules, eligibility rules, and model weights remain frozen for V1.

## Frozen V1 portfolio construction

Current live portfolio construction:

- rank `top_conviction_eligible` names by `long_growth_v1_score` descending;
- ticker ascending is the deterministic score-tie tiebreak;
- select Top 10;
- contribute no more than $10 per weekly cycle;
- split new contribution equally across currently buyable selected names;
- if a current position is already at or above 10% of portfolio value, do not add new money to that position;
- do not sell merely because appreciation pushes a position above 10%;
- a position becomes buyable again automatically if its weight later falls below 10%;
- no discretionary selling;
- forced exits remain a separate policy boundary.

Equal-dollar allocation is an intentional V1 portfolio rule, not merely a broker-minimum workaround.

## Research validation already completed

The refreshed Tiingo / SEC research baseline remains the canonical historical V1 baseline.

Key research conclusions remain unchanged:

- Top 10 remained the strongest tested breadth among Top 1 / 3 / 5 / 10 variants;
- the 10% appreciation-only add-on rule was effectively costless versus uncapped Top 10 in the historical tests;
- ranking persistence was strong enough for a long-term accumulation strategy;
- Stability + Momentum did not justify positive champion composite weights;
- turnover spikes were consistent with batches of legitimate new SEC fundamental information rather than random score instability;
- refreshed Tiingo coverage modestly changed absolute results but did not change the model-selection conclusion.

Refreshed full-period Top-10 results from the canonical research baseline:

- uncapped: about $19,663.52 ending value, 25.24% XIRR;
- 10% appreciation-only add-on threshold: about $19,632.82 ending value, 25.21% XIRR.

The historical research results are evidence for model behavior, not a forecast of future returns.

## Current production data architecture

Production/current weekly path:

```text
PITIndex current S&P 500 universe
-> SEC submissions + CompanyFacts + filing-header evidence
-> conservative SEC shadow winner merge
-> Robinhood current market/account snapshot
-> current scoring panel
-> raw factors
-> normalized factors
-> family scores
-> long_growth_v1
-> deterministic Top-10 decision
-> execution gate
-> order intents
-> fresh pre-submit snapshot + Robinhood order reviews
-> explicit human approval
-> NYSE session gate
-> read-only intraday SPY benchmark capture
-> Robinhood placement
-> receipt reconciliation
-> post-fill broker refresh
-> portfolio reconciliation
-> evaluation package
```

Data roles:

- PITIndex: current universe and identity seed;
- SEC quarterly Financial Statement Data Set ZIPs: historical archive / deterministic research baseline;
- SEC submissions, CompanyFacts, and filing headers: incremental current-fundamental path;
- Robinhood: live prices, account state, positions, buying power, tradability, reviews, execution, and broker reconciliation;
- Tiingo / Stooq: historical research/backtest price sources only, not current production market inputs.

## 2026-09-15 first live cycle

The first live run used a $10 contribution and generated 10 $1 market-buy intents.

Decision hash:

`584a64beff095bcd7a86078942b6e89399fde474b78b271ae9244d3c3b62c691`

Selected names:

1. MU
2. WDC
3. NEM
4. INTU
5. PLTR
6. NVDA
7. TPR
8. AVGO
9. TROW
10. TER

Pre-submit result:

- status: `AWAITING_APPROVAL`
- fresh snapshot age: 0.00 minutes at review completion;
- buying power: $100;
- tradable intents: 10/10;
- Robinhood reviews clean: 10/10;
- no orders placed by the pre-submit command.

Dry-run submission package:

- order count: 10;
- total dollars: $10;
- decision hash unchanged;
- all reviews clean;
- no orders placed by dry-run mode.

Approved submission:

- all 10 placement calls returned broker data;
- initial receipt reconciliation failed because the placement response stored the broker order inside a nested `order` object while the reconciler expected top-level order fields;
- blind retry correctly remained blocked;
- `recover_v1_submission.py` re-read the saved receipt without placing orders and reconciled 10/10 accepted orders;
- the post-fill verifier then required a second patch because it also expected top-level broker order IDs;
- after that patch, the existing orders were refreshed by exact broker order ID and the account state reconciled successfully.

The user independently confirmed that the positions appeared in the Robinhood Agentic account.

## Live failure patches learned from the first cycle

### SEC transient 503 recovery

During the 2026-09-15 preparation run, SEC filing discovery returned four `new_filing_partial` rows because filing-header requests returned HTTP 503 for:

- LII
- PKG
- PODD
- VRTX

CompanyFacts had cached successfully for those names. The workflow correctly failed closed and did not create order intents.

Recovery pattern:

1. retry only affected tickers with `scripts/update_sec_current.py --ticker ...`;
2. verify all targeted rows become `new_filing_cached`;
3. merge successful retry rows into the full discovery report;
4. rebuild SEC current candidates;
5. rebuild the SEC shadow winner cache;
6. resume `run_v1_prepare.py` with `--skip-sec-refresh --skip-tests` only after the SEC evidence is complete.

Do not use `--skip-sec-refresh` merely to bypass unresolved SEC errors.

### Submission receipt recovery

Robinhood placement responses may wrap the actual equity order under `data.order`.

The receipt reconciler now normalizes both:

- direct order rows;
- nested placement-response order envelopes.

If an approved submission ends in `SUBMISSION_REQUIRES_RECONCILIATION`, never rerun `--approve` blindly.

Use:

```powershell
py scripts\recover_v1_submission.py `
  --as-of YYYY-MM-DD
```

This command is read-only with respect to order placement. It only reconciles the existing saved receipt.

### Post-fill exact-order recovery

`run_v1_postfill.py` now extracts broker order IDs from either direct or nested receipt rows and refreshes the exact submitted orders before portfolio reconciliation.

It does not place orders and is safe to rerun while waiting for fills.


## V1 live evaluation

V1 evaluation is intentionally separate from the frozen model and execution logic.

Primary benchmark:

- SPY;
- matched cash flow: each weekly V1 deployed contribution is mirrored into a synthetic SPY benchmark;
- future live runs use a read-only Robinhood SPY quote captured immediately before approved V1 order placement;
- historical runs that predate intraday capture may use the documented same-date daily adjusted-close fallback.

Each completed live run produces or can be backfilled with:

```text
reports\shadow\YYYY-MM-DD\evaluation_package.json
```

The package is the stable evaluation contract for current reporting and future dashboarding. It contains run/decision provenance, deployed contribution, post-fill marked value, selected names and fill metadata, benchmark capture when available, and artifact hashes. It has no broker/order capability.

The aggregate evaluator writes under:

```text
reports\v1_evaluation\
```

Canonical evaluation documentation:

```text
docs\v1_performance_evaluation.md
```

After two live cycles, the evaluation framework is operational. Current live results are monitoring evidence only; they are not yet sufficient to conclude that V1 outperforms SPY.

## Canonical weekly live run

Use `docs/long_growth_v1_live_runbook.md` as the authoritative operational runbook.

Normal happy path:

```powershell
cd C:\Repos\finance
git pull

$env:SEC_USER_AGENT="Reece Marsden rmarsden89@gmail.com"

py scripts\run_v1_prepare.py `
  --as-of YYYY-MM-DD

py scripts\run_v1_presubmit.py `
  --as-of YYYY-MM-DD

py scripts\run_v1_submit.py `
  --as-of YYYY-MM-DD

py scripts\run_v1_submit.py `
  --as-of YYYY-MM-DD `
  --approve

py scripts\run_v1_postfill.py `
  --as-of YYYY-MM-DD
```

Required stage outcomes:

- prep: `READY_FOR_PRESUBMIT_REFRESH`
- pre-submit: `AWAITING_APPROVAL`
- dry-run submit: confirms package only, no placement
- approved submit: placement and reconciliation only after explicit approval
- post-fill: desired final status `COMPLETE`

Safety rules:

- use the current trading day's date;
- approved submission only during the intended regular market session;
- pre-submit package must be no more than 5 minutes old;
- stop on any failed gate;
- never manually bypass stale, missing, ambiguous, or reconciliation failures;
- never blindly repeat an approved submission after Robinhood may have received an order;
- post-fill and recovery commands must remain non-placement paths.

## Important implementation notes

### Direct Robinhood MCP

The production integration uses Robinhood's Agentic Trading MCP through the project's deterministic Python gateway.

Current behavior includes:

- exact Agentic account selection;
- direct account, portfolio, position, order, quote, and tradability calls;
- persistent authenticated MCP session;
- pre-submit `review_equity_order` simulation;
- explicit `place_equity_order` only inside the approved submission command;
- artifact retention for decision, broker snapshots, reviews, receipts, reconciliation, and run log.

A cosmetic `Session termination failed: 400` message may still appear after successful MCP calls. It has not prevented successful requests and is not currently treated as a trading failure.

### Safety-test command

Use Python module invocation rather than relying on a globally installed `pytest` executable:

```powershell
py -m pytest ...
```

The 2026-09-15 preparation run executed the current safety suite successfully with 45 passing tests before the SEC refresh stage.

## Known operational follow-ups

The first live cycle surfaced and fixed two Robinhood receipt-shape assumptions. Continue to watch future live cycles for any additional provider-shape variation.

Remaining operational/data-quality work worth tracking separately includes:

- current SEC coverage / `shares_outstanding` gaps;
- transient SEC retry ergonomics so a small number of 503s do not require manual report merging;
- Robinhood pagination / provider-response edge cases;
- cosmetic MCP session-close warning;
- holiday / shortened-session calendar hardening beyond basic date/weekend protections;
- clearer automated final summary after `COMPLETE`.

None of these should silently alter frozen V1 model logic.

## V1 observation plan

The first live run is primarily an operational validation milestone, not evidence that the model should already be changed.

For the next several weekly cycles, record:

- selected Top 10 and decision hash;
- current family/model coverage;
- names blocked by the 10% add-on rule;
- intended versus filled dollars;
- broker order states / fill timing;
- post-fill portfolio reconciliation;
- any SEC, Robinhood, or stale-data failures;
- any manual intervention required.

Do not tune V1 in response to a few live weeks of performance.

## V2 research track

V2 work may proceed in parallel, but V1 stays frozen while the pilot continues.

V2 should start by addressing documented known limitations and data-quality issues rather than immediately changing weights based on short-term returns.

Potential V2 research categories include:

- better handling of incomplete current fundamental coverage;
- shares-outstanding / valuation coverage improvements;
- explicitly tested alternative allocation methods such as rank-weighted, score-weighted, or conviction bands;
- additional risk / concentration controls only if validated separately;
- any factor or family changes only after full walk-forward and robustness testing.

Any V2 candidate must have its own model/version name and validation artifacts. It must not silently replace `long_growth_v1`.

## Immediate next tasks

1. Run additional weekly V1 live cycles using the canonical runbook.
2. Confirm the patched receipt and post-fill paths work without intervention on the next live cycle.
3. Improve SEC transient-retry automation so targeted recovery can be handled more cleanly.
4. Continue the non-blocking SEC `shares_outstanding` data-quality investigation.
5. Begin V2 research only as a separate branch/version focused first on documented known issues.

## Governance reminder

V1 remains frozen.

Do not change factor weights, factor definitions, family minimums, eligibility rules, portfolio breadth, add-on threshold, or live allocation logic as an ad-hoc response to weekly performance or one-off execution issues.

Execution bugs, provider-response bugs, and data-quality defects should be fixed in the execution/data layers while preserving the frozen model unless a separately validated V2 explicitly changes the model.
