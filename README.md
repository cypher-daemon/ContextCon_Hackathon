# PayPilot

PayPilot is a Python CLI prototype for accounts-payable workflows at mid-size Indian logistics companies. It ingests bank lines and invoices, matches vendors against client records, runs deterministic controls, queues invoices for human approval, records payment attempts, and writes reports and audit data. It must fail closed when identity or payment outcome is uncertain.

## Current implementation status

The repository contains working modules for request and batch loading, vendor-master matching, checks and decisions, SQLite persistence, journal entries, approvals, FakeProvider payment execution and recovery, report generation, audit verification, and a read-only dashboard (`python scripts/dashboard.py`). The dashboard cannot approve or execute payments. The RazorpayX Payouts adapter is test-mode only; access and end-to-end behavior against RazorpayX have not been verified as part of this audit.

This is a prototype, not a demonstrated production or full acceptance build. Crustdata and website adapters exist but are not called by the live matcher. The INR cost conversion for Crustdata credits remains unknown. Dedicated `tests/integration` and `tests/crash` suites are absent, as are scenario fixtures for SC-12 and SC-13. See [TECHNICAL.md](TECHNICAL.md) for observed gaps and [docs/TASKS.md](docs/TASKS.md) for the project plan.

## See it work in 2 minutes
```bash
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
cp .env.example .env            # set BANK_HMAC_KEY to a random value (demo.sh falls back to a throwaway key)
python scripts/dashboard.py --db .demo/var/paypilot.db     # terminal 1: open http://127.0.0.1:8765, it reads zero
DEMO_PAUSE=1 bash scripts/demo.sh                          # terminal 2: Enter starts each scene (DEMO_BEAT=4 holds 4s after each)
```
The demo runs 21 scenes on the FakeProvider (no real money, nothing under `clients/` or `scenarios/` is modified), and each scene prints what to watch on the dashboard:
month-end bank posting and its edge cases (wrong month, a credit, a two-vendor tie); six carrier invoices (already paid, price mismatch, no PO, look-alike vendor, missing number); refusals (never-overridable flag, non-approver, unverifiable item); one approval, one payment, a second approve is a no-op; an override with a written reason; a rejection; then the payment failures: a `kill -9` mid-payment, a lost reply (outcome unknown), a provider that is still processing, a decline with a fresh-approval retry, and a bank return after confirmed success (REVERSAL entry, CRITICAL exception, vendor contained) with the containment refusal and its evidence-backed release; then a duplicate invoice re-sent in another file, a bank-change fraud attempt, a refusal against the contained vendor, an unsupported request, a bank-statement reconcile (matched, duplicate debit, returned, unmatched), and `audit verify` replaying the books. Outputs land in `.demo/out/<run_id>/`.

**Dashboard** (`scripts/dashboard.py`, read-only, stdlib only, localhost): Overview (live board of every item in every run, runs by what they did, money position, what needs a person), Payments, Exceptions, Approvals (with the exact CLI command to run) and Books (trial balance, journal, audit trail, cost). It opens the database read-only, in one snapshot per request, and shows zeros until the first run.

Fault injection for your own drills: `PAYPILOT_CRASH_AT=<point>` kills the process at a named point (see `paypilot/core/crash.py`); `PAYPILOT_FAKE_FAULT=TIMEOUT_AFTER_CREATE|TIMEOUT_BEFORE_CREATE|DECLINE|PROCESSING|RETURNED_AFTER_SUCCESS|UNKNOWN_STATUS` makes the FakeProvider misbehave; `python -m paypilot fake-provider set REF STATUS` moves a payment by hand like the RazorpayX test Dashboard.

## Quick start

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and configure the required local keys. Never commit `.env`. Then run a bank-line scenario:

```bash
python -m paypilot run --scenario scenarios/SC-01
```

Useful commands include `resume`, `report`, `show`, `approvals`, `approve`, `exceptions`, `audit verify`, and `ledger`. Run `python -m paypilot --help` for the full CLI. Product behavior and exact input/output contracts are documented in [`docs/INTERFACES.md`](docs/INTERFACES.md); architecture and safeguards are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/FAILURE_MODES.md`](docs/FAILURE_MODES.md).

## Tests

Install the development extra and run:

```bash
python -m pytest
```

The current automated suite is mostly unit and contract coverage. It does not constitute the missing end-to-end, provider-integration, or crash acceptance suites.
