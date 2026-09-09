# Data dictionary: frozen Long Growth V1 and Robinhood parity

Documentation version: 1.0  
Reviewed: 2026-09-09  
Code baseline: [9be4e4f5d3c45b6f8e209f5964196a6a7083f678](https://github.com/Rmarsden89/finance/tree/9be4e4f5d3c45b6f8e209f5964196a6a7083f678)

## Purpose and scope

Describe the implemented research-to-shadow data contract, including mappings, cleaning, time semantics, formulas, and known limitations. This is documentation, not a change to data selection, model weights, or trading behavior.

The live target is Robinhood-derived inputs feeding the same deterministic model used in research. Field availability is not proof of semantic or point-in-time (PIT) equivalence. The first milestone is a source-parity comparison, not another performance backtest.

Scope: the ten canonical fundamental inputs, annual variants, weekly identity/price context, the four champion factor families, score/eligibility fields, and shadow-plan fields. Stability and Momentum are research/challenger features, not champion weights; their detailed formulas remain in [the factor registry](../src/finance/factors/registry.py), [stability](../src/finance/factors/stability.py), and [momentum](../src/finance/factors/momentum.py). This document is not an exhaustive catalog of every acquisition/audit CSV.

Policy constraints: maximum $10/week for the pilot, no AI trade selection, no discretionary selling. Broker execution and forced-exit policies require their own readiness gates. No Robinhood adapter or parity run is implemented by this document.

## 1. Pipeline and grains

1. SEC submission, numeric, and presentation tables are joined into canonical facts.
2. Duplicate candidates are resolved; eligible facts are selected as of the decision timestamp.
3. Historical membership/identity and canonical prices produce a weekly research panel.
4. Raw factors are validated, normalized cross-sectionally, and combined into family scores.
5. Frozen `long_growth_v1` scores feed the broker-neutral shadow planner.

| Dataset | Grain / identity | Important qualification |
|---|---|---|
| SEC submission | `adsh`, accession identifier | Numeric facts join many-to-one to submissions |
| Canonical winner group | `cik, concept, ddate_date, qtrs, uom, accepted_at` | Actual winner key uses acceptance time, not accession alone |
| Current fact snapshot | `cik, concept` | Newest eligible fact period, then acceptance time |
| Weekly research panel | Intended one row per decision date and PIT member ticker | Fundamentals join on resolved CIK; prices on PIT ticker |
| Normalized factor | Factor within `decision_date` cross-section | Depends on the supplied universe and missing-data coverage |
| Shadow decision row | Selected date, rank, ticker | Does not itself validate broker tradability |

## 2. Types, units, and missingness

The SEC loader reads TSV columns as strings, lowercases column names, coerces CIK and quarter counts to nullable integers, numeric values to numbers, and date strings to dates/datetimes. Invalid numeric values become missing. Malformed nonempty dates can raise errors. See [SEC loader](../src/finance/data/sources/sec_financial_statements.py).

Canonical monetary inputs are reported amounts in the source `uom`, normally USD for this universe; shares are counts. These are not automatically values in millions. The reviewed canonical pipeline does not perform FX conversion or explicitly restrict all monetary rows to USD. Unit compatibility is therefore a parity requirement, not an assumed implemented safeguard.

Ratios and growth are decimal fractions (0.25 means 25%). Normalized scores are on a 0–100 scale. Missing is not zero. Negative profits/cash flow are allowed where formulas permit them; denominators often must be positive.

Robinhood returns numeric fact values as decimal strings. Preserve `unit` and `decimals`: `decimals=-6` describes reporting precision, not an instruction to multiply a returned full-dollar value by a million.

## 3. Canonical fundamental mappings

Tags below are in **implemented priority order**, highest first. Alternatives are curated selection rules, not claims that every alternative has identical accounting scope. Source: [sec_concepts.py](../src/finance/data/sec_concepts.py).

| Canonical field | Accepted SEC tags, in priority order | Meaning / type | Period class |
|---|---|---|---|
| `revenue` | `Revenues`; `SalesRevenueNet`; `RevenueFromContractWithCustomerExcludingAssessedTax` | Reported revenue; numeric monetary amount | Income duration |
| `net_income` | `NetIncomeLoss`; `ProfitLoss` | Reported net income/loss; numeric monetary amount | Income duration |
| `operating_income` | `OperatingIncomeLoss` | Operating income/loss; numeric monetary amount | Income duration |
| `total_assets` | `Assets` | Total assets; numeric monetary amount | Instant |
| `total_liabilities` | `Liabilities` | Total liabilities, not just debt; numeric monetary amount | Instant |
| `shareholders_equity` | `StockholdersEquity`; `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` | Equity; alternative includes noncontrolling interests | Instant |
| `cash` | `CashAndCashEquivalentsAtCarryingValue`; `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` | Company cash measure; alternative includes restricted cash | Instant |
| `operating_cash_flow` | `NetCashProvidedByUsedInOperatingActivities`; `NetCashProvidedByUsedInOperatingActivitiesContinuingOperations` | Operating cash flow; alternative limited to continuing operations | Cash-flow duration |
| `capital_expenditures` | `PaymentsToAcquirePropertyPlantAndEquipment`; `PaymentsForAdditionsToPropertyPlantAndEquipment` | Reported PP&E payments; subtracted as reported, no absolute-value conversion | Cash-flow duration |
| `shares_outstanding` | `CommonStockSharesOutstanding`; `EntityCommonStockSharesOutstanding` | Reported share count, not weighted-average EPS shares or float | Instant |

### Period selection is part of the field definition

Source: [sec_canonical.py](../src/finance/data/sec_canonical.py).

| Context | Implemented rule |
|---|---|
| Instant fields | `qtrs=0` |
| Annual income/cash-flow fields | Annual forms and `qtrs=4` |
| Quarterly revenue/net income/operating income | Quarterly forms and `qtrs=1` |
| Quarterly operating cash flow/capex | Year-to-date: Q1 → 1 quarter, Q2 → 2, Q3 → 3 |
| Fact end date | Must equal the filing's reported period date |

Consequences: generic current fields are **not standardized TTM fields**. A quarterly free-cash-flow margin can combine YTD cash flow/capex with single-quarter revenue. Annual filings supply annual income values. This describes the frozen code; changing period treatment is a model/data-methodology change requiring separate review.

### Annual variants

`annual_revenue`, `annual_net_income`, `annual_operating_income`, `annual_operating_cash_flow`, and `annual_capital_expenditures` are selected through a separate cursor restricted to duration facts with `qtrs=4`. They retain the latest eligible annual period even when generic current fields move to a newer quarterly filing. They are not a rolling sum of four quarters.

The valuation family consumes annual revenue, net income, OCF, and capex; annual operating income is available but not used in those valuation formulas.

## 4. Cleaning and winner selection

Implemented order:

1. Keep only curated tags and join submission metadata by accession.
2. Keep forms 10-K, 10-Q, 20-F, 40-F and their /A amendments.
3. When present, require blank `coreg` and blank `segments` for consolidated/non-dimensional facts.
4. When presentation data is supplied, require appropriate statement placement using accession/tag/taxonomy version.
5. Apply the period rules above and require fact end date to match filing period.
6. Keep nonmissing numeric values.
7. Resolve duplicate groups by tag priority.

Statement placement:

| Concept | Accepted statement codes |
|---|---|
| Revenue, operating income | IS |
| Net income | IS, CI |
| Assets, liabilities, cash | BS |
| Equity | BS, EQ |
| OCF, capex | CF |
| Shares | BS, CP |

For same-value duplicates, keep the highest-priority tag. For conflicting values, select the best-priority tag if its candidate values agree. Conflicting values tied at best priority remain unresolved and are omitted from winners. Audit rows record candidates, resolution, and selected tag/value. See [winner selection](../src/finance/data/sec_winners.py) and [duplicate audit](../src/finance/data/sec_duplicates.py).

Do not sum all Robinhood facts with the same concept: totals, segment members, and multiple periods coexist.

## 5. Time and provenance

Source: [sec_snapshot.py](../src/finance/data/sec_snapshot.py).

| Field / suffix | Type | Meaning |
|---|---|---|
| `adsh` / `<concept>_adsh` | String | SEC accession lineage |
| `accepted_at` / `<concept>_accepted_at` | Datetime | Availability gate: must be nonmissing and <= as-of |
| `filed_date` / `<concept>_filed_date` | Date | Submission date; not a substitute for acceptance timestamp |
| `ddate_date` / `<concept>_period_date` | Date | Fact end/instant date |
| `period_date` / `<concept>_filing_period_date` | Date | Filing's reported period |
| `<concept>_form` | String | Filing form |
| `<concept>_fy`, `<concept>_fp` | Source metadata | Fiscal year and period label |
| `<concept>_qtrs` | Nullable integer | Duration in quarters; zero means instant |
| `<concept>_source_tag` | String | Actual selected SEC tag |
| `annual_<concept>_period_date` | Date | Annual fact period |
| `annual_<concept>_accepted_at` | Datetime | Annual availability timestamp |

Current provenance suffixes are emitted only when source columns exist. Annual pivot currently carries value, period date, and acceptance time; it does not carry the full current-fact provenance set. Units are present upstream but not emitted by these pivots.

As-of selection prefers newer fact end dates, then later accepted filings for the same fact period. Amendments enter only after acceptance. Historical comparisons embedded in a later filing must not be backdated; canonical filtering also excludes comparative periods that do not equal the filing period.

**Timing limitation:** SEC acceptance parsing and Friday 16:00 weekly timestamps are timezone-naive in the reviewed code. Date-only acceptance strings become midnight. Do not label these values verified UTC or assume a fully specified trading-session cutoff. Explicit timezone/session handling must be resolved for a live adapter.

## 6. Weekly identity and price fields

Sources: [research_panel.py](../src/finance/data/research_panel.py), [weekly_research_panel.py](../src/finance/data/weekly_research_panel.py).

| Field | Type / units | Meaning |
|---|---|---|
| `decision_date` | Date | Friday weekly row date |
| `as_of` | Datetime | Research availability cutoff, default Friday 16:00 |
| `ticker` | String | PIT membership ticker; not guaranteed to equal today's broker symbol |
| `original_cik`, `cik` | Nullable company identifier | Membership CIK and resolved CIK |
| `company_name` | String | Membership company name |
| `membership_start`, `membership_end` | Dates | Universe interval metadata |
| `identity_resolution_method` | String | Resolution provenance; can be `not_validated` |
| `identity_resolved` | Boolean | Resolved CIK is nonmissing, not a broker-identity guarantee |
| `market_ticker_used` | String | Provider ticker selected upstream |
| `price_source`, `price_date` | String, date | Provider and selected observation date |
| `close` | Numeric price/share | Raw price for valuation |
| `adjusted_close` | Nullable price/share | Provider-adjusted series |
| `return_price` | Nullable price/share | Adjusted close when not None, else raw close |
| `return_price_basis` | String | `adjusted_close` or `close_fallback` |
| `price_age_days` | Days | Decision date minus price date |
| `price_available` | Boolean | A price row exists; not proof its values are finite/fresh |
| `fundamentals_available` | Boolean | At least one canonical fundamental is nonmissing |
| `research_ready` | Boolean | Identity, price, and fundamentals availability flags all true |

CanonicalPriceStore selects the last row on/before the as-of **date**, not intraday timestamp. It skips blank PIT tickers/invalid dates, uppercases symbols, parses price numbers, and retains the last encountered duplicate date per PIT ticker. Provider selection is upstream.

The panel records price age but does not itself impose a maximum-age gate. `research_ready` is not four-family eligibility or live-trade approval.

Robinhood raw historical prices require explicit adjustment/session settings; its historical bar close is not guaranteed to be an official settled close. Split-only history is not automatically equivalent to the dividend-adjusted return basis used in research. Do not substitute adjusted prices into market capitalization. Return-history parity remains relevant to backtest measurement even though Stability/Momentum are not champion weights.

## 7. Champion raw factors

All formulas below use numeric fields; invalid/nonfinite ratios become missing. D means denominator.

| Family / factor | Formula | Formula-level rule |
|---|---|---|
| Quality: `return_on_assets` | net_income / total_assets | D > 0 |
| Quality: `return_on_equity` | net_income / shareholders_equity | D > 0 |
| Quality: `operating_margin` | operating_income / revenue | D > 0 |
| Quality: `free_cash_flow_margin` | (operating_cash_flow - capital_expenditures) / revenue | D > 0 |
| Health: `liabilities_to_assets` | total_liabilities / total_assets | D > 0; lower is better |
| Health: `cash_to_assets` | cash / total_assets | D > 0 |
| Health: `operating_cash_flow_to_liabilities` | operating_cash_flow / total_liabilities | D > 0 |
| Diagnostic: `positive_operating_cash_flow` | 1 if known OCF > 0, else 0; missing stays missing | Not weighted into Health |
| Growth: `revenue_growth_1y` | (current revenue - prior revenue) / prior revenue | Prior > 0 |
| Growth: `net_income_growth_1y` | (current NI - prior NI) / abs(prior NI) | Prior != 0 |
| Growth: `operating_income_growth_1y` | (current OI - prior OI) / abs(prior OI) | Prior != 0 |
| Growth: `operating_cash_flow_growth_1y` | (current OCF - prior OCF) / abs(prior OCF) | Prior != 0 |
| Valuation: `earnings_yield_annual` | annual_net_income / market_cap | Cap > 0 |
| Valuation: `sales_yield_annual` | annual_revenue / market_cap | Cap > 0; validation requires positive revenue |
| Valuation: `free_cash_flow_yield_annual` | (annual_operating_cash_flow - annual_capital_expenditures) / market_cap | Cap > 0 |
| Valuation: `book_to_market` | shareholders_equity / market_cap | Equity > 0 and cap > 0 |

`market_cap = close * shares_outstanding`, with both inputs positive. `annual_free_cash_flow` stores annual OCF minus annual capex. The model does not use Robinhood's headline market cap, P/E, or P/B directly.

Growth sorts by ticker/date and shifts **52 observed rows** within ticker. Calendar separation must be 350–378 days inclusive. Outputs include `<source>_prior_52w`, `growth_lookback_days`, and `growth_lookback_valid`. This is not automatically same-fiscal-quarter YoY growth and does not enforce matching fiscal duration across snapshots.

Sources: [quality](../src/finance/factors/quality.py), [health](../src/finance/factors/financial_health.py), [growth](../src/finance/factors/growth.py), [valuation](../src/finance/factors/valuation.py).

## 8. Validation and scoring

### Validation

[Validation code](../src/finance/factors/validation.py) preserves each raw factor and adds `<factor>_valid` (boolean), `<factor>_invalid_reason` (string), and `<factor>_validated` (numeric or missing).

| Check | Implemented default |
|---|---|
| Asset-relative scale | For ROA, liabilities/assets, cash/assets: invalidate when abs(liabilities, cash, or equity) / abs(assets) > 100 |
| Growth tiny prior | abs(prior) / max(abs(assets), abs(revenue)) < 0.0001 |
| Growth excessive jump | abs(current - prior) / abs(prior) > 1,000,000 |
| Growth lookback | Require valid lookback when its flag column exists |
| Market-cap scale | Cap/assets or cap/positive annual revenue outside 0.001–1000 |
| Annual age | More than 550 days since acceptance; not age since period end |
| Annual availability | Future or missing acceptance rejected when the acceptance column exists |
| Nonpositive inputs | Factor-specific revenue/equity/liability/cap checks |

Caveats: annual timing checks are skipped if the entire acceptance column is absent. Book-to-market returns before annual timing checks. Generic current-fact ages are not globally gated here. Invalid reasons are not a complete missingness diagnosis: already-missing raw factors can have an empty reason, and later checks do not add reasons once a factor is invalid.

### Normalization

Within each decision-date cross-section, [normalization](../src/finance/scoring/normalize.py) requires at least 20 finite values per factor. It clips at the 1st/99th percentiles and ranks using average ties and `pct=True`.

- Higher-is-better score = percentile * 100.
- Lower-is-better score = (1 - percentile) * 100.
- Adds `_winsorized`, `_winsorized_flag`, `_percentile`, and `_score`.
- No imputation of missing factors. Insufficient cross-sections remain unscored.
- Universe and coverage differences can change scores even when one company's facts match.

### Family and model weights

| Family | Component weights in factor-table order | Minimum factors | Long Growth weight |
|---|---|---|---|
| Quality | ROA 35%, ROE 15%, operating margin 25%, FCF margin 25% | 2 | 35% |
| Financial Health | Liabilities/assets 35%, cash/assets 30%, OCF/liabilities 35% | 2 | 20% |
| Growth | Revenue 30%, NI 20%, OI 20%, OCF 30% | 2 | 25% |
| Valuation | Earnings yield 30%, sales yield 20%, FCF yield 30%, book/market 20% | 2 | 20% |

Available weights are renormalized proportionally within families and at model level. Family outputs: `<family>_factor_count`, `_weight_coverage` (fraction), `_eligible`, `_score`.

`long_growth_v1_eligible` requires at least three families. `top_conviction_eligible` requires all four and a nonmissing model score; it does **not** require every individual factor. `evaluation_eligible` requires a score and date >= 2016-01-01. Other outputs include `long_growth_v1_family_count`, `long_growth_v1_weight_coverage`, `full_family_coverage`, `health_missing`, `growth_missing`, `valuation_missing`, and `model_id`.

Sources: [family scores](../src/finance/scoring/family_scores.py), [champion](../src/finance/models/long_growth_v1.py).

## 9. Shadow and broker boundary

Source: [shadow planner](../src/finance/shadow/decision.py).

| Field | Type / meaning |
|---|---|
| Input `ticker, market_value` | Position symbol and current marked dollars; not cost basis |
| `starting_cash` | Nonnegative dollars included in weight denominator |
| `weekly_contribution` | Planned allocation budget, default 10; not entire broker buying power |
| `pre_contribution_portfolio_value` | starting_cash + summed position values |
| `pre_contribution_weight` | Position market value / pre-contribution value, or 0 if empty |
| `rank, ticker, score` | Top-N ranked score descending, ticker ascending for ties |
| `current_market_value` | Selected ticker's summed input position value |
| `status, reason` | blocked/position_cap when weight >= threshold; otherwise buy/empty reason |
| `allocation_dollars` | Contribution equally divided among unblocked selected names |
| `selected_count, buyable_count, blocked_count` | Planner counts, not broker eligibility counts |
| `planned_investment, unallocated_contribution` | Sum of allocations and remaining contribution |
| `decision_hash` | SHA-256 of deterministic serialized plan fields; floats rounded to 10 decimals for hashing |

Planner defaults: Top 10, threshold 0.10, signal age <= 7 calendar days. It rejects too few eligible names and stale/missing signal dates. Blocking does not trigger selling or replacement with rank 11.

Limitations: the function accepts contributions above $10; the pilot cap is policy, not a hard maximum enforced here. It does not reconcile broker state, check pending orders, enforce order minimums/rounding, verify live prices, or place orders. The portfolio CSV loader treats blank market_value as zero, so it is not a fail-closed broker importer. Generic truthiness is used for selection flags; adapters must supply genuine booleans.

Broker buying power is an affordability check, not permission to spend the account balance. Pre-funded cash treatment must be explicitly reconciled with the contribution-based backtest because starting_cash affects the 10% weight denominator.

## 10. Robinhood mapping and evidence status

Status vocabulary:

- **Sample observed:** a read-only response returned the concept; no numerical parity certification.
- **Transform required:** mapping exists conceptually but selection/type/time rules must be reproduced.
- **Unresolved:** metadata, coverage, or semantics remain to be demonstrated.

A prior read-only check retrieved AMAT's 2025 10-K (filed 2025-12-12), Robinhood filing ID `7b0dbdc4-de18-4581-9878-3aac447d7740`. All ten canonical concepts were represented: revenue via RevenueFromContractWithCustomerExcludingAssessedTax and the first-priority tag listed above for each other concept. Totals and dimensional rows were both returned. This is one-filing availability evidence only; raw response retention and comparison against the research dataset remain work to do.

| Robinhood field / capability | Target | Status / required handling |
|---|---|---|
| Filing facts `concept, value` | source_tag and canonical numeric value | Sample observed; apply existing tag rules, not first returned row |
| `entity` | CIK | Transform required; normalize zero-padding and validate ticker/entity |
| `end_date, start_date, period` | Fact period and duration | Sample observed; reproduce quarter/YTD/annual context; do not infer everything from end_date |
| `unit, decimals` | Units and precision audit | Sample observed; normalize unit labels, preserve precision; no magnitude rescaling from decimals |
| `axises` | Consolidated/segment filter | Sample observed; non-dimensional totals must be selected; presentation/coreg equivalence still unresolved |
| `filing_id` | Provider filing lineage | Sample observed; not the same identifier as SEC accession |
| Filing index `date_filed, form_type` | filed_date, form | Sample observed; acceptance timestamp not supplied by this response |
| SEC acceptance time, filing period/fiscal metadata, presentation placement | accepted_at, period_date, fy/fp, statement check | Unresolved; need supported metadata acquisition or explicit evidence of equivalent processing |
| Current financial-summary endpoint | Revenue/income trends | Not approved as drop-in PIT input; fiscal dates alone do not establish historical availability |
| Current fundamentals P/E, P/B, shares, market cap | Potential cross-checks | Not approved substitutes for frozen annual yields and filing-derived share selection |
| Quotes/raw bars | close and current position marks | Transform required; freeze session, date, adjustment, and official-close semantics |
| Adjusted history | return_price | Unresolved dividend/split equivalence and historical coverage |
| Portfolio/positions/orders/tradability | Broker-state contract | Available capabilities; adapter/reconciliation and per-symbol checks not validated |

Headline P/E is TTM-based; `1 / PE` is not the tested annual earnings yield. Today's fundamentals must never be backfilled into a historical decision date. Newly fetched historical comparisons may contain later revisions.

## 11. Parity acceptance plan — not yet executed

1. Freeze a research commit, input dataset version, universe, and decision cutoff.
2. Start with company/filing comparisons, including annual and quarterly filings, tag alternatives, and dimensional facts.
3. Record both paths' source identifiers, units, period starts/ends, acceptance times, selected tags, values, missingness, and rejection reasons.
4. Resolve missing metadata rather than invent acceptance timestamps. If a mapping cannot meet the contract, leave it unresolved; any proposed SEC metadata supplement must be explicit.
5. Compare raw and validated factors using the unchanged implementation. Establish numeric tolerances from reporting precision before judging results.
6. Compare normalized/family/model scores only using the same full cross-section and coverage; compare eligibility and deterministic Top-10 membership.
7. Separate source-value differences, period-selection differences, unit/identity differences, timing differences, and coverage differences.
8. Only after unexplained differences are closed, evaluate current-week shadow production. A material methodology change requires separate approval and validation; a faithful adapter does not imply model redesign.

Historical dates here are test fixtures for source parity, not a new return-optimization exercise. No new backtest performance or live readiness is claimed.

## Maintenance

Update this document with changes to mappings, period selection, validation thresholds, normalization, weights, or adapter contracts. Cite the code revision and evidence scope. Keep proposed controls distinct from implemented behavior, and never commit account balances, account identifiers, credentials, or private portfolio snapshots to this public repository.
