# CLAUDE.md: rules for the Claude agent (runtime lane)

Read `HACKATHON_INFORMATION.md` (Part A is binding), then `PLAN.md` (the final implementation spec), then `TASKS.md` (your tasks are the `C**` ids). Codex works in parallel on the decision-logic lane, with rules in `AGENTS.md`.
If they conflict, the order is: Part A > kickoff kit > PLAN.md > unified plan.

## Your lane: runtime (every side effect)
**You own:**
- Shared contract: `apsvc/schemas.py`, `apsvc/context.py`, `db/schema.sql`, `pyproject.toml`, `.gitignore`, `.env.example`
- `apsvc/core/**`, `apsvc/pay/**`, `apsvc/cli/**`, `apsvc/resolve/http.py`, `apsvc/__main__.py`
- `tests/contract/**`, `tests/integration/**`, `tests/kill/**`, `tests/fakes.py`, `tests/fixtures/official/**`, `tests/golden/official_*.jsonl`
- `README.md`, `docs/architecture.md`
- `config.yaml`: only the sections under `# ---------- owner: claude ----------`

**Never edit** Codex's paths (listed in AGENTS.md). Requests go to `docs/CONTRACT_CHANGES.md`.

## Hard rules
1. **Persist before side effects.** Write the ledger row before an external call. Write the outbox row before Stripe. Write `provider_ref` before confirm. State change + `audit_events` row in **one** transaction. CAS on `(state, version)`.
2. **Reconcile before acting** on every `run`/`resume`. An unknown provider outcome → `PAYMENT_UNCERTAIN`. **Never create a payment because a search came back empty.**
3. Idempotency key = `pp_` + sha256(tenant, vendor_id, invoice_no_canonical, amount_paise, currency)[:32]. **Never add approval ids or versions to it.**
4. Approvals bind to `decision_hash`. `NEVER_OVERRIDABLE` is enforced in the CLI. `APSVC_TEST_AUTOAPPROVE` is honoured only with `provider=fake`.
5. **Stripe: test keys only.** Refuse `sk_live_`. Secrets come only from `.env`.
6. Bank numbers → HMAC + last4 at load time. Never log or store the full number.
7. Prices that are `null` in config are reported as "price unknown", never ₹0.
8. **Contract changes:** additive changes go in a `contract:` commit, ticked in `docs/CONTRACT_CHANGES.md`. Renames/removals need both people.

## Workflow
- Pull with `git pull --rebase` before each task and push. Make small commits with a prefix: `core:`, `pay:`, `cli:`, `http:`, `contract:`, `test:`, `docs:`. No force-push.
- **Commit or push only when your person says so.**
- Gate before each push: `pytest -q tests/contract tests/integration`. Run `tests/kill` before each checkpoint.
