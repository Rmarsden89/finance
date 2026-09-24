# V3 External Fundamental Data Research

Issue #26 is a research-only data-quality track. It must not modify
`long_growth_v1`, the frozen `long_growth_v2_ttm_valuation_v1` challenger,
or any live/shadow input while the V2 shadow period is in progress.

## Current residual boundary

The validated V2 research state established:

- `shares_outstanding`: 440/501 supported current values, leaving 61/501
  residual names outside the supported SEC-only boundary;
- `total_liabilities`: 364/501 current coverage, leaving 137/501 residual
  rows and making Financial Health the principal current family-coverage
  limitation.

Those residuals are classified evidence, not unexplained pipeline failures.
Issue #26 starts from the existing V2 share-cleanup and liabilities-audit
artifacts rather than reclassifying them ad hoc.

Build the deterministic inventory for an already-audited V2 date with:

```powershell
py scripts\build_v3_gap_inventory.py --as-of YYYY-MM-DD
```

Outputs are written beneath:

```text
reports/v3/data_sources/YYYY-MM-DD/gap_inventory/
```

The builder only reads completed V2 audit artifacts. It does not refresh SEC
data, change model inputs, or contact a broker.

## Candidate-source evaluation fields

Every candidate must be evaluated on the same frozen fields:

1. exact target field or defensible equivalent;
2. historical point-in-time support;
3. filing/publication timestamp;
4. identity mapping;
5. restatement/revision behavior;
6. documented methodology;
7. access/cost;
8. licensing constraints;
9. SEC overlap-validation feasibility;
10. population-bias risk;
11. fail-closed provenance/fingerprint fit.

Current-only headline fundamentals are insufficient for historical model use.

## Initial candidate matrix

| Source | Target fit | PIT/provenance evidence | Identity/revisions | Access/cost | Initial status |
| --- | --- | --- | --- | --- | --- |
| SEC raw filing / Inline XBRL instance data | Potentially strong for both fields, especially residual custom/alternate tags not exposed by CompanyFacts | Strong. EDGAR filings carry accession/acceptance context. SEC states the CompanyFacts/XBRL APIs aggregate only non-custom taxonomy facts, while filing instances may contain filer custom-taxonomy concepts. | CIK/accession-native. Raw filing history preserves the filed evidence; amendments can be treated as separate accepted filings. | Public SEC data; no vendor fee. Must obey SEC automated-access policy. | **Priority 1 research candidate.** Test whether raw filing facts recover residual names without weakening concept semantics. |
| Intrinio U.S. Fundamentals | Strong. Dedicated front-cover shares-outstanding endpoint plus standardized/as-reported balance-sheet data | Strong on paper. Fundamentals expose filing date/time, updated date, first-calculable time, reported/standardized signatures; filing records expose SEC accepted time. Historical fundamentals extend back to 2006. | Company objects expose CIK/LEI; security-history endpoints support ticker history. Signatures provide explicit revision detection. | Published U.S. Fundamentals access starts at $150/month; API/CSV/Snowflake/S3. Licensing terms still need review for stored research artifacts. | **Priority 2 external candidate.** Best documented all-around vendor for a small overlap test. |
| Massive Financials | Strong for `total_liabilities`; shares fit still needs endpoint-level confirmation | Financial statement records expose `filing_date`; vendor explicitly says to use filing date rather than period end for PIT work. Historical financials begin as early as April 2009, varying by company. | Financial records include CIK/tickers. Revision/restatement semantics need explicit validation before acceptance. | Requires a Stocks plan carrying Financials/Ratios; exact cost/license to verify before experiment. | **Priority 3.** Good liabilities comparison candidate; do not assume it solves shares. |
| Financial Modeling Prep as-reported financials | Potentially strong for both. As-reported balance data includes raw liability tags and examples include common-stock shares outstanding | Standard financial statement responses document `filingDate` and SEC `acceptedDate`. Historical as-reported balance sheets are available. | CIK is exposed; symbol-change endpoint exists. Revision/restatement versioning is not yet proven from reviewed docs. | API-key service with free/premium tiers; exact endpoint entitlement and storage/license terms require review. | **Secondary candidate.** Useful overlap test only after revision semantics are understood. |
| Nasdaq Data Link / Sharadar SF1 | Known industry candidate for historical fundamentals and PIT-style research | Publicly discoverable product metadata identifies Sharadar as filing-derived fundamentals, but the detailed SF1 schema/PIT contract was not available in the public pages reviewed in this pass. | Needs schema review for datekey/lastupdated, identifiers, restatement dimensions, and exact shares/liabilities fields. | Premium product; pricing/details require authenticated Data Link access. | **Pending evidence.** Do not accept based on reputation alone. |
| SimFin / other lower-cost fundamentals APIs | Possible field coverage | PIT, publication-time and revision semantics were not sufficiently established from authoritative documentation in the initial pass. | Unverified. | Varies. | **Defer until documentation proves the Issue #26 requirements.** |

## Why raw SEC filing extraction is first

The current pipeline already consumes SEC Submissions + CompanyFacts. That does
not mean all filed XBRL evidence has been exhausted.

SEC documents that the aggregated XBRL APIs use facts from non-custom
taxonomies that apply to the whole filing entity, while filers may extend the
standard taxonomies with their own custom concepts. The raw Inline XBRL filing
therefore has a plausible evidence class that is deliberately outside the
CompanyFacts aggregation boundary.

That makes a narrow raw-filing experiment especially valuable for the
liabilities residuals already classified as alternate-tag candidates, and
potentially for shares residuals where cover-page evidence exists but the
supported standard concept is absent.

The experiment must not equate any tag containing "liability" or "shares" with
the model field. Candidate mappings require filing-context review and
cross-company validation.

## First overlap-validation design

Before any source is allowed to expand coverage:

1. select a small stratified sample containing:
   - SEC-supported controls;
   - residual shares names;
   - residual liabilities names;
   - names missing both fields;
   - at least a few historical corporate-action/identity cases;
2. collect vendor/raw-filing values with filing/accepted timestamps and stable
   identifiers;
3. compare overlapping values against the canonical SEC winners using exact
   accession/period context where available;
4. report exact-match, tolerance-band, missing, stale, and conflicting values;
5. separately report coverage gained in the residual population;
6. reject any source whose PIT timestamp, revision lineage, or identity cannot
   be proved;
7. only after a source passes this data validation define a separate versioned
   V3 model-input experiment.

## Sources reviewed in the initial pass

- SEC EDGAR Application Programming Interfaces:
  https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC Inline XBRL:
  https://www.sec.gov/data-research/structured-data/inline-xbrl
- Intrinio U.S. Fundamentals:
  https://intrinio.com/products/us-fundamentals
- Intrinio shares outstanding:
  https://docs.intrinio.com/documentation/web_api/shares_outstanding_by_company_v2
- Intrinio standardized financials:
  https://docs.intrinio.com/documentation/web_api/get_fundamental_standardized_financials_v2
- Intrinio company filings:
  https://docs.intrinio.com/documentation/web_api/getCompanyFilings_v2
- Massive financials PIT guidance:
  https://massive.com/knowledge-base/article/does-massive-provide-the-filing-date-for-any-financial-reports
- Financial Modeling Prep as-reported balance sheets:
  https://site.financialmodelingprep.com/developer/docs/stable/as-reported-balance-statements
- Nasdaq Data Link publisher catalog:
  https://data.nasdaq.com/publishers

## Promotion boundary

Nothing in this document promotes a new data source.

A candidate that looks promising still requires:

- deterministic extraction;
- immutable raw-response/file fingerprints;
- historical PIT replay;
- overlap validation against SEC;
- residual-coverage analysis;
- missingness/selection sensitivity;
- separately tracked versioned V3 experiment and review.
