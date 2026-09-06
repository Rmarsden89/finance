# Known Limitations

This document records accepted limitations for the current research baseline.
A limitation listed here is not considered fixed merely because a later data
source becomes available; it should be removed only after the canonical
dataset is rebuilt and the validation gate passes.

## Market data baseline V1

Baseline date: 2026-09-06

The current canonical 2015-2025 PIT S&P 500 market dataset is intentionally
frozen as the starting point for model development and shadow validation.

Current canonical provider selection:

- 493 PIT tickers selected from Tiingo
- 188 PIT tickers selected from Stooq bulk
- 73 PIT tickers unresolved
- 681 PIT tickers with selected canonical price coverage
- approximately 94% of PIT S&P 500 membership-days covered
- approximately 6% of PIT S&P 500 membership-days unresolved

The unresolved population is not assumed to be random. It contains a
disproportionate number of historical, acquired, renamed, delisted, or otherwise
difficult securities. This can create survivorship-like or regime-specific
bias in historical backtests, especially if unresolved exposure is concentrated
in particular years or exit types.

For that reason:

- unresolved members remain visible in the research panel;
- they must not be silently removed from universe-quality reporting;
- backtest results must report research-ready coverage alongside performance;
- improvements to coverage should be evaluated by PIT membership-days and year,
  not only by ticker count.

## Provider precedence

V1 provider precedence is:

1. Tiingo
2. Stooq bulk
3. unresolved

Twelve Data was evaluated as a possible additional fallback but was not promoted
into the V1 canonical dataset. Its historical coverage for the remaining gaps
was limited by symbol-history availability, date-range availability, entitlement
restrictions, and ticker reuse/resolution risk.

No future provider may be added directly to the research panel. It must first
be integrated into the canonical market-data build and pass the validation gate.

## No partial-source stitching

The canonical build does not combine partial histories from different providers
to manufacture full coverage for a PIT ticker.

A provider is selected only when its series satisfies the accepted coverage
criteria for that security. Otherwise the ticker remains unresolved.

This is intentionally conservative. It avoids hidden adjustment, identifier,
corporate-action, and price-scale discontinuities at provider boundaries.

## Price adjustment differences

Tiingo and Stooq do not expose identical adjustment semantics.

- Tiingo provides adjusted-close data.
- Stooq canonical rows may have a blank `adjusted_close`.
- Raw `close` and `adjusted_close` must not be treated as interchangeable
  without an explicit factor/model rule.

The research panel preserves both fields and records `price_source`.

## Historical identifiers

PIT universe membership ticker, market-data ticker, and SEC registrant identity
are separate concepts.

Historical market-ticker overrides and SEC identity resolution are date-bounded.
Ticker aliases must not be treated as eternal mappings.

Ticker reuse is a known risk when researching old or delisted securities. A
symbol match alone is not sufficient evidence that a price history belongs to
the intended historical company.

## Quality validation

The current baseline passed the structural canonical validator after the latest
Tiingo recovery work.

The price-quality audit can surface suspicious price discontinuities even when
the canonical structure is valid. Any such issue must be reviewed before a new
baseline is accepted.

The validation gate for a future market-data change is:

1. canonical structural validation passes;
2. market price-quality issues are reviewed and accepted or quarantined;
3. full-universe coverage risk is recomputed;
4. new provider/ticker mappings are reviewed for historical identity correctness;
5. before/after coverage changes are understood.

## Model-development implication

The approximately 6% unresolved membership-day exposure is accepted for V1 so
the project can progress into factor development, walk-forward validation, and
shadow mode.

It is a known source of uncertainty, not evidence that the missing securities
would have had neutral performance.

Model conclusions should therefore be treated as provisional until they remain
stable under:

- different walk-forward periods;
- year-by-year coverage reporting;
- benchmark-relative evaluation;
- future improvements to the unresolved historical universe.

Coverage improvement is a parallel data-quality track and should not block V1
model development unless validation shows the missing exposure materially
changes conclusions.

## Factor-family coverage V1

The current factor-family layer does not have uniform source coverage.

In the accepted 2015-2025 research panel:

- Quality family coverage is approximately 98.5% overall.
- Financial Health family coverage is approximately 67.0% overall.
- Growth has no valid one-year lookback in 2015 by construction, then reaches
  approximately 93-96% annual coverage from 2016 onward.

Financial Health is the principal coverage limitation. Approximately one-third
of research-panel rows have only one of its three ranked component factors.
V1 deliberately requires at least two of the following three components before
assigning a Financial Health family score:

- liabilities / assets;
- cash / assets;
- operating cash flow / liabilities.

The requirement is not relaxed merely to increase coverage. A family score
based on only one available component would create false precision.

Missing family scores are not assumed to be neutral and are never silently
replaced by zero or a median score. Composite models may proportionally
reweight available family scores only when their versioned model definition
explicitly allows it.

Missingness is also not assumed to be random. Historical performance reports
must therefore include family-score coverage by year alongside investment
performance so that richer SEC reporting does not silently become a model
selection effect.

For the V1 core-business composite:

- a score may be computed when at least two of the three core families are
  available;
- a missing Financial Health score is carried explicitly as `health_missing`;
- rows with missing Financial Health cannot qualify for the highest-conviction
  classification;
- 2015 is treated as a Growth warm-up / diagnostic year and is excluded from
  clean composite-model evaluation;
- clean historical evaluation begins in 2016.

These rules are part of the V1 model contract. They should not be changed
retroactively after performance results are observed; a material rule change
requires a new model version.
