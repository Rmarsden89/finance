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
- compares only the live universe source files `pitindex/data/sp500_current.csv` and `pitindex/data/sp500_changes.csv`;
- hashes the local source files with SHA-256;
- writes an auditable provenance artifact;
- fails closed if upstream contains unreviewed changes to either relevant universe source file or if upstream state cannot be resolved.

The artifact is:

```text
reports\shadow\YYYY-MM-DD\pitindex_provenance.json
```

Upstream may contain unrelated code/documentation changes without blocking V1. Only drift in the relevant S&P 500 universe source files blocks preparation.

If preparation blocks on PITIndex drift, review the upstream changes before updating the local fork:

```powershell
cd C:\Repos\pitindex

git fetch upstream
git log --oneline HEAD..upstream/master
git diff HEAD..upstream/master -- pitindex/data/sp500_current.csv pitindex/data/sp500_changes.csv
```

If the reviewed upstream universe changes are appropriate, sync the local PITIndex branch deliberately. With a clean working tree and no local PITIndex changes to preserve, the normal fast-forward path is:

```powershell
cd C:\Repos\pitindex

git fetch upstream
git merge --ff-only upstream/master
git push origin master
```

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
- Approved submission must pass the authoritative NYSE regular-session gate before any placement calls.
- Do not override a holiday, before-open, after-close, early-close, or calendar-resolution block.
- The pre-submit package must be no more than 5 minutes old.
- Never continue past a failed preparation, execution, pre-submit, review, market-session, submission, or post-fill gate.
- Never blindly retry `run_v1_submit.py --approve` after Robinhood may have accepted an order.
- Use `recover_v1_submission.py` for ambiguous submission receipts.
- `run_v1_postfill.py` is read-only and safe to rerun while waiting for fills.
- Transient SEC failures may recover automatically when evidence becomes complete; unresolved partials remain fail-closed.
