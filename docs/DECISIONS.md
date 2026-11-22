# PayPilot: Decision Log & Open Questions

> Stops decisions being re-argued across chats and agent sessions. Change a row only when the team agrees, and log it at the bottom.

## 1. Agreed decisions
| # | Topic | Decision |
|---|---|---|
| D1 | What we ship | Our own B2B product, judged on system design + demo video + live demo. **We define the input/output formats and the test world** (`scenarios/`, `clients/northbay/`). Hackathon Part A rules still bind (sources, approval, test-mode payments, limits, live data, cannot-verify, kill/restart). |
| D2 | Form | Python CLI modular monolith, package `paypilot`, SQLite WAL. No dashboard in the critical path (a static HTML report is P2, for the video). |
| D3 | Layering | Pure logic (lanes B, C) + one side-effect runtime (lane A). |
| D4 | Operations | `POST_BANK_LINES`, `PAY_INVOICES`, `RECONCILE`. The request must state one explicitly; otherwise the result is a run-level CANNOT_VERIFY. A bank line is never a payment instruction. |
| D5 | Exactly-once | At most one successful provider action per logical payment: immutable payment key + per-attempt provider key, outbox, two-step create/confirm, reconcile-before-act, fail-closed PAYMENT_UNCERTAIN. |
| D6 | Provider | **The provider is an implementation detail.** `PaymentProvider` interface + a durable, fault-injecting **FakeProvider** (success, decline, timeout, accepted-just-before-crash, restart retry) is the foundation and the test target. Stripe test mode is an **optional extension (P2)** behind the same interface, labelled a card-charge simulation. RazorpayX = the documented production payouts adapter. |
| D7 | Live data | **Crustdata** identify-by-domain + web fetch is the live-data source (P0, SC-08). The Razorpay IFSC lookup is an optional extra check (P1); it confirms bank and branch, never ownership. |
| D8 | Identity | Client vendor master first. Tie / lookalike / parent brand / unknown → CANNOT_VERIFY. Fuzzy = HOLD at most. |
| D9 | Decisions | Declarative rules in config; all reasons collected; `decision_hash` over normalized facts + decision-policy config only. |
| D10 | Exceptions | Durable register + recovery cases. Severity: CRITICAL = money may be wrong; HIGH = unsafe action blocked; MEDIUM = data missing; LOW = info. |
| D11 | Config | `config.yaml` (product) + `clients/<id>/policy.yaml` (client), strict, hashed, re-read every command. |
| D12 | LLM | **No LLM.** All inputs are structured CSV/JSON; financial decisions are deterministic. |
| D13 | Approvers | `--by` must be in the client policy's `approvers` allowlist (declared identity; SSO in production). |
| D14 | Team lanes | **A = Claude** (runtime, payments, CLI, integration, reports). **B = Codex** (identity and evidence). **C = Codex** (checks, decisions, exceptions, reconciliation, scenarios/fixtures). B and C run as two Codex sessions in separate checkouts, or one session doing B then C. **H = humans** (keys, research, video, review). |
| D15 | Priorities | (1) correct ingestion and bookkeeping (double-entry journal) → (2) vendor resolution and evidence → (3) duplicate and bank-detail fraud detection → (4) exception management and approvals → (5) persistent state, idempotency, crash recovery → (6) bank-statement reconciliation → (7) audit report (`audit verify`) and CLI demo → (8) real provider test mode, if time permits. |
| D16 | Testing | Hand-written scenarios with expected results, asserted against the **DB, output files and audit log**, not just return values. No Stripe, real payment data or LLM needed for any test. |

## 2. Non-negotiable claims
- `POSTED` = local journal only.
- Stripe PaymentIntent = test card charge, not a vendor payout.
- IFSC valid ≠ account ownership verified.
- A website claim is self-published.
- The HTTP Date header is retrieval time, not a record date.
- Provider-confirmed ≠ bank-settled.
- Recovery requested ≠ recovered.
- Unknown cost ≠ ₹0.

## 3. Open questions (default stands until closed)
| Question | Default | Owner |
|---|---|---|
| Do we add the Stripe test-mode extension? | Only after S3 is green and someone has an `sk_test_` key; otherwise FakeProvider only, stated honestly in the README | H |
| ₹ per Crustdata credit? | `null` → shown as UNKNOWN | H (ask the Crustdata team) |
| Crustdata endpoint paths for identify / web fetch | B confirms them from docs.crustdata.com/llms.txt before B5 | B |
| Real IFSC codes for the fixtures | B verifies live before committing | B |
| Video length / format the organizers want | ~3 min MP4, 1080p | H |

## 4. Change log
| Date | Change | Agreed by |
|---|---|---|
| 2026-11-22 | Consolidated plan v4: own product + scenarios; GPT team docs + Claude/Codex plans merged; originals archived in `docs/archive/` | team |
| 2026-11-22 | v4.1: provider-agnostic (FakeProvider foundation, Stripe optional); Crustdata = live data; no LLM; bookkeeping journal + `audit verify`; scenario tests assert DB, files, audit log; added SC-11, SC-12, INV-206 | team |
