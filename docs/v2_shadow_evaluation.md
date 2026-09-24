# V2 TTM Shadow Evaluation Protocol

Issue #24 freezes the evaluation framework for the Issue #18 shadow period before
the completed eight-week evidence is available.

The frozen challenger remains:

\`long_growth_v2_ttm_valuation_v1\`

This protocol does not change the challenger and does not authorize live V2.

## Evaluation questions

The final shadow review answers five questions.

### 1. Observation validity

- Were at least eight weekly observations fully valid?
- Were any attempted weeks excluded or invalid?
- Were there any PIT violations?
- Did the weekly artifacts remain internally consistent with the ledger?
- Were input fingerprints, decision hashes, and code provenance preserved?

### 2. Decision stability

For every valid week:

- V1/V2 Top-10 overlap;
- entered/exited names;
- V1/V2 rank evidence for compared names.

Across the period:

- mean, median, and minimum Top-10 overlap;
- frequency of V2 entrants and exits;
- names with two or more entry/exit events, treated as persistent differences
  requiring review.

No overlap threshold beyond the already-completed historical promotion gates is
introduced by this shadow protocol. The purpose of these statistics is to
describe prospective behavior, not to invent a favorable post-hoc gate.

### 3. Coverage stability

For each successful observation, report:

- current universe rows;
- Quality-family availability;
- Financial Health-family availability;
- Growth-family availability;
- annual Valuation-family availability;
- TTM Valuation-family availability;
- V1 Top-Conviction eligibility;
- V2 Top-Conviction eligibility.

The cumulative package records the observed minimum and maximum of each field.

### 4. Provenance and reproducibility

Each weekly observation already records:

- V1 decision hash;
- V2 decision hash;
- input-bundle SHA-256;
- Git commit;
- PIT status;
- immutable weekly artifacts.

The cumulative evaluator cross-checks the successful ledger against the weekly
summary, V2 decision, input-fingerprint artifact, and code provenance. A mismatch
is reported as an artifact-consistency failure.

This consistency check is not the same as re-executing every historical weekly
run from scratch. A full replay may be performed separately if required by the
final governance review.

### 5. Final disposition

After at least eight valid observations, the final review must document:

- valid observation count;
- any failed/excluded attempted weeks;
- any manual intervention;
- weekly and cumulative decision differences;
- coverage stability;
- PIT/fingerprint/artifact consistency;
- whether prospective behavior is qualitatively consistent with the already
  validated historical challenger evidence;
- remaining operational/data limitations.

Completing this review does not promote V2. Live integration remains a separate
explicit human decision.

## Existing Issue #18 evidence map

The reporting design intentionally reuses the existing Issue #18 artifacts.

| Evaluation need | Existing artifact | Status |
| --- | --- | --- |
| Valid successful weeks | \`shadow_ledger/shadow_ledger.csv\` | Sufficient |
| V1/V2 Top-10 overlap | weekly \`summary.json\` and ledger | Sufficient |
| Entrants / exits | weekly comparison CSV and ledger | Sufficient |
| V1/V2 ranks | weekly \`v1_v2_top10_comparison.csv\` | Sufficient |
| TTM coverage | weekly scores/summary | Sufficient |
| Family coverage across weeks | weekly \`v2_current_scores.csv\` | Source existed; cumulative reporting added by #24 |
| PIT status | weekly summary and ledger | Sufficient |
| Input fingerprints | weekly \`input_fingerprints.json\` | Sufficient |
| Code commit | weekly summary/fingerprints/ledger | Sufficient |
| V1/V2 decision hashes | weekly decision/summary/ledger | Sufficient |
| Cross-artifact consistency | existing artifacts | Cumulative validation added by #24 |
| Repeated entrant/exit persistence | existing weekly comparisons/ledger | Cumulative reporting added by #24 |
| Failed attempted weeks | not stored in successful ledger | Final-review notation required unless #18 later adds a failure-attempt artifact |
| Manual intervention outside successful command | not inferable automatically | Final-review notation required |

The final two items are deliberately not guessed. A successful run proves the
successful command completed; it cannot prove that no manual action occurred
before the command, and the current success ledger does not record failed
attempts.

## Cumulative evaluation command

After one or more valid Issue #18 observations exist:

\`\`\`powershell
cd C:\Repos\finance

py scripts\build_v2_shadow_evaluation.py
\`\`\`

The command reads only existing shadow artifacts and writes:

\`\`\`text
reports\v2\long_growth_v2_research\shadow_ledger\evaluation_v1\
  weekly_evidence.csv
  ticker_events.csv
  summary.json
  evaluation.md
\`\`\`

The package is deterministic for a fixed shadow ledger and fixed weekly
artifacts. It has no broker, order, model-tuning, or live-authorization
capability.

## Frozen reporting fields

Protocol identifier:

\`issue24_shadow_evaluation_v1\`

Cumulative fields are limited to:

- valid/recorded observation counts;
- first/latest valid observation dates;
- mean/median/minimum Top-10 overlap;
- entrant and exit frequencies;
- persistent difference tickers;
- PIT violation count;
- family/eligibility coverage ranges;
- unique V1/V2 decision hash counts;
- unique input-bundle count;
- unique code-commit count;
- weekly artifact-consistency failure count;
- explicit notation that failed attempts/manual intervention are not inferred;
- \`live_promotion_authorized = false\`.

Changing this evaluation protocol after shadow results accumulate requires a
documented new protocol/version and must not silently replace this frozen
template.
