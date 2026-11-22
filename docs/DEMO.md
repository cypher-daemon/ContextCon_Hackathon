# PayPilot: Demo Video, Live Demo & Judge Q&A

> **Thesis:** a successful payment is ordinary. Handling financial failures safely and provably is the product.
> Everything shown is real CLI output from `scenarios/` (SCENARIOS.md). Nothing is mocked in the video except the FakeProvider in SC-06, which is labelled on screen.

## 1. Recording setup
- Terminal at 120×32, large font, dark theme. Output rendered with `rich` (tables, coloured outcomes) and `--plain` available for logs.
- `python -m paypilot demo reset` before each take. `scripts/demo.sh` runs each scene with pauses, so takes are repeatable.
- Second window: the Stripe **test-mode** dashboard (Payments list), shown when "exactly one payment" is the claim.
- Diagrams come from `docs/ARCHITECTURE.md`, rendered as images (Mermaid CLI) for the opening.
- Record with OBS or asciinema → MP4 at 1080p. Keep a backup take. The live demo must still run live.

## 2. Video storyboard (~3:00)
| Time | Scene | On screen | Narration (key line) |
|---|---|---|---|
| 0:00–0:20 | Problem and buyer | Title card + one line on who pays | "Mid-size 3PLs pay hundreds of carriers a month. Duplicates and bank-change fraud cost real money. PayPilot does the AP clerk's job, safely." |
| 0:20–0:45 | System design | C4 context → layered container diagram (pure logic vs side-effect runtime) | "Decision logic never moves money. One runtime layer owns every side effect, and that's the layer we crash-test." |
| 0:45–1:10 | SC-01 month-end posting | `run --scenario scenarios/SC-01`, results table; `show BL-001` source line | "Swiggy posted to staff meals, sourced to the vendor-master field and date. The second AWS line is held as a duplicate. Sharma Traders can't be verified, so we say so, at zero cost." |
| 1:10–1:40 | SC-03 invoices + approval | `approvals` (3 bank statuses, reasons, PO match), `approve INV-201 --by "Finance Controller"`, Stripe dashboard | "IFSC checked live. Ownership is *not* verified, and we say that too. A human approves; Stripe test mode records one charge." |
| 1:40–2:10 | SC-05 crash drill | `approve` killed mid-payment (`kill -9`), then `resume` → "reconciled … 0 new payments"; Stripe still shows **one** | "The process died after Stripe accepted. On restart we ask Stripe before acting. Same idempotency key, same payment, no double pay." |
| 2:10–2:35 | SC-04 + SC-07 exceptions | `exceptions --open` (CRITICAL duplicate debit, HIGH bank-change), `exception EX-…` with the recovery case; refused `resolve-exception … RECOVERED` without evidence | "Fraud attempt contained. A duplicate debit opens a recovery case, and it can't be marked recovered without a bank reference." |
| 2:35–2:50 | Cost | `ledger`: ₹ per item, cache hits, % resolved at ₹0, UNKNOWN where a price isn't known | "Every call is in the ledger. Most items cost nothing because the client's own records answer them." |
| 2:50–3:00 | Close | Architecture slide + repo URL | "Never guess. Never pay twice. Never hide a problem." |

## 3. Live demo (finals, 3 min, judge kills at random)
1. `demo reset` → `run --scenario scenarios/SC-01` (30 s).
2. `run --scenario scenarios/SC-03` → `approvals` → `approve INV-201` (45 s).
3. **The judge kills the process at any moment.** `resume` (or re-run the same command). Show `show INV-201` (audit timeline with two sessions), `ledger`, and the Stripe dashboard (45 s).
4. `exceptions --open` on SC-04 (30 s).
5. Live fix, e.g. "add a due_date output field" or "lower the cost limit": one line in `config.yaml`, re-run `report` (≤ 5 min slot).

## 4. Pre-flight checklist
- [ ] `.env` has `STRIPE_SECRET_KEY=sk_test_…`, `BANK_HMAC_KEY`, `CRUSTDATA_API_KEY` (if SC-08)
- [ ] All IFSC placeholders in `clients/northbay/vendor_master.csv` verified live
- [ ] `pytest -q` green, including `tests/crash/test_kill_loop.py`
- [ ] Stripe test dashboard filtered to today
- [ ] Backup video file on two laptops

## 5. Judge Q&A (short, defensible)
- **"Exactly once?"** At most one successful provider action per logical payment: a durable outbox, an immutable payment key, provider idempotency, reconcile-before-act, and fail-closed PAYMENT_UNCERTAIN. We don't claim distributed atomic exactly-once, because a local DB can't commit with Stripe.
- **"What did you consider?"** An LLM agent loop, a web app with a dashboard, Postgres, Razorpay / RazorpayX. See ARCHITECTURE §11 for each trade-off.
- **"Why not let the LLM decide?"** Payments must be reproducible and explainable. Rules over normalized facts give the same answer every time; the LLM is at most a parser for messy text.
- **"How do you know the vendor is real?"** The client's vendor master is the source of truth. Live sources prove only narrow facts (IFSC → branch; Crustdata → company behind a domain; website → a self-published claim). Anything else is CANNOT_VERIFY.
- **"Money already went to the wrong vendor?"** We contain the vendor, open a recovery case and prepare the human action. We mark it recovered only with a bank or provider reference and the amount. A local rollback can't reverse a bank transfer.
- **"100× volume / second client?"** The logic layers stay as they are; the runtime moves to Postgres, queue workers, provider webhooks, a shared rate limiter and cache, and an SSO approver app. Clients are `clients/<id>/` with `client_id` in every key (ARCHITECTURE §12).
- **"Personal data?"** None about people. Account numbers are HMAC-fingerprinted at load, with only the last 4 shown. Live keys are refused. See BUSINESS.md §3.
- **"What does a job cost?"** Read it off `ledger`: measured ₹/item, ₹0 for vendor-master hits, worst case shown.
