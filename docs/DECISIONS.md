# PayPilot: Decision Log & Open Questions

> Stops decisions being re-argued across chats and agent sessions. Change a row only when the team agrees, and log it at the bottom.

## 1. Agreed decisions
| # | Topic | Decision |
|---|---|---|
| D1 | What we ship | Our own B2B product, judged on system design + demo video + live demo. **We define the internal input/output formats and the test world** (`scenarios/`, `clients/northbay/`). If the organizers' kickoff kit specifies an external I/O format, a thin adapter maps it to and from our internal models (TASKS A18). The internal schemas stay unchanged and we don't guess the external one. Hackathon Part A rules still bind (sources, approval, test-mode payments, limits, live data, cannot-verify, kill/restart). |
| D2 | Form | Python CLI modular monolith, package `paypilot`, SQLite WAL. No dashboard in the critical path (a static HTML report is P2, for the video). |
| D3 | Layering | Pure logic (lanes B, C) + one side-effect runtime (lane A). |
| D4 | Operations | `POST_BANK_LINES`, `PAY_INVOICES`, `RECONCILE`. The request must state one explicitly; otherwise the result is a run-level CANNOT_VERIFY. A bank line is never a payment instruction. |
| D5 | Exactly-once | At most one successful provider action per logical payment: immutable payment key + per-attempt provider key, outbox, step-recorded create/confirm (RazorpayX: one create call; `confirm` only retrieves), reconcile-before-act, fail-closed PAYMENT_UNCERTAIN. The guarantee rests on provider idempotency + durable state + reconciliation. It is not distributed atomic exactly-once. |
| D6 | Provider | **The provider is an implementation detail.** `PaymentProvider` interface + a durable, fault-injecting **FakeProvider** (success, decline, timeout, accepted-just-before-crash, restart retry) is the deterministic test target. **RazorpayX Payouts in test mode** (`razorpayx_test`) is the real provider (P0 if test access is verified, TASKS H7/A9), because PayPilot pays vendors and that needs a payout operation. Config values are only `fake` and `razorpayx_test`: no live option, no automatic switching after a failure. Not used: the Razorpay Payment Gateway or Payment Links (they collect money); Stripe (discarded: a PaymentIntent charges a card). |
| D7 | Live data | **Crustdata** identify-by-domain + web fetch is the live-data source (P0, SC-08). The Razorpay IFSC lookup is an optional extra check (P1); it confirms bank and branch, never ownership. |
| D8 | Identity | Client vendor master first. Tie / lookalike / parent brand / unknown → CANNOT_VERIFY. Fuzzy = HOLD at most. |
| D9 | Decisions | Declarative rules in config; all reasons collected; `decision_hash` over normalized facts + decision-policy config only. |
| D10 | Exceptions | Durable register + recovery cases. Severity: CRITICAL = money may be wrong; HIGH = unsafe action blocked; MEDIUM = data missing; LOW = info. |
| D11 | Config | `config.yaml` (product) + `clients/<id>/policy.yaml` (client), strict, hashed, re-read every command. |
| D12 | LLM | **No LLM.** All inputs are structured CSV/JSON; financial decisions are deterministic. |
| D13 | Approvers | `--by` must be in the client policy's `approvers` allowlist (declared identity; SSO in production). |
| D14 | Team lanes | **A = Claude** (runtime, payments, CLI, integration, reports). **B = Codex** (identity and evidence). **C = Codex** (checks, decisions, exceptions, reconciliation, scenarios/fixtures). B and C run as two Codex sessions in separate checkouts, or one session doing B then C. **H = humans** (keys, research, video, review). |
| D15 | Priorities | (1) correct ingestion and bookkeeping (double-entry journal) → (2) vendor resolution and evidence → (3) duplicate and bank-detail fraud detection → (4) exception management and approvals → (5) persistent state, idempotency, crash recovery → (6) bank-statement reconciliation → (7) audit report (`audit verify`) and CLI demo. **RazorpayX test-mode payouts are P0** (Part A requires test-mode payments through a real provider); they are built on (5) and never replace the FakeProvider tests. |
| D16 | Testing | Hand-written scenarios with expected results, asserted against the **DB, output files and audit log**, not just return values. No provider credentials, real payment data or LLM needed for any default test. The RazorpayX test-mode scenario (SC-13) is opt-in (`-m razorpayx`). |

## 2. Non-negotiable claims
- `POSTED` = local journal only.
- `PAID` = the provider returned its documented successful terminal status (RazorpayX `processed`). Approved, submitted or acknowledged (`queued` / `processing`) ≠ paid.
- RazorpayX test-mode payout = a provider-side simulation with a dummy balance: no real money moves.
- FakeProvider = our own simulation, not a provider sandbox.
- `payment_instructions.csv` = an audit export of payments already submitted, never an instruction to pay.
- IFSC valid ≠ account ownership verified.
- A website claim is self-published.
- The HTTP Date header is retrieval time, not a record date.
- Provider-confirmed ≠ bank-settled.
- Recovery requested ≠ recovered.
- Unknown cost ≠ ₹0.

## 3. Open questions (default stands until closed)
| Question | Default | Owner |
|---|---|---|
| Do we have RazorpayX test-mode API access (test keys, test Customer Identifier, any KYC or IP-allowlist prerequisite for test mode)? | Unverified. Until H7 passes: FakeProvider only, and the README and video say the provider requirement is **not yet met** | H |
| RazorpayX idempotency-key validity: claimed 7 days / 168 h, **UNVERIFIED** until the official source is checked and linked here. Search by `reference_id`: not documented, not used | Replay only within `idem_replay_window_h: 24` (one-day event); after that → PAYMENT_UNCERTAIN, reconciled by hand in the test Dashboard | H (add the link) / A |
| ₹ per Crustdata credit? | `null` → shown as UNKNOWN | H (ask the Crustdata team) |
| Crustdata endpoint paths for identify / web fetch | B confirms them from docs.crustdata.com/llms.txt before B5 | B |
| Real IFSC codes for the fixtures | B verifies live before committing | B |
| Video length / format the organizers want | ~3 min MP4, 1080p | H |

## 4. Change log
| Date | Change | Agreed by |
|---|---|---|
| 2026-11-22 | Consolidated plan v4: own product + scenarios; GPT team docs + Claude/Codex plans merged; originals archived in `docs/archive/` | team |
| 2026-11-22 | `docs/archive/` removed (earlier drafts remain in git history) | team |
| 2026-11-22 | v4.4 (docs frozen): a decline before success is retryable with a fresh approval; a reversal after success → REVERSAL entry + CRITICAL recovery exception + vendor contained, no retry; SC-13 blocked until B4's IFSC; 168 h validity marked unverified | team |
| 2026-11-22 | v4.3: `approval_seq` + per-attempt `approval_id` (fresh approval for a retry after a confirmed failure); CLI item selector (`item_id` or a unique source id); SC-13 needs the B4-verified ‹IFSC-B› (P0); RazorpayX key validity 168 h documented, replay window 24 h; `find_by_attempt_key` = same-key replay, no search; export row timing; A9 moved to S3; agents commit, humans push | team |
| 2026-11-22 | v4.2: RazorpayX Payouts test mode replaces Stripe as the real provider (P0 if access verified); PAID = documented terminal success, separate from posting and settlement; `payment_instructions.csv` = audit export with `do_not_execute`; `DateBasis` drops `HTTP_DATE_HEADER` / `FETCH_TIME` (retrieval time lives only in `retrieved_at`); organizer I/O adapter task; Crustdata functions P0 in INTERFACES | team |
| 2026-11-22 | v4.1: provider-agnostic (FakeProvider foundation, Stripe optional); Crustdata = live data; no LLM; bookkeeping journal + `audit verify`; scenario tests assert DB, files, audit log; added SC-11, SC-12, INV-206 | team |
