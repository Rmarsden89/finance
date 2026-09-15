# long_growth_v1 live runbook

This runbook is for the live weekly Robinhood Agentic workflow. The model is deterministic and the weekly contribution is capped at $10.

Use the current trading day's date everywhere `YYYY-MM-DD` appears.

## 1. Update the local repository and set SEC identity

```powershell
cd C:\Repos\finance
git pull

$env:SEC_USER_AGENT="Reece Marsden rmarsden89@gmail.com"
```

## 2. Run preparation

```powershell
py scripts\run_v1_prepare.py `
  --as-of YYYY-MM-DD
```

Do not use `--skip-sec-refresh`, `--reuse-broker-inputs`, or `--skip-tests` during the normal live path.

Required terminal status:

```text
READY_FOR_PRESUBMIT_REFRESH
```

If preparation fails or any gate blocks, stop. Do not continue to pre-submit until the failure is resolved.

### SEC recovery: `new_filing_partial` or transient SEC 503s

If SEC discovery fails closed with `new_filing_partial`, inspect only the failed rows:

```powershell
Import-Csv C:\Repos\finance\reports\sec_current_filing_discovery.csv |
  Where-Object { $_.status -eq 'new_filing_partial' } |
  Format-List *
```

For transient SEC errors such as HTTP 503, retry only the affected tickers rather than rerunning all 501 names. Replace the example tickers with the actual failed tickers:

```powershell
py scripts\update_sec_current.py `
  --pitindex-data "C:\Repos\pitindex\pitindex\data" `
  --as-of YYYY-MM-DD `
  --ticker LII `
  --ticker PKG `
  --ticker PODD `
  --ticker VRTX `
  --request-delay 0.5 `
  --output "reports\sec_current_filing_discovery_retry.csv"
```

Confirm every retry row is now `new_filing_cached` with a populated `accepted_at` and blank `error`:

```powershell
Import-Csv reports\sec_current_filing_discovery_retry.csv |
  Format-Table ticker,status,accession,accepted_at,error -AutoSize
```

If any retry is still partial, stop. Do not waive it for a live run.

If all retry rows are clean, merge them back into the full discovery report:

```powershell
Copy-Item `
  reports\sec_current_filing_discovery.csv `
  reports\sec_current_filing_discovery_before_retry.csv

$base  = Import-Csv reports\sec_current_filing_discovery.csv
$retry = Import-Csv reports\sec_current_filing_discovery_retry.csv
$retryTickers = @($retry.ticker)

$merged = @(
  $base | Where-Object { $retryTickers -notcontains $_.ticker }
) + @($retry)

$merged |
  Export-Csv reports\sec_current_filing_discovery.csv `
    -NoTypeInformation
```

Confirm there are no remaining partial/error statuses:

```powershell
Import-Csv reports\sec_current_filing_discovery.csv |
  Group-Object status |
  Sort-Object Name |
  Format-Table Count,Name
```

Then finish the SEC candidate and shadow-cache build:

```powershell
py scripts\build_sec_current_candidates.py `
  --discovery "reports\sec_current_filing_discovery.csv" `
  --output "reports\sec_current_candidate_facts.csv"

py scripts\build_sec_shadow_merge.py `
  --current-candidates "reports\sec_current_candidate_facts.csv" `
  --as-of YYYY-MM-DD `
  --output "data\cache\sec\shadow\sec_winner_facts_shadow.csv"
```

Resume preparation without repeating the already-completed SEC crawl or safety tests:

```powershell
py scripts\run_v1_prepare.py `
  --as-of YYYY-MM-DD `
  --skip-sec-refresh `
  --skip-tests
```

Required result is still `READY_FOR_PRESUBMIT_REFRESH`.

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

## 5. Approve and submit

Run immediately after reviewing the dry-run package while the pre-submit package is still fresh.

```powershell
py scripts\run_v1_submit.py `
  --as-of YYYY-MM-DD `
  --approve
```

This is the only command in the normal workflow that can place orders.

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

- Use the current trading day's date for `--as-of`.
- Run the approved submission only during the intended regular market session.
- The pre-submit package must be no more than 5 minutes old.
- Never continue past a failed preparation, execution, pre-submit, review, submission, or post-fill gate.
- Never blindly retry `run_v1_submit.py --approve` after Robinhood may have accepted an order.
- Use `recover_v1_submission.py` for ambiguous submission receipts.
- `run_v1_postfill.py` is read-only and safe to rerun while waiting for fills.
- For transient SEC partial failures, retry only the affected tickers and do not waive unresolved partials.
