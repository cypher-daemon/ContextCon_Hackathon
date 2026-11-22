# PayPilot: Failure Modes, Exception Policy & Recovery

> **Core rule:** detect early → persist a typed result → open or update a durable exception → **contain** (block downstream side effects) → keep the evidence → recommend a next action → resolve only with evidence.
> A log line alone is not exception management. A source error never counts as a pass.

## 1. Flags (`paypilot/flags.py`, owner C, additive only)
| Group | Flags |
|---|---|
| Input | `MISSING_FIELD`, `PARSE_FAILED`, `UNSUPPORTED_REQUEST`, `OUT_OF_PERIOD`, `CREDIT_LINE`, `UNSUPPORTED_CURRENCY`, `NON_POSITIVE_AMOUNT` |
| Identity | `PAYEE_UNRESOLVED`, `PAYEE_TIE`, `LOOKALIKE`, `FUZZY_MATCH`, `PARENT_BRAND`, `WEBSITE_IDENTITY_CLAIM_ONLY`, `VENDOR_ONBOARDING_REQUIRED`, `VENDOR_MASTER_CONFLICT` |
| Duplicates | `DUPLICATE_INVOICE`, `DUPLICATE_TRANSACTION`, `ALREADY_PAID`, `DUPLICATE_PAYMENT`, `DUPLICATE_DEBIT` |
| PO / account | `PO_MISSING`, `PO_NOT_FOUND`, `PO_VENDOR_MISMATCH`, `PO_AMOUNT_MISMATCH`, `PO_QTY_MISMATCH`, `PO_EXHAUSTED`, `ACCOUNT_UNMAPPED` |
| Bank | `BANK_CHANGED`, `BANK_CHANGE_REQUEST`, `IFSC_INVALID` |
| Sources / limits | `SOURCE_UNAVAILABLE`, `CREDITS_EXHAUSTED`, `COST_LIMIT`, `TIME_LIMIT` |
| Payment | `PROVIDER_TIMEOUT`, `PAYMENT_UNCERTAIN`, `WRONG_PAYEE_SUSPECTED`, `AMOUNT_MISMATCH`, `SETTLEMENT_MISMATCH`, `RETURNED_PAYMENT`, `REVERSED_PAYMENT`, `STALE_APPROVAL`, `VENDOR_CONTAINED` |
| System | `PERSISTENCE_FAILURE` (never a DB row; see §2 last row) |

**`NEVER_OVERRIDABLE`** = `PAYEE_UNRESOLVED, PAYEE_TIE, LOOKALIKE, PARENT_BRAND, VENDOR_MASTER_CONFLICT, BANK_CHANGED, BANK_CHANGE_REQUEST, IFSC_INVALID, DUPLICATE_INVOICE, ALREADY_PAID, DUPLICATE_PAYMENT, DUPLICATE_DEBIT, WRONG_PAYEE_SUSPECTED, AMOUNT_MISMATCH, PAYMENT_UNCERTAIN, PROVIDER_TIMEOUT, RETURNED_PAYMENT, REVERSED_PAYMENT, MISSING_FIELD, PARSE_FAILED, UNSUPPORTED_REQUEST, VENDOR_CONTAINED, PERSISTENCE_FAILURE`.
Client policy may make overrides stricter, never looser. The CLI enforces it.

**Severity rule:** `CRITICAL` = money may already be wrong. `HIGH` = an unsafe payment action was blocked. `MEDIUM` = an item is held for missing or unavailable data. `LOW` = informational.
The same type can be HIGH before payment and CRITICAL after (`severity_pre` / `severity_post` in `exceptions.map`).

## 2. Failure-mode matrix
| # | Scenario | Detection | Immediate action | Exception (type / severity) | Safe resolution | Test |
|---|---|---|---|---|---|---|
| 1 | Duplicate invoice, reformatted number | vendor + canonical invoice no. | HOLD; no payment row | DUPLICATE_INVOICE / HIGH | Human confirms the original; not overridable | `unit/checks/test_duplicate.py::reformatted` |
| 2 | Duplicate bank line in one batch (official AWS case) | UTR, or same vendor+amount+date within window | Post the first; HOLD the later one, citing the first | DUPLICATE_TRANSACTION / HIGH | Overridable with a reason (it can be a real second charge) | `integration/test_scenario_month_end.py` |
| 3 | Invoice already in payment history | history: vendor + canonical invoice no. | HOLD | ALREADY_PAID / HIGH | Not overridable | `unit/checks/test_duplicate.py::history` |
| 4 | Duplicate payout already happened | two provider successes or two history rows for one payment_key | Contain the vendor; no new payment | DUPLICATE_PAYMENT / CRITICAL + recovery case | Human-led return; RECOVERED only with evidence | `unit/reconciliation/test_three_way.py::dup_payout` |
| 5 | Wrong payee at the provider | provider metadata payee ≠ approved vendor | Contain the vendor | WRONG_PAYEE / CRITICAL + recovery case | Recovery case | `…::wrong_payee` |
| 6 | Wrong amount at the provider | provider amount ≠ approved amount | Contain | AMOUNT_MISMATCH / CRITICAL + recovery case | Adjustment / recovery | `…::wrong_amount` |
| 7 | Bank details on an invoice differ from master | account fingerprint or IFSC ≠ master | HOLD; never update master | BANK_CHANGE_REQUEST / HIGH | Client verifies outside PayPilot → master update → fresh decision + approval | `unit/checks/test_bank.py::changed` |
| 8 | "Please update our bank account" email | VENDOR_MESSAGE kind=BANK_CHANGE | HOLD and contain vendor payments until reviewed | BANK_CHANGE_REQUEST / HIGH | Same as #7 | `integration/test_scenario_fraud.py` |
| 9 | IFSC invalid | Razorpay IFSC 404 | HOLD | IFSC_INVALID / HIGH | Correct the details via the vendor | `unit/sources/test_ifsc.py::not_found` |
| 10 | IFSC/Crustdata unavailable | timeout / 5xx / 429 after retries | HOLD; never assume valid | SOURCE_UNAVAILABLE / MEDIUM | Re-run within limits | `unit/sources/test_http.py` |
| 11 | Crustdata credits exhausted | HTTP 402 | No retry; stop paid calls | SOURCE_UNAVAILABLE / MEDIUM | Top up; re-run | `unit/sources/test_http.py::402` |
| 12 | Lookalike vendor ("Delhivery Logistics Pvt") | fuzzy band / token superset | CANNOT_VERIFY, naming the candidate | IDENTITY_AMBIGUOUS / HIGH | Ask the vendor for a GSTIN / master entry | `unit/resolve/test_matcher.py::lookalike` |
| 13 | Tie (two "Om Sai Roadways" entities) | > 1 vendor at the winning level | CANNOT_VERIFY with candidates | IDENTITY_AMBIGUOUS / HIGH | Same | `…::tie` |
| 14 | Unknown payee, no identifier (official PAYTM case) | no master match, no domain | CANNOT_VERIFY; **no paid call** | IDENTITY_UNRESOLVED / MEDIUM | Add to master | `integration/test_scenario_month_end.py` |
| 15 | Parent brand ≠ billed entity | Crustdata name ≠ invoice entity | CANNOT_VERIFY | LEGAL_ENTITY_MISMATCH / HIGH | Exact-entity evidence needed | `unit/resolve/test_matcher.py::parent_brand` |
| 16 | Vendor master has two vendors with the same GSTIN/alias/account | load-time data-quality scan | Any item hitting them → HOLD | VENDOR_MASTER_CONFLICT / HIGH | Clean up the master | `unit/io/test_client_data.py::conflicts` |
| 17 | PO missing / over tolerance / exhausted | PO check | HOLD with variance + PO source | PO_MISMATCH / MEDIUM | Get the PO / authorized override | `unit/checks/test_po.py` |
| 18 | No account mapping | vendor default + category map both empty | HOLD | ACCOUNT_UNMAPPED / MEDIUM | Mapping update in policy | `unit/checks/test_account.py` |
| 19 | Line outside the requested period | date ∉ request.period | HOLD | INPUT_OUT_OF_SCOPE / LOW | Override or re-request | `unit/checks/test_period.py` |
| 20 | Credit (incoming) line in a posting batch; non-INR; ≤ 0 | posting check | HOLD | INPUT_OUT_OF_SCOPE / MEDIUM | Human | `unit/checks/test_posting_scope.py` |
| 21 | Missing invoice number / required field | required check | CANNOT_VERIFY naming the field | INPUT_INCOMPLETE / MEDIUM | Corrected input | `unit/checks/test_required.py` |
| 22 | Unsupported or missing operation (a "question" request) | request validation | Run-level CANNOT_VERIFY result, exit 0 | INPUT_UNSUPPORTED / LOW | — | `integration/test_unsupported_request.py` |
| 23 | Provider timeout after create/confirm | exception during call | PAYMENT_UNCERTAIN → reconcile with the **same** key | PROVIDER_OUTCOME_UNKNOWN / HIGH | Reconcile; retry only via a new attempt after a confirmed decline | `crash/test_provider_timeout.py` |
| 24 | Crash after provider success, before local commit | attempt step=confirm_sent on resume | retrieve → PROVIDER_CONFIRMED; no new call | CRASH_RECOVERY / LOW (audit only) | — | `crash/test_crash_points.py` |
| 25 | Replay window passed (RazorpayX: past `idem_replay_window_h` = 24 h, the claimed 168 h key validity (UNVERIFIED: official source not yet checked)), lookup finds nothing | attempt age | PAYMENT_UNCERTAIN; **never create** | PROVIDER_OUTCOME_UNKNOWN / HIGH | Manual reconcile in the provider dashboard | `crash/test_expired_key_fails_closed.py` |
| 26 | Provider declines | explicit decline | FAILED → item HELD | PAYMENT_FAILED / MEDIUM | Fresh checks + a new approval (`approval_seq` n+1) → attempt n+1 linked to it | `integration/test_decline_then_retry.py` |
| 27 | Provider confirmed, no statement yet | three-way | settlement PENDING / NOT_AVAILABLE | SETTLEMENT_UNCONFIRMED / LOW (non-blocking) | Wait for the statement | `unit/reconciliation/test_settlement.py` |
| 28 | Duplicate debit on the statement | two debits ↔ one payment | Contain | DUPLICATE_DEBIT / CRITICAL + recovery case | Bank/provider investigation | `…::dup_debit` |
| 29 | Returned / reversed **after** confirmed success | provider status (or statement) | One txn: payment RETURNED / REVERSED; item PAID → HELD; REVERSAL journal entry; vendor contained. **No retry** in this build (a reversal *before* success is a decline, #26) | PAYMENT_RETURNED / CRITICAL + recovery case | Human-led recovery case | `integration/test_reversal_after_success.py` |
| 30 | Approval stale (data/policy changed) | decision_hash mismatch | Refuse approval; recompute | STALE_APPROVAL / HIGH | New approval | `integration/test_stale_approval.py` |
| 31 | Double approve | approval CAS | No-op with "already approved by X" (a new `approval_seq` is possible only after a provider-confirmed decline before success, #26) | — | — | `integration/test_approvals.py::double` |
| 32 | Override of a never-overridable flag | CLI | Refused | — | — | `integration/test_approvals.py::refused` |
| 33 | Approver not in the allowlist | policy `approvers` | Refused | — | — | `integration/test_approvals.py::allowlist` |
| 34 | Cost limit reached | BudgetPort.reserve | No more paid calls; the rest are HELD; run STOPPED_COST | RUN_LIMIT_REACHED / MEDIUM | Raise the limit; resume | `integration/test_limits.py::cost` |
| 35 | Time limit reached | check_time | Run PARTIAL | RUN_LIMIT_REACHED / MEDIUM | `resume` | `integration/test_limits.py::time` |
| 36 | Second process / approve during a run | flock | Wait `lock_wait_s`, then exit 3 | — | — | `integration/test_lock.py` |
| 37 | Graceful stop (Ctrl-C / SIGTERM) | signal handler | Finish or roll back the current txn; close the session; exit | — | `resume` | `crash/test_sigterm.py` |
| 38 | kill -9 anywhere | session without ended_at | Resume protocol (§3) | CRASH_RECOVERY / LOW | — | `crash/test_kill_loop.py` |
| 39 | Output file half-written | — | Always write temp + atomic rename | — | Re-run `report` | `unit/reports/test_atomic_write.py` |
| 40 | Live or non-test RazorpayX key, or missing RazorpayX credentials, with `razorpayx_test` | `RAZORPAYX_KEY_ID` not starting `rzp_test_`; any of the three variables empty; unknown provider name | Refuse to start (exit 2); never fall back to `fake` or live; secrets never printed | — | — | `unit/payments/test_razorpayx_config.py` |
| 41 | Client data / policy changed mid-run | hash ≠ the run's on resume | Non-final items STALE → re-decide; pending approvals SUPERSEDED; PAID/POSTED untouched | STALE_APPROVAL / HIGH (per affected approval) | New approvals | `integration/test_data_change.py` |
| 42 | Same case worded differently | — | Identical decision_hash | — | — | `unit/decide/test_consistency.py` |
| 43 | Malformed batch file (bad JSON/CSV, wrong types, float money) | strict schema at load | Row → ParseFailure → CANNOT_VERIFY (PARSE_FAILED); other rows continue | INPUT_INCOMPLETE / MEDIUM | Corrected file | `unit/io/test_batch_parse.py` |
| 44 | Books don't replay (decision_hash mismatch, unbalanced entry, orphan payment, broken audit chain) | `audit verify` | Report each discrepancy; exit non-zero | AUDIT_DISCREPANCY / CRITICAL | Investigate; never auto-fix the books | `integration/test_audit_verify.py` |
| 45 | DB / audit / outbox write fails | exception on commit | **Fail closed before any provider call:** JSONL + stderr, exit 70. (No DB exception row is possible) | PERSISTENCE_FAILURE (log only) | Fix storage; resume | `crash/test_persistence_failure.py` |
| 46 | Provider acknowledged but not terminal (RazorpayX `queued` / `pending` / `scheduled` / `processing`; FakeProvider `PROCESSING` fault) | status mapping (INTERFACES §8) | Payment stays SUBMITTED, item PAYMENT_IN_PROGRESS; **no PAID, no payment journal entry**; re-polled on every `run` / `resume` | — (the state is visible in `show`/`report`) | Provider reaches a terminal status | `integration/test_provider_pending.py` |
| 47 | Provider returns an unrecognised or missing status | adapter raises `ProviderStatusUnknown` | PAYMENT_UNCERTAIN | PROVIDER_OUTCOME_UNKNOWN / HIGH | Reconcile by hand | `unit/payments/test_razorpayx_mapping.py::unknown` (recorded fixtures) |
| 48 | Replay with the same idempotency key is rejected (RazorpayX `BAD_REQUEST`, different body) | 400 on a replayed create | PAYMENT_UNCERTAIN; **no new key, no new create** | PROVIDER_OUTCOME_UNKNOWN / HIGH | Reconcile in the test Dashboard | `unit/payments/test_razorpayx_mapping.py::replay_rejected` |
| 49 | Vendor-master bank account changed between approval and execution | `HMAC(raw) ≠ approved account_fingerprint` at execute | Refuse before any provider call; item HELD | BANK_CHANGE_REQUEST / HIGH | Fresh decision + approval | `integration/test_execute_refingerprint.py` |
| 50 | Vendor website blocked (Cloudflare challenge / 403) | `cf_markers` / FORBIDDEN | No claim recorded; never a verification | SOURCE_UNAVAILABLE / MEDIUM | Other evidence or onboarding | `unit/sources/test_webfetch.py::blocked` |
| 51 | Crustdata returns empty or incomplete data (no match, missing name/domain) | parse of a 200 body | Not a verification: VENDOR_ONBOARDING_REQUIRED (HELD) or CANNOT_VERIFY | IDENTITY_UNRESOLVED / MEDIUM | Add to master | `unit/sources/test_crustdata.py::empty` |
| 52 | Sources disagree on identity (Crustdata company ≠ website claim ≠ billed entity) | matcher comparison | CANNOT_VERIFY (existing `PARENT_BRAND` flag) | LEGAL_ENTITY_MISMATCH / HIGH | Exact-entity evidence | `unit/resolve/test_matcher.py::conflicting_sources` |

### 2.1 Required provider and live-source cases → coverage
Default tests use the FakeProvider and recorded fixtures/mocks; no external calls. RazorpayX cases marked † are also run once against RazorpayX test mode in SC-13 (opt-in).

| Case | Row(s) | Test |
|---|---|---|
| Successful test payout † | — | SC-03 INV-201 (fake); SC-13 (`razorpayx_test`) |
| Provider rejection † | 26 | `integration/test_decline_then_retry.py`; SC-13 step 4 (Dashboard → `failed`) |
| Timeout before any response | 23 | `crash/test_provider_timeout.py` |
| Ambiguous / pending status † | 46, 47 | `integration/test_provider_pending.py`; SC-13 step 2 |
| Crash after sending, before persisting the response | 24, §3 `after_create_response_before_ref` | `crash/test_crash_points.py`; SC-05 |
| Restart discovers the existing payout | §3 resume table | SC-05; `crash/test_kill_loop.py` |
| Duplicate execution for the same logical payment | 31, `payments.payment_key UNIQUE` | `integration/test_approvals.py::double`; kill loop asserts ≤ 1 success per key |
| Retrieval returns the existing payout | §3 REF_SAVED / CONFIRM_SENT | `crash/test_crash_points.py` |
| Reversal after confirmed success † | 29 | `integration/test_reversal_after_success.py`; SC-13 step 6 |
| Retrieval cannot establish the outcome | 25, 47, 48 | `crash/test_expired_key_fails_closed.py` |
| Website blocked / inaccessible | 50 | `unit/sources/test_webfetch.py::blocked` |
| Empty or incomplete Crustdata response | 51 | `unit/sources/test_crustdata.py::empty` |
| Conflicting identity information | 15, 52 | `unit/resolve/test_matcher.py` |
| Crustdata credits exhausted | 11 | `unit/sources/test_http.py::402` |
| API rate limiting | 10 | `unit/sources/test_http.py::429` |
| IFSC unavailable | 10 | `unit/sources/test_ifsc.py::unavailable` |

**Rule:** an uncertain payment is never re-submitted with a new key. Only the same key is replayed, and only inside the documented or configured window. A failed lookup is never a successful verification.

## 3. Crash points and resume protocol (lane A)
`PAYPILOT_CRASH_AT=<point>` → `os._exit(137)` at:
`after_ingest · after_reserve · after_ext_response_before_cache · after_resolve · after_check · after_decide · after_post_txn · after_approval_commit · after_payment_insert · after_create_sent · after_create_response_before_ref · after_ref_saved · after_confirm_sent · after_confirm_response_before_commit · after_paid_commit`

**Resume protocol, run first on every `run` / `resume` / `approve`:**
1. Take the lock.
2. Open a new session.
3. RESERVED ledger rows → UNCONFIRMED (they still count against the budget).
4. Reconcile every open payment attempt (table below).
5. Re-read config, policy and client data. If the hashes changed → STALE handling (#41).
6. Continue the in-flight items from their persisted state.

| Attempt state on resume | Within `idem_window_h` | After the window |
|---|---|---|
| step=CREATE_SENT, no ref | re-send create with the **same** key + identical body → adopt the returned payment (RazorpayX: only within `idem_replay_window_h`) | FakeProvider: `find_by_attempt_key` looks in its own store (×2, ~30 s apart); found → adopt. RazorpayX: no search exists, so no call is made. Not found / RazorpayX → **PAYMENT_UNCERTAIN; never create** |
| step=REF_SAVED | retrieve → `requires_confirmation` → confirm with the same `:confirm` key (RazorpayX: never `requires_confirmation`; retrieve only) | same |
| step=CONFIRM_SENT | retrieve → SUCCEEDED → PROVIDER_CONFIRMED; DECLINED → FAILED; PROCESSING → stay SUBMITTED, poll with backoff, then leave for the next `resume` (#46) | same |
| provider unreachable | PAYMENT_UNCERTAIN + exception; never infer from silence | same |

| Killed at | Why it's safe |
|---|---|
| after_ingest | deterministic item_id + INSERT OR IGNORE |
| after_reserve / after_ext_response_before_cache | the UNCONFIRMED row counts at its estimate; a re-call is attempt+1 (spend is never under-reported) |
| after_resolve / after_check / after_decide | logic stages are pure; re-running them is harmless |
| after_post_txn | the journal entry, its lines and the state change are one transaction; `journal_entries UNIQUE(item_id, kind)` |
| after_approval_commit / after_payment_insert | `payments.item_id` + `payment_key` UNIQUE; the executor picks the row up |
| after_create_sent / after_create_response_before_ref | the provider (FakeProvider, or RazorpayX via `X-Payout-Idempotency`) returns the saved payment for the same key + body; past the replay window → PAYMENT_UNCERTAIN |
| after_ref_saved / after_confirm_sent / after_confirm_response_before_commit | we retrieve the provider's truth before acting |
| after_paid_commit | final |

## 4. Three-way reconciliation
| Layer | Meaning |
|---|---|
| **Instruction** | what PayPilot decided and a human approved (decision + approval) |
| **Provider** | what the provider says: accepted, declined, confirmed, returned |
| **Bank** | what the statement shows, if supplied |

Outcomes:
- `MATCHED` (exact ref/amount/currency/payee)
- `POSSIBLE_MATCH` (partial; never claimed as settled)
- `CONFLICT` → exception
- `UNMATCHED`
- `NOT_AVAILABLE` (no statement supplied)
- `PENDING` (inside the settlement window)

**Provider-confirmed ≠ bank-settled.**

## 5. Recovery case lifecycle
`OPEN → ACTION_PREPARED → HUMAN_ACTION_REQUIRED → EXTERNAL_RESPONSE_PENDING → RECOVERED`. Alternative endings: `UNRECOVERABLE`, `CLOSED_NO_ACTION`, `FALSE_POSITIVE`.
- The recovery case is the **single source of truth** for recovery. The payment row only links to it.
- **To mark RECOVERED you need:** a provider/bank transaction reference, the returned amount + currency, a Source, a timestamp and an actor.
- **These never count as recovered:** email sent, refund requested, ticket opened, provider acknowledged, local status changed.
- We don't automate refunds. RazorpayX payouts can't be refunded through the payout API, so `refund()` is not supported and recovery stays human-led.

## 6. Containment rules
- A blocking exception stops its item's downstream side effects.
- An open CRITICAL exception (or a pending BANK_CHANGE_REQUEST) for a vendor sets `VENDOR_CONTAINED` on every new payment candidate for that vendor. The check runs inside the payment-enqueue transaction.
- Reopening an exception writes an audit event and keeps its history.
