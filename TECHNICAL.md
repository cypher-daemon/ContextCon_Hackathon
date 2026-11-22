# PayPilot Technical Status

This document records what is implemented in this checkout and the gaps found while reviewing the README, architecture, contracts, failure modes, and tests. `docs/` describes the intended behavior; it is not proof that every acceptance scenario has been demonstrated.

## Implemented in the repository

- Python 3.11+ CLI using Typer/Rich, strict Pydantic models, YAML configuration, SQLite persistence, transaction helpers, a durable process lock, and atomic output writes.
- Request and batch parsing, client vendor/PO/history/chart loading, HMAC bank-account fingerprints, vendor-master resolution, deterministic checks and decisions, and source adapters for Crustdata, website fetch, and IFSC lookups.
- Item state transitions, durable audit events, exception and recovery-case workflows, double-entry journals, reports, cost ledger, audit verification, and operational reconciliation code.
- Approval records bound to decision hashes, approver checks, an atomic payment outbox enqueue, FakeProvider execution, idempotent payment attempt steps, and crash/reconcile handling. The RazorpayX adapter is explicitly test-mode only.
- The CLI lock uses the Windows locking API on Windows and `fcntl` on POSIX. The report writer emits UTF-8; interactive Windows CLI output selects UTF-8, while redirected output retains its consumer encoding and replaces unsupported characters.

## Known gaps and verification limits

1. The live resolver does not call the Crustdata or website adapters. Those sources therefore do not contribute evidence to current identity decisions.
2. Crustdata credit-to-INR conversion is unset/unknown. The ledger cannot report a complete INR cost for those calls.
3. RazorpayX credentials/access, real test payouts, provider pending states, and provider crash behavior have not been verified in this audit. The adapter is test-mode only; no live payment mode is supported.
4. The repository has unit and contract tests, but no dedicated `tests/integration` or `tests/crash` suites. The SC-12 tamper and SC-13 RazorpayX fixtures are absent. Existing unit/contract coverage does not replace those acceptance drills.
5. Windows file mode bits do not describe Windows ACLs. The logger requests restrictive POSIX mode `0600`, but inherited ACL permissions on Windows were not inspected or enforced here.
6. The test run in this audit is on native Windows/Python 3.14, not the project's minimum Python 3.11 or a POSIX environment. Provider/network integrations were not exercised.

## Safety notes

- Bank-account fingerprints are used for matching and display; raw account values are only read at the payout boundary and should not be logged or persisted.
- `payment_instructions.csv` is an audit/reconciliation export with `do_not_execute=TRUE`, never a manual payment file.
- If a provider outcome is uncertain, the same attempt is reconciled and no new approval proceeds until that uncertainty is resolved. Missing provider references are treated as uncertain instead of being sent to a provider lookup.
- A payment success is recorded only from the provider's terminal-success status. Provider confirmation and bank settlement are distinct.
- `.env` is ignored by Git. Do not commit local credentials; check tracked paths with `git ls-files -- .env` without printing the file.

## Test layout

`tests/unit` exercises isolated modules; `tests/contract` exercises interfaces and CLI/database behavior. Run both with `python -m pytest`. Current acceptance targets and unverified scenarios are listed in [`docs/SCENARIOS.md`](docs/SCENARIOS.md), [`docs/FAILURE_MODES.md`](docs/FAILURE_MODES.md), and [`docs/TASKS.md`](docs/TASKS.md).
