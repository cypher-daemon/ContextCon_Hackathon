# PayPilot: System Architecture

> **PayPilot** is a B2B accounts-payable operations service for mid-size Indian logistics (3PL) companies. It posts bank lines to the right payee and account, pays approved vendor invoices **at most once**, and manages every financial exception (duplicates, lookalike vendors, bank-detail fraud, uncertain or wrong payments) until there is evidence it is resolved.
>
> **Status:** final design, planning phase. No code yet. Exact contracts are in [`INTERFACES.md`](INTERFACES.md), failure behaviour in [`FAILURE_MODES.md`](FAILURE_MODES.md), the demo world in [`SCENARIOS.md`](SCENARIOS.md).
> **Precedence:** `HACKATHON_INFORMATION.md` Part A (binding rules) > `docs/DECISIONS.md` > this file > the other docs. Earlier drafts are kept unchanged in `docs/archive/` for history only.

---

## 1. The product in one page

| | |
|---|---|
| **Job replaced** | The payables clerk / outsourced AP firm that matches bank lines and invoices to vendors, catches duplicates and bank-change fraud, gets approvals, pays, and chases errors. |
| **Customer (who pays)** | The finance controller of a mid-size 3PL (₹100–1,000 Cr revenue, hundreds of carriers and suppliers). Second segment: outsourced AP / BPO firms serving many 3PLs. This makes the architecture multi-client ready (§12). |
| **Input** | A **request** (post these bank lines / pay these invoices / reconcile this statement) plus a batch file, checked against the client's **own records**: vendor master (source of truth for legal name, GSTIN, CIN, bank account, IFSC), POs, payment history, chart of accounts. |
| **Finished result** | Every item ends in exactly one outcome: `POSTED`, `PAID` (test provider, after human approval), `HELD` (with reasons), `CANNOT_VERIFY` (what is missing is named), `REJECTED`, or `PAYMENT_UNCERTAIN` (stop and reconcile). Every blocking issue is a durable **exception**; wrong payments open a **recovery case**. Every fact cites its source, and every external call is in the cost ledger. |
| **Product promise** | Never guess who to pay. Never pay the same invoice twice, even through crashes. Never take an irreversible action without a human. Keep every problem visible until it is resolved with evidence. |
| **Pass mark** | All scenarios in `SCENARIOS.md` produce their expected outcomes; the kill -9 loop yields ≤ 1 successful provider payment per invoice; every output fact has a source. |

**What PayPilot deliberately is not:** an LLM that decides payments; a bank; a proof of account ownership; a dashboard product (the operator surface is a CLI plus generated reports); a distributed system (yet, see §12).

---

## 2. System context (C4 level 1)

```mermaid
flowchart LR
  FIN([Finance controller / AP approver<br/>client company])
  OPS([AP operator])
  subgraph PP["PayPilot (this system)"]
    CORE[PayPilot service<br/>CLI + pipeline + SQLite]
  end
  REC[(Client records<br/>vendor master, POs, payment history,<br/>chart of accounts, bank statement)]
  STRIPE[[Payment provider<br/>Stripe TEST mode]]
  IFSC[[Razorpay IFSC API<br/>bank / branch metadata, free]]
  CRU[[Crustdata<br/>company identify by domain, web fetch]]
  LLM[[Claude LLM<br/>extraction of unstructured text only]]
  OPS -- "run / resume / report" --> CORE
  FIN -- "approve / reject / resolve exception" --> CORE
  REC -- "files, re-read every run" --> CORE
  CORE -- "create / confirm / retrieve PaymentIntent<br/>(idempotency keys)" --> STRIPE
  CORE -- "GET /{IFSC}" --> IFSC
  CORE -- "identify(domain), fetch(url)" --> CRU
  CORE -. "optional, P2" .-> LLM
  CORE -- "results, report, exceptions, ledger, audit" --> OPS
```

**Trust boundaries:**
1. The **client's records** are trusted as the source of truth for vendor identity and bank details, but are still validated (duplicates and conflicts inside the vendor master are exceptions).
2. **Live sources** are evidence of specific facts only: IFSC → bank and branch; Crustdata → the company behind a domain; a company website → a self-published claim.
3. **Batch input** (bank lines, invoices, vendor emails) is untrusted. A changed bank account in an invoice is treated as possible fraud.
4. The **payment provider** is the only system that can move money. We never assume what it did; we ask it.

---

## 3. Containers and components (C4 level 2–3)

PayPilot is a **modular monolith**: one Python 3.11 process, one SQLite database (WAL), and three clearly bounded layers. The key design rule:

> **Decision logic never causes side effects. Everything that writes, calls out, or moves money lives in one runtime layer, and that layer is the one the crash tests target.**

```mermaid
flowchart TB
  subgraph CLI["Operator surface"]
    C1[cli: run, resume, approvals, approve, reject,<br/>show, exceptions, resolve-exception, report, ledger]
  end
  subgraph RT["Runtime layer (lane A): owns ALL side effects"]
    IO[io: request + batch + client-data loaders,<br/>account fingerprinting at the boundary]
    PIPE[pipeline: item state machine,<br/>CAS transitions, crash points]
    APR[approvals: decision-hash binding]
    PAY[payments: outbox, executor,<br/>provider reconcile]
    LED[ledger + budget: reserve, confirm,<br/>cost and time limits]
    AUD[audit + JSONL log]
    REP[reports: results, report.md,<br/>exceptions, ledger exports]
    DB[(SQLite WAL)]
  end
  subgraph ID["Identity & evidence (lane B): pure + ExtClient"]
    NRM[normalize]
    MAT[resolve_vendor]
    EXT[ExtClient: rate limit, retry,<br/>cache, typed errors]
    SRC[sources: ifsc, crustdata, webfetch]
  end
  subgraph POL["Policy & exceptions (lane C): pure"]
    CHK[checks: required, period, duplicate,<br/>PO, account, bank]
    DEC[decide: rules → outcome + reasons]
    EXC[exceptions: classify, severity,<br/>lifecycle, recovery]
    RCN[reconciliation: three-way match]
  end
  C1 --> PIPE
  C1 --> APR
  IO --> PIPE
  PIPE --> MAT --> CHK --> DEC --> EXC
  MAT --> NRM
  MAT -.-> EXT
  CHK -.-> EXT
  EXT --> SRC
  EXT --> LED
  APR --> PAY --> RCN --> EXC
  PIPE --> DB
  APR --> DB
  PAY --> DB
  LED --> DB
  AUD --> DB
  PIPE --> REP
```

| Layer | Modules | Owner | Rule |
|---|---|---|---|
| Runtime | `core/ io/ cli/ approvals/ payments/ ledger/ reports/` | Lane A | The only code that touches SQLite, the payment provider, files and locks |
| Identity & evidence | `resolve/ sources/` | Lane B | Pure functions. Network only through `ctx.ext`. Writes (cache, ledger) only through ports the runtime provides |
| Policy & exceptions | `checks/ decide/ exceptions/ reconciliation/` | Lane C | Pure functions. Reads history only through `ctx.history` (read-only). Returns drafts; the runtime persists them |

Why this split: the exactly-once property can be proven by testing one layer. The logic layers are fully unit-testable with fakes. And the three lanes can be built in parallel against a frozen contract.

---

## 4. Request lifecycle

```mermaid
flowchart TD
  A[request.json + batch + client records] --> B[LOAD: validate schema, money → paise,<br/>fingerprint bank accounts, hash inputs]
  B --> C[PERSIST items: deterministic item_id,<br/>INSERT OR IGNORE]
  C --> D[RESOLVE payee: GSTIN/CIN → account fingerprint →<br/>exact name → alias → fuzzy candidate]
  D --> E[CHECK: required fields, period, duplicate,<br/>PO, account mapping, bank change + IFSC]
  E --> F[DECIDE: flags → config rules →<br/>outcome + all reasons + decision_hash]
  F --> G[CLASSIFY exceptions: type, severity, blocking]
  G --> H{proposed outcome}
  H -->|POSTED| I[local posting, one txn]
  H -->|AWAITING_APPROVAL| J[approval queue]
  H -->|HELD / CANNOT_VERIFY| K[exception register]
  J -->|human approves this decision_hash| L[payment outbox row]
  L --> M[provider: create → save ref → confirm]
  M --> N[three-way reconcile: instruction vs provider vs statement]
  N -->|conflict| K
  K -->|critical post-payment| R[recovery case]
  I & N & K --> O[REPORT: results, report.md, exceptions, ledger, audit]
```

| Stage | Does | Never does |
|---|---|---|
| Load | Strict schema validation; money as integer paise; account numbers → HMAC fingerprint + last 4 at the boundary | Store a raw account number |
| Resolve | Client vendor master first; live lookups only when an identifier (domain, GSTIN) exists | Identify by name via Crustdata; merge lookalikes; pick a winner in a tie |
| Check | Every check returns PASS / FAIL / SKIPPED / ERROR / UNKNOWN with evidence | Treat a source error or missing optional file as PASS |
| Decide | Pure function of normalized facts + policy; collects **all** reasons | Use an LLM; stop at the first flag |
| Approve | Shows payee, amount, account last 4, three bank statuses, reasons, sources; binds to `decision_hash` | Approve a stale decision; override a never-overridable flag |
| Execute | Persist intent → call provider with an idempotency key → persist the result | Retry blindly; infer success or failure from silence |
| Reconcile | Compares what we intended, what the provider did, what the bank shows | Call "provider-confirmed" "settled" |

---

## 5. Exactly-once payments (the core robustness argument)

**What we claim:** one logical payment instruction produces **at most one successful provider action**, across duplicate inputs, repeated approvals, crashes, restarts and timeouts. If the outcome cannot be established, the payment stops at `PAYMENT_UNCERTAIN` and is reconciled, never resent. A local database cannot commit atomically with an external provider, so we don't claim distributed atomic exactly-once.

**How:**
1. **Two keys:**
   - The **logical payment key** = hash(client, vendor, canonical invoice no., amount, currency). It never changes and has a local UNIQUE constraint.
   - The **provider attempt key** = logical key + `:a<n>`. Attempt *n+1* is allowed only after the provider has *confirmed* that attempt *n* failed or was returned, and only with a fresh approval. (Stripe replays the original response for a reused key, declines included, so a retry after a real decline needs a new attempt key.)
2. **Outbox:** the payment row is committed *after* approval and *before* any provider call.
3. **Two-step provider call:** create the PaymentIntent (not confirmed) → **persist its id** → confirm. Each step has its own provider idempotency key. A `step` column records which call may have landed.
4. **Reconcile before acting:** every `run`, `resume` and `approve` reconciles open payments with the provider *before* any new work.
5. **Fail closed:** if the provider is unreachable, or the idempotency window has expired and a search finds nothing, the payment becomes `PAYMENT_UNCERTAIN` with an exception. A person reconciles it. **An empty search is never treated as permission to pay again.**
6. **Containment:** while a CRITICAL payment exception is open for a vendor, no new payment to that vendor can be queued.

```mermaid
sequenceDiagram
  autonumber
  actor Approver
  participant CLI as PayPilot CLI
  participant DB as SQLite (WAL)
  participant ST as Stripe (test)
  Approver->>CLI: approve ITEM --by "Controller"
  CLI->>DB: txn: approval APPROVED (decision_hash) + payment CREATED + audit
  CLI->>DB: step=create_sent
  CLI->>ST: create PaymentIntent (Idempotency-Key: K:a1:create)
  Note over CLI: kill -9 here (response lost)
  Approver->>CLI: resume
  CLI->>DB: open payments? → step=create_sent, no ref
  CLI->>ST: create again with SAME key
  ST-->>CLI: same PaymentIntent pi_123 (replayed, no new charge)
  CLI->>DB: provider_ref=pi_123, step=ref_saved
  CLI->>DB: step=confirm_sent
  CLI->>ST: confirm pi_123 (Idempotency-Key: K:a1:confirm)
  ST-->>CLI: succeeded
  CLI->>DB: txn: payment PROVIDER_CONFIRMED + item PAID + audit
  CLI->>CLI: three-way reconcile → settlement PENDING (no statement yet)
```

Every crash point and its recovery is in [`FAILURE_MODES.md`](FAILURE_MODES.md) §3.

---

## 6. State machines (three independent lifecycles)

A payment can be provider-confirmed while its exception is still open (for example, it went to the wrong vendor). So item, payment and exception states are **separate**.

```mermaid
stateDiagram-v2
  direction LR
  [*] --> NEW
  NEW --> PARSED
  PARSED --> RESOLVED
  RESOLVED --> CHECKED
  CHECKED --> DECIDED
  DECIDED --> POSTED: proposed POSTED (local journal)
  DECIDED --> AWAITING_APPROVAL: payment candidate
  DECIDED --> HELD
  DECIDED --> CANNOT_VERIFY
  AWAITING_APPROVAL --> APPROVED
  AWAITING_APPROVAL --> REJECTED
  HELD --> APPROVED: overridable flags only + reason
  HELD --> REJECTED
  APPROVED --> PAYMENT_IN_PROGRESS
  PAYMENT_IN_PROGRESS --> PAID
  PAYMENT_IN_PROGRESS --> PAYMENT_UNCERTAIN
  PAYMENT_IN_PROGRESS --> FAILED
  PAYMENT_UNCERTAIN --> PAID: reconcile evidence
  PAYMENT_UNCERTAIN --> FAILED: reconcile evidence
  FAILED --> HELD: fresh checks + approval before retry
  POSTED --> [*]
  PAID --> [*]
  REJECTED --> [*]
  CANNOT_VERIFY --> [*]
```

```mermaid
stateDiagram-v2
  direction LR
  state "Payment" as P {
    [*] --> CREATED: after approval
    CREATED --> SUBMITTED: create_sent → ref_saved → confirm_sent
    SUBMITTED --> PROVIDER_CONFIRMED
    SUBMITTED --> FAILED: explicit decline
    SUBMITTED --> PAYMENT_UNCERTAIN: timeout / unreachable / no proof
    PAYMENT_UNCERTAIN --> PROVIDER_CONFIRMED
    PAYMENT_UNCERTAIN --> FAILED
    PROVIDER_CONFIRMED --> RETURNED
    PROVIDER_CONFIRMED --> REVERSED
  }
```

```mermaid
stateDiagram-v2
  direction LR
  state "Exception" as E {
    [*] --> DETECTED
    DETECTED --> CLASSIFIED
    CLASSIFIED --> CONTAINED: risky action blocked
    CONTAINED --> INVESTIGATING
    INVESTIGATING --> ACTION_REQUIRED
    ACTION_REQUIRED --> RESOLVED: evidence required
    INVESTIGATING --> FALSE_POSITIVE
    INVESTIGATING --> ACCEPTED_RISK: overridable only + reason
    ACTION_REQUIRED --> CLOSED_NO_ACTION
  }
  state "Recovery case (source of truth for recovery)" as R {
    [*] --> OPEN
    OPEN --> ACTION_PREPARED
    ACTION_PREPARED --> HUMAN_ACTION_REQUIRED
    HUMAN_ACTION_REQUIRED --> EXTERNAL_RESPONSE_PENDING
    EXTERNAL_RESPONSE_PENDING --> RECOVERED: provider/bank ref + amount + source
    EXTERNAL_RESPONSE_PENDING --> UNRECOVERABLE
  }
```

**Rules:**
- Every transition is compare-and-swap on `(state, version)`, with an audit event written in the **same transaction**.
- A change to client data or decision policy moves non-final items to `STALE` → re-decided. A pending approval whose `decision_hash` no longer matches is `SUPERSEDED`.
- `PAID`, `POSTED`, `REJECTED` and `CANNOT_VERIFY` are never re-run.

---

## 7. Data model

```mermaid
erDiagram
  RUNS ||--o{ SESSIONS : "restarts"
  RUNS ||--o{ ITEMS : contains
  ITEMS ||--o| POSTINGS : "at most one"
  ITEMS ||--o{ APPROVALS : "one per decision_hash"
  ITEMS ||--o| PAYMENTS : "one logical payment"
  PAYMENTS ||--o{ PAYMENT_ATTEMPTS : "attempt n+1 only after confirmed failure"
  ITEMS ||--o{ ITEM_EVIDENCE : cites
  SOURCES ||--o{ ITEM_EVIDENCE : "is cited by"
  ITEMS ||--o{ EXCEPTIONS : raises
  PAYMENTS ||--o{ EXCEPTIONS : raises
  EXCEPTIONS ||--o| RECOVERY_CASES : opens
  RUNS ||--o{ COST_LEDGER : "every external call"
  RUNS ||--o{ AUDIT_EVENTS : "every transition"
```

The full DDL, with every UNIQUE constraint that enforces "at most once", is in [`INTERFACES.md`](INTERFACES.md) §6.

| Guarantee | Enforced by |
|---|---|
| One item row per input row | `items.item_id` deterministic + `UNIQUE(run_id, input_name, row_no)` |
| One posting per item | `postings.item_id UNIQUE` |
| One approval per decision | `approvals UNIQUE(item_id, decision_hash)` |
| One logical payment per invoice | `payments.payment_key UNIQUE`, `payments.item_id UNIQUE` |
| One provider attempt key per attempt | `payment_attempts.attempt_key UNIQUE` |
| One exception per cause | `exceptions UNIQUE(subject_id, type, origin_key)` |
| One ledger row per call attempt | `cost_ledger.call_key PRIMARY KEY` |

---

## 8. Evidence model ("every fact has a source")

Every factual claim in the output is a `Source`:
- provider and record reference
- the **exact field or quoted passage** (for passages, the value must appear in the quote)
- the record's own date, with its **basis** (`RECORD_FIELD`, `PUBLICATION_DATE`, `HTTP_DATE_HEADER`, `FETCH_TIME`, `FILE_METADATA`, `UNKNOWN`)
- when we retrieved it
- its limitations

| Source | Proves | Does **not** prove |
|---|---|---|
| Client vendor master | Legal name, identifiers, approved bank account, default account (as of its `last_updated`) | That the vendor still uses that account |
| Razorpay IFSC | The IFSC exists and maps to a bank and branch (retrieval time from the HTTP `Date` header) | Who owns an account at that branch |
| Crustdata Identify (by domain) | A company is associated with that domain | The legal entity that issued the invoice (it often returns the parent brand) |
| Company website passage | The website **claims** a legal name / CIN (self-published) | Registry status |
| Stripe test mode | The provider accepted, declined or confirmed a test charge | A real vendor bank payout or bank settlement |

Bank verification is always shown as **three separate statuses**: `ifsc_valid`, `account_matches_master`, `ownership_verified` (always `NOT_VERIFIED`, because no source we use proves ownership).

Rendered example: *"Payee **Swiggy Limited**, from the `legal_name` field of vendor-master record V-0001 (record dated 30 Sep 2026), retrieved 22 Nov 2026 14:03 IST."*

---

## 9. Exception management (the differentiator)

Every meaningful failure, discrepancy or suspicious condition becomes a durable `Exception` record. A log line is not enough.
- **Severity rule:** `CRITICAL` = money may already be wrong (duplicate payout, wrong payee, wrong amount, duplicate debit). `HIGH` = an unsafe action was blocked *before* payment (bank change, duplicate invoice, ambiguous identity, uncertain provider outcome). `MEDIUM` = an item is held for missing or unavailable data. `LOW` = an informational warning.
- **Containment first:** blocking exceptions stop the related action. A CRITICAL one also blocks new payments to the same vendor.
- **Recovery is compensation, not rollback:** a confirmed external payment cannot be undone by a local transaction. PayPilot opens a recovery case with a prepared human action (contact provider/bank/vendor, request a return) and tracks it. It marks the case `RECOVERED` **only** with a provider or bank reference, the amount and a source.
- **Never overridable:** unresolved, tied, lookalike or parent-brand identity; bank change or invalid IFSC; duplicate invoice or payout; uncertain provider outcome; missing required fields; persistence failure.

The full matrix is in [`FAILURE_MODES.md`](FAILURE_MODES.md).

---

## 10. Cost, limits and observability
- **Cost ledger:**
  - A row is **reserved before** every external call (Crustdata, IFSC, Stripe, LLM), then confirmed, failed or marked a cache hit.
  - After a crash, reserved rows become `UNCONFIRMED` and still count, so spend is never under-reported.
  - Credits and tokens are converted to ₹ via config. An unknown price is reported as `UNKNOWN`, never ₹0.
- **Limits:** a per-run time limit and cost limit; the stricter of the request's and the client policy's applies.
  - Hitting the cost limit → no more paid calls; the remaining items are held with `COST_LIMIT`; run status `STOPPED_COST`.
  - Hitting the time limit → run status `PARTIAL`; `resume` continues.
- **Measured unit economics:** `ledger` shows ₹ per item, ₹ per finished item, the worst item, the cache-hit rate, and the share of items resolved at ₹0 from the vendor master.
- **Observability:**
  - An append-only `audit_events` table, written in the same transaction as each state change, is the source of truth.
  - A JSONL log per run is the readable copy. A new `session_id` on each restart makes crash recoveries visible.
  - `show <item>` prints the chain **decision → rule → flag → check → evidence → source**.

---

## 11. Key design decisions (options considered)

| # | Decision | Chosen | Alternatives | Why / trade-off |
|---|---|---|---|---|
| 1 | Form factor | CLI modular monolith | LLM agent loop; web app + dashboard; microservices | Deterministic, crash-testable, buildable in 7 h. Trade-off: no GUI (generated reports instead) |
| 2 | Layering | Pure logic layers + one side-effect runtime | Each module persists its own state | Exactly-once is provable in one place; logic is unit-testable |
| 3 | Store | SQLite WAL, `synchronous=FULL`, single-instance lock | Postgres; JSON files | Atomic multi-row transactions, zero setup. Trade-off: one writer (fine now; Postgres at scale) |
| 4 | Lifecycles | Item, payment, exception and recovery as separate machines | One merged enum | A payment can succeed while its exception stays open |
| 5 | Payment identity | Logical key + provider attempt key | Approval- or version-suffixed keys; DB-only uniqueness | No override, retry or restart can create a second success, yet a confirmed failure can still be retried safely |
| 6 | Provider | Stripe test mode (labelled "test card-charge simulation"); FakeProvider for the crash suite | Razorpay Payments (needs browser Checkout); RazorpayX Payouts (true payouts but KYC) | Native idempotency keys and retrieve/search. RazorpayX is the production adapter behind the same interface |
| 7 | Identity | Client master first; Crustdata by **domain** only; tie / lookalike / parent brand → CANNOT_VERIFY | Name-based lookup; embeddings or an LLM judge | Probes showed 3 different "Sharma Traders" at confidence 1.0. A wrong payee is worse than "cannot verify" |
| 8 | Decisions | Flags → declarative rules in config → all reasons | Rules DSL / eval; LLM | Reproducible `decision_hash`; explainable; same facts → same answer |
| 9 | Decision hash | Normalized facts + decision-policy config only | Hash everything, including evidence timestamps | Re-fetching a source or editing output config doesn't silently invalidate approvals |
| 10 | Exceptions | Durable register + recovery cases | Logs | Problems stay visible and owned until resolved with evidence |
| 11 | Config | `config.yaml` (product defaults) + `clients/<id>/policy.yaml` (client rules), strict schema, hashed, re-read every run | Constants in code | A requirement change is a 1–2 line edit; a second client is a new folder |
| 12 | LLM | Only for extracting unstructured text (P2), validated by schema, with a deterministic fallback | LLM extraction or decisions everywhere | Cost, latency, reproducibility |

---

## 12. Scaling to 100× and to many clients (design answer)

```mermaid
flowchart LR
  subgraph INGEST["Ingest API"]
    API[batch upload / SFTP / bank feed]
  end
  API --> Q[(queue: items by client)]
  subgraph WORKERS["Stateless workers (same pure logic layers)"]
    W1[resolve/check/decide worker ×N]
    W2[payment executor ×M<br/>claims outbox rows FOR UPDATE SKIP LOCKED]
    W3[reconciler: provider webhooks + statements]
  end
  Q --> W1 --> PG[(Postgres: same schema,<br/>client_id in every key, row-level security)]
  PG --> W2 --> PROV[[providers: RazorpayX payouts / Stripe]]
  PROV -- webhooks --> W3 --> PG
  W1 --> RL[(Redis: shared per-endpoint rate limiter + response cache)]
  PG --> UI[approver web app + SSO + maker-checker]
```

**What changes and what stays:**
- The pure logic layers, contracts, state machines, idempotency keys and reconcile-before-act stay **unchanged**. That's the payoff of the layering.
- The runtime layer is swapped: SQLite → Postgres; the single loop → queue workers; polling → provider webhooks; the CLI approver → a web app with SSO and maker-checker (two-person approval above a ₹ threshold).
- **Multi-client:**
  - `client_id` is already on every row and every key.
  - Each client gets `clients/<id>/` (vendor master + policy).
  - At scale: composite `(client_id, id)` keys, row-level security, per-client secrets and per-client cost limits.
- **Cost at volume:** vendor-master hits cost ₹0. External lookups are cached per client and domain, and Crustdata Identify can be batched (up to 25 domains per call).

---

## 13. Security, privacy and ethics by design
- **Data minimization:**
  - Only the needed vendor-master columns are loaded.
  - Bank account numbers become HMAC fingerprints (key in `.env`) + last 4 at load time; the full number is never stored or logged.
  - No data about individual people is stored.
  - Emails and phone numbers are redacted before any LLM call.
- **Human control:** every payment needs a named approver. Approvers come from the client policy's allowlist (`approvers:`); this is identity by declaration in the hackathon build, with SSO in production. Overrides need a written reason, and some flags can never be overridden.
- **Honest claims:**
  - The output never says "verified" when a source only *claims* something.
  - "Provider-confirmed" is never called "settled", and "recovery requested" is never called "recovered".
- **Secrets:** only in `.env`; live payment keys (`sk_live_`) are refused at startup; test mode only.
- **Explainability:** every decision lists every reason, rule and source; `show` gives the full chain; decisions are reproducible from the recorded facts.

The buyer, pricing and the ethics narrative are in [`BUSINESS.md`](BUSINESS.md).

---

## 14. Repository layout

```text
paypilot/                     Python package
  __main__.py  schemas.py  context.py  flags.py  policy_config.py
  core/        config db state ids audit log lock crash budget history pipeline       (A)
  io/          request batch client_data fingerprint                                   (A)
  cli/  approvals/  payments/  ledger/  reports/                                       (A)
  resolve/     normalize matcher                                                       (B)
  sources/     http ifsc crustdata webfetch llm                                        (B)
  checks/      required period duplicate po account bank runner                        (C)
  decide/      rules engine                                                            (C)
  exceptions/  classify lifecycle recovery                                             (C)
  reconciliation/ match                                                                (C)
clients/northbay/             demo client: vendor_master.csv, purchase_orders.csv,
                              payment_history.csv, chart_of_accounts.csv, policy.yaml
scenarios/<id>/               request.json + batch files + expected.jsonl (the demo world)
config.yaml   db/schema.sql   pyproject.toml   .env.example
tests/        contract/ unit/ integration/ crash/ fakes.py
docs/         ARCHITECTURE INTERFACES FAILURE_MODES SCENARIOS DEMO BUSINESS DECISIONS TASKS CONTRACT_CHANGES
var/ out/     (gitignored) database, cache, logs, run outputs
```
