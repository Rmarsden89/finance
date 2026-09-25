# V3 raw SEC shares rule v1

## Status

Frozen for formal V3 research testing.

- Rule ID: `v3_raw_sec_entity_common_shares_v1`
- Evidence as of: 2026-09-15
- Scope: V3 research only
- Source: SEC
- V1 modified: no
- Frozen V2 modified: no
- Paid vendor used: no
- Broker/order capability: no

## Purpose

Recover missing/nonpositive canonical `shares_outstanding` using strict SEC-native
cover-page evidence without weakening the frozen canonical pipeline or overwriting
an existing positive canonical share value.

## Frozen extraction contract

The rule accepts only `EntityCommonStockSharesOutstanding` from supported
10-K/10-Q/20-F/40-F filings and amendments.

A candidate must:

- use an instant fact (`qtrs = 0`);
- use a share unit;
- have a positive numeric value;
- have a valid filing/acceptance timestamp;
- have a context instant no later than filing acceptance;
- satisfy the existing conservative point-in-time availability gate.

At the latest eligible instant for a filing:

1. Prefer an undimensioned, non-coreg fact.
2. The undimensioned candidate must resolve to one unique positive value.
3. If no undimensioned value exists, recognized share-class dimensions may be
   summed.
4. Share-class fallback rejects coreg facts.
5. Every dimension must be a recognized share-class member.
6. Each recognized share class must resolve to one unique positive value.
7. Opaque or unrecognized dimension serialization fails closed.

The recognized member-term allowlist is frozen in
`V3_SHARES_RULE.allowed_member_terms`.

## Application contract

The candidate is applied only where canonical `shares_outstanding` is missing
or nonpositive. It never overwrites a positive canonical share value.

This is intentionally a structural rule rather than an issuer allowlist. The
historical panel already has very high canonical shares coverage, so only two
issuers required recovery during historical replay; restricting the rule to
those issuers would encode panel-specific missingness rather than the validated
SEC extraction rule.

## Historical evidence

Historical raw-SEC replay across the frozen panel produced:

- 194 filing candidates across 53 CIKs;
- 1,260 PIT replay rows;
- 674 recovery rows across 574 decision dates;
- positive shares coverage: 168,026 -> 168,700;
- valuation eligibility gained/lost: 574 / 0;
- Top-Conviction eligibility gained/lost: 522 / 0;
- mean/latest Top-10 overlap: 9.9904 / 10;
- mean weekly replacement rate: 12.1881% -> 12.2265%;
- median/max absolute rank displacement: 0 / 4;
- PIT violations: 0.

Canonical overlap diagnostics:

- total overlap/material difference: 586 / 506;
- same-context overlap/material difference: 12 / 0;
- different-context overlap/material difference: 574 / 506.

The high different-context mismatch rate is expected to include timing-definition
differences because the frozen canonical pipeline can select quarter-end
`CommonStockSharesOutstanding`, while this V3 rule specifically uses
`EntityCommonStockSharesOutstanding` at the cover-page share-count instant.
The strongest like-for-like validation set is the same-context overlap, where
there were zero material differences.

## Governance

Any change to the following requires a new rule version:

- accepted SEC source tag;
- supported filing forms;
- instant/unit/positivity requirements;
- PIT availability policy;
- undimensioned preference;
- share-class fallback behavior or member allowlist;
- coreg handling;
- uniqueness requirements;
- overwrite policy;
- source/vendor policy;
- broker or order capability.

The rule remains research-only until a separate promotion decision explicitly
integrates it into a versioned model or data pipeline.
