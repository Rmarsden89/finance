# long_growth_v1 live runbook

This runbook is for the live weekly Robinhood Agentic workflow. The model is deterministic and the weekly contribution is capped at $10.

Use the current trading day's date everywhere `YYYY-MM-DD` appears.

## 1. Update the local repository and set SEC identity

```powershell
cd C:\Repos\finance
git pull

$env:SEC_USER_AGENT="Reece Marsden rmarsden89@gmail.com"
```

The live preparation path also verifies the PITIndex universe source before contacting SEC or Robinhood. The PITIndex repository is expected at `C:\Repos\pitindex` with an `upstream` remote pointing to the authoritative repository.

One-time upstream setup, if needed:

```powershell
cd C:\Repos\pitindex
git remote add upstream https://github.com/arielNacamulli/pitindex.git
git fetch upstream
```

Do not automatically merge or pull upstream PITIndex changes as part of the live run. V1 fetches and compares upstream only; universe changes must be reviewed explicitly.

## 2. Run preparation

```powershell
cd C:\Repos\finance

py scripts\run_v1_prepare.py `
  --as-of YYYY-MM-DD
```

Do not use `--skip-sec-refresh`, `--reuse-broker-inputs`, or `--skip-tests` during the normal live path.

Required terminal status:

```text
READY_FOR_PRESUBMIT_REFRESH
```

If preparation fails or any gate blocks, stop. Do not continue to pre-submit until the failure is resolved.

### PITIndex freshness and universe provenance gate

At the start of preparation, before SEC or Robinhood work, V1:

- locates the PITIndex git repository that contains the configured data directory;
- requires the `upstream` remote;
- fetches `upstream` without merging or changing the working tree;
- resolves the local `HEAD` and `upstream/master` commits;
- compares upstream-only changes since the common ancestor for `pitindex/data/sp500_current.csv` and `pitindex/data/sp500_changes.csv`;
- requires those relevant files to be clean in the local working tree;
- hashes the local source files with SHA-256;
- writes an auditable provenance artifact;
- fails closed if upstream contains unreviewed changes to either relevant universe source file or if upstream state cannot be resolved.

The artifact is:

```text
reports\shadow\YYYY-MM-DD\pitindex_provenance.json
```

Upstream may contain unrelated code/documentation changes without blocking V1. A reviewed local correction may also intentionally differ from upstream. That is allowed once the reviewed upstream commit is contained in local history and the correction is committed. New upstream changes to the relevant universe files will still block.

If preparation blocks on PITIndex drift, review the upstream changes before updating the local fork:

```powershell
cd C:\Repos\pitindex

git fetch upstream
git log --oneline HEAD..upstream/master
git --no-pager diff HEAD...upstream/master -- pitindex/data/sp500_current.csv pitindex/data/sp500_changes.csv
```

If the reviewed upstream universe changes are appropriate and require no local correction, a clean fast-forward is fine:

```powershell
cd C:\Repos\pitindex

git fetch upstream
git merge --ff-only upstream/master
git push origin master
```

If an upstream data-quality issue requires a reviewed local correction, first merge the reviewed upstream commit into the local fork, then make the minimal correction in a separate commit and push that commit to `origin/master`. The V1 gate will accept this reviewed fork divergence because the upstream commit is in local history; it will still block on future upstream universe changes. Never leave relevant PITIndex files modified but uncommitted for a live run.

Then return to `C:\Repos\finance` and rerun preparation. Do not use a force reset or automatic merge solely to make the V1 gate pass.

### SEC recovery: `new_filing_partial` or transient SEC 503s

The current SEC path includes bounded retry/backoff and targeted recovery for transient 429/5xx failures. If all affected tickers recover successfully, the workflow can continue without manually recrawling the full universe.

If any SEC partial/error remains unresolved after automatic recovery, preparation still fails closed. Do not use `--skip-sec-refresh` merely to bypass unresolved SEC evidence.

For diagnosis, inspect unresolved rows:

```powershell
Import-Csv C:\Repos\finance\reports\sec_current_filing_discovery.csv |
  Where-Object { $_.status -match 'partial|error' } |
  Format-List *
```

Only use the documented targeted/manual recovery path when the automated retry path cannot resolve a transient provider failure and the evidence can be completed safely. Required result before continuing remains `READY_FOR_PRESUBMIT_REFRESH`.

## 3. Run the fresh pre-submit review

```powershell
py scripts\run_v1_presubmit.py `
  --as-of YYYY-MM-DD
```

Required terminal status:

```text
AWAITING_APPROVAL
```

Confirm:

- fresh snapshot age is under 5 minutes;
- buying power is sufficient;
- all selected intents are tradable;
- all Robinhood reviews are clean.

If this stage blocks, stop. Do not submit orders.

## 4. Inspect the submission package in dry-run mode

```powershell
py scripts\run_v1_submit.py `
  --as-of YYYY-MM-DD
```

This command does not place orders without `--approve`.

Confirm at minimum:

- decision hash is present and unchanged;
- order count is the expected count;
- total dollars is no more than $10;
- pre-submit package is still younger than 5 minutes;
- all reviews are clean.

Dry-run remains usable outside market hours because it does not place orders.

## 5. Approve and submit

Run immediately after reviewing the dry-run package while the pre-submit package is still fresh.

```powershell
py scripts\run_v1_submit.py `
  --as-of YYYY-MM-DD `
  --approve
```

This is the only command in the normal workflow that can place orders.

### Intraday SPY evaluation benchmark capture

After the NYSE market-session gate passes and **before any V1 order placement**, the approved submission path captures a read-only SPY quote from Robinhood for evaluation.

The capture writes:

```text
reports\shadow\YYYY-MM-DD\benchmark_spy_capture.json
```

It records the selected SPY trade price, Robinhood venue timestamp, capture timestamp, quote age, selected price field, bid/ask when available, and market-session context.

This benchmark capture is evaluation-only. It does not create an order intent, preview, review, or SPY trade, and it does not alter the frozen V1 decision.

A missing, unusable, or stale SPY quote blocks approved submission **before any V1 placement call**. Do not bypass this gate. The purpose is to preserve a same-run benchmark timestamp because weekly runs may occur at different times of day.

### NYSE market-session gate

Before any placement call, approved submission resolves the authoritative NYSE regular session for `--as-of` using the maintained market calendar.

The gate:

- blocks weekends and full-day NYSE holidays;
- requires `--as-of` to match the current New York market date;
- blocks before the regular-session open;
- blocks at or after the regular-session close;
- honors shortened/early-close sessions, including the actual shortened close time;
- never expands V1 into extended-hours trading;
- fails closed if the NYSE session cannot be resolved reliably.

When the gate is evaluated, it writes:

```text
reports\shadow\YYYY-MM-DD\market_session_gate.json
```

The artifact records the exchange, market timezone, check timestamp, resolved open/close, whether the date is an early-close session, whether the current clock is inside the regular session, and the allow/block reason.

A successful approved submission should print a resolved session similar to:

```text
NYSE regular session: <open timestamp> -> <close timestamp>
Early close:          NO
```

On a shortened session, `Early close` will be `YES` and the resolved close timestamp controls the placement cutoff. Do not override a blocked market-session gate.

Never blindly rerun an approved submission after a partial or ambiguous result.

### Submission recovery: `SUBMISSION_REQUIRES_RECONCILIATION`

If the submit command reports `SUBMISSION_REQUIRES_RECONCILIATION`, do **not** rerun `--approve`.

Use the read-only recovery command:

```powershell
py scripts\recover_v1_submission.py `
  --as-of YYYY-MM-DD
```

Required recovery result before moving on:

```text
RECONCILED:    YES
ALL ACCEPTED:  YES
Workflow status: SUBMITTED_RECONCILED
```

The recovery command does not place orders. If recovery still reports `RECONCILED: NO`, stop and inspect the saved receipt/reconciliation artifacts before any further action.

## 6. Run post-fill verification

Run the read-only post-fill verifier after submission or successful submission recovery:

```powershell
py scripts\run_v1_postfill.py `
  --as-of YYYY-MM-DD
```

Desired result:

```text
Status: COMPLETE
Filled orders: 10/10
Portfolio reconciled: YES
```

If some orders are accepted but not yet filled, the workflow may end at:

```text
POSTFILL_PENDING
```

In that case, wait briefly and rerun only:

```powershell
py scripts\run_v1_postfill.py `
  --as-of YYYY-MM-DD
```

Do not submit new orders while waiting for fills.

If the workflow ends with `POSTFILL_RECONCILIATION_REQUIRED`, stop and inspect the saved artifacts before taking further action.

### Evaluation package

When post-fill reconciliation reaches `COMPLETE`, the workflow automatically builds:

```text
reports\shadow\YYYY-MM-DD\evaluation_package.json
```

This package is the stable read-only contract for V1 evaluation and future dashboarding. It includes run identity, model/decision provenance, deployed contribution, post-fill marked portfolio value, reconciled selections/fill metadata, the SPY benchmark capture when available, and source-artifact hashes.

Evaluation-package generation has no broker/order capability and cannot change the V1 model or trade decisions. If package generation fails after the live run is already `COMPLETE`, do **not** rerun submission. Rebuild only the evaluation package:

```powershell
py scripts\build_v1_evaluation_package.py `
  --run-dir reports\shadow\YYYY-MM-DD
```

Historical runs that predate intraday SPY capture can still be packaged; their package records `missing_historical_capture`, and the evaluator may use the documented same-date daily SPY fallback.

For detailed evaluation semantics and outputs, see:

```text
docs\v1_performance_evaluation.md
```

## Normal happy-path command sequence

```powershell
cd C:\Repos\finance
git pull

$env:SEC_USER_AGENT="Reece Marsden rmarsden89@gmail.com"

py scripts\run_v1_prepare.py `
  --as-of YYYY-MM-DD

py scripts\run_v1_presubmit.py `
  --as-of YYYY-MM-DD

py scripts\run_v1_submit.py `
  --as-of YYYY-MM-DD

py scripts\run_v1_submit.py `
  --as-of YYYY-MM-DD `
  --approve

py scripts\run_v1_postfill.py `
  --as-of YYYY-MM-DD
```

## Live safety rules

- Use the current intended trading day's date for `--as-of`.
- V1 preparation must pass the PITIndex freshness/provenance gate before contacting SEC or Robinhood.
- Never auto-merge PITIndex upstream changes into the live universe; review relevant universe-file drift first.
- Reviewed local PITIndex corrections must be committed; uncommitted relevant-file changes block the live run.
- Approved submission must pass the authoritative NYSE regular-session gate before any placement calls.
- Approved submission must capture a fresh read-only SPY evaluation quote before any placement calls.
- Do not override a holiday, before-open, after-close, early-close, or calendar-resolution block.
- The pre-submit package must be no more than 5 minutes old.
- Never continue past a failed preparation, execution, pre-submit, review, market-session, submission, or post-fill gate.
- Never blindly retry `run_v1_submit.py --approve` after Robinhood may have accepted an order.
- Use `recover_v1_submission.py` for ambiguous submission receipts.
- `run_v1_postfill.py` is read-only and safe to rerun while waiting for fills.
- A completed run should produce `evaluation_package.json`; package rebuilds are evaluation-only and must never trigger a submission retry.
- Transient SEC failures may recover automatically when evidence becomes complete; unresolved partials remain fail-closed.
