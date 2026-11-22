# Contract Changes

Append one entry per change to a shared contract (`schemas.py`, `context.py`, `flags.py`, `policy_config.py`, `db/schema.sql`, config keys, CLI, output fields). Lane A applies the change in one `contract:` commit; the other lanes pull before relying on it. Additive changes are fine; renames and removals need every human's agreement.

## YYYY-MM-DD: short title
- **Requested by (lane):**
- **Reason:**
- **Change:** models / enums / signatures / DB / config / CLI / output
- **Affected modules and tests:**
- **Status:** requested / applied in `<commit>`

## 2026-11-22: approval sequence, CLI item selector, RazorpayX replay window, export rows
- **Requested by (lane):** H (team review)
- **Reason:** a fresh approval after a confirmed failed payout was impossible under `UNIQUE(item_id, decision_hash)`; the demo's `approve INV-201` didn't match the hash-only `ITEM_ID`; the export's row timing was undefined.
- **Change:**
  - models / DB: `Approval.approval_seq` + `approvals.approval_seq` (UNIQUE now `(item_id, decision_hash, approval_seq)`); `PaymentAttempt.approval_id` + `payment_attempts.approval_id`. `approval_id` = sha256(item_id | decision_hash | approval_seq)[:16]. Payment and attempt keys unchanged.
  - CLI: `approve` / `reject` / `show` take `ITEM` (exact `item_id` or a unique `invoice_id` / `line_id` / `message_id`) + `--run`; ambiguous → exit 2.
  - config: `payments.razorpayx_test.idem_replay_window_h: 24` (was 1); claimed key validity 168 h (unverified).
  - output: `payment_instructions.csv` has one row per `payments` row in every status, including `PAYMENT_UNCERTAIN`.
- **Affected modules and tests:** `schemas.py`, `db/schema.sql`, `core/ids.py`, `approvals/`, `payments/`, `cli/`, `reports/`, `audit/verify`; `tests/contract/test_cli_selector.py`, `integration/test_decline_then_retry.py`, `integration/test_approvals.py`.
- **Status:** applied in the docs (INTERFACES §4–§10); code pending (A0)

## 2026-11-22: reversal after success
- **Requested by (lane):** H
- **Reason:** simplify reversal handling; no retry after a reversal in this build.
- **Change:** item transition `PAID → HELD` (reversal after success only); `NEVER_OVERRIDABLE` += `RETURNED_PAYMENT`, `REVERSED_PAYMENT`; `PAYMENT_RETURNED` severity CRITICAL + recovery case; `REVERSAL` journal entry = mirror of `INVOICE_PAYMENT` (existing kind, one per item); attempt n+1 / `approval_seq` n+1 only after a decline before success.
- **Affected modules and tests:** `core/state.py`, `core/journal.py`, `payments/`, `flags.py` (C), `exceptions.map` (C), `audit/verify`; `integration/test_reversal_after_success.py`.
- **Status:** applied in the docs; code pending
