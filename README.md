# PayPilot

**Accounts-payable operations for mid-size Indian logistics companies: pay every invoice at most once, never guess a payee, and manage every financial exception until it is resolved.**
ContextCon 05 · BITS Pilani · Track: Buying & Paying

> **Status: planning phase.** The design is final; implementation follows [`docs/TASKS.md`](docs/TASKS.md). Commands below are the target interface.

## The job and who pays
Payables teams and outsourced AP firms are paid to match bank lines and invoices to vendors, catch duplicates and bank-detail fraud, get approvals, pay, and chase errors. PayPilot does that job for the **finance controller of a mid-size 3PL**, or for an **outsourced AP firm** serving many of them.

## Input → finished result
- **Input:** a request (`POST_BANK_LINES`, `PAY_INVOICES` or `RECONCILE`) plus a batch file, checked against the client's own records: vendor master (source of truth), POs, payment history, chart of accounts.
- **Finished result:** every item is `POSTED` (local books only), `PAID` (after human approval, once the provider reports its documented successful terminal status; settlement against the bank statement is tracked separately), `HELD` (with reasons), `CANNOT_VERIFY` (what is missing is named), `REJECTED`, or `PAYMENT_UNCERTAIN` (stop and reconcile). Every money movement is a balanced double-entry journal entry an auditor can replay (`audit verify`). Every blocking problem is a tracked exception; wrong payments open a recovery case. Every fact cites its source; every external call is in the cost ledger.
- **Pass mark:** every scenario in [`docs/SCENARIOS.md`](docs/SCENARIOS.md) matches its expected outcome; 30 random `kill -9`s produce at most one successful payment per invoice; every output fact has a source.

## Quick start (target)
```bash
pip install -e ".[dev]" && cp .env.example .env    # BANK_HMAC_KEY + CRUSTDATA_API_KEY (+ RAZORPAYX_* test keys for SC-13)
python -m paypilot run --scenario scenarios/SC-01    # month-end bank posting
python -m paypilot run --scenario scenarios/SC-03                    # invoices → approval queue
python -m paypilot approvals
python -m paypilot approve <ITEM_ID> --by "Finance Controller"
python -m paypilot resume                                             # after any crash
python -m paypilot show <ITEM_ID>    # decision → rule → flag → check → evidence → source
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
Client records (₹0) · **Crustdata** Company Identify by domain + Web Fetch (the live-data source; credits; self-published evidence is labelled) · Razorpay IFSC API (optional, free; bank/branch only, never ownership) · Vendor payouts through a `PaymentProvider` interface: **RazorpayX Payouts test mode** (`--provider razorpayx_test`; dummy balance, no real money, not bank settlement) and a durable FakeProvider (`fake`, our own simulation, used by every deterministic test). Live mode is not supported. The Razorpay IFSC API is a separate free lookup. **Status: the RazorpayX adapter is not yet built or verified (TASKS H7, A9).** No LLM. Not usable: the MCA registry (Cloudflare/login). Details and limitations: [`docs/BUSINESS.md`](docs/BUSINESS.md) §4.

## Rules we hold ourselves to
Test-mode payments only (`rzp_test_` keys; live keys refused) · secrets only in `.env` · no data about people · bank accounts stored only as fingerprints + last 4 · a person approves every payment · "cannot verify" beats a wrong answer.
