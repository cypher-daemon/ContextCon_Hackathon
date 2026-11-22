# PayPilot — Failure Modes, Exception Policy & Recovery Playbook

**Purpose:** shared checklist for implementation, tests, CLI output and judge Q&A.  
**Core rule:** contain first; investigate with evidence; recover only through a supported and approved action.

## 1. Failure handling pattern

For every failure:
1. Detect it at the earliest stage.
2. Persist a typed flag/check result.
3. Create or update a durable exception.
4. Block downstream side effects if the issue is material.
5. Preserve evidence and related IDs.
6. Recommend a next action; do not invent facts.
7. Resolve only when the resolution is evidenced and audited.

A log line alone is not exception management.

## 2. Failure-mode matrix

| Scenario | Detection | Immediate system action | Exception | Safe recovery / resolution |
|---|---|---|---|---|
| Duplicate invoice before payment | Canonical vendor + invoice identity | Hold; no payment | `DUPLICATE_INVOICE` / HIGH | Human confirms original or corrected invoice; never override silently |
| Duplicate bank line in same input | UTR/reference or normalized fingerprint | Post first once; hold later occurrence | `DUPLICATE_TRANSACTION` / HIGH | Link to first line; human resolves false positive if policy allows |
| Duplicate payout already happened | Provider/payment history has two successful actions for same logical invoice | Stop related payments; do not attempt another charge/refund automatically | `DUPLICATE_PAYMENT` / CRITICAL | Open recovery case; human/provider-led return/refund; close only on evidence |
| Wrong vendor/payee paid | Provider record differs from approved payee identity | Stop related payments; escalate | `WRONG_PAYEE` / CRITICAL | Recovery case; contact provider/bank/vendor through approved process |
| Wrong bank account / bank change request | Account fingerprint or IFSC differs; change request observed | Block payment; never update vendor master automatically | `BANK_CHANGE_REQUEST` / CRITICAL | Independent vendor verification outside the agent; authorized master update, fresh review and approval |
| Wrong amount submitted | Invoice, PO, approved amount and provider amount mismatch | Stop further related actions; record actual amount | `AMOUNT_MISMATCH` / CRITICAL or HIGH | Adjustment/recovery case; human approval; reconcile actual funds |
| Provider timeout before response | Network timeout after submit may be ambiguous | Mark `PAYMENT_UNCERTAIN`; query provider by same idempotency key | `PROVIDER_OUTCOME_UNKNOWN` / HIGH | Retry only if provider proves no action and policy/provider semantics make retry safe |
| Crash after provider success, before local commit | Outbox remains in-flight; provider lookup finds success | Reconcile provider; mark existing payment confirmed | `CRASH_RECOVERY` / HIGH if inconsistent | Resume without creating new payment |
| Provider declines payment | Explicit decline response | Mark known failure; do not label uncertain | `PAYMENT_FAILED` / MEDIUM | Retry only for a classified retryable reason, with same logical identity and policy |
| Provider says success, bank statement absent | Provider success but no statement evidence yet | Mark provider-confirmed; settlement status pending/unknown | `SETTLEMENT_UNCONFIRMED` / MEDIUM | Wait for statement/settlement window; do not claim bank settlement |
| Bank statement has duplicate debit | Two debits map to one intended payment | Open critical exception; block any further payment | `DUPLICATE_DEBIT` / CRITICAL | Recovery case and bank/provider investigation |
| Payment returned/reversed | Provider/statement gives explicit return/reversal status | Update payment lifecycle; reopen payable only after policy review | `PAYMENT_RETURNED` / HIGH | New payment requires fresh checks and approval |
| Vendor lookalike / name tie | Multiple candidates or fuzzy-only match | `CANNOT_VERIFY`; no payment | `IDENTITY_AMBIGUOUS` / HIGH | Request GSTIN/CIN/domain/vendor-master confirmation |
| Parent brand vs billed entity | Live source resolves parent, not legal entity on invoice | `CANNOT_VERIFY` | `LEGAL_ENTITY_MISMATCH` / HIGH | Exact legal-entity evidence required |
| IFSC invalid | Live IFSC lookup returns not found | Hold | `IFSC_INVALID` / HIGH | Correct details through verified vendor process |
| IFSC service unavailable | Timeout/5xx/429 after safe retry | Hold; never assume valid | `SOURCE_UNAVAILABLE` / MEDIUM | Retry later within limits or human review |
| PO missing/mismatch | PO absent when required, or amount/quantity outside tolerance | Hold | `PO_MISMATCH` / MEDIUM | Obtain/verify PO or authorized exception |
| Account mapping missing | No approved account mapping | Hold local posting | `ACCOUNT_UNMAPPED` / MEDIUM | Finance mapping update, then rerun |
| Cost/time limit reached | Budget guard | Stop new external calls/actions; persist partial run | `RUN_LIMIT_REACHED` / MEDIUM | Resume under permitted limit/config |
| DB locked / second process | Process lock | Refuse second writer | `CONCURRENCY_GUARD` / MEDIUM | Use existing process or resume after lock release |
| Approval stale | Decision hash changed | Reject approval attempt; require fresh approval | `STALE_APPROVAL` / HIGH | Recompute decision and obtain new approval |
| Audit/ledger write fails | Persistence error | Fail closed before side effect | `PERSISTENCE_FAILURE` / CRITICAL | Repair DB; never proceed without durable record |

## 3. Non-overridable conditions

At minimum:
- unresolved/ambiguous payee or legal entity;
- bank-change request or mismatch;
- duplicate invoice;
- provider outcome uncertain;
- missing required evidence;
- audit/outbox persistence failure.

A policy override must never bypass these. For other held cases, if overrides are enabled, record the flag, human, reason, timestamp and decision hash.

## 4. Three-way reconciliation model

Keep these distinct:

1. **Instruction:** what PayPilot intended and what was approved.
2. **Provider:** what the test/live provider says it accepted, failed, returned or reversed.
3. **Bank/statement:** what the bank statement actually records, if supplied.

Possible reconciliation outcomes:
- `MATCHED` — exact reference/amount/currency/payee evidence agrees.
- `POSSIBLE_MATCH` — partial match; never claim settled.
- `CONFLICT` — amount/payee/reference disagreement; create exception.
- `UNMATCHED` — no matching record found within supplied data/window.
- `NOT_AVAILABLE` — no statement/history source supplied.
- `PENDING` — expected settlement evidence not yet available.

Provider success alone means provider-confirmed, not bank-settled.

## 5. Recovery case lifecycle

`OPEN → ACTION_PREPARED → HUMAN_ACTION_REQUIRED → EXTERNAL_RESPONSE_PENDING → RECOVERED`

Alternative terminal outcomes: `UNRECOVERABLE`, `CLOSED_NO_ACTION`, `FALSE_POSITIVE`.

Required evidence to mark recovered:
- provider/bank transaction reference;
- returned/credited amount and currency;
- source record or explicit provider response;
- timestamp and actor.

Never mark recovered based solely on:
- email sent;
- refund requested;
- support ticket opened;
- provider acknowledged request;
- local status changed.

## 6. Required tests

- duplicate invoice with formatting differences;
- duplicate bank line by same UTR;
- same vendor/amount/date with different descriptions;
- two provider successes for one invoice (detect and open recovery);
- wrong payee and wrong amount after payment (contain + recovery case);
- timeout before provider response;
- kill after submit before response;
- kill after provider success before local commit;
- stale approval after bank or amount changes;
- provider success but no bank statement record;
- duplicate debit in statement;
- returned/reversed payment;
- source timeout, 429, 402 and invalid IFSC;
- cost/time limit;
- second process lock;
- audit DB write failure before external side effect.

For every test assert both the final state and the absence of unsafe side effects.
