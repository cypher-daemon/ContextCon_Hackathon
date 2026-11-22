# PayPilot: Build Tasks (parallel lanes)

> **Lanes:** **A = Claude** (runtime). **B = Codex** (identity & evidence). **C = Codex** (policy, exceptions, scenarios). **H = humans.** Ownership is in §1; contracts are in INTERFACES.md.
> **Done when:** the "done when" condition passes **and** `pytest -q tests/contract` is green. ★ = critical path. P0 must ship · P1 if time · P2 cut first.
> **Times** are targets (IST, freeze 18:00). If S0 lands late, shift every checkpoint by the same amount and cut P2 first.
> **Priority order (DECISIONS D15):** bookkeeping → vendor resolution and evidence → duplicate/fraud detection → exceptions and approvals → crash-safe state → reconciliation → audit report + CLI demo → real payment-provider test mode (only if time). **Nothing in P0 depends on Stripe, Razorpay payments or an LLM.**

## 1. Ownership (edit only your own paths)

| Lane | Paths | Config sections |
|---|---|---|
| A | `paypilot/{__main__,schemas,context}.py`, `paypilot/{core,io,cli,approvals,payments,ledger,reports,audit}/`, `db/`, `pyproject.toml`, `.gitignore`, `.env.example`, `scripts/`, `tests/{contract,integration,crash}/`, `tests/fakes.py`, `README.md` | `limits payments approvals costs output logging storage` |
| B | `paypilot/{resolve,sources}/`, `tests/unit/{resolve,sources}/` | `sources normalize match` |
| C | `paypilot/{flags,policy_config}.py`, `paypilot/{checks,decide,exceptions,reconciliation}/`, `clients/`, `scenarios/`, `tests/unit/{checks,decide,exceptions,reconciliation,io}/` | `required_fields checks_order period posting duplicate po bank accounts reconcile decision exceptions` |
| H | `docs/` (agents only append to `docs/CONTRACT_CHANGES.md`), `.env` (never committed) | — |

## 2. S0: Contract (target 13:15)

| ID | Task | Lane | Deps | Est | P | Done when |
|---|---|---|---|---|---|---|
| ★A0 | Contract commit: `pyproject.toml` (pydantic, pyyaml, httpx, rapidfuzz, typer, rich, pytest; `stripe` only as an optional extra `[stripe]`), `.gitignore`, `.env.example`; `schemas.py`, `context.py` (ports); `db/schema.sql`; `config.yaml` (all sections, defaults); stub for **every** module in INTERFACES §9 (signature + docstring + `NotImplementedError`); `tests/fakes.py` (FakeExtClient, FakeHistory, FakeLedger, FakeCache, FakeBudget, `make_ctx`); `tests/contract/` (imports, signatures, DDL applies, config validates) | A | – | 40m | P0 | contract tests green on a fresh clone |
| ★C0 | `flags.py` (FAILURE_MODES §1 + `NEVER_OVERRIDABLE`); `policy_config.py` (models for C and B config sections) | C | – | 20m | P0 | `core/config.py` stub validates `config.yaml` through these models |
| H1 | `.env`: `BANK_HMAC_KEY` (random), `CRUSTDATA_API_KEY`; confirm the Crustdata credit balance | H | – | 10m | P0 | `GET /account/credits` works |
| H2 | Ask the Crustdata team for ₹ per credit; note the answer + source | H | – | 10m | P0 | recorded in DECISIONS.md §3 |

## 3. S1: Vertical slice, SC-01 end to end (target 14:15)

| ID | Task | Lane | Deps | Est | P | Done when |
|---|---|---|---|---|---|---|
| ★A1 | `core/config.py` (merge config + policy, strict, `config_hash` / `policy_hash`), `core/db.py`, `core/lock.py`, `core/ids.py`, `core/log.py` + `core/audit.py` | A | A0, C0 | 40m | P0 | hashes stable; lock refuses a 2nd process; JSONL masks accounts |
| ★A2 | `core/state.py` (ALLOWED map, CAS on state+version, audit in the same txn) + `core/crash.py` | A | A1 | 30m | P0 | illegal/stale transitions raise; audit row is atomic |
| ★A3 | `io/` request + batch (decimal-string → paise, fingerprint at load) + client data (+ data-quality scan → `data_quality`) | A | A1 | 40m | P0 | SC-01 loads; no raw account in any model |
| ★A4 | `core/history.py` + `core/pipeline.py` drive (ingest → resolve → check → decide → classify → post/queue/hold) + **`core/journal.py`**: balanced double-entry entries written in the same txn as POSTED / accrual / PAID, balance asserted before commit | A | A2, A3 | 75m | P0 | SC-01 runs with stub logic; journal balanced; `journal.csv` written |
| ★A5 | `cli/` run/resume/report/show (+ `rich` tables) + `reports/writer.py` (results.jsonl, report.md, summary.json, atomic writes, FIELD_GETTERS) | A | A4 | 45m | P0 | `python -m paypilot run --scenario scenarios/SC-01 --provider fake` prints the table |
| ★B1 | `resolve/normalize.py`: descriptor_key, name_key, invoice_canonical | B | A0 | 30m | P0 | table tests: RAZ*, PAYTM*, UPI/…/BENGALURU, EMEA kept, invoice variants (`BDE/26-27/7781` ≡ `BDE-26-27-07781`) |
| ★B2 | `resolve/matcher.py`: GSTIN/CIN → fingerprint → exact → alias → fuzzy; tie; lookalike; vendor-master Sources | B | B1 | 60m | P0 | Swiggy, AWS EMEA, PAYTM unresolved, Om Sai tie, Delhivery lookalike |
| ★C1 | `clients/northbay/` (vendor master with ‹IFSC› placeholders, POs, history, policy.yaml) + `scenarios/SC-01`, `SC-02` + `expected.jsonl` | C | C0 | 40m | P0 | files validate against schemas |
| ★C2 | `checks/required.py`, `period.py`, posting-scope (credit / currency / non-positive), `runner.py` | C | C0 | 40m | P0 | SC-02 BL-102 / BL-103 flagged |
| ★C3 | `checks/duplicate.py` (canonical invoice, txn window, fingerprint/UTR, payment history) | C | B1 | 45m | P0 | SC-01 BL-003, SC-03 INV-202 |
| ★C4a | `checks/account.py` (vendor default → category map → ACCOUNT_UNMAPPED) | C | C0 | 20m | P0 | Swiggy → Staff meals with source |
| ★C6 | `decide/rules.py` (load + validate: every flag covered) + `decide/engine.py` (all reasons, precedence, decision_hash) | C | C2 | 45m | P0 | SC-01 proposals match; consistency test (BL-001 vs BL-101) |
| ★C7a | `exceptions/classify.py` (flag → type/severity pre/post via `exceptions.map`) | C | C6 | 30m | P0 | SC-01 BL-003 / BL-004 produce the right drafts |
| ★A-S1 | **Integrate:** `tests/integration/test_scenarios.py` runs SC-01 against the real B/C modules | A | A5, B2, C1–C7a | 20m | P0 | SC-01 matches `expected.jsonl` |

## 4. S2: Payment safety (target 15:30) **Checkpoint commit**

| ID | Task | Lane | Deps | Est | P | Done when |
|---|---|---|---|---|---|---|
| ★A6 | `ledger/` (reserve/confirm/fail/cache-hit, UNCONFIRMED on resume, summary) + Budget (cost/time; unknown_reserve) + CachePort | A | A1 | 40m | P0 | the cost-limit test stops safely |
| ★A7 | `approvals/` + CLI approvals/approve/reject (allowlist, NEVER_OVERRIDABLE, decision_hash binding, lock wait, inline execute) | A | A4 | 40m | P0 | double approve = no-op; override refused; stale refused |
| ★A8 | `payments/`: PaymentProvider, FakeProvider (own SQLite, fault injection), payments + attempts, two-step executor, `reconcile_open`, vendor containment check in the enqueue txn | A | A7 | 70m | P0 | FAILURE_MODES #23–26 tests green |
| ★A10 | `tests/crash/`: every crash point, kill loop ×30, SIGTERM, persistence failure (exit 70, no provider call) | A | A8 | 60m | P0 | 30/30 green; at most one provider success per payment key |
| ★B3 | `sources/http.py` ExtClient (token bucket per endpoint, retry + jitter, 429 → `x-ratelimit-reset`, 402 → stop, 401/403 → typed, timeout, CachePort, LedgerPort, BudgetPort, redaction) | B | A0 | 60m | P0 | fake-transport tests for 200/429/402/403/timeout/budget |
| ★C4b | `checks/po.py` (SKIPPED if no POs; tolerance pct/abs; qty; exhausted) | C | C0 | 30m | P0 | SC-03 INV-203 |
| ★C5 | `checks/bank.py` (fingerprint vs master → BANK_CHANGED; BANK_CHANGE_REQUEST from messages; VENDOR_CONTAINED via history; 3 statuses with `ifsc_valid` = NOT_CHECKED until B4 lands) | C | C0 | 40m | P0 | SC-04 MSG-301 / INV-302 |
| ★C9a | `scenarios/SC-03` (incl. INV-206 missing PO)…`SC-06` + `expected.jsonl` + `expected_db.json` + expected audit sequences | C | C1 | 60m | P0 | integration test diffs clean on files, DB and audit log |
| ★A-S2 | **Integrate SC-03…SC-06** on the FakeProvider | A | above | 30m | P0 | all green; after a kill, the FakeProvider DB shows exactly 1 success for INV-201; `payment_instructions.csv` has 1 row |
| ★B5 | `sources/crustdata.py` identify_by_domain (confirm endpoint/paths in docs.crustdata.com/llms.txt) + PARENT_BRAND | B | B3 | 45m | P0 | SC-08 INV-402 with a fake; **one live call recorded in the ledger** (the live-data decision) |
| ★B6 | `sources/webfetch.py` identity claim + CIN passage + Cloudflare blocklist | B | B5 | 45m | P0 | SC-08 INV-401 passage cited as `company_website_claim` |

## 5. S3: Exceptions, reconciliation, live identity (target 16:30)

| ID | Task | Lane | Deps | Est | P | Done when |
|---|---|---|---|---|---|---|
| ★A11 | Exception + recovery persistence (upsert drafts by `exception_id`), CLI exceptions/exception/resolve-exception (uses C's lifecycle evidence rule) | A | A8, C7b | 45m | P0 | RECOVERED without evidence refused |
| ★A12 | RECONCILE operation wiring (statement load, three_way per payment/line, exceptions, containment) | A | A11, C8 | 40m | P0 | SC-07 green |
| ★A13 | STALE on data/policy change + approval SUPERSEDE; limits → PARTIAL / STOPPED_COST; unsupported-request run-level result | A | A7 | 40m | P0 | SC-09, SC-10, data-change test |
| ★C7b | `exceptions/lifecycle.py` (allowed transitions; RESOLVED/RECOVERED need evidence) + `exceptions/recovery.py` (prepare case + action) | C | C7a | 30m | P0 | unit tests |
| ★C8 | `reconciliation/match.py` three_way (MATCHED / POSSIBLE / CONFLICT / UNMATCHED / NOT_AVAILABLE / PENDING; dup debit, returned, wrong payee/amount) | C | C0 | 45m | P0 | FAILURE_MODES #4–6, #27–29 unit tests |
| ★C9b | `scenarios/SC-07`, `SC-08`, `SC-09`, `SC-10`, `SC-11` (re-sent file) + expected files, DB and audit | C | C9a, B6 | 45m | P0 | diffs clean |
| C10 | Remaining C-owned FAILURE_MODES unit tests | C | above | 40m | P0 | every C row has a passing test |
| ★A17 | **`audit verify`**: replay decisions from stored facts + policy snapshot; approval ↔ attempt ↔ journal links; one confirmed attempt per payment key; balanced entries + trial balance; audit-chain continuity; source resolution. Plus the SC-12 tamper test | A | A4, A11 | 50m | P0 | SC-12 green; a tampered DB copy fails with the entry named |
| B4 | `sources/ifsc.py` (optional check) + **verify the real IFSC codes** for ‹IFSC-A/B/C/D› and a 404 code for ‹IFSC-INVALID›; give them to C; C5 then sets `ifsc_valid` | B | B3 | 30m | P1 | live 200 + 404 recorded in the ledger; SC-04 INV-303 shows IFSC_INVALID |



## 6. S4: Video, docs, finance (target 17:30) and freeze (18:00)

| ID | Task | Lane | Deps | Est | P | Done when |
|---|---|---|---|---|---|---|
| ★A14 | `demo reset`, `scripts/demo.sh` (scene-by-scene with pauses), rich output polish | A | A-S2 | 30m | P0 | the full storyboard runs unattended |
| ★A15 | README (job, buyer, input, finished result, pass mark, data sources table, how to run, limitations) + Mermaid diagrams exported as images | A | A14 | 40m | P0 | a fresh clone + README steps works |
| A16 | `report --html` static read-only report (for the video) | A | A5 | 40m | P2 | opens in a browser |
| A9 | **Optional extension:** `payments/stripe_provider.py` behind `PaymentProvider` (test key only, refuses `sk_live_`; create/confirm/retrieve/search) + live probe. Only after S3 is green and an `sk_test_` key exists | A | A8 | 40m | P2 | one Stripe test PaymentIntent via `approve`; the kill drill shows 1 PI; labelled a card-charge simulation |
| ★H3 | Finance benchmark research: cited outsourced-AP cost per invoice (URL + date) → BUSINESS §2 | H | – | 30m | P0 | every number has a citation |
| ★H4 | Fill BUSINESS §2 from real `ledger` runs | H | A-S2 | 15m | P0 | no placeholders left |
| ★H5 | Record the video (DEMO §2), plus a backup take | H | A14 | 40m | P0 | MP4 ≤ 3:30 |
| ★H6 | Rehearse the live demo twice, including a random judge kill and a 5-min live fix | H | A14 | 20m | P0 | under 3 min, fix under 5 min |

**Critical path:** A0/C0 → A1–A5 ∥ B1–B2 ∥ C1–C7a → **S1** → A6–A8, A10 ∥ B3, B5–B6 ∥ C4b, C5, C9a → **S2** → A11–A13, A17 ∥ C7b, C8, C9b ∥ B4 → **S3** → A14, A15, H3–H6 → freeze. A9 (Stripe) and A16 (HTML) are optional.

## 7. Working rules (all lanes)
- Trunk-based on `main` after this plan merges. Small commits with a lane prefix (`core:` `io:` `cli:` `payments:` `ledger:` `reports:` / `resolve:` `sources:` / `checks:` `decide:` `exceptions:` `reconciliation:` `scenarios:` / `contract:` `test:` `docs:`).
- `git pull --rebase` before each push. **No force-push, no history rewrite.** Rebase conflict in another lane's file → stop and tell your human.
- **Gate before each push:** `pytest -q tests/contract` + your lane's tests.
- Shared-contract change → entry in `docs/CONTRACT_CHANGES.md` → lane A applies it (`contract:` commit) → the others pull.
- If a lane finishes early, take a P1 task from another lane only after telling that lane's human. No parallel redesigns.
