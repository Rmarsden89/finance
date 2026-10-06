# V4 DECK Liabilities Rule v1

## Status

Frozen research candidate under Issue #30.

- Rule ID: `v4_deck_current_plus_noncurrent_post_2015q1_v1`
- Evidence as of: `2026-09-15`
- Issuer: DECK / CIK 910521
- Source: SEC
- Mode: research-only
- V1 modified: no
- V2 modified: no
- V3 modified: no
- Broker/order capability: none

## Recovery rule

Recover `total_liabilities` only when the canonical value is missing/nonpositive
and all of the following are true:

- issuer is DECK / CIK 910521;
- filing period is on or after 2015-03-31;
- `LiabilitiesCurrent` and `LiabilitiesNoncurrent` are both present;
- both facts are positive USD instant facts;
- both facts share the same accession, filing period, and instant;
- facts are undimensioned, non-coreg, and uniquely resolved;
- the filing is point-in-time eligible for the decision;
- an existing positive canonical liabilities value is never overwritten.

The recovery value is:

`LiabilitiesCurrent + LiabilitiesNoncurrent`.

Assets minus Equity and LiabilitiesAndStockholdersEquity minus Equity are
validation evidence only and are never recovery inputs.

## Why the regime starts at 2015-03-31

DECK historical collapsed identity validation contained 48 filing rows.

- material filings: 1;
- last material period: 2014-12-31;
- material relative error: approximately 9.50%;
- post-material filing rows: 45;
- post-material material rows: 0;
- post-material ambiguous rows: 0;
- first clean post-material period: 2015-03-31.

The period boundary is therefore part of the rule contract. Removing or moving it
requires a new version.

## Historical replay

Using the PIT-qualified post-material regime:

- recovery rows applied: 93;
- Health eligibility gained: 93;
- Top-Conviction eligibility gained/lost: 41 / 0;
- mean/latest Top-10 overlap: 9.9617 / 10;
- mean weekly replacement rate: 12.1881% -> 12.1497%;
- median/max absolute rank displacement: 0 / 3;
- comparable one-week outcome weeks: 518;
- diagnostic mean one-week Top-10 return: 0.4448% -> 0.4384%;
- PIT violations: 0.

The return comparison is diagnostic only and is not promotion evidence.

## Rejected neighboring candidates

EL and UAL are not included in this rule.

- EL: 12/50 material historical filings across 2021-2024.
- UAL: 4/10 material historical filings across 2013-2014, with a maximum
  relative error of approximately 47.69%.
- ADM is also excluded from the first frozen V4 candidate because its current
  independent identity validation was only within 1%, and including ADM added
  574 Health-only recovery rows while adding no incremental Top-Conviction gains
  relative to the exact-match cohort.

Any future reconsideration of ADM, EL, UAL, or another issuer requires a separate
versioned V4 rule and its own historical validation.

## Governance boundary

This freeze authorizes continued V4 research and candidate evaluation only. It
does not add V4 to the weekly shadow workflow and does not promote any model to
live use. A combined V4 challenger, if created, requires a separate versioned
contract, reproducibility checks, execution-isolation tests, and an explicit
human governance decision.
