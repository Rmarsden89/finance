# long_growth_v1 live runbook

This runbook is for the live weekly Robinhood Agentic workflow. The model is deterministic and the weekly contribution is capped at $10.

## 1. Update the local repository

```powershell
cd C:\Repos\finance
git pull
```

## 2. Run preparation

Use the trading day's date for `--as-of`.

```powershell
py scripts\run_v1_prepare.py `
  --as-of 2026-09-14
```

Do not use `--skip-sec-refresh`, `--reuse-broker-inputs`, or `--skip-tests` for the normal live run.

Required terminal status:

```text
READY_FOR_PRESUBMIT_REFRESH
```

If preparation fails or any gate blocks, stop. Do not manually bypass a gate.

## 3. Run the fresh pre-submit review

```powershell
py scripts\run_v1_presubmit.py `
  --as-of 2026-09-14
```

Required terminal status:

```text
AWAITING_APPROVAL
```

The pre-submit broker snapshot must be no more than 5 minutes old, all selected intents must be tradable, and every Robinhood review must be clean.

If this stage blocks, stop. Do not submit orders.

## 4. Inspect the submission package in dry-run mode

```powershell
py scripts\run_v1_submit.py `
  --as-of 2026-09-14
```

This command does not place orders without `--approve`.

Confirm at minimum:

- decision hash is present and unchanged
- order count is the expected count
- total dollars is no more than $10
- pre-submit package is still younger than 5 minutes
- all reviews are clean

## 5. Approve and submit

Run immediately after reviewing the dry-run package while the pre-submit package is still fresh.

```powershell
py scripts\run_v1_submit.py `
  --as-of 2026-09-14 `
  --approve
```

This is the only command in the normal workflow that can place orders.

The submit command:

1. revalidates the frozen package and approval conditions;
2. submits the exact frozen intents using their existing idempotency keys;
3. writes the broker submission receipt;
4. reconciles the submission receipt;
5. automatically performs the first post-fill verification.

Never blindly rerun an approved submission after a partial or ambiguous result. If a submission receipt exists, reconcile it first.

## 6. Post-fill result

The approved submit command automatically runs the first post-fill check.

If all orders have filled and portfolio deltas reconcile, the workflow ends with:

```text
COMPLETE
```

If orders are accepted but not all have filled yet, the workflow ends with:

```text
POSTFILL_PENDING
```

In that case, wait briefly and rerun only the read-only post-fill verifier:

```powershell
py scripts\run_v1_postfill.py `
  --as-of 2026-09-14
```

This command never places orders. It refreshes the exact submitted order IDs and current account state, then reconciles fills and portfolio deltas.

If the workflow ends with `POSTFILL_RECONCILIATION_REQUIRED`, stop and inspect the saved artifacts before taking further action.

## Live safety rules

- Use the current trading day's date for `--as-of`.
- Run the approved submission only during the intended regular market session.
- The pre-submit package must be no more than 5 minutes old.
- Never bypass a failed execution, pre-submit, review, submission, or post-fill gate.
- Never blindly retry after Robinhood may have accepted any order.
- `run_v1_postfill.py` is read-only and safe to rerun while waiting for fills.
