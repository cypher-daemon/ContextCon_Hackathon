# PayPilot

**PayPilot is a CLI concept and prototype for accounts-payable work at mid-size Indian logistics companies.** It is designed to take a finance request and its batch of bank lines or invoices through vendor matching, controls, a human approval point, payment handling, reconciliation and an audit-ready result. Its governing rule is simple: never guess who a payee is, and never repeat a payment when the outcome is uncertain.

<<<<<<< HEAD
> **Submission status:** this repository contains a substantial implementation of ingestion, vendor-master resolution, deterministic checks and decisions, exception classification, journal posting, output generation and source adapters. It is not yet a complete runnable end-to-end service. The payment executor and payment reconciliation adapters are still stubs; the RazorpayX adapter, full audit verifier, scenario integration suite and crash suite are not present. On Windows, the current runtime also fails to import its `fcntl`-based process lock. See [Technical Notes](TECHNICAL.md) for exact implementation boundaries and blockers.
=======
> **Status: working build (hackathon).** Bookkeeping, payment safety (crash recovery, reconcile-first, reversal after success), RECONCILE against a bank statement, exceptions with the full lifecycle, and `audit verify` run end to end on the FakeProvider. The RazorpayX test-mode adapter is written and has been shown to create and poll a real test payout, but the crash drill and terminal states are not yet verified against it. Automated tests for the new code are still to be written (some older contract tests assume the stub executor).
>>>>>>> 7de5a9e3a36a307697633c1d1e3902c3e217ffbc

## The service and its buyer

Today, an AP clerk or outsourced AP provider receives invoices and bank activity, identifies vendors, checks invoices against records and purchase orders, catches duplicate transactions and bank-detail changes, obtains approval, pays suppliers, reconciles statements and explains exceptions. PayPilot is intended to automate the repeatable data and control steps for the **finance controller or head of AP at a mid-size Indian third-party logistics (3PL) company**. A second target buyer is an outsourced AP/BPO firm serving multiple 3PL clients.

<<<<<<< HEAD
The demo client, **NorthBay Logistics Pvt Ltd**, is fictional. Its supplier and carrier names represent real companies, but the client’s vendor records, purchase orders and bank account numbers are demo fixtures. All fixture bank account numbers are fictional. Company identifiers are used only when the project has supporting evidence.

## Input and finished result

PayPilot’s internal request format is JSON plus a batch file. A request states one explicit operation:

- `POST_BANK_LINES` — identify bank-line payees, map expenses and post eligible debits to the local journal.
- `PAY_INVOICES` — check invoices against the vendor master, payment history, purchase orders and bank details; eligible items enter an approval queue.
- `RECONCILE` — compare payment instructions, provider state and a supplied bank statement. (The model and matching logic exist, but CLI/runtime operation wiring is incomplete.)

Inputs are checked against the client’s own `vendor_master.csv` (identity and approved bank details), `purchase_orders.csv`, `payment_history.csv`, `chart_of_accounts.csv` and `policy.yaml`. The vendor master is the source of truth for vendor identity. `config.yaml` provides product defaults; the client policy supplies permitted policy overrides.

The intended finished result is one explicit outcome per item: `POSTED` (local books only), `AWAITING_APPROVAL`, `PAID` (only after provider-confirmed terminal success and approval), `HELD`, `CANNOT_VERIFY`, `REJECTED` or `PAYMENT_UNCERTAIN`. A posted journal entry is not a payment; provider confirmation is not bank settlement. Missing evidence, ambiguity, lookalikes, parent-brand results and source failures must not become a guessed identity or a pass.

The prototype already defines the JSON request and batch formats in [docs/INTERFACES.md](docs/INTERFACES.md). Amounts enter as decimal strings and are converted exactly to integer paise. The repository does not include the organizers’ kickoff kit or a kit-specific adapter; the current format is the project’s internal contract.

## Pass mark

The project’s acceptance target is that the required scenarios produce the specified output, database and audit results; every output fact links to its source; and crash/restart drills result in at most one successful provider action per logical payment. Hidden cases include a duplicate, a lookalike name and an unsettleable question that must return `CANNOT_VERIFY`.

**This target is not yet demonstrated end to end in the current checkout.** SC-01 through SC-11 scenario data is present, but there is no scenario integration/crash test suite in the repository, no SC-12 tamper test, no SC-13 RazorpayX adapter, and payment execution remains unimplemented. The current Windows CLI run is blocked by `fcntl` in `paypilot/core/lock.py`. Do not present the acceptance target as a passing result until these gaps are closed and measured.

## Design and safeguards

PayPilot is structured as a modular Python application with a SQLite persistence layer. The intended flow is:

```mermaid
flowchart LR
  OP[Operator: CLI] --> ING[Request and batch ingestion]
  ING --> DB[(SQLite: items, evidence, journal, exceptions, ledger)]
  ING --> RES[Vendor resolution]
  RES -->|vendor master first| CHECK[Deterministic checks]
  RES -.->|known domain only; when wired| LIVE[Crustdata Identify + website fetch]
  CHECK --> DEC[Policy decision]
  CHECK -.-> IFSC[Razorpay IFSC: branch metadata only]
  DEC -->|eligible invoice| HUMAN[Named human approval]
  HUMAN --> PAY[Payment provider adapter]
  PAY --> REC[Provider / bank reconciliation]
  DEC -->|post, hold, cannot verify| DB
  PAY --> DB
  REC --> DB
  DB --> OUT[Reports, audit and cost ledger]
=======
## See it work in 2 minutes
```bash
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
cp .env.example .env            # set BANK_HMAC_KEY to a random value (demo.sh falls back to a throwaway key)
bash scripts/demo.sh            # DEMO_PAUSE=1 waits for Enter between scenes
```
The demo runs eight scenes on the FakeProvider (no real money, nothing under `clients/` is modified): month-end posting; invoices with a duplicate held; refused approvals (never-overridable flag, non-approver); one approval → one payment, a second approve is a no-op; a `kill -9` mid-payment that `resume` reconciles with the same key; a bank return *after* confirmed success (payment RETURNED, item back to HELD, REVERSAL entry, CRITICAL exception + recovery case, vendor contained); a bank-statement reconcile (matched, duplicate debit, returned payment, unknown debit); and `audit verify` replaying the books. Outputs land in `.demo/out/<run_id>/`.

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
>>>>>>> 7de5a9e3a36a307697633c1d1e3902c3e217ffbc
```

Identity, check and policy code is kept separate from the runtime that owns file and database changes. Current source code uses the vendor master for resolution and can query IFSC during bank checks. Crustdata and website adapters are present, but **the current matcher does not yet call them**, so the required live identity decision is not currently integrated into an end-to-end run.

<<<<<<< HEAD
Key safeguards in the code and design:
=======
## Data sources called
Client records (₹0) · **Crustdata** Company Identify by domain + Web Fetch (the live-data source; credits; self-published evidence is labelled) · Razorpay IFSC API (optional, free; bank/branch only, never ownership) · Vendor payouts through a `PaymentProvider` interface: **RazorpayX Payouts test mode** (`--provider razorpayx_test`; dummy balance, no real money, not bank settlement) and a durable FakeProvider (`fake`, our own simulation, used by every deterministic test). Live mode is not supported. The Razorpay IFSC API is a separate free lookup. **Status: the RazorpayX adapter (`payments/razorpayx_test.py`) is written but not yet verified against the live test API (TASKS H7); needs `RAZORPAYX_*` keys in `.env`.** No LLM. Not usable: the MCA registry (Cloudflare/login). Details and limitations: [`docs/BUSINESS.md`](docs/BUSINESS.md) §4.
>>>>>>> 7de5a9e3a36a307697633c1d1e3902c3e217ffbc

- Exact identifiers and vendor-master names/aliases are considered before fuzzy similarity. Ties and lookalikes are returned as non-resolving outcomes; fuzzy candidates are not an automatic identity confirmation.
- Duplicate, period, purchase-order, account mapping and bank checks emit typed results. Decisions are deterministic rules, not an LLM.
- Bank account numbers are intended to be HMAC-fingerprinted at ingestion and masked to last four digits outside the payment boundary.
- Payment approval is bound to a decision hash and an approver allowlist. Some flags are never overridable.
- The SQLite schema and runtime implement durable item state, journal and audit primitives. The complete payment execution/recovery path is not finished, so the “pay at most once” guarantee is a design objective, not a verified claim for this checkout.
- Every source records its provider, record reference, field/passage, record-date basis and retrieval time. A website is self-published evidence; an IFSC lookup does not establish account ownership.
- External calls are intended to be reserved and accounted through a cost ledger. Crustdata’s rupee-per-credit conversion is unknown, so a cost involving those calls must remain `UNKNOWN` or incomplete rather than being represented as ₹0.

## Data sources

| Source | Purpose | Cost treatment | Limits |
|---|---|---|---|
| Client vendor master, POs, payment history and chart of accounts | Identity, approved bank details, PO status/history and expense mapping | Local files; no external call | Authoritative for this client’s recorded facts, but quality depends on the client’s records |
| Crustdata Company Identify, by domain | Associate an unknown vendor domain with a company | Per-call credits; rupee conversion is not configured | Does not establish the invoicing legal entity; can return a parent brand. Adapter exists, but is not yet called by the matcher |
| Company website pages | Capture a CIN passage as a company’s own claim | Crustdata Web Fetch credits through external client | Self-published, not registry verification; configured challenge/blocklist handling. Adapter exists, but is not yet called by the matcher |
| Razorpay IFSC API | Check IFSC branch/bank metadata | Free endpoint | Does not prove account ownership. Fixture IFSC placeholders remain unresolved, preventing the planned live fixture checks |
| FakeProvider | Deterministic payment simulation for tests/demo intent | Simulation; not a real provider | No FakeProvider adapter is present in this checkout yet |
| RazorpayX Payouts test mode | Intended real-provider sandbox payout | Test mode only; dummy balance and no real funds | Adapter and test scenario are not present in this checkout |

The MCA company registry was not usable in the project’s earlier probes (access challenge/login). Do not claim registry verification from Crustdata or a company website. The project’s [business case](docs/BUSINESS.md) records the source limitations and current pricing evidence.

## Getting started

Python 3.11+ is declared in `pyproject.toml`. Install the package and test dependency from the repository root:

```powershell
py -m pip install -e ".[dev]"
```

The full CLI currently has a Windows portability blocker: `paypilot/core/lock.py` imports `fcntl`, which is unavailable on Windows. On a compatible Unix-like runtime, the documented command shape is:

```text
python -m paypilot run --scenario scenarios/SC-01 --provider fake
```

The command should not be represented as verified until the missing runtime pieces are implemented and its scenario results have been checked. Do not place secrets in source control. `.env.example` is currently absent from this checkout; secrets belong only in a local, git-ignored `.env`. Never use live payment credentials; only a verified test-mode integration is acceptable.

## Repository guide

- `paypilot/resolve/`, `paypilot/sources/` — normalization, vendor matching and external evidence adapters.
- `paypilot/checks/`, `paypilot/decide/`, `paypilot/exceptions/`, `paypilot/reconciliation/` — deterministic policy and exception logic.
- `paypilot/io/`, `paypilot/core/`, `paypilot/ledger/`, `paypilot/cli/`, `paypilot/reports/` — ingestion, persistence, lifecycle, reporting and command surface.
- `clients/northbay/` — fictional demo client records and policy.
- `scenarios/SC-01`–`SC-11/` — demo request fixtures and expected files; `SC-12` and `SC-13` are documented but have no fixture directories here.
- `tests/contract/`, `tests/unit/` — contract and unit coverage currently present. No integration or crash test directories are present.
- [TECHNICAL.md](TECHNICAL.md) — implementation map, data model, algorithms, interfaces, run safety and known gaps.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/INTERFACES.md](docs/INTERFACES.md), [docs/FAILURE_MODES.md](docs/FAILURE_MODES.md), [docs/SCENARIOS.md](docs/SCENARIOS.md) — design and acceptance contracts.
