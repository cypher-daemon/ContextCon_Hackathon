# PayPilot

PayPilot is a Python CLI prototype for accounts-payable workflows at mid-size Indian logistics companies. It ingests bank lines and invoices, matches vendors against client records, runs deterministic controls, queues invoices for human approval, records payment attempts, and writes reports and audit data. It must fail closed when identity or payment outcome is uncertain.

## Current implementation status

The repository contains working modules for request and batch loading, vendor-master matching, checks and decisions, SQLite persistence, journal entries, approvals, FakeProvider payment execution and recovery, report generation, and audit verification. The RazorpayX Payouts adapter is test-mode only; access and end-to-end behavior against RazorpayX have not been verified as part of this audit.

This is a prototype, not a demonstrated production or full acceptance build. Crustdata and website adapters exist but are not called by the live matcher. The INR cost conversion for Crustdata credits remains unknown. Dedicated `tests/integration` and `tests/crash` suites are absent, as are scenario fixtures for SC-12 and SC-13. See [TECHNICAL.md](TECHNICAL.md) for observed gaps and [docs/TASKS.md](docs/TASKS.md) for the project plan.

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
