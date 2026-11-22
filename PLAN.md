# PLAN.md (v2, final): PayPilot, an exactly-once AP service for a mid-size Indian 3PL

> **Status:** FINAL implementation plan. Major decisions are closed; only minor changes come later, through `docs/CONTRACT_CHANGES.md`.
> **Precedence when documents disagree:** `HACKATHON_INFORMATION.md` Part A (binding) > kickoff kit > **this file** > `plan/PayPilot_Unified_Architecture_and_Build_Plan.md` (design rationale) > everything else.
> **Build model:** two coding agents in parallel, **Claude** (runtime lane) and **Codex** (decision-logic lane), each supervised by a person. See §6.
> **Kit status:** the kickoff kit is not found yet. We design against the official example in Part A. All kit-format knowledge lives in `apsvc/kitio/`; swapping in the real format changes only that folder and `config.yaml`.

---

## 1. Summary (5 lines)
- **Job:** post each bank line to the correct payee and account, and pay each vendor invoice **exactly once**, through crashes, duplicates, lookalike names and bank-detail fraud. Payables teams and outsourced AP firms are paid to do this today.
- **Who pays:** the 3PL's finance controller / AP team, or an outsourced AP firm serving many 3PLs. Priced per item processed.
- **Input:** a request (e.g. "Post these bank lines from October 2026 to the correct accounts") plus bank lines and/or invoices, checked against the client's vendor master (plus POs, payment history and chart of accounts *if the kit supplies them*).
- **Finished result:** every item ends in exactly one sourced outcome: `POSTED`, `PAID` (Stripe **test mode**, after human approval), `HELD` (reasons given), `CANNOT_VERIFY` (what is missing is named), `REJECTED`, or `PAYMENT_UNCERTAIN` (stop and reconcile). Each run also produces a per-call cost ledger, a JSONL log, a report, and an audit trail.
- **Pass mark:** the official 4 lines give exactly the official results. The hidden cases (duplicate, lookalike, unsettleable) never produce a wrong payee or a second post or payment. The kill -9 loop produces at most one successful payment per invoice and no duplicate posting, approval or ledger rows.

---

## 2. Changes from the unified plan
Everything in `plan/PayPilot_Unified_Architecture_and_Build_Plan.md` is adopted, with these four changes:

| # | Unified plan | This plan | Reason |
|---|---|---|---|
| D1 | Stripe test mode is P1; FakeProvider is the main provider | **Stripe test mode is P0** (due by 2:30 PM). FakeProvider is used for automated crash tests only | Part A requires "Payments in test mode (Razorpay or Stripe)". A demo on FakeProvider alone fails a binding rule. |
| D2 | IFSC lookup is P1 | **Razorpay IFSC lookup is P0** | Part A requires live data for at least one decision. IFSC is free, needs no login and takes about 0.45 s. Crustdata stays P1. |
| D3 | Idempotency key includes `payment_instruction_version` | **No version component.** `idem_key = "pp_" + sha256(tenant_id, vendor_id, invoice_no_canonical, amount_paise, currency)[:32]` | A version field is a back door that gives the same invoice a new payment identity. A genuinely distinct payment needs a distinct invoice number. |
| D4 | Three builders | **Two parallel lanes (Claude, Codex)** with humans supervising and reviewing | That is how the team works. The split follows the boundary between decision logic and side effects (§6). |

---

## 3. Definitions (binding for code, report and Q&A)
- **Exactly-once (our claim):** one logical payment instruction produces **at most one successful external payment**, across duplicate inputs, repeated approvals, crashes and restarts. If the provider outcome cannot be established, PayPilot stops and reconciles (`PAYMENT_UNCERTAIN`) instead of issuing another payment. A local transaction cannot commit atomically with Stripe. Provider idempotency plus a persisted outbox plus reconcile-before-act is what makes this hold.
- **POSTED:** a write to our **local** journal (`postings` table + export file), done once. It is reversible and internal, so it runs without approval (`actions.POST.requires_approval: false`). Posting into an external accounting system would require approval.
- **HELD:** we know what the issue is (duplicate, PO variance, bank change, source down…) and a person must resolve it. Only flags explicitly marked overridable can be approved through.
- **CANNOT_VERIFY:** reliable evidence for a required fact does not exist (unknown payee, tie, lookalike, parent brand, parse failure). It names what is missing. It can **never** be converted into a payment by an override.
- **PAYMENT_UNCERTAIN:** the payment was sent to the provider but its outcome is unknown. It is never retried blindly; it is only reconciled against the provider.
- **WEBSITE_IDENTITY_CLAIM:** a company website states its own legal name or CIN. This is self-published evidence, labelled as such. It is never "verified".
- **Bank verification has three statuses, always shown separately:**
  - `ifsc_valid`: Razorpay IFSC returned the bank and branch.
  - `account_matches_master`: the invoice account hash equals the vendor-master hash.
  - `ownership_verified`: **always `NOT_VERIFIED`**. No source we use proves who owns an account.

---

## 4. Architecture decisions (chosen · alternatives · why · trade-off)

**4.1 Structure: a CLI modular monolith; pure decision logic inside a side-effecting runtime.**
- The stages are `INGEST → RESOLVE → CHECK → DECIDE → (APPROVE) → EXECUTE → REPORT`.
- Stage functions (Codex lane) are pure, or perform only reads through `ctx.ext` and `ctx.history`.
- The runtime (Claude lane) persists every stage result before the next stage runs, and owns every side effect.
- *Alternatives:* an LLM agent loop (non-deterministic; can't defend exactly-once); queue + workers (that's the 100× answer); FastAPI + dashboard (time, plus more surface for the kill test).
- *Trade-off:* sequential throughput. That is fine at hackathon volume.

**4.2 State store: SQLite, WAL, `synchronous=FULL`, `foreign_keys=ON`, one file `var/paypilot.db`, plus an `fcntl.flock` single-instance lock.**
- *Alternatives:* Postgres (setup time, no gain for one process); JSON files (no atomic multi-row transactions).
- *Trade-off:* one writer at a time, enforced by the lock. `tenant_id` columns are present, but the system is single-client today (composite tenant foreign keys are future work).

**4.3 Item state machine** (§8): `version` compare-and-swap on every transition. Every external side effect is preceded by a committed row describing it.

**4.4 Exactly-once:**
- deterministic `item_id`
- `transaction_fingerprint` for logical identity
- the `payments` outbox, with UNIQUE `idempotency_key` and UNIQUE `item_id`
- two-step Stripe calls (create, then confirm), each with a provider idempotency key
- reconcile on every `run`/`resume` before any new work
- approvals UNIQUE on `(item_id, decision_hash)`
- ledger UNIQUE on `call_key`
- *Alternatives:* create-and-confirm in one call (fewer states, but no persisted provider reference before money moves); no outbox (a crash between "decided" and "paid" is ambiguous).

**4.5 Payment provider: Stripe test mode (P0) behind `PaymentProvider`; FakeProvider (SQLite, separate file) for kill tests.**
- *Alternatives:* Razorpay Payments (a server-only payment needs Checkout to capture; no general idempotency header); RazorpayX Payouts (the right semantics and has `X-Payout-Idempotency`, but activation/KYC risk today, so it becomes a documented future adapter).
- *Trade-off:* a Stripe PaymentIntent is a **test card-charge simulation, not a vendor bank payout**. The README and report say so.
- Fallback: if INR fails on our test account, set `payments.currency_override: usd` and keep the INR amount in metadata.

**4.6 Payee resolution:**
- Vendor master first. Order: GSTIN/CIN → bank-account hash → exact name key → exact alias → fuzzy (HOLD only).
- Tie, lookalike or parent brand → CANNOT_VERIFY.
- Crustdata Identify **by domain only**, and only when a domain is known. Web Fetch only for a citable CIN passage, which yields a `WEBSITE_IDENTITY_CLAIM`.
- No domain → no paid call → CANNOT_VERIFY at ₹0.
- *Alternatives:* Crustdata name match (Part C: three "Sharma Traders" all at 1.0); embedding or LLM name judge (non-deterministic). Full algorithm in §7.6.

**4.7 Checks:**
- duplicate: K1 = invoice identity; K2 = transaction similarity / fingerprint / UTR
- PO (only if the kit has POs)
- account mapping
- bank change + IFSC
- required fields
- reconciliation (only if the kit has a statement or history)
- Errors never pass; they become `SOURCE_UNAVAILABLE` → HOLD.

**4.8 Decision engine:**
- Checks emit flags. Config rules map flags to an outcome and a reason template.
- *All* matching reasons are collected. The most severe outcome wins (`CANNOT_VERIFY > HOLD > default action`).
- `decision_hash = sha256(canonical_json(Facts) + config_hash + client_data_hash)`.
- *Alternatives:* a rules DSL or expression evaluation (eval risk); LLM decisions (forbidden).
- *Guarantee:* same normalized facts → byte-identical decision.

**4.9 Config:** one `config.yaml`, sectioned by lane owner, validated strictly (`extra="forbid"`), hashed, and re-read on every run/resume (§7.8). An hour-3 change is a 1–2 line edit (§7.8 examples).

**4.10 Output:** `kitio/adapter.py` is the only module that knows the kit format. `output.fields` sets the field order. `kitio/render.py` produces `report.md`. Every fact has a `Source` with `record_date` + `record_date_basis` + `retrieved_at`.

**4.11 Cost ledger + limits:**
- Every external call is reserved, then confirmed/failed/cache-hit. Rows still RESERVED after a crash become `UNCONFIRMED` on resume and count at their estimate (conservative).
- Pricing (₹ per credit, ₹ per Mtok) comes from config and **must be filled with real numbers before screening**.
- Hitting the cost limit → `COST_LIMIT` → HOLD, and no more paid calls. Hitting the time limit → run `PARTIAL`, and `resume` continues.

**4.12 Logging:**
- JSONL per run (`var/logs/<run_id>.jsonl`), plus an `audit_events` table written **in the same transaction** as each state change. The DB audit trail is the truth; JSONL is the readable copy.
- `session_id` changes on each restart, so recoveries are visible.

**4.13 Approvals:**
- CLI `approvals / approve / reject`. An approval binds to `decision_hash`.
- Override requires `--reason`. Identity flags, bank-change flags and `PAYMENT_OUTCOME_UNKNOWN` can never be overridden.
- Re-approving is a no-op, because the update is compare-and-swap on `status='PENDING'`.

**4.14 External calls: one wrapper (`resolve/http.py`, Claude) handles everything for every provider.**
- It does: timeouts; a per-endpoint token bucket; retry with jitter; 429 → wait for `x-ratelimit-reset`; 402 → stop paid calls; 401/403 → loud failure, never treated as evidence; the response cache; ledger writes; redaction.
- Provider-specific request building and response parsing (`crustdata.py`, `webfetch.py`, `ifsc.py`; Codex) go through the wrapper. They never call `httpx` directly.

**4.15 Scale / second client:**
- 100×: Postgres with the same DDL; workers claim items with `FOR UPDATE SKIP LOCKED`; a separate outbox poller; Redis for the cache and a shared rate limiter; batch Identify. Idempotency keys already make retries safe.
- Second client: per-client `clients/<tenant>/config.yaml` and data; composite `(tenant_id, id)` keys.
- Both are future work; today the system is single-client and multi-tenant ready.

---

## 5. Architecture diagram (goes into README)
```mermaid
flowchart LR
  subgraph IN["Client files + config (re-read and hashed every run)"]
    REQ[Request + bank lines / invoices]
    VM[Vendor master]
    PO[POs - if in kit]
    HIST[Payment history / statement - if in kit]
    CFG[config.yaml]
  end
  subgraph LOGIC["Codex lane - decision logic (no DB writes, no direct network)"]
    ADP[kitio adapter]
    ING[ingest: structured validate / LLM extract]
    RES[resolve: GSTIN, exact, alias, fuzzy<br/>tie / lookalike / parent brand -> CANNOT_VERIFY]
    CHK[checks: required fields, duplicate K1/K2, PO, account, bank + IFSC, reconcile]
    DEC[decide: config rules -> POST / PAY / HOLD / CANNOT_VERIFY<br/>reasons + sources + decision_hash]
  end
  subgraph SHELL["Claude lane - runtime (all side effects)"]
    PIPE[pipeline + state machine<br/>CAS transitions, crash points]
    DB[(SQLite WAL<br/>items, evidence, audit_events,<br/>postings, approvals, payments, cost_ledger, http_cache)]
    EXT[external-call wrapper<br/>rate limit, retry/backoff, cache,<br/>timeout -> HOLD, ledger reserve/confirm]
    BUD{budget: cost + time limits}
    APQ{{approval queue - CLI<br/>who / when / why, bound to decision_hash}}
    PEX[payment executor<br/>outbox: create, persist id, confirm<br/>reconcile first on every resume]
  end
  REQ --> ADP --> PIPE
  PIPE --> ING --> RES --> CHK --> DEC --> PIPE
  VM --> RES
  PO --> CHK
  HIST --> CHK
  CFG --> DEC
  RES -. ctx.ext .-> EXT
  CHK -. ctx.ext .-> EXT
  ING -. ctx.ext LLM .-> EXT
  EXT --> CRU[[Crustdata Identify by domain]]
  EXT --> WF[[Crustdata Web Fetch]]
  EXT --> IFSC[[Razorpay IFSC - live, free]]
  EXT --> LLM[[Claude LLM - extraction only]]
  EXT --> BUD
  PIPE <--> DB
  PIPE -- PAY --> APQ -- approved --> PEX --> STRIPE[[Stripe TEST mode]]
  PEX --> DB
  PIPE -- POST --> DB
  PIPE --> OUT[results in kit format + report.md + JSONL log + ledger]
```

---

## 6. Parallel build model: Claude lane vs Codex lane

### 6.1 The boundary
- **Codex lane (decision logic):** pure functions. They may read `ctx.history` (read-only views) and call `ctx.ext` (the wrapper). They **never** import `sqlite3`, `httpx`, `stripe` or `anthropic`, and never write files except through kitio output functions called by the CLI.
- **Claude lane (runtime):** everything that persists or causes a side effect: DB, state machine, outbox, payments, ledger, cache, retries, locks, crash points, CLI.
- **Why this split:** the kill test exercises only Claude's code, so Codex's code can't break exactly-once. All of Codex's code can be unit-tested with fakes and needs no infrastructure. It is also a clean design answer: "decision logic with no side effects, all side effects in one crash-tested runtime".

### 6.2 Ownership (each lane edits only its own paths)
| Path | Owner |
|---|---|
| `apsvc/schemas.py`, `apsvc/context.py`, `db/schema.sql`, `pyproject.toml`, `.gitignore`, `.env.example` | **Claude (shared contract)** |
| `apsvc/core/*`, `apsvc/pay/*`, `apsvc/cli/*`, `apsvc/resolve/http.py`, `apsvc/__main__.py` | Claude |
| `tests/contract/*`, `tests/integration/*`, `tests/kill/*`, `tests/fakes.py`, `tests/fixtures/official/*`, `tests/golden/official_*.jsonl` | Claude |
| `README.md`, `docs/architecture.md` | Claude |
| `apsvc/flags.py`, `apsvc/policy_config.py` | **Codex (shared contract)** |
| `apsvc/kitio/*`, `apsvc/ingest/*`, `apsvc/resolve/{normalize,matcher,crustdata,webfetch,ifsc}.py`, `apsvc/checks/*`, `apsvc/decide/*` | Codex |
| `tests/unit/*`, `tests/fixtures/hard_cases/*`, `tests/golden/hard_*.jsonl`, `data/*` (client files from the kit) | Codex |
| `docs/finance.md`, `docs/ethics.md` | Codex |
| `config.yaml` | **split by top-level section** (§7.8): each lane edits only its own sections |
| `PLAN.md`, `TASKS.md`, `AGENTS.md`, `CLAUDE.md`, `docs/CONTRACT_CHANGES.md` | humans; agents append to `CONTRACT_CHANGES.md` only |

### 6.3 Interfaces between the lanes (frozen in the contract commit)
```python
# Claude's pipeline calls Codex's functions in this order. All take a StageContext.
kitio.adapter.load_request(path: Path, cfg: RunConfig) -> Request
ingest.structured.to_items(request: Request, ctx: StageContext) -> list[ParsedItem]          # deterministic
ingest.document_parser.extract(raw_text: str, kind: ItemKind, ctx: StageContext) -> Payload  # P2; LLM via ctx.ext.llm_extract
resolve.matcher.resolve(item: Item, ctx: StageContext) -> Resolution
checks.runner.run_checks(item: Item, resolution: Resolution, ctx: StageContext) -> list[CheckResult]
decide.engine.decide(item: Item, resolution: Resolution, checks: list[CheckResult], ctx: StageContext) -> Decision
kitio.adapter.write_results(results: list[ItemResult], cfg: RunConfig, out_dir: Path) -> Path
kitio.render.report_md(results: list[ItemResult], ledger: LedgerSummary, run: RunInfo, cfg: RunConfig) -> str
kitio.render.item_detail(result: ItemResult, audit: list[AuditEvent], evidence: list[Source]) -> str  # for `show`

# Claude provides these, through StageContext:
class StageContext:            # apsvc/context.py
    cfg: RunConfig; cfg_hash: str; data: ClientData; data_hash: str
    run_id: str; tenant_id: str
    ext: ExtClient             # Protocol below
    history: HistoryView       # Protocol below
    clock: Callable[[], datetime]

class ExtClient(Protocol):     # implemented in resolve/http.py (Claude); FakeExtClient in tests/fakes.py
    def get_json(self, provider: str, endpoint: str, url: str, *, params: dict | None = None,
                 item_id: str | None, stage: str) -> ExtResponse: ...
    def post_json(self, provider: str, endpoint: str, url: str, body: dict, *,
                  item_id: str | None, stage: str) -> ExtResponse: ...
    def fetch_page(self, url: str, *, item_id: str | None, stage: str) -> ExtResponse: ...      # Crustdata Web Fetch
    def llm_extract(self, prompt: str, schema: type[BaseModel], *, item_id: str | None, stage: str) -> ExtResponse: ...
    # ExtResponse(ok: bool, status: int | None, data: Any, headers: dict, error: ExtError | None,
    #             cache_hit: bool, retrieved_at: datetime, request_hash: str)
    # ExtError in {TIMEOUT, RATE_LIMITED, CREDITS_EXHAUSTED, AUTH, FORBIDDEN, NOT_FOUND, SERVER, BAD_BODY, BUDGET, BLOCKED}
    # The wrapper never raises on network failure; it returns ok=False. Budget exhaustion -> error=BUDGET.

class HistoryView(Protocol):   # implemented in core/history.py (Claude); FakeHistory in tests/fakes.py
    def prior_by_invoice(self, vendor_id: str, invoice_no_canonical: str, *, exclude_item: str) -> list[PriorRecord]: ...
    def prior_by_txn(self, key: str, amount_paise: int, date_from: date, date_to: date, *, exclude_item: str) -> list[PriorRecord]: ...
    def prior_by_fingerprint(self, fingerprint: str, *, exclude_item: str) -> list[PriorRecord]: ...
    def po_billed_paise(self, po_number: str, *, exclude_item: str) -> int: ...
    # PriorRecord: item_id, input_ref, state, outcome, date, amount_paise, source ("this_run" | "db" | "payment_history")
    # Same-run ordering: an item sees only items with (date, line_no) earlier than its own, so the first occurrence proceeds.
```

### 6.4 Git protocol (two agents, one repo)
- **Branch:** work happens on `main` after this plan is merged (`plan_phase` → `main` by a person). Make small commits and run `git pull --rebase` before every push. **No force-push, no history rewrites** (organizers read the history).
- Each agent works in its own checkout. It must not edit the other lane's paths. If a rebase conflict appears in another lane's file, stop and tell your person.
- **Before every push:** `pytest -q tests/contract` plus your lane's tests must pass.
- **Commit prefixes:** `core:`, `pay:`, `cli:`, `http:` (Claude); `kitio:`, `ingest:`, `resolve:`, `checks:`, `decide:` (Codex); `contract:` (shared files); `docs:`, `test:`.
- **Contract change protocol:**
  1. The requester appends an entry to `docs/CONTRACT_CHANGES.md` (what, why, which signatures).
  2. The owner of that contract file makes the change in one small `contract:` commit and ticks the entry.
  3. The other lane pulls before relying on it.
- Additive changes (a new optional field, a new Flag, a new config key in your own section) do not need approval. Renames and removals need both people.
- **Neither agent may change:** the meaning of states or outcomes, the idempotency-key formula, the approval binding, or the rule that CANNOT_VERIFY can't be overridden.

### 6.5 Integration checkpoints (both lanes must pass; owner of the check in brackets)
| Checkpoint | Time | Must pass |
|---|---|---|
| **S0 contract** | ~12:15 | `tests/contract` passes on both checkouts: imports, signatures, config loads, DDL applies, fixtures validate [Claude] |
| **S1 vertical slice** ★ | 1:00 PM | `apsvc run --request tests/fixtures/official/official_oct.json --provider fake` gives `tests/golden/official_oct.jsonl` (ignoring timestamps); `tests/integration/test_official_four.py` [Claude wires, Codex logic] |
| Hour-3 change | ~1:15 | change applied in the owning lane's config section or module; test updated [owner of the area] |
| **S2 exactly-once** ★ | 2:30 PM | `tests/kill/test_kill_loop.py` passes 30 times; `test_crash_points[*]`; one live IFSC call in the ledger; one Stripe test PaymentIntent succeeded via `approve` [Claude]; bank/PO/required-field unit tests [Codex]. **Checkpoint submit.** |
| **S3 hidden cases** | 4:00 PM | all `tests/golden/hard_*.jsonl` match through the full pipeline; consistency test; cost-limit and timeout tests [both] |
| **S4 docs** | 5:00 PM | a fresh clone + README steps works; finance numbers come from a real ledger run [both] |
| **Rehearsal** | 5:45 PM | 3-min demo script (§12) + 5-min live fix rehearsed twice |

★ = critical path. If one lane is late, the other keeps going against `tests/fakes.py` and is not blocked.

---

## 7. Exact interfaces

### 7.1 `apsvc/schemas.py` (Claude; pydantic v2; money is always integer paise; datetimes are timezone-aware, IST for display)
```python
class State(StrEnum):   NEW, PARSED, RESOLVED, CHECKED, DECIDED, POSTING, POSTED, AWAITING_APPROVAL, APPROVED, REJECTED,
                        PAYMENT_PENDING, PAYMENT_CREATED, PAYMENT_CONFIRMING, PAID, PAYMENT_FAILED, PAYMENT_UNCERTAIN,
                        HELD, CANNOT_VERIFY, STALE
class Intent(StrEnum):  POST, PAY, RECONCILE
class Outcome(StrEnum): POST, PAY, HOLD, CANNOT_VERIFY, RECONCILED       # RECONCILED: P1, used with match_status
class ItemKind(StrEnum): BANK_LINE, INVOICE, VENDOR_MESSAGE
class DateBasis(StrEnum): RECORD_FIELD, PUBLICATION_DATE, HTTP_DATE_HEADER, FETCH_TIME, FILE_METADATA, UNKNOWN

class Source(BaseModel):
    fact: str                 # "payee.legal_name" | "account" | "ifsc.branch" | "duplicate.of" | ...
    value: str
    source_type: str          # vendor_master | po_file | payment_history | bank_statement | chart_of_accounts |
                              # crustdata.identify | company_website_claim | razorpay.ifsc | stripe | config_rule | input
    source_ref: str           # record id / URL / file:line / rule id
    field_or_passage: str     # field name, or exact quoted passage (for website/text sources the value must appear in the passage)
    record_date: date | None
    record_date_basis: DateBasis
    retrieved_at: datetime

class Reason(BaseModel): code: str; text: str; rule_id: str | None = None; source_refs: list[str] = []

class BankLine(BaseModel):  date: date; descriptor: str; amount_paise: int; currency: str = "INR"
                            direction: Literal["debit","credit"] = "debit"; bank_ref: str | None = None   # UTR if present
class Invoice(BaseModel):   vendor_name_raw: str; gstin: str | None = None; invoice_no: str; invoice_date: date
                            amount_paise: int; currency: str = "INR"; po_number: str | None = None; qty: float | None = None
                            remit_bank_hash: str | None = None; remit_bank_last4: str | None = None
                            remit_ifsc: str | None = None; domain: str | None = None
class VendorMessage(BaseModel): vendor_name_raw: str; domain: str | None = None; received_at: datetime
                            kind: Literal["BANK_CHANGE","OTHER"]; new_bank_hash: str | None = None
                            new_bank_last4: str | None = None; new_ifsc: str | None = None
Payload = BankLine | Invoice | VendorMessage

class ParsedItem(BaseModel):   # output of ingest; the pipeline turns it into Item
    kind: ItemKind; line_no: int; input_ref: str; intent: Intent; payload: Payload | None
    parse_errors: list[str] = []; raw_text_sha: str
class Item(BaseModel):
    item_id: str; tenant_id: str; run_id: str; kind: ItemKind; line_no: int; input_ref: str; intent: Intent
    state: State; version: int; payload: Payload | None; parse_errors: list[str]; raw_text_sha: str
    transaction_fingerprint: str | None; invoice_no_canonical: str | None; descriptor_key: str | None

class Vendor(BaseModel):  vendor_id: str; legal_name: str; name_key: str; aliases: list[str]; cin: str | None
                          gstin: str | None; bank_hash: str | None; bank_last4: str | None; ifsc: str | None
                          default_account: str | None; category: str | None; domain: str | None
                          record_date: date | None; record_ref: str
class PurchaseOrder(BaseModel): po_number: str; vendor_id: str; amount_paise: int; qty: float | None
                          status: str; record_date: date | None; record_ref: str
class HistoryRecord(BaseModel): ref: str; vendor_id: str | None; descriptor: str | None; invoice_no: str | None
                          amount_paise: int; date: date; bank_ref: str | None; record_ref: str
class ClientData(BaseModel): vendors: list[Vendor]; pos: list[PurchaseOrder] | None; history: list[HistoryRecord] | None
                          statement: list[BankLine] | None; accounts: dict[str, str] | None; file_dates: dict[str, date | None]

class ResolutionStatus(StrEnum): RESOLVED_VENDOR_MASTER, RESOLVED_EXACT_IDENTIFIER, FUZZY_CANDIDATE, LOOKALIKE, TIE,
                          PARENT_BRAND_MISMATCH, WEBSITE_IDENTITY_CLAIM_FOUND, UNRESOLVED, SOURCE_UNAVAILABLE
class Resolution(BaseModel): status: ResolutionStatus; vendor_id: str | None; legal_name: str | None
                          method: Literal["gstin","cin","bank_hash","exact","alias","fuzzy","website","none"]
                          score: float | None; candidates: list[tuple[str, float]]; flags: list[str]
                          reasons: list[Reason]; sources: list[Source]

class CheckResult(BaseModel): name: str; status: Literal["PASS","FLAG","ERROR","SKIPPED"]; flags: list[str]
                          reasons: list[Reason]; sources: list[Source]; missing: list[str] = []
                          details: dict[str, Any] = {}   # e.g. bank: {ifsc_valid, account_matches_master, ownership_verified}

class Facts(BaseModel):   intent: Intent; kind: ItemKind; vendor_id: str | None; amount_paise: int | None
                          date: date | None; invoice_no_canonical: str | None; account: str | None; flags: list[str]  # sorted
class Decision(BaseModel): item_id: str; outcome: Outcome; flags: list[str]; reasons: list[Reason]
                          sources: list[Source]; missing: list[str]; rule_ids: list[str]; overridable: list[str]
                          account: str | None; payee_legal_name: str | None; vendor_id: str | None
                          bank_status: dict[str, str] | None; facts: Facts
                          config_hash: str; data_hash: str; decision_hash: str; decided_at: datetime

class Approval(BaseModel): approval_id: str; item_id: str; decision_hash: str
                          status: Literal["PENDING","APPROVED","REJECTED","SUPERSEDED"]; requested_at: datetime
                          decided_by: str | None; decided_at: datetime | None; reason: str | None; overrides: list[str]
class Payment(BaseModel): payment_id: str; item_id: str; approval_id: str; idempotency_key: str; provider: str
                          provider_ref: str | None; amount_paise: int; currency: str
                          status: Literal["PENDING","CREATED","CONFIRMING","SUCCEEDED","FAILED","UNCERTAIN"]
                          last_error: str | None; created_at: datetime; updated_at: datetime
class LedgerEntry(BaseModel): call_key: str; run_id: str; item_id: str | None; stage: str; provider: str; endpoint: str
                          unit_type: Literal["credits","tokens_in","tokens_out","call"]; units: float; est_inr: float
                          inr: float | None; status: Literal["RESERVED","CONFIRMED","FAILED","CACHE_HIT","UNCONFIRMED"]
                          http_status: int | None; latency_ms: int | None; request_hash: str; attempt: int; ts: datetime
class LedgerSummary(BaseModel): run_id: str; total_inr: float; unconfirmed_inr: float; by_provider: dict[str, float]
                          calls: int; cache_hits: int; items: int; finished_items: int; inr_per_item: float
                          inr_per_finished_item: float; worst_item_inr: float; limit_inr: float
class AuditEvent(BaseModel): event_id: int; run_id: str; session_id: str; item_id: str | None; event: str
                          state_from: str | None; state_to: str | None; actor: str; data: dict[str, Any]; ts: datetime
class Request(BaseModel): request_text: str; intent: Intent | None; source_path: str; source_sha: str
                          records: list[dict[str, Any]]; kit_meta: dict[str, Any] = {}
class RunInfo(BaseModel): run_id: str; status: str; started_at: datetime; sessions: int; recovered: bool
                          config_hash: str; data_hash: str
class ItemResult(BaseModel): item: Item; decision: Decision | None; approval: Approval | None
                          payment: Payment | None; posting_account: str | None; cost_inr: float
```
`RunConfig` lives in `core/config.py` (Claude). It composes Claude's section models with Codex's section models from `apsvc/policy_config.py`. All use `extra="forbid"`.

### 7.2 `apsvc/flags.py` (Codex; `StrEnum Flag`, additive only)
`PAYEE_UNRESOLVED, PAYEE_TIE, LOOKALIKE, FUZZY_MATCH, PARENT_BRAND, WEBSITE_IDENTITY_CLAIM_ONLY, VENDOR_ONBOARDING_REQUIRED, MISSING_FIELD, PARSE_FAILED, INTENT_AMBIGUOUS, DUPLICATE_INVOICE, DUPLICATE_TRANSACTION, ALREADY_PAID_PER_STATEMENT, PO_MISSING, PO_NOT_FOUND, PO_VENDOR_MISMATCH, PO_AMOUNT_MISMATCH, PO_QTY_MISMATCH, PO_EXHAUSTED, BANK_CHANGED, BANK_CHANGE_REQUEST, IFSC_INVALID, ACCOUNT_UNMAPPED, SOURCE_UNAVAILABLE, CREDITS_EXHAUSTED, COST_LIMIT, TIME_LIMIT, PAYMENT_OUTCOME_UNKNOWN`
`NEVER_OVERRIDABLE = {PAYEE_UNRESOLVED, PAYEE_TIE, LOOKALIKE, PARENT_BRAND, DUPLICATE_INVOICE, BANK_CHANGED, BANK_CHANGE_REQUEST, IFSC_INVALID, PAYMENT_OUTCOME_UNKNOWN, MISSING_FIELD, PARSE_FAILED}`. The CLI enforces this **and** the config can only narrow it, never widen it.

### 7.3 Identity formulas (Claude implements in `core/ids.py`; Codex computes the canonical inputs)
- `item_id = sha256(tenant_id | request_sha | line_no)[:20]`: one occurrence in one input file.
- `transaction_fingerprint`:
  - Bank line with a UTR: `sha256("utr" | bank_ref)`.
  - Bank line without one: `sha256(descriptor_key | amount_paise | currency | date | direction)`.
  - Invoice: `sha256(vendor_id or name_key | invoice_no_canonical)`.
  - A fingerprint without a UTR is a duplicate *signal*, not proof.
- `idem_key = "pp_" + sha256(tenant_id | vendor_id | invoice_no_canonical | amount_paise | currency)[:32]`. A **PAY requires an invoice number**: a missing one gives `MISSING_FIELD:invoice_no` → CANNOT_VERIFY. So the key never depends on file position, and an override never changes it.
- `call_key = sha256(run_id | item_id | stage | provider | endpoint | request_hash | attempt)`.
- `decision_hash = sha256(canonical_json(facts) | config_hash | data_hash)`.

### 7.4 `db/schema.sql` (Claude)
```sql
PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;
CREATE TABLE runs (run_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, request_path TEXT NOT NULL, request_sha TEXT NOT NULL,
  intent TEXT, config_hash TEXT NOT NULL, data_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('RUNNING','PARTIAL','COMPLETE','STOPPED_COST','STOPPED_TIME','REFUSED')),
  started_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(tenant_id, request_sha));
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs, kind TEXT NOT NULL,  -- run|resume|approve
  pid INTEGER, started_at TEXT NOT NULL, ended_at TEXT);
CREATE TABLE items (item_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs,
  kind TEXT NOT NULL, line_no INTEGER NOT NULL, input_ref TEXT NOT NULL, intent TEXT NOT NULL,
  state TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0, payload_json TEXT, parse_errors_json TEXT NOT NULL DEFAULT '[]',
  raw_text_sha TEXT NOT NULL, transaction_fingerprint TEXT, invoice_no_canonical TEXT, descriptor_key TEXT,
  vendor_id TEXT, amount_paise INTEGER, item_date TEXT,
  resolution_json TEXT, checks_json TEXT, decision_json TEXT, decision_hash TEXT, outcome TEXT, updated_at TEXT NOT NULL,
  UNIQUE(run_id, line_no));
CREATE INDEX ix_items_state  ON items(tenant_id, state);
CREATE INDEX ix_items_inv    ON items(tenant_id, vendor_id, invoice_no_canonical);
CREATE INDEX ix_items_txn    ON items(tenant_id, vendor_id, amount_paise, item_date);
CREATE INDEX ix_items_desc   ON items(tenant_id, descriptor_key, amount_paise, item_date);
CREATE INDEX ix_items_fp     ON items(tenant_id, transaction_fingerprint);
CREATE TABLE evidence (evidence_id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, item_id TEXT NOT NULL REFERENCES items,
  decision_hash TEXT NOT NULL, check_name TEXT, rule_id TEXT, flag TEXT, fact TEXT NOT NULL, value TEXT NOT NULL,
  source_type TEXT NOT NULL, source_ref TEXT NOT NULL, field_or_passage TEXT NOT NULL, record_date TEXT,
  record_date_basis TEXT NOT NULL, retrieved_at TEXT NOT NULL,
  UNIQUE(item_id, decision_hash, fact, source_ref, field_or_passage));
CREATE TABLE audit_events (event_id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, run_id TEXT NOT NULL, session_id TEXT NOT NULL,
  item_id TEXT, event TEXT NOT NULL, state_from TEXT, state_to TEXT, actor TEXT NOT NULL, data_json TEXT NOT NULL, ts TEXT NOT NULL);
CREATE INDEX ix_audit_item ON audit_events(item_id, event_id);
CREATE TABLE postings (posting_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, item_id TEXT NOT NULL UNIQUE REFERENCES items,
  vendor_id TEXT, account TEXT NOT NULL, amount_paise INTEGER NOT NULL, decision_hash TEXT NOT NULL, posted_at TEXT NOT NULL);
CREATE TABLE approvals (approval_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, item_id TEXT NOT NULL REFERENCES items,
  decision_hash TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN('PENDING','APPROVED','REJECTED','SUPERSEDED')),
  requested_at TEXT NOT NULL, decided_by TEXT, decided_at TEXT, reason TEXT, overrides_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE(tenant_id, item_id, decision_hash));
CREATE INDEX ix_approvals_status ON approvals(tenant_id, status);
CREATE TABLE payments (payment_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, item_id TEXT NOT NULL UNIQUE REFERENCES items,
  approval_id TEXT NOT NULL REFERENCES approvals, idempotency_key TEXT NOT NULL UNIQUE, provider TEXT NOT NULL,
  provider_ref TEXT UNIQUE, amount_paise INTEGER NOT NULL, currency TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('PENDING','CREATED','CONFIRMING','SUCCEEDED','FAILED','UNCERTAIN')),
  last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX ix_payments_open ON payments(status) WHERE status IN('PENDING','CREATED','CONFIRMING','UNCERTAIN');
CREATE TABLE cost_ledger (call_key TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, run_id TEXT NOT NULL, session_id TEXT NOT NULL,
  item_id TEXT, stage TEXT NOT NULL, provider TEXT NOT NULL, endpoint TEXT NOT NULL, unit_type TEXT NOT NULL,
  units REAL NOT NULL DEFAULT 0, est_inr REAL NOT NULL, inr REAL,
  status TEXT NOT NULL CHECK(status IN('RESERVED','CONFIRMED','FAILED','CACHE_HIT','UNCONFIRMED')),
  http_status INTEGER, latency_ms INTEGER, request_hash TEXT NOT NULL, attempt INTEGER NOT NULL, ts TEXT NOT NULL);
CREATE INDEX ix_ledger_run ON cost_ledger(run_id, provider);
CREATE TABLE http_cache (provider TEXT NOT NULL, endpoint TEXT NOT NULL, request_hash TEXT NOT NULL, status INTEGER NOT NULL,
  headers_json TEXT, body TEXT NOT NULL, retrieved_at TEXT NOT NULL, expires_at TEXT,
  PRIMARY KEY(provider, endpoint, request_hash));
CREATE TABLE domain_blocklist (domain TEXT PRIMARY KEY, reason TEXT NOT NULL, added_at TEXT NOT NULL);
```

### 7.5 Module signatures

**Claude lane:**
```python
core/config.py   load_config(path="config.yaml") -> tuple[RunConfig, str]; load_client_data(cfg) -> tuple[ClientData, str]
                 # client loaders read the kit CSV/JSON via kitio.loaders (Codex) -> ClientData; bank numbers -> HMAC + last4 here
core/db.py       connect(path) -> Connection; migrate(conn); tx(conn) -> ContextManager[Connection]
core/state.py    ALLOWED: dict[State, set[State]]
                 transition(conn, item_id, frm: State, to: State, *, version: int, actor="system", **cols) -> int
                 # CAS; also writes audit_events in the same txn; raises IllegalTransition / StaleWrite
core/ids.py      item_id(...); fingerprint(...); idem_key(...); call_key(...); decision_hash(...)
core/history.py  class SqliteHistoryView(HistoryView)
core/pipeline.py start_or_get_run(request: Request, cfg, cfg_hash, data_hash) -> str
                 drive(run_id, ctx: StageContext, provider: PaymentProvider) -> RunInfo
                 # order: reconcile payments -> mark STALE if data/config changed -> ingest missing items
                 #        -> advance each in-flight item one stage at a time -> write outputs
core/budget.py   class Budget: reserve(est_inr) -> None (raises BudgetExceeded); check_time() (raises TimeExceeded); remaining() -> float
core/ledger.py   reserve(conn, entry) -> str; confirm(conn, call_key, units, inr, http_status, latency_ms); fail(...);
                 cache_hit(...); mark_unconfirmed_on_resume(conn, run_id) -> int; summary(conn, run_id) -> LedgerSummary
core/log.py      bind(run_id, session_id) -> Logger; Logger.event(name, **data)    # JSONL; masks bank numbers
core/lock.py     single_instance(lock_path) -> ContextManager
core/crash.py    maybe_crash(point: str)                                           # os._exit(137) if APSVC_CRASH_AT == point
resolve/http.py  class HttpExtClient(ExtClient)                                    # limiter, retry, cache, ledger, budget, redaction
pay/base.py      class PaymentProvider(Protocol): create(idem_key, amount_paise, currency, metadata) -> ProviderPayment
                 confirm(ref, idem_key) -> ProviderPayment; retrieve(ref) -> ProviderPayment
                 find_by_idem_key(idem_key) -> ProviderPayment | None; refund(ref, idem_key) -> ProviderPayment
pay/fake.py      class FakeProvider(PaymentProvider)          # own SQLite file; injectable latency/failures; succeeded-count API for tests
pay/stripe_provider.py class StripeProvider(PaymentProvider)  # test key only; refuses sk_live_*
pay/executor.py  enqueue(conn, item, approval) -> Payment; process(conn, provider, payment) -> Payment; reconcile(conn, provider) -> list[Payment]
cli/main.py      typer app: run, resume, approvals, approve, reject, show, ledger, report
cli/approvals.py list_queue(conn, run_id); approve(conn, item_id, by, reason, overrides); reject(conn, item_id, by, reason)
```

**Codex lane:**
```python
kitio/loaders.py   load_vendor_master(path, cfg) -> list[dict]; load_pos(...); load_history(...); load_statement(...); load_accounts(...)
kitio/adapter.py   load_request(path, cfg) -> Request; write_results(results, cfg, out_dir) -> Path
                   FIELD_GETTERS: dict[str, Callable[[ItemResult], Any]]   # output.fields -> values
kitio/render.py    report_md(results, ledger, run, cfg) -> str; item_detail(result, audit, evidence) -> str
ingest/structured.py to_items(request, ctx) -> list[ParsedItem]; detect_intent(request, cfg) -> Intent | None
ingest/document_parser.py extract(raw_text, kind, ctx) -> Payload    # P2; redact -> ctx.ext.llm_extract -> validate -> regex fallback
resolve/normalize.py descriptor_key(s, cfg) -> str; name_key(s, cfg) -> str; invoice_canonical(s, cfg) -> str
resolve/matcher.py   build_index(vendors, cfg) -> VendorIndex; resolve(item, ctx) -> Resolution
resolve/crustdata.py identify_by_domain(ctx, domain, item_id) -> IdentifyResult
resolve/webfetch.py  fetch_identity_claim(ctx, domain, item_id) -> IdentityClaim | None; find_cin_passage(text) -> tuple[str, str, str] | None
resolve/ifsc.py      lookup(ctx, ifsc, item_id) -> IfscResult      # status: VALID | NOT_FOUND | UNAVAILABLE
checks/required.py   check(item, ctx) -> CheckResult
checks/duplicate.py  check(item, resolution, ctx) -> CheckResult
checks/po.py         check(item, resolution, ctx) -> CheckResult
checks/account.py    check(item, resolution, ctx) -> CheckResult
checks/bank.py       check(item, resolution, ctx) -> CheckResult
checks/reconciliation.py check(item, resolution, ctx) -> CheckResult     # P1, only if statement/history present
checks/runner.py     run_checks(item, resolution, ctx) -> list[CheckResult]   # order from config; a SKIPPED check is reported, never silent
decide/rules.py      load_rules(cfg) -> list[Rule]; validate_rules(rules) -> None   # unknown flag in config = startup error
decide/engine.py     build_facts(item, resolution, checks) -> Facts; decide(item, resolution, checks, ctx) -> Decision
```

### 7.6 Resolution algorithm (Codex, `resolve/`)
**Normalisation:**
- `descriptor_key`: NFKC → upper → strip configured processor prefixes (`^RAZ\*`, `^PAYTM\*`, `^NEFT[-/ ]`, `^IMPS[-/ ]`, `^UPI[-/ ]`, `^POS `, `^ACH `, `^BIL/`, trailing `/\d+`) → strip trailing configured city tokens → punctuation to space → collapse whitespace.
- `name_key`: additionally drops legal suffixes and expands configured abbreviations (`TRD→TRADERS`).
- **`EMEA` and other region or entity qualifiers are never stripped.**

**Matching** (stop at the first level with candidates):
1. GSTIN / CIN exact.
2. Remit bank-hash exact (invoices only).
3. `name_key` exact.
4. Alias exact (vendor-master `aliases` + `match.aliases_extra`).
5. Fuzzy, using `rapidfuzz.fuzz.ratio` (**not** `token_set_ratio`):
   - best score ≥ `fuzzy_min_score` (92) and margin ≥ `fuzzy_min_margin` (5) → `FUZZY_CANDIDATE` → HOLD;
   - token subset/superset, or score ≥ `lookalike_floor` (75) → `LOOKALIKE` → CANNOT_VERIFY, naming the candidate and the differing tokens.

**Tie:** more than one distinct `vendor_id` at the winning level → `TIE` → CANNOT_VERIFY, listing the candidates.

**Unknown vendor:**
- No match + a known domain (invoice field, email domain, or a domain inside the descriptor):
  - `crustdata.identify_by_domain`. A name/entity mismatch against the billed entity → `PARENT_BRAND`.
  - Then `webfetch.fetch_identity_claim` on `webfetch.paths` (skipping `skip_domains` and anything on the blocklist; a Cloudflare marker adds the domain to the blocklist). A CIN regex `[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}` with the legal name within ±200 chars → `WEBSITE_IDENTITY_CLAIM_FOUND`.
  - For POST, that cited payee may be used, with flag `WEBSITE_IDENTITY_CLAIM_ONLY` and a self-published label. For PAY → `VENDOR_ONBOARDING_REQUIRED` → HOLD.
- No match + no domain → **no paid call** → `UNRESOLVED` → CANNOT_VERIFY ("missing: a vendor-master record, or a GSTIN/domain identifying the payee").

### 7.7 Checks (Codex, `checks/`)
- **required:** `required_fields[kind][intent]`. A missing field → `MISSING_FIELD:<name>` → CANNOT_VERIFY, naming the field.
- **duplicate** (compared against earlier items in this run, DB history, and payment history if supplied):
  - `DUPLICATE_INVOICE`: same vendor + same `invoice_no_canonical`. Canonicalisation: upper → alnum groups → drop noise tokens (`INV, INVOICE, BILL, NO, NUM`) → `int()` numeric groups → join with `|`. FY tokens are kept by default.
  - `DUPLICATE_TRANSACTION`: same vendor (or `descriptor_key` if unresolved) + amount + currency + date within `window_days`, or an equal fingerprint, or an equal UTR.
  - `ALREADY_PAID_PER_STATEMENT`: a PAY invoice matches a statement debit (P1).
  - The first occurrence proceeds; later ones HOLD, citing the earlier item's `input_ref`.
- **po** (SKIPPED with a reason if the kit has no POs; HOLD if `po.required_for` includes the intent and data is missing): `PO_MISSING / NOT_FOUND / VENDOR_MISMATCH / AMOUNT_MISMATCH` (beyond max(pct, abs) tolerance) `/ QTY_MISMATCH / EXHAUSTED` (uses `history.po_billed_paise`).
- **account:** vendor-master `default_account` → `accounts.by_category` → `ACCOUNT_UNMAPPED` → HOLD. The source is that field or the config rule.
- **bank:**
  - Invoice remit hash or IFSC ≠ master → `BANK_CHANGED`. A `VENDOR_MESSAGE` asking to change bank details → `BANK_CHANGE_REQUEST`. Both always HOLD.
  - `ifsc.lookup` runs on the new IFSC, and on the master IFSC before every PAY. `NOT_FOUND` → `IFSC_INVALID` → HOLD; `UNAVAILABLE` → `SOURCE_UNAVAILABLE` → HOLD.
  - `details` carries the three bank statuses. The vendor master is never written.
- **reconciliation** (P1, needs a statement or history): reference/UTR first, then payee + amount + currency + date window → `MATCHED / POSSIBLE_MATCH / UNMATCHED / CONFLICT`. Only `MATCHED` with exact fields is a settlement claim.
- **Any `ext` error** → status `ERROR` + `SOURCE_UNAVAILABLE` (or `CREDITS_EXHAUSTED` / `COST_LIMIT`). A check never passes on an error.

### 7.8 `config.yaml` (sectioned; `# owner:` comment on every section)
```yaml
# ---------- owner: claude ----------
tenant_id: client01                       # from kit
client:
  vendor_master: data/vendor_master.csv
  purchase_orders: null                   # set when the kit provides; null = PO check SKIPPED
  payment_history: null
  bank_statement: null
  chart_of_accounts: null
limits:
  time_s_per_invocation: 600
  cost_inr_per_run: 25.0                  # hour-3 "lower cost limit" = edit this line
  cost_inr_per_item: 5.0
  call_timeout_s: 20
payments:
  provider: stripe                        # stripe | fake
  currency: INR
  currency_override: null
  test_payment_method: pm_card_visa
  idem_window_h: 23
ext:
  crustdata: {base_url: https://api.crustdata.com, api_version: "2025-11-01", retries: 3, backoff_base_s: 1.0,
              rate_per_min: {identify: 30, enrich: 15, web_search: 10, web_fetch: 10}}
  ifsc: {base_url: https://ifsc.razorpay.com, retries: 2, backoff_base_s: 0.5}
  llm: {provider: anthropic, model: claude-haiku-5-5, max_tokens: 1024, retries: 1}   # verify model id in docs
costs:
  crustdata: {inr_per_credit: null, estimates: {identify: 0.2, enrich: 2, web_search: 1, web_fetch: 1}}  # null = report "price unknown", never 0
  llm: {claude-haiku-5-5: {inr_per_mtok_in: null, inr_per_mtok_out: null}}
  infra_inr_per_item: 0.05
  notional: {stripe_fee_pct: null}
cache_ttl_h: {crustdata: 168, web_fetch: 24, ifsc: 720, llm: null}
logging: {dir: var/logs, mask_bank: true}
storage: {db_path: var/paypilot.db, lock_path: var/paypilot.lock, fake_provider_db: var/fake_provider.db}
actions: {POST: {requires_approval: false}, PAY: {requires_approval: true}}   # PAY=false is rejected at load
# ---------- owner: codex ----------
kit:
  input_format: official_example          # adapter switch; becomes the real kit format
  intent_field: null                      # use the kit's explicit intent field if it has one
output:
  format: jsonl
  path: out/{run_id}/results.jsonl
  report: out/{run_id}/report.md
  fields: [item_ref, date, descriptor, amount_inr, outcome, payee_legal_name, vendor_id, account,
           reasons, missing, sources, approval, payment, cost_inr]   # hour-3 "new output field" = add one name here
request_intents: {POST: [post, book, categori], PAY: [pay, settle, remit], RECONCILE: [reconcile, match]}  # 0 or >1 hits -> REFUSED
required_fields:
  BANK_LINE: {POST: [date, descriptor, amount_paise]}
  INVOICE:   {PAY: [vendor_name_raw, invoice_no, invoice_date, amount_paise]}
normalize:
  processor_prefixes: ['^RAZ\*', '^PAYTM\*', '^NEFT[-/ ]', '^IMPS[-/ ]', '^UPI[-/ ]', '^POS ', '^ACH ', '^BIL/']
  city_tokens: [BLR, BANGALORE, BENGALURU, MUM, MUMBAI, DEL, DELHI, GGN, GURGAON, CHN, CHENNAI, HYD, PUNE, KOL]
  legal_suffixes: [PVT, PRIVATE, LTD, LIMITED, LLP, INC, SARL, GMBH, CO]
  abbreviations: {TRD: TRADERS, LOGS: LOGISTICS, SVCS: SERVICES}
  invoice_noise_tokens: [INV, INVOICE, BILL, NO, NUM]
match: {fuzzy_min_score: 92, fuzzy_min_margin: 5, lookalike_floor: 75, aliases_extra: {}}
duplicate: {window_days: 3, compare_against: [this_run, db_history, payment_history]}
po: {required_for: [PAY], tolerance_pct: 1.0, tolerance_abs_inr: 100, qty_tolerance_pct: 0}
bank: {verify_master_ifsc_before_pay: true}
accounts: {by_category: {food: "Staff meals", cloud: "Cloud hosting", freight: "Freight - carriers", fuel: "Fuel"}}
reconcile: {window_days: 3, amount_tolerance_inr: 0}
checks_order: [required, duplicate, po, account, bank, reconciliation]
webfetch: {paths: ["/", "/contact-us", "/about-us"], skip_domains: [zaubacorp.com, filesure.in],
           cf_markers: ["Just a moment", "cf-chl"], passage_window: 200}
llm_parse: {enabled: false, redact: [email, phone]}   # true only if the kit has unstructured input
decision:
  rules:
    - {id: R01, flags_any: [PAYEE_UNRESOLVED], outcome: CANNOT_VERIFY, reason: "No source identifies the payee for '{descriptor}'", missing: "vendor-master record or GSTIN/domain"}
    - {id: R02, flags_any: [PAYEE_TIE], outcome: CANNOT_VERIFY, reason: "Several vendors match equally: {candidates}"}
    - {id: R03, flags_any: [LOOKALIKE], outcome: CANNOT_VERIFY, reason: "'{descriptor}' resembles {candidate} but differs ({diff}); not merged"}
    - {id: R04, flags_any: [PARENT_BRAND], outcome: CANNOT_VERIFY, reason: "Domain resolves to {found_entity}, not billed entity {billed_entity}"}
    - {id: R05, flags_any: [PARSE_FAILED, MISSING_FIELD], outcome: CANNOT_VERIFY, reason: "Required input missing/unreadable: {fields}", missing: "{fields}"}
    - {id: R10, flags_any: [DUPLICATE_INVOICE], outcome: HOLD, reason: "Same invoice already seen as {dup_ref}"}
    - {id: R11, flags_any: [DUPLICATE_TRANSACTION, ALREADY_PAID_PER_STATEMENT], outcome: HOLD, reason: "Possible duplicate of {dup_ref}", overridable: true}
    - {id: R12, flags_any: [BANK_CHANGED, BANK_CHANGE_REQUEST], outcome: HOLD, reason: "Bank details differ from vendor master (possible fraud); vendor master must be updated through the client's verification process"}
    - {id: R13, flags_any: [IFSC_INVALID], outcome: HOLD, reason: "IFSC {ifsc} not found at Razorpay IFSC"}
    - {id: R14, flags_any: [PO_MISSING, PO_NOT_FOUND, PO_VENDOR_MISMATCH, PO_AMOUNT_MISMATCH, PO_QTY_MISMATCH, PO_EXHAUSTED], outcome: HOLD, reason: "PO check: {po_detail}", overridable: true}
    - {id: R15, flags_any: [FUZZY_MATCH], outcome: HOLD, reason: "Probable match {candidate} ({score}); confirm", overridable: true}
    - {id: R16, flags_any: [VENDOR_ONBOARDING_REQUIRED], outcome: HOLD, reason: "Vendor not in master; onboarding required before payment"}
    - {id: R17, flags_any: [SOURCE_UNAVAILABLE, CREDITS_EXHAUSTED], outcome: HOLD, reason: "{provider} unavailable after retries; not guessing"}
    - {id: R18, flags_any: [COST_LIMIT, TIME_LIMIT], outcome: HOLD, reason: "Run limit reached before verification"}
    - {id: R19, flags_any: [ACCOUNT_UNMAPPED], outcome: HOLD, reason: "No account mapping for {vendor_id}", overridable: true}
    - {id: R20, flags_any: [WEBSITE_IDENTITY_CLAIM_ONLY], outcome: HOLD, reason: "Payee identity is self-published on {url} only", applies_to: [PAY]}
```
**Live-change rehearsal:**
- Lower the cost limit: edit `limits.cost_inr_per_run` (1 line).
- New output field that is already computed: add its name to `output.fields` (1 line).
- New output field that isn't computed yet: add one `FIELD_GETTERS` entry (1 line) plus the config line.
- New rule: append one `decision.rules` entry. A new flag also needs 1 line in `flags.py` and an emit in one check.
- Window change: edit `duplicate.window_days`.

### 7.9 CLI (`python -m apsvc …`)
| Command | Args | Effect |
|---|---|---|
| `run` | `--request PATH [--config config.yaml] [--provider stripe\|fake]` | same request file → same run (resume semantics). Reconcile, drive to rest, write outputs |
| `resume` | `[--run RUN_ID]` (default: latest non-COMPLETE) | mark UNCONFIRMED ledger rows, reconcile payments, re-read config and data, continue |
| `approvals` | `[--run RUN_ID]` | queue: amount, payee + source, bank last4 + 3 bank statuses, outcome, reasons, sources, overridable flags |
| `approve` | `ITEM_ID --by NAME [--reason TEXT] [--override FLAG ...]` | approves the current decision_hash only; an override needs a reason; NEVER_OVERRIDABLE is refused; then the executor runs |
| `reject` | `ITEM_ID --by NAME --reason TEXT` | → REJECTED |
| `show` | `ITEM_ID` | decision → rule → flag → check → evidence → source chain, plus the audit timeline |
| `ledger` | `[--run RUN_ID] [--json]` | per-call rows; totals by provider; ₹/item, ₹/finished item, worst item; unconfirmed ₹ |
| `report` | `[--run RUN_ID]` | regenerate the kit output + `report.md` + summary |

The test-only env var `APSVC_TEST_AUTOAPPROVE=1` is honoured **only** when `provider=fake`.

---

## 8. State machine + kill walkthrough (Claude)
```
NEW → PARSED → RESOLVED → CHECKED → DECIDED
  (PARSED/RESOLVED/CHECKED → DECIDED directly when a hard flag makes later stages pointless)
DECIDED → POSTING → POSTED*                      (outcome POST, requires_approval=false)
DECIDED → AWAITING_APPROVAL → APPROVED | REJECTED*
DECIDED → HELD → APPROVED (overridable flags only, with reason) | REJECTED*
DECIDED → CANNOT_VERIFY*                         (reject/close only)
APPROVED → PAYMENT_PENDING → PAYMENT_CREATED → PAYMENT_CONFIRMING → PAID*
PAYMENT_CONFIRMING → PAYMENT_FAILED → HELD
PAYMENT_PENDING | PAYMENT_CREATED | PAYMENT_CONFIRMING → PAYMENT_UNCERTAIN   (provider unreachable / outcome unknown)
PAYMENT_UNCERTAIN → PAID* | PAYMENT_FAILED       (only via reconcile with provider evidence)
any of {PARSED…DECIDED, HELD, AWAITING_APPROVAL} → STALE → PARSED   (config/data hash changed; PENDING approvals SUPERSEDED)
```
`*` = terminal. Rest states (waiting for a person): `HELD, AWAITING_APPROVAL, CANNOT_VERIFY, PAYMENT_UNCERTAIN`. A run is COMPLETE when no item is in `NEW…DECIDED, POSTING, APPROVED, PAYMENT_PENDING/CREATED/CONFIRMING`.

**Persisted before each side effect** (same transaction as the state change, plus an `audit_events` row):
- Before any external call: a ledger row RESERVED.
- Posting: the `postings` row and POSTED are written in one transaction.
- Approval request: the `approvals` row PENDING and AWAITING_APPROVAL together.
- Stripe create: the `payments` row PENDING (with idem_key) and PAYMENT_PENDING.
- Before confirm: `provider_ref` and PAYMENT_CREATED, then PAYMENT_CONFIRMING.

**Payment lifecycle and reconcile** (`pay/executor.py`, run first on every run/resume):

| Payment row | Within `idem_window_h` | After the window |
|---|---|---|
| PENDING, no ref | `create` with the same key (Stripe returns the original if it landed) | `find_by_idem_key` (metadata search, retried twice ~30 s apart). Found → adopt it. **Not found → UNCERTAIN (manual). Never create.** |
| CREATED | `retrieve`; `requires_confirmation` → `confirm(key+":confirm")`; `succeeded` → PAID | same |
| CONFIRMING | `retrieve`; `succeeded` → PAID; `processing` → poll with backoff; `requires_payment_method`/`canceled` → FAILED → HELD | same |
| Provider unreachable | UNCERTAIN, with reason "will reconcile"; never PAID or FAILED from silence | same |

**Kill walkthrough:**
| Killed at (crash point) | State on restart | Resume does | Why safe |
|---|---|---|---|
| `after_ingest` | some items NEW | `INSERT OR IGNORE` the rest | deterministic `item_id` |
| `after_reserve` (before call) | ledger RESERVED | → UNCONFIRMED (counted at estimate); cache miss → attempt+1 | over-count only, labelled |
| `after_call_before_cache` | RESERVED | → UNCONFIRMED; real re-call, attempt+1 | genuine double spend, recorded honestly |
| `after_resolve` / `after_check` / `after_decide` | that state | continue at the next stage | stages are pure; re-running is harmless |
| `after_post_txn` | POSTED or rolled back to DECIDED | nothing / post again | one transaction + `postings.item_id` UNIQUE |
| `after_approval_commit` | APPROVED | enqueue | `payments.item_id` UNIQUE |
| `after_pay_pending` | PENDING, no ref | create with the same key | Stripe idempotency |
| `after_create_call` (response lost) | PENDING, no ref | create with the same key → same PaymentIntent | Stripe idempotency (24h) |
| `after_create_persist` | CREATED | retrieve → confirm with `:confirm` key | confirm idempotency |
| `after_confirm_call` | CONFIRMING | retrieve → succeeded → PAID | provider truth, not assumption |
| `after_paid` | PAID | nothing | terminal |

---

## 9. Failure-mode table
| Failure | Detection | Behaviour | Proving test (lane) |
|---|---|---|---|
| kill at each crash point | session with no `ended_at`; in-flight states | resume reconciles, then continues | `tests/kill/test_crash_points.py[point]` (C) |
| random kill -9 | — | converges to the golden result | `tests/kill/test_kill_loop.py` (C) |
| create response lost | PENDING without ref | same-key create returns the same PI | `test_pay_create_lost_response` (C) |
| provider unreachable at reconcile | exception / timeout | PAYMENT_UNCERTAIN; no new create | `test_provider_down_uncertain` (C) |
| idem window expired + search empty | age > window | UNCERTAIN; never create | `test_expired_key_fails_closed` (C) |
| Crustdata 429 | 429 | wait for `x-ratelimit-reset`, bounded retry | `test_http_429_backoff` (C) |
| Crustdata 402 | 402 | no retry; CREDITS_EXHAUSTED → HOLD; paid calls stop | `test_http_402_hold` (C wrapper, X check) |
| 401 / 403 / bad body | status / validation | error, never evidence → HOLD | `test_http_auth_forbidden` (C) |
| source timeout | retries exhausted | SOURCE_UNAVAILABLE → HOLD | `test_timeout_holds_not_guesses` (X) |
| Cloudflare page | marker in body | blocklist the domain; no claim | `test_cloudflare_skipped` (X) |
| bad LLM parse (P2) | ValidationError | retry once → regex → PARSE_FAILED → CANNOT_VERIFY | `test_llm_bad_json_fallback` (X) |
| missing required field | required check | MISSING_FIELD → CANNOT_VERIFY, naming the field | `test_missing_invoice_no` (X) |
| ambiguous intent | 0 or >1 intent hits | run REFUSED with a message; no processing | `test_intent_ambiguous_refused` (X) |
| exact duplicate (AWS×2) | K2 | the 2nd is HELD, citing line 2 | `test_official_four` (C+X) |
| reformatted duplicate invoice | K1 canonical | HELD, not overridable | `test_dup_reformatted_invoice` (X) |
| lookalike | fuzzy / token superset | CANNOT_VERIFY, naming the candidate | `test_lookalike_delhivery` (X) |
| tie | >1 vendor at the winning level | CANNOT_VERIFY with candidates | `test_tie_cannot_verify` (X) |
| parent brand | identify name ≠ billed entity | CANNOT_VERIFY | `test_parent_brand_aws` (X) |
| bank change | hash/IFSC ≠ master; vendor message | HELD; 3 bank statuses; not overridable | `test_bank_change_hold`, `test_bank_change_not_overridable` (X, C) |
| invalid IFSC | 404 | IFSC_INVALID → HELD | `test_ifsc_invalid` (X) |
| PO missing / over tolerance | PO check | HELD with variance + PO source | `test_po_missing`, `test_po_over_tolerance` (X) |
| cost limit hit | `Budget.reserve` → ext error BUDGET | COST_LIMIT → HELD for the rest; STOPPED_COST; report says so | `test_cost_limit_stops_safely` (C+X) |
| time limit hit | `check_time` | PARTIAL; resume continues | `test_time_limit_partial_resume` (C) |
| client data / config changed mid-run | hash ≠ the run's | non-terminal items STALE → re-run; approvals SUPERSEDED if the hash changed; PAID/POSTED untouched | `test_data_change_supersedes_approval` (C) |
| double approve / approve after change | CAS / hash mismatch | no-op / refusal with an explanation | `test_approve_idempotent` (C) |
| override of a never-overridable flag | CLI | refused | `test_override_refused` (C) |
| second process | flock | exits with a message | `test_single_instance_lock` (C) |
| same case worded differently | — | identical decision_hash | `test_consistency_two_wordings` (X) |

(C = Claude lane, X = Codex lane.)

---

## 10. Test plan + pass mark
**Fixtures:**
- `tests/fixtures/official/` (Claude, in the contract commit):
  - `official_oct.json`: the 4 Part A lines plus the request text.
  - `vendor_master.csv`:
    - Swiggy Limited: alias SWIGGY, category food.
    - Amazon Web Services EMEA SARL: alias "AWS EMEA", category cloud.
    - Delhivery Limited: CIN L63090DL2011PLC221234, GSTIN, bank hash, IFSC.
    - Blue Dart Express Limited.
    - Sharma Traders Pvt Ltd (V-0101) and Sharma Traders LLP (V-0102), for the tie case.
    - No alias matches "SHARMA TRD".
  - `golden/official_oct.jsonl`.
- `tests/fixtures/hard_cases/` (Codex): the invoice/PO fixtures, a reformatted duplicate, a lookalike, a bank-change message, PO over tolerance, a timeout script, unknown + CIN site, unknown + no record, two wordings. Each has a `golden/hard_<case>.jsonl`.
- `tests/fakes.py` (Claude): `FakeExtClient` (scripted responses per URL, with latency/429/402/timeout injection and a charge counter), `FakeHistory`, `make_ctx()`. Codex's unit tests use only these.

**Expected results:**
| Case | Expected |
|---|---|
| 3 Oct RAZ*SWIGGY BLR ₹1,240 | POSTED · Swiggy Limited · Staff meals · source: vendor_master `legal_name` + `category`→config rule |
| 7 Oct AWS EMEA ₹18,500 (1st) | POSTED · Amazon Web Services EMEA SARL · Cloud hosting |
| 7 Oct AWS EMEA ₹18,500 (2nd) | HELD · DUPLICATE_TRANSACTION of line 2 · in the approval queue · not posted |
| 9 Oct PAYTM*SHARMA TRD ₹4,000 | CANNOT_VERIFY · PAYEE_UNRESOLVED · missing named · **₹0 spent, 0 external calls** |
| carrier invoice = PO | AWAITING_APPROVAL → approve → PAID once (Stripe test) |
| same invoice, reformatted number | HELD · DUPLICATE_INVOICE · not overridable |
| "Delhivery Logistics Pvt" | CANNOT_VERIFY · LOOKALIKE (candidate Delhivery Limited) |
| bank-change email from Delhivery | HELD · BANK_CHANGE_REQUEST · ifsc_valid shown · ownership NOT_VERIFIED |
| invoice > PO beyond tolerance | HELD · PO_AMOUNT_MISMATCH with variance |
| Crustdata/web timeout | HELD · SOURCE_UNAVAILABLE |
| unknown payee, site shows CIN (POST) | HELD · WEBSITE_IDENTITY_CLAIM_ONLY, citing the passage + URL + fetch time |
| unknown payee, no record | CANNOT_VERIFY |
| same line, two wordings | identical decision_hash |

**Kill loop** (`tests/kill/test_kill_loop.py`, `-m slow`):
- Setup: a fresh tmp DB and FakeProvider DB (50–200 ms latency), with `APSVC_TEST_AUTOAPPROVE=1`.
- Repeat 30×: spawn `run --provider fake`, SIGKILL after uniform(0.05, 1.5) s, then `resume` until COMPLETE (killing again with p=0.5).
- Assert:
  1. FakeProvider has ≤ 1 succeeded payment per idem key, and exactly 1 for each approved PAY item.
  2. `results.jsonl` == the uninterrupted golden run (ignoring timestamps).
  3. Postings ≤ 1 per item.
  4. Approvals ≤ 1 per `(item, decision_hash)`.
  5. Every FakeExtClient charge has a ledger row (CONFIRMED or UNCONFIRMED).
  6. No PAID without a provider-side success.

**Pass mark** (green at S2 and at freeze; the README says this): `test_official_four`, `test_crash_points[*]`, `test_kill_loop`, `test_approve_idempotent`, `test_expired_key_fails_closed`, `test_dup_reformatted_invoice`, `test_lookalike_delhivery`, `test_tie_cannot_verify`, `test_parent_brand_aws`, `test_bank_change_hold`, `test_timeout_holds_not_guesses`, `test_cost_limit_stops_safely`, `test_consistency_two_wordings`. Plus one recorded live smoke run (real IFSC, real Stripe test, and real Crustdata if P1 is done) whose ledger appears in the README.

---

## 11. Ethics + finance talking points (each tied to a feature)
**Ethics:**
- **Minimal data:**
  - Only the configured vendor-master columns are loaded.
  - Bank numbers become an HMAC (key in `.env`) + last4 at load time. The full number is never stored or logged.
  - Emails and phone numbers are redacted before any LLM call.
  - No personal data is kept about people.
- **Approval before harm:**
  - Every PAY needs `approve --by`.
  - Bank changes, identity problems and an unknown payment outcome can't be overridden.
  - An approval binds to the decision_hash, so changed data invalidates it.
- **Explainability:**
  - `show <item>` prints the decision → rule → flag → check → evidence → source chain.
  - CANNOT_VERIFY names what's missing.
  - Decisions are deterministic.
- **Honest evidence:**
  - Website claims are labelled self-published.
  - IFSC ≠ ownership.
  - A Stripe test charge ≠ a payout.
  - The HTTP Date header is a retrieval time, never a record date.
  - MCA isn't usable (we disclose this).
- **Fail closed:**
  - Timeouts, 402s, unknown payment outcomes and limit hits all HOLD, never guess.
  - The audit trail lives in the DB, in the same transaction as each state change.

**Finance:**
- **Measured** cost per job = `ledger` ₹/finished item and the worst item, from real runs. It is shown live.
- Vendor-master hits cost ₹0 externally, so we show the share of items resolved at ₹0 and the cache-hit rate.
- **Price:** a per-item fee as a multiple of measured cost, benchmarked against a **cited** outsourced-AP per-invoice price (Codex researches it in `docs/finance.md`; never invented).
- **Buyer:** a 3PL finance controller, or an outsourced AP firm.
- **Value:** duplicates and bank-change fraud blocked; manual matching hours removed.

---

## 12. Demo script (3 min, built around failure)
1. `run` the official 4. Show Swiggy POSTED with its vendor-master source. Show AWS EMEA as the exact entity (not the AWS parent). The 2nd AWS line is HELD as a duplicate. PAYTM Sharma is CANNOT_VERIFY at ₹0.
2. `run` the invoice fixture. It is AWAITING_APPROVAL; `approvals` shows the reasons, sources and 3 bank statuses; `approve --by`.
3. **The judge kills the process during payment.** `resume` shows "reconciled pi_… (succeeded), 0 new payments". `ledger` shows ₹/item and the cache hits.
4. `show` the bank-change item: HELD, not overridable, ownership NOT_VERIFIED.
5. Live fix: add an output field or lower the cost limit, then re-run `report`.

---

## 13. Risks + cut list
**Risks:**

| Risk | Mitigation |
|---|---|
| Kit format differs | Isolated in `kitio`, owned by Codex |
| Stripe INR fails | `currency_override` |
| Web Fetch path unknown | Codex confirms it from docs.crustdata.com/llms.txt (P1); fallback is identify-only → HOLD |
| Prices unknown | `null` shows as "price unknown", never ₹0. A person gets the ₹/credit from the Crustdata team |
| Two agents collide | Folder ownership, `contract:` commits, rebase-only |
| Merge drift | `tests/contract` gate before every push |

**Cut order** (first to go → last):
1. Dashboard
2. LLM parsing (if the kit is structured)
3. PDF intake
4. Reconciliation (if the kit has no statement)
5. Web Fetch CIN (keep identify-by-domain)
6. Crustdata entirely (IFSC stays as the live source)
7. Fuzzy level (exact/alias only; else CANNOT_VERIFY)
8. `show` polish

**Never cut:** kit I/O, the official-4 results, no guessing, sources, approvals, Stripe test mode, IFSC live, outbox/idempotency/reconcile + kill test, cost/time limits + ledger, secrets hygiene.

## 14. Open questions (need the kit; the default stands until then)
1. Input/output format → default: JSON `{request, lines:[{date, descriptor, amount_inr}]}` in; JSONL with `output.fields` out.
2. Which files exist (vendor master, POs, history, statement, chart of accounts) → default: vendor master only; everything else `null` → checks SKIPPED with a reason.
3. Vendor-master columns → default: `vendor_id, legal_name, aliases, cin, gstin, bank_account, ifsc, default_account, category, domain, last_updated`.
4. Explicit intent in the request → default: keyword detection; ambiguous → REFUSED.
5. Tenant/client name → default `client01`.
6. Cost/time ceiling → default ₹25 / 600 s.
7. Crustdata ₹ per credit → ask the Crustdata team on the floor.
8. Stripe test account ready → a person creates `sk_test_…` in `.env` by 12:30.

## 15. Team checklist (tick before S0)
- [ ] This plan merged to `main` · [ ] `AGENTS.md` / `CLAUDE.md` read by each agent · [ ] contract commit at S0 · [ ] `.env` has `CRUSTDATA_API_KEY`, `STRIPE_SECRET_KEY` (sk_test), `ANTHROPIC_API_KEY`, `BANK_HMAC_KEY` · [ ] kit located (path recorded in `HACKATHON_INFORMATION.md` Part A) · [ ] WhatsApp is watched for the hour-3 change
