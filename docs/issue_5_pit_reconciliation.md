# Issue #5: historical SEC availability reconciliation

The original issue acceptance criteria and guardrails remain unchanged. This
implementation addresses the historical point-in-time blocker, not issue closure
or V2 promotion. The before/after V2 turnover and concentration evidence remains
an outstanding requirement; historical V1 statistics do not substitute for it.

## Evidence

The supplied audit flagged 22 filed-date fields across NWS/NWSA on 2017-11-10
and ILMN on 2023-11-10. Both SEC filings were accepted the preceding evening
but carry November 13 filing dates:

- News Corp: https://www.sec.gov/Archives/edgar/data/1564708/000119312517339122/0001193125-17-339122-index.htm
- Illumina: https://www.sec.gov/Archives/edgar/data/1110803/0001110803-23-000086-index.html
- SEC filing/dissemination guidance: https://www.sec.gov/info/edgar/specifications/edgarfm-vol2-v51.pdf

Acceptance alone does not establish public availability. Do not waive these flags.

## Research correction

Run from the repository root after pulling the implementation:

```powershell
py -m pytest tests\test_pit_reconciliation.py tests\test_missingness_bias.py
py scripts\audit_v2_missingness_history.py --as-of 2026-09-15 --reconcile-pit
```

The command locates the original panel and historical SEC winner cache through
the completed research manifest and verifies both fingerprints. No SEC refresh,
price download, broker access, order action, or V1 rule change occurs.

It first replays acceptance-only winner selection for every original observation.
Each existing canonical value and its recorded provenance must match the original
panel. Mismatches, ambiguous winner ties, and missing required cache provenance
stop the run rather than guessing. A failure requires investigation of the
recorded inputs; do not bypass the replay or replace the manifest hashes.

The corrected selection requires both acceptance by the decision timestamp and
the filing-day eligibility bound. Legacy naive timestamps are interpreted as
America/New_York; aware timestamps are converted to that zone. Filing-day 06:00
Eastern is used as an eligibility bound for this weekly financial-statement
research, not an assertion of exact dissemination time. Friday 16:00 cutoffs
are after that bound. These two November filings are therefore unavailable on
November 10. Among eligible facts, the existing period-then-acceptance precedence
is preserved. Previous eligible facts are selected where possible; otherwise
values remain missing. Annual inputs are rebuilt too, with filing/accession
provenance added for auditability.

Membership, identity, prices, and the frozen scoring definitions are preserved.
The entire history is rescored, so dependent lookbacks and cross-sectional
normalization are recomputed. This is a controlled input correction, not a
retrospective V2 counterfactual. Identity and market-data lineage are not newly
validated by this SEC-focused correction.

## Outputs and review

All reconciled outputs are written under:

`reports/v2/long_growth_v2_research/2026-09-15/missingness/history/pit_reconciled/`

Existing output directories cause the reconciled command to stop. Preserve or
rename a previous attempt before rerunning. Original panel, manifest, winner
cache, and prior audit outputs are not overwritten.

Review:

- `pit_reconciliation_changes.csv`: before/after values and provenance, separating
  changes to existing fields from added annual lineage.
- `reconciled_historical_panel.csv`: corrected research inputs.
- `historical_pit_audit.csv`: full-timestamp and filing-date checks, including
  missing provenance on populated inputs; must contain zero violations.
- `history_qualified_summary.json`: replay result, change counts, panel fingerprint,
  historical evidence gates, and unchanged current V1/V2 rank diagnostics.
- Existing coverage, cohort-return, Top-10, turnover, concentration, and rank-shift
  CSVs, plus `conclusion.md` and `input_fingerprints.json`.

Compare these outputs with the original historical audit. Its statistics remain
provisional because the original input had availability defects. A zero-violation
corrected audit does not establish a V2 promotion pass or satisfy missing
historical V2 comparisons. The current median rank displacement threshold remains
unchanged at <=10; the previously observed 42 remains a failed promotion gate.

Real-data verification must be completed in the environment containing the
fingerprinted historical datasets. Record the result and remaining AC gaps in
issue #5 before deciding the next work item.
