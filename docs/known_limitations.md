# Known Limitations

This document records accepted limitations, resolved/remediated limitations, and
known governance constraints for the current unified research/live codebase.

A limitation is not considered fixed merely because a later data source or
research path exists. It moves to resolved/remediated only after the relevant
canonical dataset or model path has been rebuilt, validated, and reviewed.

Status labels used below:

- **Resolved/remediated** — the specific defect or uncertainty was materially
  reduced and the validated replacement is now the canonical research baseline.
- **Characterized residual** — the limitation remains, but the remaining cases
  have been investigated and classified rather than left unexplained.
- **Accepted/open** — the limitation still constrains interpretation or
  operation and remains intentionally in place.

## Canonical historical market data

**Status: resolved/remediated with characterized residuals**

Issues #7 and #19 re-audited the 2015-2025 PIT S&P 500 market-data baseline and
promoted the validated candidate to the canonical research dataset.

Current canonical provider selection:

- 754 PIT tickers in the audited universe;
- 690 Tiingo-selected tickers;
- 0 Stooq-selected tickers;
- 64 unresolved tickers;
- 1,322,131 canonical daily price rows;
- 99,954 unresolved PIT membership-days;
- 4.947% unresolved PIT membership-day exposure.

Compared with the prior baseline, the promoted dataset recovered 13,718
membership-days across five PIT tickers:

- BF.B
- BRK.B
- DXC
- BHGE
- WYND

The weekly research panel gained:

- 1,960 price-available rows;
- 1,957 research-ready rows;
- zero unexpected changes among previously priced rows.

The frozen V1 and frozen V2 Top-10 memberships were unchanged across all 574
historical decision weeks after the canonical promotion. The previously
validated V1/V2 historical performance evidence also reproduced unchanged.

The remaining 64 unresolved tickers are not assumed to be random. Their
membership-day exposure is classified as follows:

- rename_or_successor_review: 17 tickers / 38,240 days / 38.26%;
- historical_identity_research: 33 / 27,896 / 27.91%;
- active_at_period_end: 6 / 20,041 / 20.05%;
- acquisition: 4 / 6,724 / 6.73%;
- merger: 3 / 5,470 / 5.47%;
- bankruptcy_or_failure: 1 / 1,583 / 1.58%.

`historical_identity_research` is a fallback/unknown research category, not a
confirmed corporate-event classification.

The residual population therefore remains a source of survivorship-like or
regime-specific uncertainty. Historical performance reporting should continue
to report research-ready coverage alongside model results.

## Provider precedence and source stitching

**Status: accepted/open policy**

Canonical provider selection remains conservative:

1. Tiingo when the full accepted boundary is satisfied;
2. Stooq only when Tiingo cannot satisfy the accepted boundary and Stooq can;
3. unresolved otherwise.

The promoted baseline currently selects no Stooq tickers, but the fallback
policy remains part of the canonical builder.

The canonical build does not combine partial histories from different providers
to manufacture full coverage for a PIT ticker. This avoids hidden adjustment,
identifier, corporate-action, and price-scale discontinuities at provider
boundaries.

Historical market-ticker segments may be used within one provider when
explicitly supported by the historical-identity mapping. Direct PIT-symbol
Tiingo coverage is still preferred when it independently satisfies the
full-boundary rule.

No new provider should enter the canonical dataset without the same structural,
quality, identity, and before/after sensitivity review used for Issue #7.

## Price adjustment and price-quality limitations

**Status: accepted/open**

Provider adjustment semantics are not assumed to be interchangeable.

- Tiingo provides adjusted-close data.
- Stooq rows may not provide equivalent adjusted-close semantics.
- Raw close and adjusted close must not be substituted without an explicit
  versioned rule.

The canonical price-quality audit currently reports three medium-severity
`extreme_adjacent_return` observations, all for PARA, with zero high-severity
tickers.

The PARA anomaly was present in the Tiingo raw price series as well as the
adjusted series and was not introduced by the Issue #19 canonical promotion.
It remains a documented provider-quality limitation pending independent
validation. It should not be described as a validated corporate action or as
an adjustment artifact.

## Historical identifiers

**Status: accepted/open**

PIT universe membership ticker, market-data ticker, and SEC registrant identity
remain separate concepts.

Historical aliases and successor mappings are date-bounded. A symbol match
alone is not sufficient evidence that a historical price series belongs to the
intended company, particularly for renamed, acquired, delisted, or reused
symbols.

The Issue #7 residual audit substantially improved classification, but the
remaining `historical_identity_research` group means identifier uncertainty is
not eliminated.

## Shares outstanding coverage

**Status: substantially remediated; residual boundary characterized**

The original current-week V1 observation had only 318/501 canonical
`shares_outstanding` values, materially constraining market capitalization
and Valuation-family coverage.

Issue #3 investigated the missing population using the supported SEC-only
research path. The validated V2 research state reached:

- 440/501 supported current shares values;
- 61/501 residual names classified as unavailable or unsupported;
- no remaining retrieval defect;
- no remaining value-quality defect;
- no remaining candidate-selection defect;
- no remaining point-in-time defect in the supported population.

This does **not** retroactively change the frozen V1 model contract. The live V1
candidate builder retains its exact-only default behavior. V2-only DEI
fallback/cover-date behavior is explicit opt-in research behavior and is
output-isolated.

The remaining 61 names should therefore be treated as the current supported SEC
boundary, not as an unexplained data-pipeline failure. Future SEC taxonomy or
source improvements may change that boundary, but any expansion must be
revalidated point-in-time.

## Total liabilities and Financial Health coverage

**Status: characterized residual**

Financial Health remains the principal family-coverage limitation.

The current V2 research audit found:

- 364/501 current `total_liabilities` coverage;
- 364/501 current Financial Health eligibility;
- 137 residual current rows classified;
- approximately 72.06% historical liabilities coverage in the 2025 panel;
- approximately 72.26% historical Financial Health coverage in the 2025 panel;
- approximately 70.93% current-period 2026 liabilities/Health coverage in the
  audited history.

The investigation rejected a blanket `Assets - Equity` fallback because it was
not sufficiently safe as a general accounting-identity substitute.

One nonpositive V2 liabilities winner was correctly normalized to missing; this
removed a stale zero rather than manufacturing coverage.

The two-component Financial Health minimum remains intentional:

- liabilities / assets;
- cash / assets;
- operating cash flow / liabilities.

At least two components are required. Missing family scores are not replaced
with zero, medians, or synthetic values merely to increase coverage.

## Missingness and selection effects

**Status: characterized residual**

Issue #5 showed that coverage improvements are not selection-neutral.

In the current same-input comparison:

- frozen V1 Top-Conviction eligibility: 227 names;
- V2 research Top-Conviction eligibility: 301 names;
- net gain: +74 / -0;
- all 74 gains came from improved Valuation availability;
- the ordered Top 10 remained unchanged.

Across the historical missingness analysis:

- zero PIT violations were observed after reconciliation;
- the all-ranks median absolute displacement was 42 ranks, exceeding the
  predeclared <=10 threshold;
- within the Top-10 rank band, median/max displacement was 0/1;
- coverage effects were materially associated with market-cap band.

The implication is that improved coverage can materially change the broader
eligible/ranked population even when the highest-ranked names remain stable.
Missingness must therefore continue to be treated as a model-selection effect,
not as neutral absence.

## Frozen V1 family/model limitations

**Status: accepted/open**

`long_growth_v1` remains the live champion and its model contract remains
frozen.

Historical family coverage is not uniform. Financial Health remains the main
coverage constraint, and Growth has a one-year warm-up by construction.

For the V1 core-business composite:

- a score may be computed when at least two of the three core families are
  available;
- a missing Financial Health score remains explicitly missing;
- rows with missing Financial Health cannot qualify for the highest-conviction
  classification;
- 2015 remains a Growth warm-up / diagnostic year;
- clean historical evaluation begins in 2016.

These rules are not changed by the V2 research findings.

The historical Stability price-basis limitation also remains relevant:
Tiingo normally uses adjusted close while other provider series may require a
different return-price basis. Return chains must continue to break when source
or return-price basis changes.

## V2 TTM Valuation challenger

**Status: historically validated; prospective shadow still required**

The frozen challenger is:

`long_growth_v2_ttm_valuation_v1`

It uses point-in-time-safe TTM Valuation reconstruction while preserving the
predeclared family/model framework.

Historical validation completed with:

- 288,655 rows;
- 574 decision dates;
- zero PIT violations;
- V1 XIRR: 25.21%;
- V2 XIRR: 25.73%;
- V2-minus-V1 XIRR: +0.52 percentage points;
- V1/V2 max drawdown: 37.76% / 37.76%;
- VOO XIRR: 15.68%;
- 3-year rolling V2 win/tie rate: 87.5%;
- 5-year rolling V2 win/tie rate: 100%;
- all predeclared historical hard gates passed.

Current family-level comparison showed annual Valuation eligibility of 436/501
versus TTM Valuation eligibility of 435/501. The challenger is therefore not a
simple coverage-expansion strategy; its primary purpose is improved valuation
timeliness/measurement while preserving point-in-time discipline.

Historical PASS does not authorize live integration.

Issue #18 still requires at least eight valid weekly research-only shadow
observations using the same point-in-time inputs as the live V1 decision.
Only fully successful observations count. A failed V2 shadow observation does
not invalidate an otherwise valid V1 live run.

After the shadow requirement is satisfied, any live V2 use still requires a
separate explicit human promotion decision.

## Allocation research

**Status: evaluated; no live change promoted**

Issue #8 evaluated four contribution rules on identical frozen V1/V2 ranked
inputs:

- equal-dollar;
- rank-weighted;
- score-weighted;
- conviction bands.

The alternatives were researched independently from model changes. Equal-dollar
parity reproduced the validated V1/V2 historical results before challenger
results were accepted.

Rank-weighted produced the largest full-period XIRR uplift in both model
versions, but the uplift was not uniformly robust under the V2 rolling-window
tests. Conviction bands showed a smaller, more consistent directional effect.
Score-weighted remained close to equal-dollar because score dispersion is
compressed.

Leave-winner-out analysis identified NVDA as the largest terminal-gain
contributor in all eight model/rule combinations; removing it reduced XIRR by
roughly 4.9 to 5.5 percentage points. This winner dependence is material but
was not unique to one allocation rule.

No allocation challenger was promoted. The live equal-dollar rule remains
unchanged. Any future allocation change requires a separately governed
promotion decision.

## Current live/shadow operating boundary

**Status: accepted/open governance constraint**

The unified `main` branch may contain both:

- live champion `long_growth_v1`;
- research-only challenger `long_growth_v2_ttm_valuation_v1`.

Isolation is enforced by model identifiers, configuration, artifact namespaces,
and runtime capability boundaries rather than by permanently separate Git
branches.

Only the V1 live workflow may reach broker review, order-intent generation,
placement, modification, or cancellation capabilities.

The V2 shadow command remains execution-inert and uses the same saved weekly
point-in-time inputs produced by V1 preparation. It writes only to isolated V2
research/shadow artifacts.

Code integration onto `main` is not model promotion.

## Validation expectations for future changes

Any future change that materially affects data coverage, model inputs, model
rules, or historical performance should continue to pass the relevant subset
of these gates:

1. canonical structural validation;
2. price/data-quality review;
3. historical identity review where applicable;
4. PIT/no-look-ahead validation;
5. before/after coverage and selection-effect analysis;
6. frozen V1 regression checks;
7. walk-forward/rolling robustness;
8. benchmark-relative performance and drawdown;
9. concentration/turnover/rank-stability diagnostics;
10. immutable input/code fingerprints for promotion-relevant evidence.

Known limitations should be updated when those gates materially change the
accepted boundary. They should not be silently removed because a new research
path exists.
