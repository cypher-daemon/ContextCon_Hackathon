# PayPilot — Parallel Build Tasks (3-person team)

**Goal:** build one working CLI service, with exception management and payment safety as the core.  
**Team model:** three independent workstreams against the frozen interfaces in `INTERFACES.md`.  
**Priority:** P0 = must ship; P1 = strong demo if time; P2 = cut first.

## 1. Team ownership

Replace Builder A/B/C with actual names in the first commit.

| Builder | Owns | Primary responsibility | Must not edit without coordination |
|---|---|---|---|
| **A — Runtime & integration** | `core/`, `kitio/`, `cli/`, `payments/`, app entrypoint, DB migrations | CLI runs, persistence, approvals, provider calls, idempotency, crash/restart, final integration | `resolve/`, `checks/`, `exceptions/` internals |
| **B — Identity & evidence** | `resolve/`, `sources/`, `evidence/` | Vendor resolution, Crustdata/live API, IFSC, source records, cache/rate-limit/cost metadata | DB schema, CLI, payment executor |
| **C — Checks & exception management** | `checks/`, `exceptions/`, `reconciliation/`, `tests/fixtures/`, most tests | Duplicate/PO/bank/reconciliation checks, exception lifecycle, recovery cases, failure-mode tests | DB schema, payment executor, resolver implementation |

**Shared ownership:** `INTERFACES.md`, `config.yaml`, `schemas.py`, DB schema, `kitio` contract tests are agreed first. Builder A is integrator and final merge owner. B and C review safety-critical changes. Each person must review at least one other lane's code.

If someone finishes early, take a clearly bounded P1 task from another lane; do not start a parallel redesign.

## 2. Rules for parallel work

1. First inspect the organizer kickoff kit. Record exact filenames, schema, required/optional inputs and output format in `docs/KICKOFF_CONTRACT.md`. Until then, provisional internal contract only.
2. Freeze shared models and function signatures before coding in parallel. Commit `schemas.py`, `StageContext`, config defaults, DB schema, stubs and contract tests as the **contract commit**.
3. Each builder edits only their owned folders. Shared files require a short agreement and one owner.
4. All lanes code against the same `StageContext`, Pydantic models and fakes. Do not import another lane's private implementation.
5. Every task is complete only when its acceptance condition and relevant tests pass.
6. Small commits; no force-push or history rewrite. Merge frequently through integrator.
7. No dashboard, microservices, LLM agent orchestration or PDF/OCR unless the kit makes it necessary.
8. A failure must become a typed result/exception, not an unhandled exception or a silent pass.

## 3. S0 — Contract and skeleton (all three; do this first)

| ID | Task | Owner | Done when |
|---|---|---|---|
| S0-01 | Locate and inspect kickoff kit; record exact I/O in `docs/KICKOFF_CONTRACT.md` | A + one reviewer | Exact contract documented; if not found, explicitly record not found |
| S0-02 | Agree `INTERFACES.md`, model fields, outcome/flag enums, `StageContext`, function signatures | All | No unresolved ownership or schema ambiguity |
| S0-03 | Create package skeleton, `pyproject.toml`, `.env.example`, `.gitignore`, config defaults | A | Fresh environment imports and config validates |
| S0-04 | Create DB schema/migrations: runs, items, audit_events, approvals, postings, payments/outbox, exceptions, recovery_cases, ledger | A; C reviews | DB applies cleanly; foreign keys and unique constraints tested |
| S0-05 | Add contract tests and deterministic fakes (`FakeExtClient`, `FakeHistory`, `FakeProvider`) | A creates harness; B/C contribute | All packages import; contract tests green |
| S0-06 | Commit contract baseline; announce immutable shared interfaces | A | Other lanes can work without waiting for one another |

**Gate S0:** no feature coding until the model contract and package stubs exist. This is a short alignment gate, not a long design phase.

## 4. S1 — Parallel implementation (three lanes)

### Builder A — Runtime, persistence, CLI and payments

| ID | Priority | Task | Done when |
|---|---|---|---|
| A1 | P0 | SQLite WAL, FULL sync, foreign keys, process lock, migrations | Reopen/restart works; concurrent second process refuses cleanly |
| A2 | P0 | State machine with CAS/version and audit event in same transaction | Illegal/stale transitions rejected; event persisted atomically |
| A3 | P0 | Organizer `kitio` adapter and internal normalized loader | Official fixture loads; exact output serialization contract tests pass |
| A4 | P0 | CLI `run`, `show`, `approvals`, `approve`, `reject`, `resume`, `report`, `ledger` | One complete bank-line run from terminal |
| A5 | P0 | Approval bound to `decision_hash`; duplicate approval idempotent | Changed data invalidates approval; repeat approval cannot enqueue twice |
| A6 | P0 | Durable payment outbox and stable idempotency key | Unique constraints prevent duplicate logical payment |
| A7 | P0 | Stripe test-mode adapter + FakeProvider adapter | Test provider operation semantics documented; no live payment |
| A8 | P0 | Reconcile provider state before every retry/resume | Timeout/kill test cannot cause blind second action |
| A9 | P0 | Run report, output writer, JSONL audit and CLI error handling | All outcomes visible; secrets/account numbers redacted |

### Builder B — Vendor resolution, live data and evidence

| ID | Priority | Task | Done when |
|---|---|---|---|
| B1 | P0 | Vendor master loader mapping (using shared normalized model) | Exact identifiers and aliases match deterministically |
| B2 | P0 | Normalization and deterministic resolver | Exact ID/name/alias prioritized; tie/lookalike cannot become confident match |
| B3 | P0 | Source/Evidence model and citation rendering | Each external claim points to exact field/passage, date basis and retrieval time |
| B4 | P0 | IFSC live lookup adapter (if confirmed usable) | Live result for at least one decision; timeout/404/error typed and ledgered |
| B5 | P0 | Crustdata adapter: domain-only identify where justified, cache, rate-limit, timeout | No name-only identity claims; 402/429/401/403 handled safely |
| B6 | P1 | Web fetch for specific citable legal-identity passage | Self-published claims labelled; blocked sites skipped; no invented record date |
| B7 | P0 | External call cost hooks/metadata integrated with A's ledger interface | Every call has stable `call_key`, provider, operation and status |
| B8 | P0 | Source outage and missing evidence fixtures | Failure never maps to PASS; cannot verify names missing evidence |

### Builder C — Checks, exceptions, reconciliation and tests

| ID | Priority | Task | Done when |
|---|---|---|---|
| C1 | P0 | Required-field and intent checks | Missing required fields produce typed result, not guessed defaults |
| C2 | P0 | Duplicate invoice canonicalization and duplicate transaction fingerprint | Same logical invoice/transaction held across formatting changes |
| C3 | P0 | PO and account checks, only when input exists | Missing optional data is SKIPPED; mismatch creates reason/exception |
| C4 | P0 | Bank-change and IFSC/account comparison checks | Changed details always hold; ownership remains NOT_VERIFIED |
| C5 | P0 | Exception schema, classifier, severity and lifecycle | Every blocking check creates durable exception with evidence and next action |
| C6 | P0 | Recovery case workflow for wrong payee/duplicate/amount mismatch | Case opened, related payment stopped, status tracked; no false recovery claim |
| C7 | P0 | Reconciliation matching: intended vs provider vs bank statement | Match/conflict/unmatched/uncertain are distinct; only supplied sources used |
| C8 | P0 | Official four-line golden tests and hidden-style fixtures | Exact required results reproduced |
| C9 | P0 | Crash/timeout/duplicate/approval/exception integration tests | No duplicate posting/payment; uncertain state fails closed |
| C10 | P1 | Exception CLI/report tests and severity prioritization | Operator can inspect open exceptions and recommended actions |

## 5. S2 — Integration and end-to-end (A leads; B/C support)

| ID | Task | Owner | Done when |
|---|---|---|---|
| S2-01 | Integrate resolver → checks → decision → exception manager → runtime | A + B + C | Single CLI run completes with typed outcomes |
| S2-02 | Official four bank lines | A runs, B/C debug | Swiggy correct; AWS exact entity; second AWS held; Sharma cannot verify |
| S2-03 | Invoice approval and test-provider path | A; C tests | Approval → one provider action; accurately labelled test semantics |
| S2-04 | Kill/restart at pre-submit, in-flight, post-provider-success, pre-DB-commit | A + C | Reconcile before retry; at most one successful provider action |
| S2-05 | Wrong payout/duplicate payout simulation | C + A | Related work contained; exception and recovery case visible |
| S2-06 | Cost/time limit and source failure | A + B | Run stops safely; ledger reflects every call including unconfirmed |
| S2-07 | Fresh DB and fresh-clone rehearsal | All | No manual DB edits; deterministic output |

## 6. S3 — Documentation and demo (all)

| ID | Task | Owner | Done when |
|---|---|---|---|
| S3-01 | README: buyer, job, input, output, pass mark, setup, sources | A drafts; all verify | A teammate can run from README |
| S3-02 | Architecture diagram updated to match code | A; B/C review | Shows data calls, approval, retries, cost ledger, exception/recovery |
| S3-03 | Failure-mode matrix and test evidence | C | Every P0 scenario has expected behaviour and test ID |
| S3-04 | Finance: measured cost/item, pricing assumptions, limitations | B gathers costs; A records ledger | No invented zero costs or uncited market price |
| S3-05 | Record terminal demo video and rehearse judge kill | All; A drives | CLI visible; no fake results; repeatable |
| S3-06 | Final README, output, logs, cost ledger and repo commits | A integrates; all sign off | Repo tells one consistent story |

## 7. Priority and cut order

**Never cut:** official I/O adapter, official four case, cannot-verify behavior, source evidence, human payment approval, test-mode provider, idempotency/outbox, reconcile-before-retry, crash test, cost/time ledger, exception records for blocking issues.

**Cut first:** dashboard, rich terminal styling, PDF/OCR/LLM extraction, advanced fuzzy matching, broad Crustdata enrichment, automated external recovery/refund, optional reconciliation if no statement/history input exists.

**Important:** do not cut reconciliation of provider state after an uncertain payment. This is essential to payment safety even if bank-statement reconciliation is unavailable.

## 8. Definition of done

A feature is done only when:
- its typed interface is implemented;
- happy path and failure path are tested;
- it emits audit/evidence/cost records where applicable;
- it does not bypass approval/idempotency/state checks;
- it is integrated into a CLI run;
- README and demo do not claim more than it does.

## 9. Team sign-off

- [ ] Three actual owners assigned
- [ ] Kickoff kit inspected / missing status recorded
- [ ] `INTERFACES.md` and contract tests frozen
- [ ] Each lane working in its owned folders
- [ ] Official four regression test green
- [ ] Wrong/duplicate/uncertain payment cases green
- [ ] Kill/restart test green
- [ ] README, diagram, demo, logs and ledger ready
