# CLAUDE.md: rules for Claude (lane A, runtime & integration)

**Read first:**
1. `HACKATHON_INFORMATION.md` Part A
2. `docs/ARCHITECTURE.md`
3. `docs/INTERFACES.md`
4. `docs/FAILURE_MODES.md`
5. `docs/SCENARIOS.md`
6. `docs/TASKS.md` (your tasks are `A*`)

`docs/archive/` is history only. Codex works lanes B and C in parallel under `AGENTS.md`.

## You own
- **Shared contract:** `paypilot/schemas.py`, `paypilot/context.py`, `db/schema.sql`, `pyproject.toml`, `.gitignore`, `.env.example`. You apply every `docs/CONTRACT_CHANGES.md` request as one `contract:` commit.
- `paypilot/{__main__.py,core,io,cli,approvals,payments,ledger,reports}/`, `scripts/`, `tests/{contract,integration,crash}/`, `tests/fakes.py`, `README.md`.
- `config.yaml` sections: `limits payments approvals costs output logging storage`.

**Never edit** lane B/C paths (listed in AGENTS.md) or `docs/` without the human asking.

## Hard rules
1. **Persist before side effects:** ledger row before any call; payment + attempt rows before Stripe; `provider_ref` before confirm; state change + audit event in **one** transaction; CAS on `(state, version)`. If a write fails → exit 70 before any provider call.
2. **Reconcile first** on every `run` / `resume` / `approve`. Unknown outcome → `PAYMENT_UNCERTAIN`. **Never create a payment because a search came back empty.**
3. **`payment_key` is immutable.** A new `attempt_key` only after a provider-confirmed decline/return **and** a fresh approval. Never add approval ids or versions to a key.
4. **Approvals** bind to `decision_hash`; the approver must be in the policy allowlist; NEVER_OVERRIDABLE is refused; vendor containment is checked inside the enqueue transaction.
5. **Stripe:** test keys only; refuse `sk_live_`. Secrets only from `.env`.
6. **Accounts:** HMAC fingerprint + last 4 at load. Never log or store the raw number.
7. **Cost:** a `null` price is reported as UNKNOWN, never ₹0.

## Workflow
- `git pull --rebase` before each task and push. Lane prefixes: `core:` `io:` `cli:` `payments:` `ledger:` `reports:` `contract:` `test:`. No force-push.
- **Commit or push only when the human says so.**
- **Gate before each push:** `pytest -q tests/contract tests/integration`. Run `tests/crash/` before every checkpoint.
