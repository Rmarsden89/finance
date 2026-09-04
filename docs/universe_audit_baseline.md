# Historical S&P 500 Universe Audit Baseline

This baseline uses the free arielNacamulli/pitindex S&P 500 seed, event, and current files before additional SEC ticker-map enrichment.

## Source snapshot

- Seed effective date: 2004-12-30
- Seed members: 495
- Change events: 1,028
- Current rows with CIK metadata: 503
- Unique tickers active at some point from 2015 onward: 769
- Unique tickers resolved from the PIT source current-roster CIK metadata: 503
- Unique tickers still unresolved: 266

The low historical CIK coverage is expected because the PIT source enriches current constituents with CIKs but does not preserve CIK metadata for every historical constituent.

## Annual identity coverage

| Date | Members | CIK resolved | CIK unresolved | Coverage |
| --- | ---: | ---: | ---: | ---: |
| 2015-01-02 | 499 | 310 | 189 | 62.1% |
| 2016-01-02 | 502 | 325 | 177 | 64.7% |
| 2017-01-02 | 506 | 342 | 164 | 67.6% |
| 2018-01-02 | 505 | 358 | 147 | 70.9% |
| 2019-01-02 | 505 | 369 | 136 | 73.1% |
| 2020-01-02 | 505 | 391 | 114 | 77.4% |
| 2021-01-02 | 505 | 404 | 101 | 80.0% |
| 2022-01-02 | 505 | 414 | 91 | 82.0% |
| 2023-01-02 | 503 | 433 | 70 | 86.1% |
| 2024-01-02 | 503 | 451 | 52 | 89.7% |
| 2025-01-02 | 503 | 470 | 33 | 93.4% |
| 2026-01-02 | 503 | 489 | 14 | 97.2% |

## Interpretation

The point-in-time membership history is usable for the 2015 and later research window. The current limiting factor is historical identity resolution, not membership reconstruction.

The next pass should enrich historical tickers with SEC ticker-to-CIK associations, then isolate the remaining unresolved names for manual historical identity research. Price-data collection should use the final resolved historical population and membership intervals.
