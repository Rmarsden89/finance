
## Current-week shadow status — 2026-09-09

The first end-to-end current `long_growth_v1` shadow score has completed.

Current production-path data roles are now:

- PITIndex: current S&P 500 universe and identity seed;
- SEC quarterly Financial Statement Data Set ZIPs: historical archive,
  deterministic rebuild source, and reconciliation checkpoint;
- SEC submissions/companyfacts/filing-header evidence: incremental current
  fundamentals, merged only through the conservative shadow path;
- Robinhood: current timestamped raw market prices and, later, broker/account
  state and execution;
- Tiingo/Stooq: historical research/backtest price sources only, not current
  production market-price inputs.

2026-09-09 current shadow results:

- 501 model-universe rows;
- 499 current rows with usable fundamentals;
- 499 valid Robinhood current prices;
- 497 research-ready rows;
- 434 rows with a `long_growth_v1` score;
- 218 `top_conviction_eligible` rows with all four required families.

Current Top 10 by frozen `long_growth_v1` score:

1. PTC
2. MU
3. NEM
4. TROW
5. GEN
6. ADBE
7. NVDA
8. STE
9. NTAP
10. PLTR

The current scoring path has been demonstrated end to end. The immediate
validation task is family-coverage diagnosis, especially Financial Health and
Valuation, before portfolio allocation/execution is enabled. Do not change
frozen factor definitions or family minimums to improve coverage.


# Current Research Checkpoint

Last updated: 2026-09-09

This file is the handoff point for continuing the project in a new chat or work session.

## Current champion candidate

Frozen model:

- `long_growth_v1`
- Family weights:
  - Quality 35%
  - Financial Health 20%
  - Growth 25%
  - Valuation 20%
- Missing available family weights are proportionally reweighted.
- `top_conviction_eligible` requires all four families.
- Model definition remains immutable; recent work has only tested portfolio construction and data quality.

Current leading portfolio construction:

- Top 10 `top_conviction_eligible` names by `long_growth_v1_score`
- $10 weekly contribution
- Equal-dollar allocation across currently buyable names
- 10% appreciation-only add-on threshold:
  - if current position weight is >= 10%, do not add new money
  - never sell merely because a winner appreciates above 10%
  - if the position later falls below 10%, it becomes buyable again automatically
- No Stability guardrail
- No discretionary selling
- Forced exits only when the PIT investable-universe/data boundary requires them

## Portfolio validation completed

### Initial full-period accumulation backtest, 2016-2025

Top-5 `long_growth_v1`:

- contributions: $5,220
- ending value: about $18,523
- XIRR: about 24.1%
- annualized TWR: about 20.6%
- max drawdown: about 35.8%

Top-5 `full_growth_v1` challenger:

- ending value: about $17,402
- XIRR: about 23.0%
- annualized TWR: about 20.3%
- max drawdown: about 37.8%

VOO:

- ending value: about $11,782
- XIRR: about 15.7%
- annualized TWR: about 14.9%
- max drawdown: about 33.7%

Conclusion: keep `long_growth_v1` as champion; Stability + Momentum did not earn positive composite weights.

### Top-N sensitivity

`long_growth_v1` full-period results:

- Top 1: about $16,065, 21.5% XIRR
- Top 3: about $18,877, 24.5% XIRR
- Top 5: about $18,523, 24.1% XIRR
- Top 10: about $19,472, 25.1% XIRR

Top 10 became the leading portfolio breadth.

### Concentration robustness

Top-10 baseline:

- ending value: about $19,472
- XIRR: about 25.1%

Profit concentration was meaningful but capital allocation was not dominated by one stock.

Approximate gain shares:

- NVDA: 37.8%
- top 3 winners: 58.3%
- top 5 winners: 70.0%

Historical concentration:

- median largest position: about 9.7%
- maximum largest position: about 33%
- ending largest position: about 29%
- ending top-3 concentration: about 45.5%
- ending top-5 concentration: about 56.1%

Leave-winner-out tests degraded gradually rather than collapsing:

- exclude NVDA: about $14,295, 19.3% XIRR
- exclude top 3 historical winners: about 15.8% XIRR
- exclude top 5 historical winners: about 14.3% XIRR

Interpretation: the ranking signal is broader than one lucky pick, but exceptional winners materially amplify excess return.


### Scaling note: equal-dollar is a V1 portfolio rule, not a broker minimum

The current V1 shadow planner allocates the weekly contribution equally across
all buyable Top-10 names. For a $10 weekly contribution and 10 buyable names,
that produces $1 per name.

This equal-dollar behavior is intentional and comes from the validated V1
portfolio-construction baseline. It is **not** being used merely because the
broker requires a $1 minimum order. Rank 1 and rank 10 therefore receive the
same new-money allocation in V1 as long as both remain buyable under the 10%
appreciation-only add-on rule.

As the pilot scales, alternative allocation rules may be evaluated separately,
for example rank-weighted, score-weighted, or conviction-banded allocations.
Those alternatives must not silently replace V1. They require separate
walk-forward/backtest comparison, concentration analysis, turnover review, and
shadow validation before promotion.

The current micro-stakes phase should preserve equal-dollar allocation so that
live operational behavior is compared against the portfolio construction that
was already validated.

### 10% appreciation-only add-on threshold

The engine now distinguishes:

- `max_position_weight`: earlier allocation-cap experiment
- `max_addon_position_weight`: current appreciation-only rule

Re-entry is explicitly tracked with trade reason:

`position_cap_reentry`

10% threshold full-period result was nearly identical to uncapped Top 10:

- uncapped: about $19,471.73, 25.05% XIRR
- 10% add-on threshold: about $19,442.91, 25.03% XIRR
- 153 blocked purchases
- 19 re-entry purchases

Conclusion: 10% add-on threshold is the current leading concentration control.

### Rolling-window robustness

Fresh-start rolling windows:

- 3-year windows: 2016-2018 through 2023-2025
- 5-year windows: 2016-2020 through 2021-2025

Against VOO:

- 3-year windows: Long Growth beat VOO in 6 of 8
- 5-year windows: Long Growth beat VOO in 5 of 6

Average XIRR edge versus VOO:

- 3-year: roughly +2.8 percentage points
- 5-year: roughly +3.8 percentage points

The 10% add-on threshold remained effectively costless across rolling windows.

### Rank persistence / turnover

Across 522 decision weeks:

- average week-to-week Top-10 overlap: 87.6%
- median overlap: 90%
- average replacement rate: 12.4%
- 183 weeks had zero replacements

Next-week persistence:

- ranks 1-3: about 93.2%
- ranks 4-5: about 93.6%
- ranks 6-10: about 81.9%

Examples of durable Top-10 membership:

- TROW: 400 weeks
- VRTX: 255
- NVDA: 254
- AMAT: 219
- LRCX: 211

Actual purchase persistence:

- 86.9% of buy dollars went to tickers with at least 26 Top-10 weeks
- 77.0% went to tickers with at least 52 Top-10 weeks

Conclusion: ranking behavior is persistent enough for a long-term accumulation strategy.

## Turnover-spike investigation

Large Top-10 changes clustered around filing-season periods, especially May.

Root-cause audit found:

- no evidence of family-coverage collapse
- no meaningful eligibility-flip artifact
- no random score instability
- Growth was the largest family mover, followed by Quality
- batches of companies received new underlying fundamental values, causing legitimate cross-sectional percentile reordering

The prior weekly panel did not carry exact accepted/filed lineage for generic current fundamentals, so exact filing-to-factor traceability was incomplete.

## SEC lineage patch

Completed in `src/finance/data/sec_snapshot.py`.

`pivot_snapshot()` now preserves per-concept PIT provenance alongside existing value columns.

For a concept such as revenue, the weekly panel can now carry:

- `revenue`
- `revenue_period_date`
- `revenue_filing_period_date`
- `revenue_filed_date`
- `revenue_accepted_at`
- `revenue_form`
- `revenue_fy`
- `revenue_fp`
- `revenue_qtrs`
- `revenue_source_tag`
- `revenue_adsh`

Equivalent provenance is carried for other current SEC concepts when source columns are available.

This is an auditability-only patch. It does not alter winner selection, factor formulas, normalization, model weights, or eligibility.

The turnover-spike audit was also updated to detect concept-level `*_accepted_at` and `*_filed_date` fields.

## Refreshed Tiingo / canonical market baseline

Priority Tiingo acquisition is complete.

Canonical market validation after rebuild:

- coverage rows: 754
- materialized price rows: 1,312,685
- tickers with price rows: 685
- Tiingo selected: 685
- Stooq selected: 0
- unresolved: 69
- structural validation: PASS, all checks zero

Previous baseline was roughly:

- Tiingo: 493
- Stooq: 188
- unresolved: 73
- covered: 681

The priority work mostly replaced Stooq fallback / partial coverage with full Tiingo coverage rather than dramatically increasing the number of covered tickers.

Important implication: overall coverage changed only slightly, but return-basis consistency changed materially because former Stooq names now use Tiingo adjusted-close data.

Known market-data limitation still worth remembering: earlier V1 work identified a PARA adjusted-return quality issue for review. Do not assume all adjusted-price history is perfect merely because structural validation passes.

## Refreshed weekly panel

The weekly PIT panel has already been rebuilt after both:

1. Tiingo/canonical refresh
2. SEC current-fact lineage patch

Current output:

`reports/weekly_research_panel_2015_2025.csv`

Audit:

- decision weeks: 574
- panel rows: 288,655
- average members/week: 502.9
- identity-resolved rows: 288,655
- price-available rows: 272,407
- fundamentals-available rows: 288,004
- research-ready rows: 272,165

Approximate panel rates:

- price available: 94.37%
- research ready: 94.29%

## Tiingo / SEC rebuild validation: CLOSED

The frozen V1 pipeline was rebuilt from the refreshed weekly panel in the required order:

```text
weekly panel
-> raw factors
-> normalized factors
-> family scores
-> long_growth_v1
```

The rebuild validation is complete. No factor weights, thresholds, model definitions, or eligibility rules were changed.

### Refreshed frozen-model structure

Key refreshed audit results:

- raw/factor rows: 288,655
- Quality family coverage: about 98.52%
- Financial Health family coverage: about 66.96%
- Growth family coverage: about 86.52% overall, including the intentional 2015 warm-up year
- Valuation family coverage: about 55.03%
- Stability family coverage: about 85.13%
- Momentum family coverage: about 88.30%
- normalized scores outside 0-100: 0
- `long_growth_v1` score coverage: about 77.14%
- full four-family / top-conviction coverage: about 33.35%
- evaluation-eligible coverage: about 74.55%

The known family-coverage pattern remains intact:

- Quality remains near-full coverage.
- Financial Health remains the principal core-family limitation.
- Growth is intentionally unavailable in 2015 because the one-year lookback does not yet exist, then returns to roughly 93-96% annual coverage from 2016 onward.
- Valuation remains lower-coverage because the frozen validation rules reject stale or scale-inconsistent observations rather than manufacturing coverage.

### Refreshed rank persistence

Across the same 522 decision weeks:

- mean week-to-week Top-10 overlap: about 87.79%
- median overlap: 90%
- mean replacement rate: about 12.21%
- 186 weeks had zero replacements

Next-week persistence:

- ranks 1-3: about 93.47%
- ranks 4-5: about 92.80%
- ranks 6-10: about 82.38%

These are effectively unchanged from the frozen baseline.

Examples of refreshed durable Top-10 membership:

- TROW: 391 weeks
- VRTX: 255
- NVDA: 255
- AMAT: 223
- LRCX: 213

### Refreshed purchase persistence

Using the refreshed appreciation-cap trade log:

- buy transactions: 5,064
- buy dollars: $5,220
- unique bought tickers: 151
- about 85.51% of buy dollars went to tickers with at least 26 Top-10 weeks
- about 76.17% went to tickers with at least 52 Top-10 weeks

Pre-refresh values were approximately 86.93% and 77.03%, respectively. The small decline is consistent with minor rank movement after the market-data refresh and does not change the conclusion that most deployed capital went to durable Top-10 names.

### Refreshed champion portfolio

The refreshed Tiingo baseline modestly increased absolute results but did not change relative strategy conclusions.

Full-period Top-10 results:

- uncapped: about $19,663.52, 25.24% XIRR
- 10% appreciation-only add-on threshold: about $19,632.82, 25.21% XIRR

Earlier frozen baseline:

- uncapped: about $19,471.73, 25.05% XIRR
- 10% add-on threshold: about $19,442.91, 25.03% XIRR

The relative effect of the 10% add-on threshold remains effectively unchanged.

### Turnover-spike / SEC provenance validation

The four major turnover-spike weeks remained unchanged after the Tiingo rebuild:

- 2020-05-08
- 2022-05-06
- 2023-05-05
- 2025-05-09

The same entrant/exit counts and ticker transitions remained present.

The SEC lineage patch materially improved auditability. The refreshed audit now observes concept-level filing and period provenance changes alongside the fundamental changes behind the turnover events.

Interpretation:

- the turnover spikes are not Tiingo artifacts;
- they are consistent with legitimate batches of new SEC fundamental information entering the PIT panel;
- the refreshed lineage evidence strengthens the prior root-cause conclusion.

### Rebuild conclusion

Validation status: **PASS WITH MINOR NOTES**

The Tiingo / SEC rebuild changed individual price-sensitive observations and modestly changed absolute portfolio results, but it did not materially change:

- factor coverage structure
- normalization behavior
- family behavior
- `long_growth_v1` model behavior
- Top-10 rank persistence
- turnover characteristics
- actual purchase persistence
- portfolio-construction conclusions
- identified SEC-driven turnover events

The refreshed dataset is now the canonical V1 research baseline.

## Robinhood readiness path

The research model is far enough along to begin an execution-readiness phase, but historical backtest validation alone is not sufficient to authorize live automated trading.

The next work should focus on proving that the frozen model can operate correctly on current data, produce deterministic weekly decisions, and survive broker/execution edge cases before real money is exposed.

### Gate 1 - Current-data production pipeline

Build and validate a current weekly production path that can reproduce the research logic without relying on historical backtest shortcuts.

Required flow:

```text
current PIT universe
-> current SEC shadow/fundamental snapshot
-> current Robinhood raw market prices
-> raw factors
-> normalized factors
-> family scores
-> long_growth_v1
-> Top-10 eligible ranking
-> current portfolio-aware buy plan
```

Requirements:

- same frozen factor and model definitions as research
- reproducible outputs for the same as-of timestamp
- explicit data freshness checks
- explicit missing-data / stale-data failure behavior
- no look-ahead inputs
- artifact/log retention for every weekly decision

### Gate 2 - Shadow mode

Run the production pipeline on schedule without placing trades.

Minimum goals:

- record the weekly Top-10
- record eligible/buyable names
- calculate the 10% appreciation-only add-on rule from the actual shadow portfolio
- record intended dollar allocation
- record prices available at decision time
- record any data-quality failures or skipped decisions
- compare subsequent shadow behavior with the research assumptions

Do not change model weights or thresholds in response to short-term shadow performance.

A useful initial target is several consecutive clean weekly cycles with no unexplained decision changes, stale-data use, or execution-plan errors.

### Gate 3 - Broker execution contract

Before automation touches Robinhood, define the broker-facing execution contract independently from the model.

The execution layer must specify:

- fractional-share support and minimum order sizing
- market-order versus limit-order policy
- when during the trading day orders may be submitted
- treatment of market holidays and shortened sessions
- treatment of rejected, canceled, partially filled, or delayed orders
- duplicate-order prevention / idempotency
- cash-available checks
- buying-power checks
- symbol/corporate-action handling
- reconciliation between intended holdings and broker-reported holdings
- manual kill switch

The research model should output desired actions; broker-specific code should not be allowed to alter model rankings or silently improvise portfolio rules.

### Gate 4 - Portfolio state and reconciliation

Create a persistent portfolio ledger that can be reconciled against Robinhood.

At minimum track:

- ticker
- shares
- average cost / tax-lot information available from the broker
- current market value
- portfolio weight
- cash
- pending orders
- last model decision
- trade reason
- whether a purchase was blocked by `max_addon_position_weight`
- whether a prior blocked position later qualified for `position_cap_reentry`

Every run should reconcile internal state with broker state before generating new orders.

If reconciliation fails materially, the safe behavior is to generate no new orders.

### Gate 5 - Forced-exit policy for live investing

The historical backtest contains forced exits when the PIT investable-universe/data boundary requires them. A live account needs an explicit policy for what those exits mean operationally.

Before live trading, define whether a held company is sold when it:

- leaves the S&P 500
- loses current data coverage
- becomes temporarily unscorable
- becomes permanently ineligible
- is acquired or delisted
- undergoes a ticker or corporate-action change

This is especially important because the current strategy otherwise has no discretionary selling.

The live policy must be frozen before evaluating live results.

### Gate 6 - Micro-stakes pilot

After shadow mode and execution validation pass, begin with the intentionally small pilot size rather than scaling immediately.

Current intended pilot:

- approximately $5-$10 per week
- long-term accumulation
- no day trading
- frozen `long_growth_v1`
- Top 10
- 10% appreciation-only add-on threshold

Initial live goals are operational, not performance-maximizing:

- correct model decision
- correct intended order
- correct broker execution
- correct reconciliation
- no duplicate or unintended orders
- understandable logs
- easy manual intervention

### Gate 7 - Live review framework

Track model and execution performance separately.

Model monitoring:

- weekly Top-10
- rank persistence
- family/model coverage
- concentration
- benchmark-relative performance
- realized versus backtest-like behavior

Execution monitoring:

- intended versus filled dollars
- fill price / slippage
- rejected orders
- delayed or partial fills
- broker/model position mismatches
- cash drift
- duplicate-order incidents
- manual overrides

A broker/execution problem must not be misdiagnosed as a model problem.

## Robinhood-specific implementation note

As of 2026-09-08, Robinhood supports Agentic Trading through a dedicated Agentic account. Connected agents can inspect portfolio state, positions, tax lots, tradability, quotes, order history, review equity orders, place equity orders, and cancel equity orders.

Important distinction:

- do not design the project around unofficial automation against a normal Robinhood brokerage account;
- Robinhood's standard third-party policy does not permit ordinary trading APIs or third-party applications to control a normal account without written authorization;
- if full automation is pursued, the supported path to evaluate is a dedicated Robinhood Agentic account;
- a manual human-in-the-loop micro-stakes pilot can begin before broker automation is complete.

Recommended staged live path:

1. production model generates a deterministic weekly recommendation artifact;
2. user reviews the artifact and manually places the $5-$10 Robinhood purchase;
3. internal portfolio ledger records the intended and actual trade;
4. repeat until current-data/shadow behavior is trusted;
5. separately evaluate Robinhood Agentic Trading for automated execution;
6. do not enable unattended order placement until reconciliation, idempotency, and kill-switch controls have passed.

This separation lets live model validation begin without making broker automation a prerequisite.

## Shadow-mode implementation status

The first broker-neutral shadow component is now implemented.

New files:

- `src/finance/shadow/decision.py`
- `src/finance/shadow/__init__.py`
- `scripts/build_shadow_decision.py`
- `tests/test_shadow_decision.py`

The shadow decision planner:

- consumes frozen `long_growth_v1` output;
- selects the latest eligible decision date on or before the requested as-of date;
- fails closed when signals are stale;
- ranks Top 10 deterministically with ticker as an explicit score-tie tiebreak;
- accepts optional current portfolio state as `ticker,market_value`;
- applies the same 10% appreciation-only add-on rule used by the champion backtest;
- redistributes the weekly contribution equally across currently buyable Top-10 names;
- emits CSV and JSON decision artifacts;
- emits a SHA-256 decision hash so repeated runs can be checked for determinism;
- does not connect to a broker and cannot place orders.

Initial tests cover:

- equal allocation for an empty portfolio;
- blocking a position already at or above the 10% threshold;
- automatic buyability when a position is below the threshold again;
- stale-signal fail-closed behavior;
- deterministic decision hashing.

## Immediate next task

Validate the new shadow planner locally, then extend the canonical inputs through the current 2026 decision date.

### Step 1 - local planner validation

```powershell
cd C:\Repos\finance
git pull
pytest tests\test_shadow_decision.py
```

For an empty initial shadow portfolio, once `long_growth_v1.csv` contains a current signal week:

```powershell
py scripts\build_shadow_decision.py `
  --long-growth "reports\long_growth_v1.csv" `
  --as-of YYYY-MM-DD `
  --weekly-contribution 10 `
  --top-n 10 `
  --max-addon-position-weight 0.10 `
  --output-dir "reports\shadow\YYYY-MM-DD"
```

For a non-empty shadow/manual portfolio, provide a CSV containing:

```text
ticker,market_value
AAA,12.34
BBB,8.91
```

and pass it with `--portfolio-state`.

### Step 2 - extend current market data

The existing Tiingo PIT coverage tooling already defaults `--end-year` to the current year and uses `TIINGO_API_TOKEN`. The next production-data patch should make the weekly refresh incremental rather than re-downloading full histories for every current constituent.

The current canonical market build should then be regenerated through the current year and validated before scoring.

### Step 3 - extend current SEC data

The SEC winner-fact pipeline is already incremental once quarterly SEC ZIPs exist locally. The missing production piece is a controlled acquisition/update step for newly available SEC quarterly data, followed by:

```text
SEC quarter update
-> incremental winner facts
-> current PIT weekly panel
-> frozen raw factors
-> frozen normalization
-> frozen family scores
-> frozen long_growth_v1
```

### Step 4 - first true current-week shadow decision

Current SEC + Robinhood inputs are now validated for shadow scoring.

1. rebuild through the current decision week;
2. run `build_shadow_decision.py`;
3. save the decision JSON/CSV under a date-specific folder;
4. rerun the same command and verify the decision hash is identical;
5. manually review the Top-10, coverage/freshness state, blocked names, and allocations;
6. do not place automated orders.

The first successful current-week artifact begins the shadow-mode observation period.

## Governance reminder

Do not tune factor weights, thresholds, or model definitions in response to this data refresh.

The current exercise is a frozen-model validation against:

- improved Tiingo return-price consistency
- slightly improved market coverage
- improved SEC provenance/auditability

Any material performance change should first be explained as a data effect before proposing a new model version.
