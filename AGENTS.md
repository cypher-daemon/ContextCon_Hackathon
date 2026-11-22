# AGENTS.md: rules for Codex (lanes B and C)

**Read first:**
1. `HACKATHON_INFORMATION.md` Part A (binding rules)
2. `docs/ARCHITECTURE.md`
3. `docs/INTERFACES.md` (the frozen contract)
4. `docs/FAILURE_MODES.md`
5. `docs/SCENARIOS.md`
6. `docs/TASKS.md` (your tasks are `B*` or `C*`)

`docs/archive/` is history only; do not follow it.

Your human tells you which lane this session is: **B** (identity & evidence) or **C** (policy, exceptions, scenarios). Claude is lane **A** (runtime) and works in parallel.

## What you may edit
| Lane | Paths | `config.yaml` sections |
|---|---|---|
| B | `paypilot/resolve/`, `paypilot/sources/`, `tests/unit/resolve/`, `tests/unit/sources/` | `sources normalize match` |
| C | `paypilot/flags.py`, `paypilot/policy_config.py`, `paypilot/checks/`, `paypilot/decide/`, `paypilot/exceptions/`, `paypilot/reconciliation/`, `clients/`, `scenarios/`, `tests/unit/{checks,decide,exceptions,reconciliation,io}/` | `required_fields checks_order period posting duplicate po bank accounts reconcile decision exceptions` |

**Never edit:**
- `schemas.py`, `context.py`, `db/`, `pyproject.toml`
- `paypilot/{core,io,cli,approvals,payments,ledger,reports,audit}/`
- `tests/{contract,integration,crash}/`, `tests/fakes.py`
- lane A's config sections, `README.md`
- `docs/` (except appending to `docs/CONTRACT_CHANGES.md`)

Need a contract change? Append to `docs/CONTRACT_CHANGES.md` and tell your human.

## Hard rules
1. **Pure code:**
   - No `sqlite3`, payment-provider SDK (`razorpay`) or `anthropic` imports.
   - Network only via `ctx.ext`; only `sources/http.py` (lane B) may import `httpx`.
   - History only via `ctx.history`.
   - Return drafts (`ExceptionDraft`, `Decision`, …); lane A persists them.
2. **Deterministic:** no randomness. Time only via `ctx.clock()`. No LLM in any decision.
3. **Never guess:**
   - Tie, lookalike, parent brand, unknown → CANNOT_VERIFY.
   - Fuzzy → HELD at most.
   - `ExtResponse.ok == False` is never a PASS.
   - A missing optional file → SKIPPED with a reason.
4. **Every fact is a `Source`** with `field_or_passage`, `record_date`, `record_date_basis`, `retrieved_at`.
   - Website text = `company_website_claim` (self-published), and the value must appear in the passage.
   - The HTTP Date header is never a `record_date`.
5. **Crustdata by domain only**, and only when a domain is known. Never by name.
6. **Don't weaken:**
   - `NEVER_OVERRIDABLE` (add only).
   - `CANNOT_VERIFY > HELD` precedence.
   - `ownership_verified` is always `NOT_VERIFIED`.
   - PAY requires an invoice number.
   - `decision_hash` excludes timestamps, source ids and non-policy config.
7. **Fixtures:** never invent CIN/GSTIN for real companies. Bank accounts are fictional. IFSC codes only as verified live by lane B. No personal data.
8. Money is integer paise; ₹ appears only in reports.

## Workflow
- `git pull --rebase` before every task and push. Small commits with your lane prefix (`resolve:` `sources:` / `checks:` `decide:` `exceptions:` `reconciliation:` `scenarios:` / `test:`). Put the task id in the message. **Agents commit; only humans push** (or explicitly tell an agent to push). **No force-push.**
- **Gate before each push:** `pytest -q tests/contract tests/unit`.
- Unit tests use `tests/fakes.py` only. No network in unit tests.
- A rebase conflict in a file you don't own → stop and tell your human.

```bash
pip install -e ".[dev]"
pytest -q tests/contract tests/unit
python -m paypilot run --scenario scenarios/SC-01 --provider fake
```
