# PayPilot

**Accounts-payable operations for mid-size Indian logistics companies: pay every invoice at most once, never guess a payee, and manage every financial exception until it is resolved.**
ContextCon 05 · BITS Pilani · Track: Buying & Paying

> **Status: working build (hackathon).** Bookkeeping, payment safety (crash recovery, reconcile-first, reversal after success), exceptions and `audit verify` run end to end on the FakeProvider. The RazorpayX test-mode adapter is written but unverified against the live test API. RECONCILE (statement matching, TASKS A12) is waiting on lane C's three-way match. Automated tests for the new code are still to be written.

## The job and who pays
Payables teams and outsourced AP firms are paid to match bank lines and invoices to vendors, catch duplicates and bank-detail fraud, get approvals, pay, and chase errors. PayPilot does that job for the **finance controller of a mid-size 3PL**, or for an **outsourced AP firm** serving many of them.

## Input → finished result
- **Input:** a request (`POST_BANK_LINES`, `PAY_INVOICES` or `RECONCILE`) plus a batch file, checked against the client's own records: vendor master (source of truth), POs, payment history, chart of accounts.
- **Finished result:** every item is `POSTED` (local books only), `PAID` (after human approval, once the provider reports its documented successful terminal status; settlement against the bank statement is tracked separately), `HELD` (with reasons), `CANNOT_VERIFY` (what is missing is named), `REJECTED`, or `PAYMENT_UNCERTAIN` (stop and reconcile). Every money movement is a balanced double-entry journal entry an auditor can replay (`audit verify`). Every blocking problem is a tracked exception; wrong payments open a recovery case. Every fact cites its source; every external call is in the cost ledger.
- **Pass mark:** every scenario in [`docs/SCENARIOS.md`](docs/SCENARIOS.md) matches its expected outcome; 30 random `kill -9`s produce at most one successful payment per invoice; every output fact has a source.

## See it work in 2 minutes
```bash
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
cp .env.example .env            # set BANK_HMAC_KEY to a random value (demo.sh falls back to a throwaway key)
bash scripts/demo.sh            # DEMO_PAUSE=1 waits for Enter between scenes
```
The demo runs seven scenes on the FakeProvider (no real money, nothing under `clients/` is modified): month-end posting; invoices with a duplicate held; refused approvals (never-overridable flag, non-approver); one approval → one payment, a second approve is a no-op; a `kill -9` mid-payment that `resume` reconciles with the same key; a bank return *after* confirmed success (payment RETURNED, item back to HELD, REVERSAL entry, CRITICAL exception + recovery case, vendor contained); and `audit verify` replaying the books. Outputs land in `.demo/out/<run_id>/`.

Fault injection for your own drills: `PAYPILOT_CRASH_AT=<point>` kills the process at a named point (see `paypilot/core/crash.py`); `PAYPILOT_FAKE_FAULT=TIMEOUT_AFTER_CREATE|TIMEOUT_BEFORE_CREATE|DECLINE|PROCESSING|RETURNED_AFTER_SUCCESS|UNKNOWN_STATUS` makes the FakeProvider misbehave; `python -m paypilot fake-provider set REF STATUS` moves a payment by hand like the RazorpayX test Dashboard.

## Quick start
```bash
pip install -e ".[dev]" && cp .env.example .env    # BANK_HMAC_KEY + CRUSTDATA_API_KEY (+ RAZORPAYX_* test keys for SC-13)
python -m paypilot run --scenario scenarios/SC-01    # month-end bank posting
python -m paypilot run --scenario scripts/demo_data/pay-invoices   # invoices → approval queue (needs real IFSC codes; see scripts/demo.sh)
python -m paypilot approvals
python -m paypilot approve INV-201 --by "Finance Controller"    # item_id or a unique invoice_id / line_id
python -m paypilot resume                                             # after any crash
python -m paypilot show BL-001       # decision → rule → flag → check → evidence → source
python -m paypilot exceptions --open
python -m paypilot audit verify      # replay the books line by line
python -m paypilot ledger            # ₹ per item, measured
```

## Documentation
| Doc | What's in it |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design: context, components, lifecycle, exactly-once, state machines, data model, evidence, exceptions, decisions considered, scaling |
| [`docs/INTERFACES.md`](docs/INTERFACES.md) | Exact contracts: I/O formats, models, IDs, DB schema, config, ports, CLI |
| [`docs/FAILURE_MODES.md`](docs/FAILURE_MODES.md) | 52 failure modes, crash points, resume protocol, reconciliation, recovery |
| [`docs/SCENARIOS.md`](docs/SCENARIOS.md) | The demo world (fictional client, real vendors) and expected outcomes |
| [`docs/DEMO.md`](docs/DEMO.md) | Video storyboard, live demo, judge Q&A |
| [`docs/BUSINESS.md`](docs/BUSINESS.md) | Buyer, measured unit economics, ethics, data sources |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) · [`docs/TASKS.md`](docs/TASKS.md) | Decision log, open questions · build plan by lane |

## Data sources called
Client records (₹0) · **Crustdata** Company Identify by domain + Web Fetch (the live-data source; credits; self-published evidence is labelled) · Razorpay IFSC API (optional, free; bank/branch only, never ownership) · Vendor payouts through a `PaymentProvider` interface: **RazorpayX Payouts test mode** (`--provider razorpayx_test`; dummy balance, no real money, not bank settlement) and a durable FakeProvider (`fake`, our own simulation, used by every deterministic test). Live mode is not supported. The Razorpay IFSC API is a separate free lookup. **Status: the RazorpayX adapter (`payments/razorpayx_test.py`) is written but not yet verified against the live test API (TASKS H7); needs `RAZORPAYX_*` keys in `.env`.** No LLM. Not usable: the MCA registry (Cloudflare/login). Details and limitations: [`docs/BUSINESS.md`](docs/BUSINESS.md) §4.

## Rules we hold ourselves to
Test-mode payments only (`rzp_test_` keys; live keys refused) · secrets only in `.env` · no data about people · bank accounts stored only as fingerprints + last 4 · a person approves every payment · "cannot verify" beats a wrong answer.
