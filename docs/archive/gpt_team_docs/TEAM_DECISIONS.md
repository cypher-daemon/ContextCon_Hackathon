# PayPilot — Team Decisions & Open Questions

This file prevents decisions from being re-litigated in separate chats or coding-agent sessions. Update it when the team agrees to a change.

## Agreed baseline

| Decision | Current choice | Reason / boundary |
|---|---|---|
| Product form | CLI-first modular monolith | Explicitly permitted; keeps focus on end-to-end service |
| Core differentiator | Error/exception management | Prevent, detect, contain, investigate and track recovery |
| Operations | Separate `POST_BANK_LINES`, `PAY_INVOICES`, `RECONCILE` | A bank line is not a payment instruction |
| Decision logic | Deterministic rules | Reproducible, explainable and testable |
| Persistence | SQLite WAL + durable state/outbox | Fits single-process hackathon; supports crash recovery |
| Approval | Human approval for every payment | Irreversible action safeguard |
| Payment provider | Stripe test mode if verified; FakeProvider for tests | Test-mode requirement; describe actual semantics honestly |
| Idempotency | Stable logical payment identity; no approval ID in key | Repeat approvals/restarts must not create new payment identity |
| Uncertain outcome | Reconcile before retry; fail closed | Never blindly submit a second payment |
| Exception management | Durable exception and recovery-case records | A log line is not a resolution workflow |
| Recovery | Compensating/human process, not DB rollback | External payment cannot be rolled back locally |
| Live evidence | IFSC lookup if verified, plus other justified live source | IFSC proves bank/branch, not account ownership |
| UI | No dashboard in critical path | Terminal is the demo surface |

## Non-negotiable claims and limitations

- `POSTED` means local posting only.
- Stripe PaymentIntent means test card-charge simulation, not vendor payout.
- IFSC valid does not mean account ownership verified.
- Self-published website content is not independent legal verification.
- HTTP response date is retrieval metadata, not record update date.
- Provider success is not proof of bank settlement.
- Recovery requested is not recovery completed.
- At-most-one successful provider action is the defensible guarantee; do not claim distributed atomic exactly-once.

## Open questions to close when the kickoff kit is found

| Question | Default until confirmed | Owner |
|---|---|---|
| Exact input/output files and fields? | Use internal contract; translate only in `kitio/` | A |
| Which client files exist (vendor master, POs, payment history, statement, chart)? | Optional absent files → `SKIPPED` with reason | A |
| Does the kit request bank-line posting, invoice payment, reconciliation, or more than one? | Keep operations separate; do not infer intent ambiguously | A |
| Is there a true payout test provider available? | Stripe test card-charge simulation; label honestly | A |
| Which live-data source will be used in the final demo? | IFSC lookup if live and relevant; verify before claiming | B |
| What recovery action can the provider actually support? | Create human recovery case; do not automate refund without verified capability | C |
| Actual cost per Crustdata/API/LLM call? | Unknown is null/unknown, never ₹0 | B + A |
| What is the official time/cost ceiling and hour-3 change? | Load from kit/config; record change and rerun tests | A |

## Change protocol

Any change to a shared schema, state enum, function signature, CLI contract or output shape must:
1. be proposed here or in `docs/CONTRACT_CHANGES.md`;
2. identify affected modules and tests;
3. be approved by Builder A and reviewed by affected lane owners;
4. update `INTERFACES.md` and contract tests before dependent code merges.
