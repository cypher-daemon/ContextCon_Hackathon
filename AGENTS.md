# AGENTS.md: rules for the Codex agent (decision-logic lane)

Read `HACKATHON_INFORMATION.md` (Part A is binding), then `PLAN.md` (the final implementation spec), then `TASKS.md` (your tasks are the `X**` ids). `plan/PayPilot_Unified_Architecture_and_Build_Plan.md` is design background.
If they conflict, the order is: Part A > kickoff kit > PLAN.md > unified plan.

## Your lane: decision logic
You write **pure functions** that turn inputs + `StageContext` into `Request`, `ParsedItem`, `Resolution`, `CheckResult`, `Decision`, and rendered output.

**You may edit only:**
- `apsvc/flags.py`, `apsvc/policy_config.py`
- `apsvc/kitio/**`, `apsvc/ingest/**`, `apsvc/checks/**`, `apsvc/decide/**`
- `apsvc/resolve/normalize.py`, `apsvc/resolve/matcher.py`, `apsvc/resolve/crustdata.py`, `apsvc/resolve/webfetch.py`, `apsvc/resolve/ifsc.py`
- `tests/unit/**`, `tests/fixtures/hard_cases/**`, `tests/golden/hard_*.jsonl`, `data/**`
- `docs/finance.md`, `docs/ethics.md`
- `config.yaml`: **only** the sections under `# ---------- owner: codex ----------`
- `docs/CONTRACT_CHANGES.md`: append-only

**Never edit:**
- `apsvc/schemas.py`, `apsvc/context.py`, `db/schema.sql`, `pyproject.toml`
- `apsvc/core/**`, `apsvc/pay/**`, `apsvc/cli/**`, `apsvc/resolve/http.py`
- `tests/contract/**`, `tests/integration/**`, `tests/kill/**`, `tests/fakes.py`
- Claude's config sections
- `PLAN.md`, `TASKS.md`, `AGENTS.md`, `CLAUDE.md`

If you need a change there, append a request to `docs/CONTRACT_CHANGES.md` (what, why, exact signature) and tell your person.

## Hard rules for your code
1. **No side effects:**
   - Do not import `sqlite3`, `httpx`, `requests`, `stripe` or `anthropic`.
   - All network access goes through `ctx.ext` (`get_json`, `post_json`, `fetch_page`, `llm_extract`); all history reads go through `ctx.history`.
   - Do not write files, except in `kitio.adapter.write_results`.
2. **Deterministic:** the same inputs + config give the same output. No randomness, no wall-clock reads except `ctx.clock()`, no LLM in any decision.
3. **Never guess:**
   - Ties, lookalikes, parent brands and unknowns → CANNOT_VERIFY.
   - An `ext` error (`ok=False`) is never a pass; it becomes `SOURCE_UNAVAILABLE` / `CREDITS_EXHAUSTED` / `COST_LIMIT`.
   - A fuzzy match is HOLD at most.
4. **Every fact has a `Source`** with `field_or_passage`, `record_date`, `record_date_basis` and `retrieved_at`.
   - Website text is `source_type="company_website_claim"`, and the value must appear in the passage.
   - The HTTP `Date` header is never a `record_date`.
5. **Crustdata:** identify **by domain only**. Never by name. Never call it when no domain is known.
6. **Config, not constants:** every threshold, token list, mapping, rule and output field comes from `config.yaml`, through `policy_config.py` models with `extra="forbid"`.
7. **Do not weaken:**
   - `NEVER_OVERRIDABLE` (you may only add to it).
   - CANNOT_VERIFY > HOLD severity.
   - "IFSC ≠ ownership" (`ownership_verified` is always `NOT_VERIFIED`).
   - The rule that PAY requires an invoice number.
8. Money is integer paise. Display ₹ only in kitio/render.

## Workflow
- Pull with `git pull --rebase` before starting each task and before each push. Make small commits with a prefix: `kitio:`, `ingest:`, `resolve:`, `checks:`, `decide:`, `test:`, `docs:`. **No force-push. No history rewrites.**
- Before each push: `pytest -q tests/contract tests/unit` must pass.
- Unit tests use only `tests/fakes.py` (`FakeExtClient`, `FakeHistory`, `make_ctx`). Never hit the network in a unit test.
- If a rebase conflicts in a file you don't own, stop and tell your person.
- Mark a task done in your commit message with its `X**` id.

## Commands
```bash
uv sync                                   # or: pip install -e .[dev]
pytest -q tests/contract tests/unit       # your gate
python -m apsvc run --request tests/fixtures/official/official_oct.json --provider fake
```
