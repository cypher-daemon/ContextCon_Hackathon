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

**`NEVER_OVERRIDABLE`** = `PAYEE_UNRESOLVED, PAYEE_TIE, LOOKALIKE, PARENT_BRAND, VENDOR_MASTER_CONFLICT, BANK_CHANGED, BANK_CHANGE_REQUEST, IFSC_INVALID, DUPLICATE_INVOICE, ALREADY_PAID, DUPLICATE_PAYMENT, DUPLICATE_DEBIT, WRONG_PAYEE_SUSPECTED, AMOUNT_MISMATCH, PAYMENT_UNCERTAIN, PROVIDER_TIMEOUT, MISSING_FIELD, PARSE_FAILED, UNSUPPORTED_REQUEST, VENDOR_CONTAINED, PERSISTENCE_FAILURE`.
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
| 25 | Idempotency window expired, search finds nothing | age > `idem_window_h` | PAYMENT_UNCERTAIN; **never create** | PROVIDER_OUTCOME_UNKNOWN / HIGH | Manual reconcile in the provider dashboard | `crash/test_expired_key_fails_closed.py` |
| 26 | Provider declines | explicit decline | FAILED → item HELD | PAYMENT_FAILED / MEDIUM | Fresh checks + approval → attempt n+1 | `integration/test_decline_then_retry.py` |
| 27 | Provider confirmed, no statement yet | three-way | settlement PENDING / NOT_AVAILABLE | SETTLEMENT_UNCONFIRMED / LOW (non-blocking) | Wait for the statement | `unit/reconciliation/test_settlement.py` |
| 28 | Duplicate debit on the statement | two debits ↔ one payment | Contain | DUPLICATE_DEBIT / CRITICAL + recovery case | Bank/provider investigation | `…::dup_debit` |
| 29 | Returned / reversed payment | provider or statement status | Payment RETURNED; item back to HELD | PAYMENT_RETURNED / HIGH | New attempt only with fresh approval | `integration/test_returned_payment.py` |
| 30 | Approval stale (data/policy changed) | decision_hash mismatch | Refuse approval; recompute | STALE_APPROVAL / HIGH | New approval | `integration/test_stale_approval.py` |
| 31 | Double approve | approval CAS | No-op with "already approved by X" | — | — | `integration/test_approvals.py::double` |
| 32 | Override of a never-overridable flag | CLI | Refused | — | — | `integration/test_approvals.py::refused` |
| 33 | Approver not in the allowlist | policy `approvers` | Refused | — | — | `integration/test_approvals.py::allowlist` |
| 34 | Cost limit reached | BudgetPort.reserve | No more paid calls; the rest are HELD; run STOPPED_COST | RUN_LIMIT_REACHED / MEDIUM | Raise the limit; resume | `integration/test_limits.py::cost` |
| 35 | Time limit reached | check_time | Run PARTIAL | RUN_LIMIT_REACHED / MEDIUM | `resume` | `integration/test_limits.py::time` |
| 36 | Second process / approve during a run | flock | Wait `lock_wait_s`, then exit 3 | — | — | `integration/test_lock.py` |
| 37 | Graceful stop (Ctrl-C / SIGTERM) | signal handler | Finish or roll back the current txn; close the session; exit | — | `resume` | `crash/test_sigterm.py` |
| 38 | kill -9 anywhere | session without ended_at | Resume protocol (§3) | CRASH_RECOVERY / LOW | — | `crash/test_kill_loop.py` |
| 39 | Output file half-written | — | Always write temp + atomic rename | — | Re-run `report` | `unit/reports/test_atomic_write.py` |
| 40 | Live Stripe key configured | key prefix `sk_live_` | Refuse to start (exit 2) | — | — | `unit/payments/test_live_key_refused.py` |
| 41 | Client data / policy changed mid-run | hash ≠ the run's on resume | Non-final items STALE → re-decide; pending approvals SUPERSEDED; PAID/POSTED untouched | STALE_APPROVAL / HIGH (per affected approval) | New approvals | `integration/test_data_change.py` |
| 42 | Same case worded differently | — | Identical decision_hash | — | — | `unit/decide/test_consistency.py` |
| 43 | LLM extraction invalid (P2) | schema validation | Retry once → regex fallback → PARSE_FAILED → CANNOT_VERIFY | INPUT_INCOMPLETE / MEDIUM | — | `unit/sources/test_llm.py` |
| 44 | DB / audit / outbox write fails | exception on commit | **Fail closed before any provider call:** JSONL + stderr, exit 70. (No DB exception row is possible) | PERSISTENCE_FAILURE (log only) | Fix storage; resume | `crash/test_persistence_failure.py` |

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
| step=CREATE_SENT, no ref | re-send create with the **same** key → adopt the returned PaymentIntent | `find_by_attempt_key` (×2, ~30 s apart). Found → adopt. Not found → **PAYMENT_UNCERTAIN; never create** |
| step=REF_SAVED | retrieve → `requires_confirmation` → confirm with the same `:confirm` key | same |
| step=CONFIRM_SENT | retrieve → SUCCEEDED → PROVIDER_CONFIRMED; DECLINED → FAILED; PROCESSING → poll with backoff | same |
| provider unreachable | PAYMENT_UNCERTAIN + exception; never infer from silence | same |

| Killed at | Why it's safe |
|---|---|
| after_ingest | deterministic item_id + INSERT OR IGNORE |
| after_reserve / after_ext_response_before_cache | the UNCONFIRMED row counts at its estimate; a re-call is attempt+1 (spend is never under-reported) |
| after_resolve / after_check / after_decide | logic stages are pure; re-running them is harmless |
| after_post_txn | the posting row and the state change are one transaction; `postings.item_id` UNIQUE |
| after_approval_commit / after_payment_insert | `payments.item_id` + `payment_key` UNIQUE; the executor picks the row up |
| after_create_sent / after_create_response_before_ref | Stripe returns the same PaymentIntent for the same key |
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
- We don't automate refunds unless `Refund.create` is explicitly demonstrated in test mode and labelled as such.

## 6. Containment rules
- A blocking exception stops its item's downstream side effects.
- An open CRITICAL exception (or a pending BANK_CHANGE_REQUEST) for a vendor sets `VENDOR_CONTAINED` on every new payment candidate for that vendor. The check runs inside the payment-enqueue transaction.
- Reopening an exception writes an audit event and keeps its history.
