# TASKS.md: two parallel lanes (Claude = runtime, Codex = decision logic)

**Rules:**
- Each lane edits only its own paths (PLAN.md §6.2).
- A task is done when its "done when" passes **and** `pytest -q tests/contract` passes.
- ★ = critical path. P0 must ship; P1 after P0 is green; P2 is cut first.
- Times are IST, freeze 6:00 PM. Checkpoints S0–S4 are in PLAN.md §6.5.
- Until the contract commit lands, Codex reads the kit and drafts; after S0, both lanes code against `tests/fakes.py`, so neither blocks the other.

## S0 — contract (target ~12:15)
| id | task | lane | paths | deps | est | P | done when |
|---|---|---|---|---|---|---|---|
| ★C00 | Contract commit: `schemas.py`, `context.py`, `db/schema.sql`, `config.yaml` (all sections with defaults), `pyproject.toml`, `.gitignore`, `.env.example`, stubs for **every** module in PLAN §7.5 (signatures + docstrings + `NotImplementedError`), `tests/fakes.py`, `tests/contract/test_contract.py`, official-4 fixtures + golden, `docs/CONTRACT_CHANGES.md` | Claude | shared + Claude paths | – | 35m | P0 | `pytest tests/contract` passes; imports work; DDL applies; config loads |
| ★X00 | `flags.py` (full Flag enum + `NEVER_OVERRIDABLE`) and `policy_config.py` (pydantic models for every Codex config section) | Codex | `apsvc/flags.py`, `apsvc/policy_config.py` | – | 20m | P0 | Claude's `core/config.py` loads `config.yaml` through these models |
| X01 | Locate the kickoff kit; write its exact I/O format into `docs/CONTRACT_CHANGES.md`; copy client files into `data/` | Codex + human | `data/`, `docs/` | – | 20m | P0 | path recorded in HACKATHON_INFORMATION.md Part A, or "not found" noted |

## S1 — vertical slice: official 4 end to end (target 1:00 PM)
| id | task | lane | paths | deps | est | P | done when |
|---|---|---|---|---|---|---|---|
| ★C01 | `core/config.py` (RunConfig, hashes, client-data load via `kitio.loaders`, bank → HMAC + last4) | Claude | core/ | C00, X00 | 30m | P0 | unit tests: hash is stable; a missing optional file gives `None` |
| ★C02 | `core/db.py`, `core/lock.py`, `core/log.py`, `core/ids.py` | Claude | core/ | C00 | 30m | P0 | WAL on; lock test; JSONL masks bank numbers |
| ★C03 | `core/state.py` ALLOWED + CAS transition + audit_events in the same txn; `core/crash.py` | Claude | core/ | C02 | 30m | P0 | an illegal transition raises; a stale version raises |
| ★C04 | `core/history.py` SqliteHistoryView; `core/pipeline.py` start_or_get_run + drive (no payments yet) | Claude | core/ | C03 | 60m | P0 | official 4 runs with stub Codex fns |
| ★C05 | CLI `run`, `resume`, `report` (typer) + `__main__.py` | Claude | cli/ | C04 | 30m | P0 | `python -m apsvc run --request tests/fixtures/official/official_oct.json --provider fake` |
| ★X01b | `kitio/loaders.py`, `kitio/adapter.py` (official-example format; FIELD_GETTERS for every default field) | Codex | kitio/ | X00 | 40m | P0 | load the official fixture → Request; write_results gives the field order from config |
| ★X02 | `ingest/structured.py` to_items + detect_intent (REFUSED on ambiguity) | Codex | ingest/ | X01b | 30m | P0 | the 4 lines → ParsedItem; paise conversion exact |
| ★X03 | `resolve/normalize.py` descriptor_key / name_key / invoice_canonical | Codex | resolve/ | X00 | 30m | P0 | table tests: RAZ*SWIGGY BLR→SWIGGY, PAYTM*SHARMA TRD, AWS EMEA keeps EMEA, invoice variants |
| ★X04 | `resolve/matcher.py` (GSTIN/CIN/bank/exact/alias/fuzzy/tie/lookalike; no ext calls yet) | Codex | resolve/ | X03 | 60m | P0 | Swiggy, AWS EMEA, Sharma tie, Delhivery lookalike, PAYTM unresolved |
| ★X05 | `checks/required.py`, `checks/duplicate.py` (K1/K2/fingerprint), `checks/account.py`, `checks/runner.py` | Codex | checks/ | X04 | 50m | P0 | AWS 2nd line → DUPLICATE_TRANSACTION; Swiggy → Staff meals with source |
| ★X06 | `decide/rules.py` + `decide/engine.py` (all reasons, severity, decision_hash) | Codex | decide/ | X05 | 45m | P0 | the official 4 decisions match golden; consistency test |
| ★C06 | **S1 integration**: `tests/integration/test_official_four.py` against the real Codex modules | Claude | tests/integration | C05, X06 | 20m | P0 | golden matches (ignoring timestamps) |

**~1:15 hour-3 change:** the person who sees it posts it in `docs/CONTRACT_CHANGES.md`. The owner of the affected config section or module applies it and updates a test. Done the same hour.

## S2 — exactly-once + live data + test payments (target 2:30 PM, checkpoint submit)
| id | task | lane | paths | deps | est | P | done when |
|---|---|---|---|---|---|---|---|
| ★C07 | `core/ledger.py` + `core/budget.py` (reserve/confirm/fail/cache-hit/UNCONFIRMED; cost + time limits) | Claude | core/ | C04 | 40m | P0 | the cost-limit test stops safely |
| ★C08 | `resolve/http.py` HttpExtClient (token bucket per endpoint, retry+jitter, 429/402/401/403/timeout mapping, cache, ledger, redaction, `llm_extract`) | Claude | resolve/http.py | C07 | 60m | P0 | wrapper tests with a fake transport |
| ★C09 | `pay/base.py`, `pay/fake.py` (separate SQLite, latency/failure injection), `pay/executor.py` (enqueue/process/reconcile, UNCERTAIN, window rule) | Claude | pay/ | C03 | 60m | P0 | create-lost-response, provider-down, expired-key tests |
| ★C10 | `cli/approvals.py` + `approvals/approve/reject` (decision_hash binding, NEVER_OVERRIDABLE, CAS) | Claude | cli/ | C09 | 40m | P0 | approve twice = no-op; override refused |
| ★C11 | `tests/kill/test_crash_points.py` + `test_kill_loop.py` (30×) | Claude | tests/kill | C09, C10 | 60m | P0 | green 30/30 |
| ★C12 | `pay/stripe_provider.py` (test key only; create/confirm/retrieve/search/refund) + live probe | Claude | pay/ | C09 | 40m | P0 | one test-mode PI succeeded via `approve`; kill during payment → resume reconciles |
| ★X07 | `resolve/ifsc.py` (via ctx.ext; VALID/NOT_FOUND/UNAVAILABLE; Source with HTTP_DATE_HEADER as retrieved_at only) | Codex | resolve/ | C00 | 20m | P0 | fake 200/404/timeout tests; one live call recorded after C08 |
| ★X08 | `checks/bank.py` (BANK_CHANGED, BANK_CHANGE_REQUEST, IFSC_INVALID, 3 statuses) | Codex | checks/ | X07 | 30m | P0 | bank-change + invalid-IFSC tests |
| X09 | `checks/po.py` (SKIPPED if no PO data; tolerance; exhausted via history) | Codex | checks/ | X05 | 30m | P1 | missing / over-tolerance tests |
| X10 | Invoice + PO + bank-change hard-case fixtures and goldens | Codex | tests/fixtures/hard_cases, tests/golden | X06 | 40m | P0 | fixtures validate against schemas |

## S3 — hidden cases + Crustdata (target 4:00 PM)
| id | task | lane | paths | deps | est | P | done when |
|---|---|---|---|---|---|---|---|
| C13 | `show` command (decision → rule → flag → check → evidence chain + audit timeline) and `ledger` CLI | Claude | cli/ | C07 | 40m | P1 | shows the chain for the bank-change item |
| C14 | STALE / data-change handling + approval SUPERSEDE; time-limit PARTIAL + resume | Claude | core/ | C04, C10 | 40m | P0 | data-change + time-limit tests |
| C15 | `tests/integration/test_hard_cases.py` runs every `hard_*` golden through the full pipeline | Claude | tests/integration | X10 | 30m | P0 | all green |
| X11 | `resolve/crustdata.py` identify_by_domain + PARENT_BRAND; confirm endpoint paths in the docs | Codex | resolve/ | C08 | 45m | P1 | aws.com fake → PARENT_BRAND; one live call in the ledger |
| X12 | `resolve/webfetch.py` identity claim + CIN passage + Cloudflare blocklist | Codex | resolve/ | X11 | 45m | P1 | delhivery passage cited as company_website_claim |
| X13 | `checks/reconciliation.py` + ALREADY_PAID_PER_STATEMENT (only if the kit has a statement/history) | Codex | checks/ | X05 | 45m | P1 | MATCHED / POSSIBLE_MATCH / UNMATCHED tests |
| X14 | `kitio/render.py` report_md (totals: ₹ awaiting approval, flagged by flag, oldest pending, recoveries, ₹/item) + item_detail | Codex | kitio/ | X01b | 40m | P0 | report for the official 4 has lines like "Payee Swiggy Limited, from the legal_name field of vendor-master record V-0012 (record dated …), retrieved …" and "CANNOT VERIFY: … Missing: …" |
| X15 | Remaining Codex failure tests (PLAN §9, rows marked X) + consistency test | Codex | tests/unit | X06 | 45m | P0 | all green |
| X16 | `ingest/document_parser.py` LLM extract + redaction + regex fallback | Codex | ingest/ | C08 | 60m | P2 | bad-JSON fallback test |

## S4 — docs (target 5:00 PM) and rehearsal (5:45 PM)
| id | task | lane | paths | deps | est | P | done when |
|---|---|---|---|---|---|---|---|
| C16 | README (job, buyer, input, finished result, pass mark, **every data source**, MCA not usable, Stripe = test charge simulation, IFSC ≠ ownership) + Mermaid diagram + `docs/architecture.md` | Claude | README.md, docs/architecture.md | C11 | 50m | P0 | a fresh clone + README steps works |
| C17 | Live smoke run (IFSC, Stripe test, Crustdata if done); paste the ledger into the README | Claude + human | – | C12, X11 | 20m | P0 | real ₹ figures (pricing filled in) |
| X17 | `docs/finance.md`: measured ₹/item from the C17 ledger, a cited outsourced-AP benchmark, proposed price | Codex + human | docs/ | C17 | 40m | P0 | every number has a source or a ledger reference |
| X18 | `docs/ethics.md`: data handled / never stored, approvals, explainability, fail-closed | Codex | docs/ | – | 30m | P0 | each point maps to a feature or test |
| R01 | Rehearsal ×2: demo script (PLAN §12), judge kill, 5-min live fix (add an output field; lower the cost limit) | both + humans | – | all P0 | 30m | P0 | under 3 min; fix under 5 min |

## Critical path
C00 → C01–C05 ∥ X00–X06 → **C06 (S1, 1:00)** → C07–C12 ∥ X07–X08, X10 → **S2 (2:30)** → C14, C15, X14, X15 → **S3 (4:00)** → C16, C17, X17 → **R01 (5:45)**.
