# Frozen V3 Liabilities Candidate Rule v1

Status: **research-only frozen candidate**

Evidence date: **2026-09-15**

Rule ID: `v3_liabilities_current_plus_noncurrent_v1`

This document freezes the liabilities recovery rule that is allowed to enter
formal V3 robustness testing. It does not promote the rule into V1, frozen V2,
a live model, a broker workflow, or an execution path.

## Recovery rule

For the approved issuer set only, when canonical positive
`us-gaap:Liabilities` is unavailable, V3 may construct total liabilities as:

```text
us-gaap:LiabilitiesCurrent + us-gaap:LiabilitiesNoncurrent
```

The construction is eligible only when both components are:

- SEC-native evidence;
- from the same accession;
- for the same reporting period / instant;
- USD;
- undimensioned;
- unique;
- positive;
- point-in-time eligible by the existing conservative availability policy.

The rule never overwrites an existing positive canonical liabilities value.

## Explicitly prohibited recovery paths

The frozen rule does not permit:

- `Assets - Equity` as a recovery source;
- `LiabilitiesAndStockholdersEquity` or other total-like tags as liabilities;
- arbitrary alternate tags;
- paid-vendor fundamentals;
- current-only evidence without historical PIT lineage;
- issuer additions discovered after this freeze without a new rule version.

Accounting identities may still be used as validation evidence.

## Approved issuer set

| Ticker | CIK |
| --- | ---: |
| ADI | 6281 |
| CDNS | 813672 |
| CDW | 1402057 |
| CMS | 811156 |
| CTAS | 723254 |
| CTVA | 1755672 |
| DAL | 27904 |
| ETN | 1551182 |
| ETR | 65984 |
| EVRG | 1711269 |
| FFIV | 1048695 |
| GD | 40533 |
| ITW | 49826 |
| LLY | 59478 |
| ORCL | 1341439 |
| PKG | 75677 |
| SYY | 96021 |
| TGT | 27419 |
| TMUS | 1283699 |
| VZ | 732712 |
| WEC | 783325 |

The issuer identity is frozen by CIK. Tickers are labels and may change over
time; historical replay must select by CIK.

## Evidence supporting the freeze

The frozen 21 are the subset of the 32 current strict
Current + Noncurrent candidates that had historical same-filing accounting
corroboration with zero material filing-level mismatches after:

1. collapsing duplicate accounting-identity paths to one filing-level result;
2. preferring the NCI-inclusive equity concept when available; and
3. treating accounting identities as validation only.

The historical rule replay across the frozen weekly research panel produced:

- 288,655 historical rows across 574 decision dates;
- 10,598 PIT-safe liabilities recoveries;
- all 21 approved issuers represented;
- 0 PIT violations;
- 10,598 Financial Health eligibility gains;
- 5,643 Top-Conviction eligibility gains and 0 losses;
- mean Top-10 overlap of 9.611/10 on populated comparison dates;
- median / maximum rank displacement of 6 / 16;
- mean weekly replacement-rate increase of approximately 0.56 percentage
  points.

These outcomes are validation diagnostics, not promotion criteria or claims of
improved investment performance.

## Excluded current candidates

The following current candidates are explicitly outside v1 of this frozen
rule because historical corroboration contained material mismatches:

`ADM, CPAY, DECK, EL, IFF, J, NI, PCG, PEG, UAL`

`FDXF` is also excluded because the archive provided no historical
corroboration for that issuer.

Any future attempt to admit an excluded issuer requires a separately versioned
rule and new evidence. The frozen v1 rule must not be silently expanded.

## Governance boundary

Formal V3 testing must import the issuer set and rule contract from
`src/finance/research/v3.py` rather than reconstructing the allowlist from
mutable report outputs.

Changes to any of the following require a new rule version:

- approved CIK set;
- accepted SEC concepts;
- context/dimension requirements;
- PIT policy;
- overwrite behavior;
- vendor/source policy.

V1 and frozen V2 remain unchanged. Broker and order capabilities remain
disabled.
