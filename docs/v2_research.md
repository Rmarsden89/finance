# V2 Research Track

## Purpose

V2 is a research-only challenger track built from the hardened V1 baseline.
The live champion remains `long_growth_v1`. The frozen challenger is
`long_growth_v2_ttm_valuation_v1`.

After the controlled governance integration tracked in Issue #20, both model
versions live on `main`. Isolation is enforced by immutable model identifiers,
separate configuration/artifact namespaces, and execution-capability boundaries
rather than by permanently separate Git branches. V2 cannot silently change V1
artifacts or broker behavior.

## Safety boundary

The first V2 model identifier is:

`long_growth_v2_research`

V2 artifacts are isolated under:

`reports/v2/long_growth_v2_research/<decision-date>/`

The V2 research configuration hard-disables:

- broker access;
- order-intent generation;
- order review;
- order placement.

The V2 entry point does not import the broker or shadow execution packages.
Any future expansion must preserve these restrictions until a separate,
explicit promotion decision changes the contract.

## Initialize a research run

From `main` after Issue #20 is merged:

```powershell
cd C:\Repos\finance
git switch main
git pull

py scripts\run_v2_research.py `
  --as-of YYYY-MM-DD `
  --data-baseline-id "<immutable baseline identifier>"
```

The initialization command writes only `research_manifest.json`. It does not
score a V2 model and does not connect to Robinhood.

The manifest records:

- model ID;
- source champion;
- configuration hash;
- data-baseline identifier;
- decision date;
- creation timestamp;
- disabled execution capabilities;
- isolated artifact directory.

## V1 and V2 remain logically separate on one code branch

V1 live operations and V2 research/shadow operations run from the same
checked-out `main` branch so a weekly shadow observation can use the exact
same saved point-in-time inputs without branch switching.

The separation boundary is the model/runtime contract:

- `long_growth_v1` remains the live champion and retains the only live
  broker/order workflow;
- `long_growth_v2_ttm_valuation_v1` remains research-only;
- V2 artifacts stay under the V2 research/shadow namespaces;
- V2 commands must not import or reach broker review, order-intent, order
  placement, modification, or cancellation capabilities;
- a failed V2 shadow observation does not change or invalidate an otherwise
  valid V1 live decision.

Feature branches may still be used for future development, but they are not the
persistent isolation mechanism for model versions.

Factor, family, coverage, and allocation experiments are added to V2 only
through their tracked GitHub issues and must carry their own validation
artifacts.

## Build the isolated V2 SEC challenger

The frozen V1 SEC candidate builder defaults to exact-date US-GAAP behavior.
The approved DEI exact-date and bounded cover-date fallbacks require the named
`v2_dei_cover_date` research policy and may write only beneath the dated V2 run
directory.

Build the V2 SEC shadow history and current panel from existing local inputs:

```powershell
py scripts\run_v2_sec_research.py `
  --as-of YYYY-MM-DD `
  --data-baseline-id "<immutable baseline identifier>" `
  --pitindex-data "C:\Repos\pitindex\pitindex\data" `
  --market-snapshot "reports\shadow\YYYY-MM-DD\robinhood_market_snapshot_normalized.csv"
```

The command reads the existing discovery, CompanyFacts cache, historical SEC
winners, historical research panel, PITIndex data, and normalized saved market
snapshot. It does not refresh SEC data or contact Robinhood.

All generated candidates, merge audits, shadow winners, scoring panel, current
snapshot, and manifest are written under:

`reports/v2/long_growth_v2_research/<decision-date>/`

The command cannot create order intents, review orders, or place orders. The V1
candidate reports and `data/cache/sec/shadow` are not modified.

V2 also treats zero or negative historical `shares_outstanding` winners as
missing rather than valid share counts. The original rows and provenance are
retained in `sec_share_quality_adjustments.csv`; only the V2 shadow copy is
normalized. This prevents an invalid zero from becoming the current winner
without falling back to an older positive value.

V2 applies the same fail-closed quality policy to `total_liabilities`. A zero
or negative winner is converted to missing in the isolated V2 SEC shadow copy,
while the original row and provenance are retained in
`sec_liabilities_quality_adjustments.csv`. This prevents an invalid zero from
creating an artificially strong liabilities-to-assets factor. V1 data and the
frozen V1 factor implementation remain unchanged.

## Compare the frozen V1 baseline with the V2 challenger

After the isolated SEC run reaches `SEC_RESEARCH_COMPLETE`, build a same-input
comparison using only saved SEC, PITIndex, market, historical-panel, and V1
decision artifacts:

```powershell
py scripts\run_v2_impact_comparison.py `
  --as-of YYYY-MM-DD `
  --pitindex-data "C:\Repos\pitindex\pitindex\data"
```

The command rebuilds an exact-only V1 baseline beneath the V2 run directory,
scores both panels with the frozen `long_growth_v1` factor and scoring stack,
and compares shares coverage, Valuation availability, top-conviction
eligibility, ranks, and the ordered Top 10. It also verifies the baseline Top
10 and scores against the saved V1 `shadow_decision.json` and audits share
filing/acceptance dates for point-in-time violations.

Comparison artifacts are written only under:

`reports/v2/long_growth_v2_research/<decision-date>/impact/`

This command reads the saved V1 decision artifact but does not import execution
modules, create order intents, or contact Robinhood.

## Immutable input fingerprints

Every completed V2 SEC research run now records SHA-256 fingerprints for the
exact discovery CSV, referenced CompanyFacts JSON files, historical SEC winner
file, historical panel, three PITIndex CSVs, normalized market snapshot, Git
commit, and tracked-worktree cleanliness. The full member list is stored in

`input_fingerprints.json`

The impact comparison recomputes these fingerprints and fails closed if any
input or the code commit differs. Run research commands from a clean tracked
worktree; ignored data/report artifacts do not make the worktree dirty.

### Pre-fingerprint V1 champion limitation

The saved 2026-09-15 V1 champion predates immutable input fingerprints. Its
decision hash identifies the frozen decision package, but the exact historical
contents of every SEC cache, discovery report, PITIndex file, historical panel,
and normalized market snapshot cannot be proven retroactively.

The later exact-only reconstruction matched the champion decision hash and all
10 selected tickers in the same order, with zero point-in-time violations. Its
scores differed by 0.0257 to 0.1256 points on a 0–100 scale (mean absolute
difference 0.0623). This is recorded as a legacy provenance limitation rather
than hidden with a wider numeric tolerance.

All fingerprinted V2 runs fail closed when an input group or the Git commit
changes. A comparison must be rebuilt from a completed research package created
at the same clean commit. This prevents the legacy limitation from recurring in
future V2 evidence.

## Audit and recover residual shares gaps

Classify every blank or nonpositive V2 shares value:

```powershell
py scripts\audit_v2_residual_shares.py `
  --as-of YYYY-MM-DD
```

The audit distinguishes targeted SEC discovery/cache gaps, invalid zero or
negative values, point-in-time eligible candidate-selection defects, candidates
accepted after the decision date, and companies with no supported current fact.
A post-decision candidate is documented as unavailable for that decision date;
it is not reported as a selection defect. The audit does not fetch or promote
data.

If targeted SEC gaps exist, set `SEC_USER_AGENT` and retry only those tickers:

```powershell
$env:SEC_USER_AGENT="Your Name your-email@example.com"

py scripts\refresh_v2_share_gaps.py `
  --as-of YYYY-MM-DD `
  --pitindex-data "C:\Repos\pitindex\pitindex\data"
```

The refresh writes a merged discovery copy beneath the V2 cleanup directory
and prints the isolated `run_v2_sec_research.py` command needed to rebuild the
challenger. It does not modify V1 reports or winner caches and has no broker or
order capability.

## Audit total-liabilities and Financial Health gaps

After the same-input impact comparison exists, classify every current missing
or nonpositive `total_liabilities` value:

```powershell
py scripts\audit_v2_liabilities_gaps.py `
  --as-of YYYY-MM-DD
```

The audit distinguishes discovery/cache failures, point-in-time eligible
candidate-selection defects, post-decision candidates, unsupported liability
tags, and companies with no supported current fact. It also reports a separate
research category when positive Assets and Equity candidates share the exact
same accession, period date, currency unit, and point-in-time eligibility.

An `Assets - Equity` row is evidence for investigation only. This command does
not derive or promote liabilities, add tags, relax the two-component Financial
Health minimum, or change V1/V2 scoring. It writes ticker detail, classification
counts, available yearly and sector coverage, and direct-input fingerprints
only beneath the dated V2 `liabilities` directory.

Before an accounting-identity fallback is considered, the same audit also
calibrates `Assets - Equity` against PIT-eligible directly reported
`Liabilities` rows that share the same accession, period, USD unit, acceptance
timestamp, and duration context. Results are stratified by equity source tag
and retain raw differences and relative errors. The calibration is evidence
only; it does not establish or activate an acceptance tolerance.
