# PayPilot — Demo Story, Acceptance Tests & Judge Walkthrough

**Demo thesis:** successful payment is ordinary; safe handling of financial failures is the product.

## 1. What the demo must prove

1. Official bank-line contract works.
2. Vendor resolution is conservative and sourced.
3. Duplicate and unanswerable cases are handled correctly.
4. Invoice payment requires human approval.
5. A process crash cannot create a second successful provider action.
6. A provider timeout becomes uncertain and is reconciled before retry.
7. A wrong/duplicate payout creates an exception and recovery case.
8. Costs, evidence and audit trail are inspectable from the CLI.

Do not claim a bank payout, refund, settlement or recovery unless the configured provider and evidence actually support that claim.

## 2. Suggested 3-minute terminal demo

### Scene 1 — Official four bank lines (35–40 seconds)
Run the official fixture. Show:
- Swiggy → correct payee and staff meals account → `POSTED`;
- first AWS EMEA → exact legal entity and cloud hosting account → `POSTED`;
- second AWS EMEA → `HELD` as possible duplicate, not posted twice;
- PAYTM Sharma Traders → `CANNOT_VERIFY`, missing evidence named.

### Scene 2 — Invoice checks and approval (35–40 seconds)
Run the separate invoice fixture. Show:
- vendor/PO/amount/bank checks;
- bank statuses separately (`ifsc_valid`, `account_matches_master`, `ownership_verified=NOT_VERIFIED`);
- payment is `AWAITING_APPROVAL`;
- inspect evidence and approve explicitly;
- test provider action labelled accurately (Stripe PaymentIntent = test card-charge simulation).

### Scene 3 — Crash/restart (35–40 seconds)
Trigger or replay a deterministic kill point after provider submission/success but before local completion. Run `resume`.
Show:
- provider reconciliation finds the prior action;
- no new payment is created;
- audit trail records recovery;
- payment ends in the correct provider-confirmed/uncertain state.

### Scene 4 — Financial exception (35–40 seconds)
Use a fixture where payment history/provider records show a duplicate payout or wrong payee.
Show:
- `CRITICAL` exception;
- related payments blocked;
- evidence and related IDs;
- recovery case opened with a human action required;
- it is not marked recovered until an external confirmation is present.

### Scene 5 — Evidence and cost (15–20 seconds)
Show `show`, `exceptions`, `report` and `ledger`. Close with:
"PayPilot does not guess, repeat an uncertain payment, or pretend an external payment can be rolled back. It contains the issue, records evidence and manages the recovery."

## 3. Acceptance test matrix

| ID | Input / injected failure | Expected result | Must assert |
|---|---|---|---|
| AT-01 | Official four bank lines | POSTED, POSTED, HELD duplicate, CANNOT_VERIFY | Exact payee/account/outcomes |
| AT-02 | Duplicate invoice with alternate formatting | HELD | No payment outbox item |
| AT-03 | Lookalike vendor | CANNOT_VERIFY or conservative HOLD per policy | No confident wrong vendor |
| AT-04 | Bank details changed | HELD | No provider call; ownership NOT_VERIFIED |
| AT-05 | Human approval | Payment may submit | Approval bound to decision hash |
| AT-06 | Double approve | One logical action | One outbox/payment record |
| AT-07 | Provider timeout after submit | PAYMENT_UNCERTAIN | No blind retry |
| AT-08 | Kill after provider success | Resume reconciles | At most one provider success |
| AT-09 | Duplicate payout already exists | CRITICAL exception + recovery case | No second payment; not falsely recovered |
| AT-10 | Wrong payee/amount already paid | CRITICAL exception + recovery case | Related work contained |
| AT-11 | Provider success, no bank statement | Provider-confirmed, settlement pending/not available | No bank-settled claim |
| AT-12 | Returned/reversed payment | Payment lifecycle updated + exception | Fresh approval before any replacement payment |
| AT-13 | Source 429/402/timeout | HOLD or CANNOT_VERIFY as appropriate | No check silently passes |
| AT-14 | Cost/time limit | PARTIAL/held safely | No further paid calls/actions |
| AT-15 | Audit persistence failure | Fail closed | No provider side effect |
| AT-16 | Second process | Refused by lock | No concurrent writer |

## 4. Demo preparation

- Use deterministic fixtures with dates after 1 Sep 2026 where the event requires.
- Use a clean DB reset script; do not manually edit DB state during the recording.
- Keep real API keys in `.env`, never in fixtures or commits.
- Keep one real live-data decision and its ledger entry available.
- Rehearse the judge's random kill, not just a scripted restart.
- Keep a backup recording, but do not substitute it for a working live CLI.
- Confirm the CLI command and output format match the organizer kit.
- Do not show real bank details or personal data.

## 5. Judge Q&A — concise, defensible answers

**How do you guarantee exactly once?**  
We do not claim distributed atomic exactly-once. We provide at-most-one successful provider action per logical payment using a durable outbox, stable provider idempotency, unique local constraints and reconcile-before-retry. Unknown outcomes stop for reconciliation.

**What if money already went to the wrong vendor?**  
We stop related payments, preserve provider/evidence references and open a recovery case. A local rollback cannot reverse a bank transaction. Recovery is marked complete only after external evidence confirms returned funds.

**What if the provider says success but the bank statement does not?**  
We distinguish provider-confirmed from bank-settled. We keep settlement pending/unconfirmed until a matching statement record exists.

**Why not let an LLM decide?**  
The LLM may assist with extraction if necessary. Identity, checks and payment decisions are deterministic and source-backed.

**What if the live source is unavailable?**  
A source error never counts as a pass. The item is held or cannot be verified; the call and cost are recorded.

**What if the same invoice changes after approval?**  
The decision hash changes; the prior approval is stale and cannot authorize the changed payment.

**What happens at 100× volume?**  
The modular boundaries allow workers and a stronger database/queue later. We chose a single-process SQLite design because the hackathon's immediate requirement is robust low-volume execution and crash safety, not premature distributed infrastructure.

## 6. Final sign-off

- [ ] Official output contract validated
- [ ] All P0 acceptance tests green
- [ ] Kill/restart rehearsal green
- [ ] Exception/recovery demo deterministic
- [ ] README and architecture diagram match implementation
- [ ] Cost ledger and live source evidence visible
- [ ] No claims exceed demonstrated provider capabilities
