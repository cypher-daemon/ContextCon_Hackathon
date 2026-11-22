# PayPilot

**Autonomous accounts-payable operations and exception-management CLI**  
ContextCon 05 · Buying & Paying · BITS Pilani

## What it does

PayPilot processes bank lines and vendor invoices for a fictional mid-size Indian 3PL. It classifies bank lines, validates invoices, checks vendor identity and available PO/payment/bank records, requires human approval before payments, and uses a test-mode payment adapter.

Its central capability is financial exception management: detecting duplicates and mismatches, containing unsafe actions, reconciling uncertain outcomes, surviving process crashes, and tracking recovery cases when an incorrect payment may already have happened.

**It is a CLI-first modular monolith. We are not building a dashboard.**

## Important distinctions

- `POST_BANK_LINES` creates a local accounting classification/posting. It does not move money.
- `PAY_INVOICES` is a separate approval and test-provider workflow.
- Provider-confirmed is not the same as bank-settled.
- A recovery case is not proof that funds have been recovered.
- Stripe PaymentIntent is a test card-charge simulation, not a vendor bank payout.

## Team documents

Read these in order:

1. [`ARCHITECTURE.md`](ARCHITECTURE.md) — product scope and system design.
2. [`INTERFACES.md`](INTERFACES.md) — shared models, provisional input/output and function boundaries.
3. [`TASKS.md`](TASKS.md) — three parallel workstreams, owners and done conditions.
4. [`FAILURE_MODES.md`](FAILURE_MODES.md) — exception policy and recovery playbook.
5. [`DEMO_AND_ACCEPTANCE.md`](DEMO_AND_ACCEPTANCE.md) — demo sequence, tests and judge Q&A.
6. `docs/KICKOFF_CONTRACT.md` — create/update this after inspecting the actual organizer kit.
7. `docs/CONTRACT_CHANGES.md` — record any shared contract change.

## Setup (implementation target)

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Never commit `.env` or real secrets. Use test-mode provider credentials only.

## CLI (target interface; commands may change during implementation)

```bash
python -m paypilot run --request PATH
python -m paypilot resume --run RUN_ID
python -m paypilot approvals --run RUN_ID
python -m paypilot approve ITEM_ID --by "Finance Controller"
python -m paypilot show ITEM_ID
python -m paypilot exceptions --run RUN_ID
python -m paypilot report --run RUN_ID
python -m paypilot ledger --run RUN_ID
```

The actual organizer I/O format overrides the provisional internal examples in `INTERFACES.md`.

## Pass mark

- Official four bank-line case produces the specified results.
- Duplicate, lookalike and unanswerable cases are conservative and source-backed.
- Payments require human approval and test mode.
- Crashes/timeouts/restarts cannot create a second successful provider action.
- Uncertain outcomes are reconciled, never blindly retried.
- Wrong/duplicate/amount-mismatch payments create durable exceptions and recovery cases.
- Every factual claim has exact source evidence/date basis.
- Every external call is cost-ledgered and run limits are enforced.

## Data sources

List the actual sources called by the implementation here before submission. For each, state endpoint/provider, what fact it supports, limitations, record date basis, retrieval time and cost. Do not list a source that was merely planned but never called.

## Architecture

See `ARCHITECTURE.md` for the Mermaid diagram. Keep the diagram synchronized with implemented code.
