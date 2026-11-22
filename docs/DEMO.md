# PayPilot: Demo Video, Live Demo & Judge Q&A

> **Thesis:** a successful payment is ordinary. Handling financial failures safely and provably is the product.
> Everything shown is real CLI output from `scenarios/` (SCENARIOS.md). The vendor payout runs through **RazorpayX Payouts test mode** (SC-13). The fault-injection scenes (SC-05 random kill, SC-06 timeout) run on the FakeProvider, labelled **"simulated provider"** on screen.
> **Claims we never make:** that a test payout moved real money or settled at a bank; that a card payment, a payment link or a CSV export is a vendor payout.
> **If H7 (RazorpayX test access) is blocked:** every payment scene runs on the FakeProvider, labelled "simulated provider", and the video states the exact blocker and that the provider requirement is **not met**.

## 1. Recording setup
- Terminal at 120×32, large font, dark theme. Output rendered with `rich` (tables, coloured outcomes) and `--plain` available for logs.
- `python -m paypilot demo reset` before each take. `scripts/demo.sh` runs each scene with pauses, so takes are repeatable.
- Second pane: `python -m paypilot ledger` / `sqlite3 var/fake_provider.db` (the provider's own record), shown when "exactly one payment" is the claim. For SC-13, show the RazorpayX **test-mode** Dashboard (the "Test Mode" label visible) with exactly one `pout_…` payout.
- Diagrams come from `docs/ARCHITECTURE.md`, rendered as images (Mermaid CLI) for the opening.
- Record with OBS or asciinema → MP4 at 1080p. Keep a backup take. The live demo must still run live.

## 2. Video storyboard (~3:00)
| Time | Scene | On screen | Narration (key line) |
|---|---|---|---|
| 0:00–0:20 | Problem and buyer | Title card + one line on who pays | "Mid-size 3PLs pay hundreds of carriers a month. Duplicates and bank-change fraud cost real money. PayPilot does the AP clerk's job, safely." |
| 0:20–0:45 | System design | C4 context → layered container diagram (pure logic vs side-effect runtime) | "Decision logic never moves money. One runtime layer owns every side effect, and that's the layer we crash-test." |
| 0:45–1:10 | SC-01 month-end posting | `run --scenario scenarios/SC-01`, results table; `show BL-001` source line | "Swiggy posted to staff meals, sourced to the vendor-master field and date. The second AWS line is held as a duplicate. Sharma Traders can't be verified, so we say so, at zero cost." |
| 1:10–1:35 | SC-03 held items + SC-13 approval and payout | `approvals` (bank statuses, reasons, PO match; INV-204 lookalike CANNOT_VERIFY, INV-203 HELD); `approve INV-201 --by "Finance Controller"` → `pout_…` in `processing`, item PAYMENT_IN_PROGRESS; Dashboard → `processed`; `resume` → PAID + journal entries | "A human approves. RazorpayX test mode accepts the payout, but accepted isn't paid. Only when the provider reports `processed` do we mark it PAID. That's test mode, so no real money moved, and settlement is still a separate check." |
| 1:35–1:50 | SC-08 live evidence | an unknown carrier: Crustdata identify(domain) + website passage cited as *self-published*; AWS India → parent-brand mismatch → CANNOT_VERIFY | "Live data supports a decision; it never overrules the client's records, and we label exactly what it proves." |
| 1:50–2:15 | SC-13 step 5 crash drill (SC-05 on the FakeProvider if H7 is blocked) | `approve` killed mid-payout (`kill -9`), then `resume` → "reconciled … 0 new payments"; the Dashboard (or FakeProvider DB) still shows **one** payout | "The process died after the provider accepted. On restart we replay the same idempotency key before doing anything else. Same payout, no double pay. If we can't prove the outcome, we stop at PAYMENT_UNCERTAIN." |
| 2:15–2:35 | SC-04 + SC-07 exceptions | `exceptions --open` (CRITICAL duplicate debit, HIGH bank-change), the recovery case; refused `resolve-exception … RECOVERED` without evidence | "Fraud attempt contained. A duplicate debit opens a recovery case, and it can't be marked recovered without a bank reference." |
| 2:35–2:52 | Auditor replay + cost | `audit verify` → every decision re-derived, books balanced; `ledger` → ₹ per item, % resolved at ₹0 | "An auditor can replay every line. And most items cost nothing, because the client's own records answer them." |
| 2:52–3:00 | Close | Architecture slide + repo URL | "Never guess. Never pay twice. Never hide a problem." |

## 3. Live demo (finals, 3 min, judge kills at random)
1. `demo reset` → `run --scenario scenarios/SC-01` (30 s).
2. `run --scenario scenarios/SC-03 --provider razorpayx_test` → `approvals` → `approve INV-201` → show `pout_…` + status → Dashboard `processed` → `resume` (45 s). The FakeProvider, labelled, if H7 is blocked.
3. **The judge kills the process at any moment.** `resume` (or re-run the same command). Show `show INV-201` (audit timeline with two sessions), the provider record (one payment), and `audit verify` (45 s).
4. `exceptions --open` on SC-04 (30 s).
5. Live fix, e.g. "add a due_date output field" or "lower the cost limit": one line in `config.yaml`, re-run `report` (≤ 5 min slot).

## 4. Pre-flight checklist
- [ ] `.env` has `BANK_HMAC_KEY`, `CRUSTDATA_API_KEY`, and `RAZORPAYX_KEY_ID` (`rzp_test_…`), `RAZORPAYX_KEY_SECRET`, `RAZORPAYX_ACCOUNT_NUMBER` (the test-mode Customer Identifier)
- [ ] RazorpayX test balance is enough for the demo payouts (otherwise they sit in `queued`)
- [ ] SC-08 live lookups run once before recording (they're cached afterwards); IFSC codes verified if the IFSC check is enabled
- [ ] `pytest -q` green, including `tests/crash/test_kill_loop.py`
- [ ] `audit verify` exits 0 on the demo DB
- [ ] Backup video file on two laptops

## 5. Judge Q&A (short, defensible)
- **"Exactly once?"** At most one successful provider action per logical payment: a durable outbox, an immutable payment key, provider idempotency, reconcile-before-act, and fail-closed PAYMENT_UNCERTAIN. We don't claim distributed atomic exactly-once, because a local DB can't commit atomically with any external provider.
- **"Why RazorpayX?"** PayPilot pays vendors, so the operation has to be a payout. Stripe PaymentIntents and the Razorpay Payment Gateway collect money from a customer instead. RazorpayX is an adapter behind `PaymentProvider`. Correctness is proven against a durable FakeProvider that simulates success, decline, timeout, pending, and "accepted just before the crash", and the same executor drives RazorpayX test mode. Limits we state openly: RazorpayX doesn't document how long an idempotency key is honoured or a search by reference, so past a short replay window we fail closed.
- **"What did you consider?"** An LLM agent loop, a web app with a dashboard, Postgres, Stripe / Razorpay Payment Gateway (discarded: they charge customers). See ARCHITECTURE §11 for each trade-off.
- **"Why not let the LLM decide?"** Payments must be reproducible and explainable. Rules over normalized facts give the same answer every time; the LLM is at most a parser for messy text.
- **"How do you know the vendor is real?"** The client's vendor master is the source of truth. Live sources prove only narrow facts (IFSC → branch; Crustdata → company behind a domain; website → a self-published claim). Anything else is CANNOT_VERIFY.
- **"Money already went to the wrong vendor?"** We contain the vendor, open a recovery case and prepare the human action. We mark it recovered only with a bank or provider reference and the amount. A local rollback can't reverse a bank transfer.
- **"100× volume / second client?"** The logic layers stay as they are; the runtime moves to Postgres, queue workers, provider webhooks, a shared rate limiter and cache, and an SSO approver app. Clients are `clients/<id>/` with `client_id` in every key (ARCHITECTURE §12).
- **"Personal data?"** None about people. Account numbers are HMAC-fingerprinted at load, with only the last 4 shown. Live keys are refused. See BUSINESS.md §3.
- **"What does a job cost?"** Read it off `ledger`: measured ₹/item, ₹0 for vendor-master hits, worst case shown.
