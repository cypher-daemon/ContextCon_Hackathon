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
| D6 | Provider | Stripe test mode (P0), labelled "test card-charge simulation". FakeProvider for crash tests. RazorpayX = documented production adapter. |
| D7 | Live data | Razorpay IFSC (P0). Crustdata identify-by-domain + web fetch (P1). |
| D8 | Identity | Client vendor master first. Tie / lookalike / parent brand / unknown → CANNOT_VERIFY. Fuzzy = HOLD at most. |
| D9 | Decisions | Declarative rules in config; all reasons collected; `decision_hash` over normalized facts + decision-policy config only. |
| D10 | Exceptions | Durable register + recovery cases. Severity: CRITICAL = money may be wrong; HIGH = unsafe action blocked; MEDIUM = data missing; LOW = info. |
| D11 | Config | `config.yaml` (product) + `clients/<id>/policy.yaml` (client), strict, hashed, re-read every command. |
| D12 | LLM | Off by default (inputs are structured). P2 only, for unstructured text, with schema validation + fallback. |
| D13 | Approvers | `--by` must be in the client policy's `approvers` allowlist (declared identity; SSO in production). |
| D14 | Team lanes | **A = Claude** (runtime, payments, CLI, integration, reports). **B = Codex** (identity and evidence). **C = Codex** (checks, decisions, exceptions, reconciliation, scenarios/fixtures). B and C run as two Codex sessions in separate checkouts, or one session doing B then C. **H = humans** (keys, research, video, review). |

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
| Can we get a Stripe test key (India signup restrictions)? | Yes. If not by S1, fall back to a Razorpay test Orders adapter (receipt = attempt key) behind the same `PaymentProvider` | H |
| ₹ per Crustdata credit? | `null` → shown as UNKNOWN | H (ask the Crustdata team) |
| Do we demo SC-08 (Crustdata) live? | Only if B6/B7 are done by S3; else describe it | B |
| Real IFSC codes for the fixtures | B verifies live before committing | B |
| Video length / format the organizers want | ~3 min MP4, 1080p | H |

## 4. Change log
| Date | Change | Agreed by |
|---|---|---|
| 2026-11-22 | Consolidated plan v4: own product + scenarios; GPT team docs + Claude/Codex plans merged; originals archived in `docs/archive/` | team |
