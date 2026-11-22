# PayPilot --- Unified Architecture & Build Plan

## Payables, Replaced: a reliable accounts-payable service for a mid-size Indian 3PL

**Document status:** Unified proposal for team review\
**Version:** 1.0\
**Purpose:** Consolidate the strongest parts of the team's `PLAN.md`,
`HACKATHON_INFORMATION.md`, and the earlier PayPilot
product/architecture proposal into one implementable design.\
**Important:** This is an architecture proposal, not a claim that every
component has been implemented or that every assumed input exists in the
kickoff kit.

------------------------------------------------------------------------

## 0. Executive summary

**PayPilot** is a CLI-first accounts-payable service for the finance
team of a mid-size Indian third-party logistics (3PL) company. It
processes bank lines and invoices against the client's own vendor
master, purchase orders, chart of accounts and payment history. It
resolves the payee, checks for duplicates and bank-detail changes,
reconciles the item against available records, and produces a sourced
action or a clear exception.

The service is designed around one principle:

> **Never guess who should be paid, never silently repeat a payment, and
> never take an irreversible action without the required approval.**

The product is not an LLM that makes payment decisions. It is a
deterministic workflow with a narrow LLM-assisted extraction boundary
where unstructured input makes that useful. The client's records are the
primary source of truth. Crustdata and public endpoints provide
supplementary, live evidence; they do not establish bank-account
ownership or replace the vendor master.

### What the service does

For each input item, PayPilot:

1.  Ingests the request and its bank lines/invoices.
2.  Normalizes and resolves the payee against the client vendor master.
3.  Runs duplicate, PO, account-mapping, bank-change and reconciliation
    checks supported by the available data.
4.  Applies deterministic, config-driven rules.
5.  Produces a result with reasons, evidence and missing information.
6.  Posts an internal accounting entry when the request is a posting
    task and the configured approval policy permits it.
7.  For a payment task, queues the payment for human approval and uses a
    payment-provider adapter in test mode.
8.  Persists state, audit events, external-call costs and payment state
    so the service can resume safely after a crash.

### Intended outcomes

The official four-line example must produce the specified outcomes:

  -----------------------------------------------------------------------
  Input                               Required result
  ----------------------------------- -----------------------------------
  3 Oct 2026, `RAZ*SWIGGY BLR`,       Payee: Swiggy Limited; account:
  ₹1,240                              staff meals

  7 Oct 2026, `AWS EMEA`, ₹18,500 --- Payee: Amazon Web Services EMEA
  first occurrence                    SARL; account: cloud hosting

  Same AWS EMEA line again            Hold as a possible duplicate; do
                                      not post it again

  9 Oct 2026, `PAYTM*SHARMA TRD`,     Cannot verify payee; state what
  ₹4,000                              evidence is missing; do not guess
  -----------------------------------------------------------------------

The service must also handle hidden duplicates, lookalike names and
unanswerable questions conservatively; provide exact source references
for factual claims; enforce run time/cost limits; and survive process
termination and restart.

------------------------------------------------------------------------

## 1. Problem, buyer and product boundary

### 1.1 The job being replaced

The selected track is **Buying & Paying**. Payables teams and outsourced
finance firms are paid to process invoices and payments correctly. The
official challenge explicitly names jobs such as paying each invoice
exactly once through crashes and bank-detail fraud, and reconciling
against a bank statement.

PayPilot's buyer is:

-   The finance controller / accounts-payable team of the fictional
    mid-size Indian 3PL; or
-   An outsourced AP firm processing payables for multiple 3PL clients.

The value proposition is reduced manual matching effort, fewer duplicate
or incorrect postings/payments, earlier detection of suspicious
bank-detail changes, faster reconciliation, and a traceable audit
record.

### 1.2 What PayPilot is --- and is not

**It is:** - A request-to-result AP workflow. - A deterministic decision
and reconciliation engine. - A persistent CLI service with a human
approval queue. - An evidence ledger and cost ledger around each
decision. - A safe test-mode payment demonstration.

**It is not:** - A general autonomous agent that can invent missing
facts. - A replacement for the client's authoritative vendor onboarding
process. - Proof of bank-account ownership based on an IFSC lookup. - A
live banking product that sends real money. - A production-ready
multi-tenant platform in the hackathon timeframe.

### 1.3 Supported actions and semantics

Use explicit action semantics in the implementation and report:

  -----------------------------------------------------------------------
  Result                  Meaning                 Permitted next step
  ----------------------- ----------------------- -----------------------
  `POSTED`                An internal/simulated   Terminal for this item
                          accounting entry was    unless the client data
                          committed exactly once  changes

  `AWAITING_APPROVAL`     Payment checks passed   Human may approve or
                          sufficiently to request reject
                          human authorization     

  `PAID`                  Test payment provider   Terminal
                          confirms successful     
                          payment                 

  `HELD`                  A known issue or policy Human investigation;
                          gate prevents automatic only explicitly
                          progress                overridable issues may
                                                  be resolved by an
                                                  authorized person

  `CANNOT_VERIFY`         A required fact cannot  Request missing
                          be established from     evidence or close;
                          available evidence      never convert directly
                                                  into payment by a
                                                  generic override

  `REJECTED`              A human rejected the    Terminal
                          proposed action         

  `PAYMENT_UNCERTAIN`     External payment        Reconcile with
                          outcome cannot          provider/manual review;
                          currently be            never blindly create
                          established             another payment
  -----------------------------------------------------------------------

`POST` and `PAY` are distinct intents. A local simulated ledger posting
may be reversible and can be automated if the task/config allows it. Any
external accounting action with material consequences should require
approval unless the client explicitly authorizes it. Every payment
requires a human approval in the competition implementation.

------------------------------------------------------------------------

## 2. Binding requirements and verified constraints

This section separates requirements stated in the official problem
material from design choices.

### 2.1 Binding competition requirements

The official problem statement / team information specifies:

1.  **Live data:** At least one decision must use live real-world data.
    Crustdata, registries, public APIs or web sources are permitted;
    mocks are for tests only.
2.  **Hidden tests:** Include a duplicate record, a lookalike name and
    an unanswerable question. Correct behaviour for the unanswerable
    case is "cannot verify". A wrong answer is worse than a conservative
    cannot-verify result.
3.  **Evidence:** Every factual claim in output must point to the exact
    source record, field or passage and show its date. The source must
    actually state the claim. Model memory is not evidence.
4.  **Approval:** Irreversible actions, including payments, wait for
    human approval. Payment integrations must be in test mode. The team
    must not contact real outside people or make real payments.
5.  **Limits:** Each run has a time limit and cost limit; record the
    cost of each call.
6.  **Kit compatibility:** Follow the kickoff kit's input/output format
    exactly. Organizers may change client data during the build; do not
    hardcode it. Re-read client files/config on each run.
7.  **Deliverables:** README explaining job, buyer, input, finished
    result, pass mark and sources; architecture diagram; readable logs
    and cost records.
8.  **Reliability demonstration:** Organizers stop/restart the service
    and may kill it during the final demo. Actions must happen once.
9.  **Hour-three change / live fix:** One requirement changes during the
    build. Finals include a small live change request. Make output
    fields and limits configurable.
10. **Secrets and repository hygiene:** Secrets stay in `.env`
    (gitignored); commit frequently and do not rewrite history.

### 2.2 Official example and expected behaviour

The four official lines are the acceptance baseline. Implement these
before broadening the system.

-   `RAZ*SWIGGY BLR` resolves to Swiggy Limited using the vendor master.
-   `AWS EMEA` resolves to the specific EMEA legal entity in the vendor
    master, not merely the AWS parent brand.
-   The second AWS line is a duplicate and is held.
-   `PAYTM*SHARMA TRD` is not resolved by guessing from a common name;
    it returns `CANNOT_VERIFY` and names the missing source.

### 2.3 Verified Crustdata / external-data findings from the team's API probes

The team's `HACKATHON_INFORMATION.md` records these observed results:

-   Crustdata did not provide a legal/registered-name field in the
    tested company response; e.g. it returned a brand-style name rather
    than a full legal name.
-   Company Identify by `aws.com` resolved to the AWS parent brand, not
    the billed `Amazon Web Services EMEA SARL` entity.
-   Name-only Identify was unreliable in the tested examples: multiple
    "Sharma Traders" matches had confidence 1.0, including unrelated
    companies. Use domain identification when a domain is available; do
    not trust a name-only match as proof.
-   Web Fetch returned a citable CIN passage from `delhivery.com`. The
    page had no reliable publication date, so record the fetch/retrieval
    time rather than inventing a record date.
-   Some registry-style sites tested by the team were Cloudflare-blocked
    and still consumed credits. Skip known blocked domains and treat
    challenge pages as unavailable evidence.
-   The tested Razorpay IFSC endpoint returned bank/branch details and
    had no record-date field; the HTTP `Date` header can be stored as
    response/retrieval metadata, not mislabelled as the source record's
    update date.
-   MCA registry access was not usable in the team's tests. The README
    should disclose this limitation rather than imply that MCA
    verification is available.
-   Team-observed Crustdata costs: Identify about 0.2 credits per
    identifier matched, Enrich about 2 credits/company, Web Search about
    1/query, Web Fetch about 1/page; confirm current account pricing and
    actual call usage from the ledger.
-   Rate limits recorded in the team's reference: Identify 30/min,
    Enrich 15/min, Web 10/min. A 429 should respect `x-ratelimit-reset`;
    402 means credits exhausted and should not be retried; 401 indicates
    authentication trouble; 403 indicates access/permission trouble.

These are observations from the team's probes, not universal guarantees.
The implementation must log actual responses and usage.

### 2.4 Important evidence limitations

-   Crustdata company identification is an identity-discovery signal,
    not a legal registry certificate.
-   A company's website can state its own legal name/CIN, but that is
    self-published evidence. Label it as such.
-   IFSC lookup verifies that an IFSC maps to a bank/branch and returns
    supported branch facts. It does **not** verify that a particular
    account number belongs to the vendor.
-   A domain match does not establish the exact legal entity invoicing
    the client.
-   A web page with no publication date has a retrieval timestamp, not a
    known publication/update date.
-   No external source should be presented as proving a fact it does not
    state.

------------------------------------------------------------------------

## 3. Product workflow

### 3.1 End-to-end pipeline

``` mermaid
flowchart TD
    A[Request + input files] --> B[Kit I/O adapter]
    B --> C[INGEST and validate]
    C --> D[RESOLVE payee]
    VM[Vendor master] --> D
    D --> E[CHECK]
    PO[Purchase orders] --> E
    PH[Payment history] --> E
    COA[Chart of accounts] --> E
    E --> F[Deterministic decision engine]
    CFG[Config and client rules] --> F
    F -->|POST permitted| G[Atomic internal posting]
    F -->|PAY candidate| H[Approval queue]
    F -->|HOLD / CANNOT_VERIFY| I[Exception queue]
    H -->|Human approval| J[Payment outbox]
    J --> K[Payment provider adapter]
    K --> L[Test-mode provider / FakeProvider]
    G --> M[Result + evidence + audit]
    I --> M
    L --> M
    N[Cost ledger + structured logs] --- C
    N --- D
    N --- E
    N --- K
```

### 3.2 Stage responsibilities

**1. INGEST** - Read the request using the official kit adapter. - Parse
structured inputs deterministically. - If an invoice/document is
unstructured, extract fields using an LLM or OCR adapter, then validate
with Pydantic. - Normalize monetary values to integer paise; preserve
original raw text/hash and input reference. - Create a persisted item
record before processing further. - If required fields are missing or
ambiguous, produce a parse exception and `CANNOT_VERIFY` rather than
inventing values.

**2. RESOLVE** - Normalize bank descriptors and vendor names. - Match
against the client vendor master. - Use exact identifiers and exact
aliases before fuzzy matching. - Use Crustdata Identify by domain only
when a reliable domain is supplied and an external lookup is
justified. - Fetch a company page only where it can contribute a
specific, citable fact. - Treat ties, lookalikes, parent-brand
mismatches and unknown entities conservatively.

**3. CHECK** - Duplicate invoice / bank-line detection. - Purchase-order
matching, if PO data exists and the request requires it. - Account
mapping against vendor defaults or the chart of accounts. - Bank-detail
change detection. - IFSC validity / bank-branch metadata lookup where
relevant. - Reconciliation against payment history / bank statement, if
present. - Source availability, freshness and completeness checks.

**4. DECIDE** - A pure deterministic function of normalized facts,
flags, policy config and source references. - Emit one outcome, all
applicable reasons, exact sources and missing evidence. - Never ask an
LLM to decide whether a payment should be made.

**5. APPROVE** - Present the proposed action, amount, payee, account,
bank last-four digits, flags, reasons and evidence. - Bind approval to
the exact decision hash. - Approval is invalidated if material facts or
policy change.

**6. EXECUTE** - For `POST`, create a unique internal posting record in
the same database transaction as the state transition. - For `PAY`,
enqueue a payment only after approval, then execute through a provider
abstraction in test mode. - Reconcile provider state on every resume
before attempting a new side effect.

**7. REPORT** - Emit results in the official kit format. - Also produce
a human-readable report, JSONL event log and per-run cost summary. -
Every output fact must be linked to its source or explicitly marked
unavailable/unverified.

------------------------------------------------------------------------

## 4. Architecture decisions

### 4.1 Form factor: CLI-first modular monolith

**Decision:** Build one Python process with a CLI and modular packages.

Why: - The competition is only seven hours. - The official deliverable
permits a CLI. - A single process and sequential pipeline are easier to
reason about under kill/restart tests. - A dashboard would consume time
without proving the central service property.

A simple dashboard is a stretch goal only after the end-to-end CLI,
official tests, recovery and evidence output are stable.

**Avoid:** microservices, distributed queues, multi-agent orchestration,
and a complex web UI during the core build. They add integration points
without helping the primary acceptance tests.

### 4.2 Suggested repository layout

``` text
apsvc/
  __init__.py
  __main__.py
  schemas.py

  core/
    config.py
    db.py
    state.py
    pipeline.py
    budget.py
    ledger.py
    audit.py
    log.py
    lock.py
    crash.py

  kitio/
    adapter.py              # only module aware of official kit formats
    loaders.py

  resolve/
    normalize.py
    matcher.py
    http.py
    crustdata.py
    webfetch.py
    ifsc.py

  ingest/
    structured.py
    document_parser.py      # optional; only if unstructured inputs exist

  checks/
    duplicate.py
    po.py
    bank.py
    reconciliation.py
    account.py
    runner.py

  decide/
    engine.py
    rules.py

  pay/
    base.py
    fake.py
    stripe_provider.py      # test-mode simulation, if used
    executor.py

  cli/
    main.py
    approvals.py
    report.py

config.yaml
db/schema.sql
data/                      # supplied/derived client data; follow repo rules
tests/
  fixtures/
  golden/
docs/
  architecture.md
  finance.md
  ethics.md
var/                       # gitignored DB, cache, logs
out/                       # gitignored run outputs
.env                       # gitignored secrets
```

Keep the actual folder structure smaller if implementation time is
tight. The boundaries matter more than creating every file in advance.

### 4.3 Module ownership / shared contracts

The first team agreement should be the shared data contract in
`schemas.py`, the config shape, CLI command contract and database state
transitions.

Do not let each person independently invent field names or statuses.
Commit the shared contract early and make all workstreams integrate
against it.

------------------------------------------------------------------------

## 5. Data model and evidence model

### 5.1 Core entities

  -----------------------------------------------------------------------
  Entity                  Purpose                 Important fields
  ----------------------- ----------------------- -----------------------
  `Run`                   One request execution   `run_id`, request hash,
                                                  config hash,
                                                  client-data hash,
                                                  status, timestamps

  `Session`               One process execution / `session_id`, run ID,
                          restart                 start time, process ID

  `Item`                  One input occurrence    item ID, run ID, kind,
                                                  intent, input
                                                  reference, payload,
                                                  state, version

  `Vendor`                Client's known          vendor ID, legal name,
                          counterparty            aliases, CIN/GSTIN,
                                                  bank hash/last4, IFSC,
                                                  account mapping,
                                                  domain, source
                                                  reference

  `PurchaseOrder`         PO record if supplied   PO number, vendor ID,
                                                  amount, quantity,
                                                  billed amount, status,
                                                  source reference

  `BankTransaction`       Bank statement line if  bank reference/UTR,
                          supplied                date, descriptor,
                                                  amount, direction,
                                                  currency, source
                                                  reference

  `Posting`               Internal accounting     item ID, vendor ID,
                          entry                   account, amount, posted
                                                  timestamp

  `Approval`              Human authorization     item ID, decision hash,
                          decision                reviewer, status,
                                                  reason, timestamp

  `Payment`               External payment        item ID, idempotency
                          attempt                 key, provider, provider
                                                  reference, amount,
                                                  status

  `Evidence`              Support for a factual   fact, value, source
                          claim                   type, record reference,
                                                  field/passage, source
                                                  date, retrieval time

  `CostLedgerEntry`       Cost of a call          provider, endpoint,
                                                  units, estimate, actual
                                                  INR, status, request
                                                  hash, latency

  `AuditEvent`            Append-only workflow    event, item/run, state
                          history                 transition, actor,
                                                  timestamp, structured
                                                  details
  -----------------------------------------------------------------------

### 5.2 Source/evidence contract

Use a structured `Source` model. Suggested fields:

``` python
class Source(BaseModel):
    fact: str
    value: str
    source_type: str
    source_ref: str
    field_or_passage: str
    record_date: date | None
    record_date_basis: str
    retrieved_at: datetime
```

`record_date_basis` should distinguish: - `record_field`: date
explicitly present in the source record. - `publication_date`:
publication date stated by a webpage/document. - `http_date_header`:
HTTP response date; not a record update date. - `fetch_time`: time the
team retrieved a source with no publication date. - `file_metadata`:
file modification metadata, only if relevant and trustworthy. -
`unknown`: no reliable date available.

Do not manufacture a source date. If a page has no date, report the
retrieval time and say that the page's publication/update date was
unavailable.

Every `Reason` should identify the rule/flag that produced it. Every
material factual claim in a result should have one or more `Source`
references. A URL alone is not enough if the exact field or passage is
unknown.

### 5.3 Data minimization

-   Load only vendor-master columns required for the task.
-   Do not retain full bank account numbers unless the official workflow
    absolutely requires them.
-   Prefer an HMAC of account number (secret key in `.env`) plus last
    four digits for comparison/display.
-   Mask account numbers and personal contact information in logs.
-   Redact email addresses and phone numbers before sending text to an
    LLM.
-   Avoid collecting or storing personal data about individual
    employees/directors unless the task explicitly requires it.
-   Never commit API keys, payment secrets, client data or generated
    sensitive run outputs.

------------------------------------------------------------------------

## 6. Payee resolution

### 6.1 Normalization

Create two separate normalizers:

-   `descriptor_key`: for bank narration / transaction descriptors.
-   `name_key`: for legal/vendor names.

Possible descriptor transformations: - Unicode normalization (NFKC). -
Uppercase and trim. - Strip known payment processor prefixes such as
`RAZ*`, `PAYTM*`, `NEFT`, `IMPS`, `UPI`, `POS`, `ACH`, where
appropriate. - Remove configured city/branch tokens only when they are
known narration suffixes. - Remove reference suffixes only when the
format is known. - Normalize punctuation and whitespace.

Possible name transformations: - Normalize Unicode and punctuation. -
Expand known abbreviations from a controlled config list. - Treat legal
suffix removal as a matching aid, not proof of legal-entity
equivalence. - Preserve region/entity qualifiers such as `EMEA`; they
can distinguish legal entities.

Normalization must be deterministic and covered by table-driven tests.
Do not remove arbitrary tokens simply to increase match rates.

### 6.2 Matching hierarchy

Use this order, stopping only when the winning level is unambiguous:

1.  Exact GSTIN / CIN where the input and vendor master contain it.
2.  Exact trusted bank-account hash for invoice remittance details,
    where available.
3.  Exact normalized legal/vendor name.
4.  Exact configured alias.
5.  Fuzzy candidate generation for investigation only.

Rules: - A unique exact identifier match is strongest. - A unique exact
name/alias match may resolve against the vendor master. - Multiple
candidates at the same winning level → `PAYEE_TIE` → `CANNOT_VERIFY`. -
Fuzzy match → `HOLD` for human confirmation; never auto-pay from fuzzy
matching alone. - A likely lookalike or token subset/superset →
`LOOKALIKE`; do not merge automatically. - If a domain resolves to a
parent brand or different legal entity than the invoicing entity, emit
`PARENT_BRAND` and `CANNOT_VERIFY` unless the exact invoicing entity is
already established by the vendor master. - No match and no reliable
domain/identifier → `PAYEE_UNRESOLVED`, no paid external lookup,
`CANNOT_VERIFY`.

### 6.3 Crustdata usage policy

Crustdata is supplementary evidence and discovery, not the source of
truth for payee legal identity.

-   Use Company Identify by domain only, when a trustworthy domain is
    available.
-   Do not use name-only Identify to resolve ambiguous common names.
-   Do not treat `confidence_score` as proof of legal identity.
-   A Crustdata brand/company match can support an existence/discovery
    claim, but not bank ownership, exact legal entity, invoice validity
    or payment authorization.
-   Enrich only if the extra data will change a decision and the budget
    permits it.
-   Cache responses with an explicit TTL and record the actual
    response/credit usage.

### 6.4 Web evidence policy

If Web Fetch returns a legal name/CIN passage: - Save the exact URL and
exact passage. - Label it `company_website_claim` / self-published
evidence. - Record retrieval time. - Do not label the company legally
verified solely because its website states a CIN. - If the source is
blocked, stale, contradictory or lacks the required fact, treat the
claim as unverified.

Suggested resolution status names: - `RESOLVED_VENDOR_MASTER` -
`RESOLVED_EXACT_IDENTIFIER` - `FUZZY_CANDIDATE` - `LOOKALIKE` - `TIE` -
`PARENT_BRAND_MISMATCH` - `WEBSITE_IDENTITY_CLAIM_FOUND` -
`UNRESOLVED` - `SOURCE_UNAVAILABLE`

Avoid `NEW_VENDOR_VERIFIED` unless an authoritative verification source
is actually integrated.

------------------------------------------------------------------------

## 7. Checks and reconciliation

Each check returns a structured result with status, flags, reasons,
evidence and missing fields. A failed external call is an error/unknown,
never a pass.

### 7.1 Duplicate detection

Use at least two independent signals.

**K1 --- Invoice identity** - Canonical invoice number + resolved vendor
ID. - Normalize case, punctuation and known invoice prefixes. - Remove
leading zeros from numeric components only where safe. - Do not drop
fiscal-year tokens by default; they may distinguish invoices. - Compare
against earlier items in the same request, persisted items, and payment
history if supplied.

**K2 --- Transaction similarity** - Same resolved vendor/payee, amount,
currency and a configurable date window. - For bank statements, prefer
bank reference/UTR when present. - If the match is probabilistic rather
than exact, emit `DUPLICATE_SUSPECTED` and hold rather than declaring a
proven duplicate.

The official AWS duplicate should be caught. The later occurrence is
held with a reference to the earlier line; it must not be posted or paid
automatically.

### 7.2 Stable item identity vs transaction identity

Keep two separate identifiers:

-   `item_id`: identifies an occurrence in a particular input/run (can
    include input hash and line number).
-   `transaction_fingerprint`: identifies a likely logical transaction
    across re-uploads or file reordering.

For a bank line, use a bank reference/UTR as the strongest stable
identifier when present. Otherwise construct a fingerprint from
normalized payee, amount, currency, date and direction, but treat it as
a duplicate signal---not absolute proof.

Do not base a payment idempotency key solely on file line number if a
stronger logical payment identity is available.

### 7.3 Purchase-order matching

Only run PO checks if the kit supplies PO data and the task requires it.

Check: - PO exists. - PO belongs to the resolved vendor. - Invoice
amount is within configured absolute/percentage tolerance. - Quantity is
within configured tolerance where available. - PO is open and not
already fully billed. - Prior invoices have not exhausted the PO amount.

Any mismatch becomes `HOLD` with the exact PO record/field and
calculated variance. Do not assume PO files exist until confirmed in the
kickoff kit.

### 7.4 Bank-detail change detection

-   Compare invoice remittance account hash and IFSC with the trusted
    vendor master.
-   A mismatch becomes `BANK_CHANGED` and is always held.
-   A vendor email/message requesting a bank change becomes
    `BANK_CHANGE_REQUEST` and is always held.
-   Do not update the vendor master automatically.
-   A callback to a known contact is a business control for the
    real-world process; the competition system must not contact people.
-   IFSC lookup can add bank/branch facts, but cannot verify account
    ownership.
-   Display bank last-four digits only.

### 7.5 Account mapping

Use this priority: 1. Vendor-specific default account from the vendor
master. 2. Vendor category → chart-of-accounts mapping. 3. If no safe
mapping exists, `ACCOUNT_UNMAPPED` → `HOLD`.

Every selected account should cite the vendor-master field or config
mapping rule. Do not hardcode fictional client mappings into business
logic; keep mappings in client data/config.

### 7.6 Bank-statement reconciliation

If both payment history and bank statement data are supplied: - Match by
provider/bank reference first. - Then compare payee, amount, currency
and date window. - Distinguish `MATCHED`, `UNMATCHED`, `POSSIBLE_MATCH`
and `CONFLICT`. - A possible match is not proof of settlement. - Never
mark an item `PAID` from a similar narration alone; require the relevant
bank/payment record and exact supporting fields.

------------------------------------------------------------------------

## 8. Decision engine and policy

### 8.1 Deterministic decisions

The decision engine receives normalized `Facts`, check results, config
hash and source references. It does not receive raw prose or ask the LLM
to choose an action.

It: 1. Collects all flags. 2. Matches every applicable rule. 3. Includes
all reasons, not only the first. 4. Chooses the most conservative
applicable outcome. 5. Attaches sources and missing evidence. 6.
Produces a stable decision hash.

Suggested severity order:

`CANNOT_VERIFY` \> `HOLD` \> allowed default action (`POST` or `PAY`
candidate)

A successful check should never erase a more severe flag from another
check.

### 8.2 Suggested core flags

-   `PAYEE_UNRESOLVED`
-   `PAYEE_TIE`
-   `LOOKALIKE`
-   `FUZZY_MATCH`
-   `PARENT_BRAND`
-   `WEBSITE_IDENTITY_CLAIM_ONLY`
-   `DUPLICATE_SUSPECTED`
-   `PO_MISSING`
-   `PO_NOT_FOUND`
-   `PO_VENDOR_MISMATCH`
-   `PO_AMOUNT_MISMATCH`
-   `PO_QTY_MISMATCH`
-   `PO_EXHAUSTED`
-   `BANK_CHANGED`
-   `BANK_CHANGE_REQUEST`
-   `IFSC_INVALID`
-   `ACCOUNT_UNMAPPED`
-   `SOURCE_UNAVAILABLE`
-   `CREDITS_EXHAUSTED`
-   `COST_LIMIT`
-   `TIME_LIMIT`
-   `PARSE_FAILED`
-   `PAYMENT_OUTCOME_UNKNOWN`

### 8.3 Override policy

An override authorizes a narrowly defined human decision; it must not
bypass identity, duplicate or payment safety.

Never overridable into payment: - `PAYEE_UNRESOLVED` - `PAYEE_TIE` -
`LOOKALIKE` - `PARENT_BRAND` - `BANK_CHANGED` - `BANK_CHANGE_REQUEST` -
`PAYMENT_OUTCOME_UNKNOWN`

Potentially overridable only with explicit role/identity, reason and
audit event: - Certain PO tolerance exceptions. - Account mapping
exception, if the correct account is independently known. - A duplicate
*suspected* from a weak similarity signal, only after the reviewer
checks the underlying records and confirms the item is distinct.

Do not allow an override to create a new payment identity or a new
idempotency key for the same logical payment.

### 8.4 Consistency

The same normalized facts + same client-data version + same policy
config should produce the same decision. Hash canonical JSON of those
inputs into `decision_hash`.

This gives: - Reproducibility. - Approval binding. - Detection of stale
approvals. - A test that two differently worded but semantically
identical requests yield the same normalized decision.

------------------------------------------------------------------------

## 9. State machine, persistence and crash safety

### 9.1 Persistence choice

Use SQLite in WAL mode for the hackathon: - `journal_mode=WAL` -
`synchronous=FULL` - `foreign_keys=ON` - A single database file under
`var/`. - A single-process lock to prevent two CLI instances mutating
the same workflow concurrently.

This is sufficient for a sequential, single-process demo. PostgreSQL and
distributed workers are future scale choices, not build-time
requirements.

### 9.2 Suggested item state machine

``` mermaid
stateDiagram-v2
    [*] --> NEW
    NEW --> PARSED
    PARSED --> RESOLVED
    RESOLVED --> CHECKED
    CHECKED --> DECIDED
    DECIDED --> POSTING: permitted POST
    POSTING --> POSTED
    DECIDED --> AWAITING_APPROVAL: PAY candidate
    AWAITING_APPROVAL --> APPROVED
    AWAITING_APPROVAL --> REJECTED
    APPROVED --> PAYMENT_PENDING
    PAYMENT_PENDING --> PAYMENT_CREATED
    PAYMENT_CREATED --> PAYMENT_CONFIRMING
    PAYMENT_CONFIRMING --> PAID
    PAYMENT_CONFIRMING --> PAYMENT_FAILED
    PAYMENT_PENDING --> PAYMENT_UNCERTAIN
    PAYMENT_CONFIRMING --> PAYMENT_UNCERTAIN
    DECIDED --> HELD
    DECIDED --> CANNOT_VERIFY
    PAYMENT_FAILED --> HELD
```

`HELD`, `CANNOT_VERIFY`, `REJECTED`, `POSTED` and `PAID` are
rest/terminal states according to the workflow. `PAYMENT_UNCERTAIN` is
deliberately not a retry-ready state; it requires reconciliation.

### 9.3 State transition rules

-   Keep an explicit allowed-transition map.
-   Use compare-and-swap updates
    (`WHERE item_id=? AND state=? AND version=?`) to reject stale
    writes.
-   Persist the result of each stage before advancing.
-   Store the decision and evidence snapshots used for approval.
-   If client data/config changes, recompute the decision for
    non-terminal items and supersede any approval whose decision hash no
    longer matches.
-   Never silently re-run an already completed posting/payment.

### 9.4 Exactly-once: what we can and cannot promise

A local database transaction cannot atomically commit with an external
payment provider. Therefore, the honest design is **at-most-one
successful logical payment, supported by provider idempotency and
reconciliation**, not a magical distributed exactly-once transaction.

Definition for this project:

> One logical payment instruction must produce at most one successful
> external payment across duplicate inputs, repeated approvals, crashes
> and restarts. If the provider outcome cannot be established, PayPilot
> must stop and reconcile instead of issuing another payment.

### 9.5 Payment idempotency

Use a stable key based on the logical payment instruction, e.g.:

`hash(tenant_id, vendor_id, canonical_invoice_id, amount_paise, currency, payment_instruction_version)`

Where a bank-line payment has no invoice number: - Prefer bank
reference/UTR or another stable instruction ID. - If no reliable
identity exists, do not derive a new key from a new file line and
automatically pay; hold for review.

Database constraints: - Unique `payments.item_id`. - Unique
`payments.idempotency_key`. - Unique provider reference where
available. - Unique logical transaction/payment fingerprint where the
business rules support it.

**Do not append an approval ID to the idempotency key when a duplicate
hold is overridden.** Approval changes authorization, not payment
identity. Otherwise a duplicate override could accidentally authorize a
second payment.

### 9.6 Outbox and payment lifecycle

1.  Human approves a specific `decision_hash`.
2.  In one DB transaction, insert the payment outbox row and move the
    item to `PAYMENT_PENDING`.
3.  Payment executor sends a create request using a stable provider
    idempotency key.
4.  Persist provider reference as soon as known.
5.  Confirm/execute according to the provider API.
6.  On restart, reconcile existing provider state before sending any new
    request.
7.  Mark `PAID` only after provider-confirmed success.
8.  If provider outcome is ambiguous or provider is unreachable, keep
    `PAYMENT_UNCERTAIN`; do not create another payment.

### 9.7 Provider idempotency expiry

Provider idempotency keys may have finite retention windows. Do not
assume a key remains valid forever.

-   Within the provider's documented idempotency window, retry only with
    the exact same key and payload.
-   After that window, retrieve/search provider records using stable
    metadata/reference and reconcile.
-   If no definitive outcome can be established, require manual
    reconciliation. A search returning no result is not automatically
    proof that the original payment never happened.
-   Never create a new payment merely because the old idempotency key
    may have expired.

### 9.8 Payment provider choice and honest demo semantics

Use a `PaymentProvider` interface with: - `create(...)` - `confirm(...)`
if the provider uses a two-step flow - `retrieve(ref)` -
`find_by_idempotency_key(key)` or equivalent reconciliation method -
`refund(...)` only if needed for test cleanup

Recommended implementation order: 1. **FakeProvider first:**
SQLite-backed fake provider with its own persistent state, so crash
tests can prove one provider-side success across process restarts. 2.
**Stripe test mode only if time permits:** The proposed Stripe
PaymentIntent flow simulates a card charge. It is not a vendor bank
payout. Label it accurately in the demo and README. 3. A true vendor
payout integration such as a suitable payout product is a future
adapter, only if the team can verify test access and implement it
safely.

Do not claim a Stripe card PaymentIntent proves money was transferred to
the vendor's bank account.

### 9.9 Crash-injection points

Add configurable crash points around: - After item ingestion. - After
external-call budget reservation. - After external response but before
cache/ledger persistence. - After resolution/check/decision commit. -
After posting transaction. - After approval commit. - After payment
outbox insert. - After provider create request but before provider
reference persistence. - After provider reference persistence. - After
payment confirmation request but before local success persistence. -
After local `PAID` commit.

A test harness should repeatedly kill and resume the process and
assert: - No more than one successful provider payment per logical
payment key. - No duplicate posting row per item. - No duplicate
approval row for the same decision hash. - Resume converges to the same
business result as an uninterrupted run, ignoring timestamps. - Cost
records do not silently disappear; ambiguous external-call spend is
labelled as estimated/unconfirmed.

------------------------------------------------------------------------

## 10. External clients, retries, cache and cost controls

### 10.1 One external-client wrapper

All network calls should go through a common wrapper that provides: -
Timeouts. - Per-endpoint rate limiting. - Retry/backoff with jitter for
transient errors. - Provider-specific status handling. - Response
caching with TTL. - Cost reservation and confirmation. -
Request/response hashes and latency logs. - Redaction of secrets and
sensitive fields.

### 10.2 Status handling

  -----------------------------------------------------------------------
  Response                            Behaviour
  ----------------------------------- -----------------------------------
  2xx                                 Validate response; cache and record
                                      actual usage

  429                                 Respect `x-ratelimit-reset` where
                                      available; retry within bounded
                                      policy

  5xx / timeout                       Bounded exponential backoff; after
                                      exhaustion, return unavailable and
                                      hold

  402                                 Credits exhausted; do not retry;
                                      stop paid lookups and report

  401                                 Authentication/configuration error;
                                      do not loop retries

  403                                 Endpoint/account permission issue;
                                      do not treat as evidence

  Malformed/unexpected body           Validation error; do not infer
                                      success
  -----------------------------------------------------------------------

A failed or unavailable source must never cause a check to pass.

### 10.3 Cost ledger

Record each billable or relevant external call: - `call_key` -
run/item/stage - provider and endpoint - request hash - attempt number -
estimated INR reserved - actual units/credits/tokens - actual INR if
known - status: `RESERVED`, `CONFIRMED`, `FAILED`, `CACHE_HIT`,
`UNCONFIRMED` - HTTP status and latency - timestamp

Reserve estimated cost before a paid call. If the call succeeds,
reconcile to actual usage. If the process dies during the call, retain
an `UNCONFIRMED` entry and count conservatively against the budget until
reconciled.

Cache hits should be recorded as zero incremental external cost. Do not
report zero cost merely because a pricing value is still a placeholder.

### 10.4 Budget and time limit

-   Enforce `cost_inr_per_run` and optionally `cost_inr_per_item`.
-   Enforce a time limit between stages and per-call timeout.
-   On budget exhaustion, stop paid calls and finish remaining items
    with a clear `HOLD`/`CANNOT_VERIFY` result as appropriate.
-   On time exhaustion, persist the current state and mark the run
    `PARTIAL`; `resume` continues later.
-   Keep the limits configurable so the hour-three requirement change
    can be implemented quickly.

### 10.5 Financial feasibility measurement

The finance score is significant. Do not invent a price or present zero
placeholders as real economics.

Show: - Actual Crustdata credits per run and their INR conversion based
on the team's account pricing. - Actual LLM token usage and current
model price, if an LLM is used. - Infrastructure estimate and any other
per-item costs. - Cost per item and cost per successfully completed
item. - Cache-hit rate and percentage of items resolved entirely from
the client vendor master. - A proposed per-invoice service price,
supported by a real external benchmark the team researches and cites in
the final README/demo.

The team's notes give observed API credit usage, but actual INR
conversion and model pricing must be confirmed. Until confirmed, label
figures as estimates.

------------------------------------------------------------------------

## 11. CLI and output contract

### 11.1 CLI commands

Suggested interface:

``` bash
python -m apsvc run --request <PATH> [--config config.yaml] [--provider fake]
python -m apsvc resume [--run <RUN_ID>]
python -m apsvc approvals [--run <RUN_ID>]
python -m apsvc approve <ITEM_ID> --by <NAME> --reason "<REASON>"
python -m apsvc reject <ITEM_ID> --by <NAME> --reason "<REASON>"
python -m apsvc ledger [--run <RUN_ID>] [--json]
python -m apsvc report [--run <RUN_ID>]
```

The actual command names can be simplified. Keep the official kit
adapter independent of CLI internals.

### 11.2 Output record

The official kit format takes priority. Internally, a result should be
able to represent:

-   Item reference, date, descriptor/vendor description, amount,
    currency and intent.
-   Outcome/status.
-   Resolved legal name and vendor ID, if established.
-   Account mapping, if applicable.
-   All reasons and rule IDs.
-   Missing evidence.
-   Sources: exact record/field/passage, date basis and retrieval time.
-   Approval state, reviewer, time and reason.
-   Payment provider, reference, idempotency key and status, if
    applicable.
-   Per-item cost.

Do not add fields to the external output if the kit forbids them. Use
the adapter to map internal result fields into the required schema.

### 11.3 Human-readable report

A report should answer: - What did the service process? - What action
did it take or propose? - Which payee/account did it resolve? - Why? -
Which checks passed or failed? - What evidence supports each material
fact? - What could not be verified? - What requires human action? - What
did the run cost? - Did the process recover from any interruption?

### 11.4 Logs

Use structured JSONL events plus a concise human summary. Suggested
events: - `run_start` - `config_loaded` - `client_data_loaded` -
`item_ingested` - `stage_start` - `stage_done` - `external_call_start` -
`external_call_done` - `retry` - `cache_hit` - `decision` -
`approval_requested` - `approval_decided` - `posting_committed` -
`payment_outbox_created` - `payment_provider_call` -
`payment_reconciled` - `crash_recovered` - `budget_exceeded` - `run_end`

Include `run_id`, `session_id`, `item_id`, timestamps, state transitions
and safe structured metadata. Mask bank details and secrets.

------------------------------------------------------------------------

## 12. Configuration and live-change readiness

Keep policy and client-specific values in `config.yaml`, not scattered
through code.

Suggested configuration groups: - `client.*`: paths to vendor master, PO
file, payment history, bank statement and chart of accounts. -
`limits.*`: run time, per-call timeout, per-run cost, per-item cost. -
`normalize.*`: processor prefixes, city tokens, legal suffixes,
abbreviations. - `matching.*`: fuzzy thresholds and alias list. -
`duplicate.*`: date window and comparison sources. - `po.*`: required
intents and tolerances. - `bank.*`: IFSC lookup policy and
non-overridable bank-change rule. - `accounts.*`:
category/vendor-to-account mapping. - `decision.rules`: flag-to-outcome
rules and reason templates. - `payments.*`: provider, currency, test
mode, idempotency policy. - `crustdata.*`: base URL, API version,
endpoint rate limits and retry policy. - `costs.*`: unit prices and
estimates. - `output.*`: required output fields/format. - `logging.*`
and `storage.*`.

Validate config strictly (e.g. Pydantic `extra="forbid"`). Hash the
canonical config and persist the hash on the run. Re-read config and
client data on each new run/resume. If material data changes, invalidate
stale decisions/approvals.

### Live change rehearsal

The team should rehearse at least: - Lowering the run cost limit. -
Adding an output field that is already available internally. - Changing
a duplicate date window. - Adding a new decision rule.

A change requiring a new data source or a new computed field is not
honestly a one-line config change; explain the minimal code addition if
that is what the judge asks.

------------------------------------------------------------------------

## 13. Testing and acceptance criteria

### 13.1 Test layers

1.  **Unit tests:** normalization, canonical invoice number, matching,
    rule severity, idempotency-key generation, state transitions.
2.  **Integration tests:** SQLite transactions, vendor/PO/history
    loaders, fake external clients, approval flow, CLI output.
3.  **Golden tests:** official four lines and expected output.
4.  **Failure tests:** timeout, 429, 402, 401/403, malformed data,
    process kill at each side-effect boundary.
5.  **Live smoke test:** one permitted live Crustdata/IFSC call with
    actual cost logged; payment provider remains test mode.
6.  **Rehearsal:** unseen request, process kill/restart, and live
    config/output change.

### 13.2 Required cases

  -----------------------------------------------------------------------
  Test                                Expected behaviour
  ----------------------------------- -----------------------------------
  Official Swiggy line                Correct vendor/account from vendor
                                      master

  First AWS EMEA line                 Exact EMEA entity from vendor
                                      master, correct cloud account

  Second AWS EMEA line                Duplicate suspected; held; no
                                      second posting/payment

  PAYTM Sharma line                   `CANNOT_VERIFY`; missing source
                                      named; no guessed payee

  Duplicate invoice with reformatted  Duplicate suspected; held
  number                              

  Lookalike vendor                    No blind merge; hold/cannot verify
                                      depending on evidence

  Two equally plausible vendors       `CANNOT_VERIFY` with candidates

  Parent brand vs invoicing legal     `CANNOT_VERIFY` unless exact
  entity                              vendor-master entity is established

  Bank-change message                 `HOLD`; no vendor-master mutation;
                                      no payment

  Invalid IFSC                        `HOLD`; exact lookup evidence
                                      attached

  Valid IFSC but unknown account      State only that IFSC/branch is
  ownership                           valid; account ownership remains
                                      unverified

  PO amount outside tolerance         `HOLD` with variance and PO source

  External source timeout             `HOLD`/`CANNOT_VERIFY`; never pass

  Cost limit exceeded                 No further paid calls; every item
                                      receives an honest result

  Same normalized request worded      Same normalized facts and decision
  differently                         hash

  Duplicate approval command          No second approval or payment

  Crash after provider accepted       Resume reconciles; no second
  create/confirm                      successful payment

  Provider outcome unavailable        `PAYMENT_UNCERTAIN`; no blind retry

  Client vendor data changes          Recompute non-terminal decisions;
                                      supersede stale approvals
  -----------------------------------------------------------------------

### 13.3 Kill-and-resume test

Use a persistent FakeProvider with a separate SQLite database so
provider-side state survives process termination.

Run many iterations: 1. Start a fresh application DB and fake-provider
DB. 2. Run the same request. 3. Kill the process at a random crash
point. 4. Resume. 5. Optionally kill again. 6. Continue until all items
reach a rest/terminal state. 7. Compare with an uninterrupted golden
run.

Assertions: - At most one successful payment per logical idempotency
key. - At most one posting per item. - At most one approval per
`(item_id, decision_hash)`. - Repeated approval commands are
idempotent. - Final business outcomes match the uninterrupted run. -
Every external call has a cost-ledger record, including unconfirmed
calls. - No `PAID` state without provider-confirmed success.

------------------------------------------------------------------------

## 14. Team plan and ownership

The official team is three builders. The earlier four-owner breakdown
should be consolidated.

Suggested three-person split:

  ----------------------------------------------------------------------------------
  Owner                   Primary responsibility             Secondary
                                                             responsibility
  ----------------------- ---------------------------------- -----------------------
  Builder 1               Core pipeline, SQLite, state       Integration and final
                          machine, resume, payment outbox    architecture defence
                          and FakeProvider                   

  Builder 2               Vendor normalization/matching,     Evidence model and
                          Crustdata, Web Fetch, IFSC,        source correctness
                          external-call wrapper              

  Builder 3               Duplicate/PO/bank/reconciliation   CLI, report,
                          checks, deterministic decision     README/diagram and
                          rules, tests and golden fixtures   finance/ethics
                                                             narrative
  ----------------------------------------------------------------------------------

This is a proposal; assign based on actual strengths and availability.
Avoid hard ownership silos: all three should understand the shared
schemas, status semantics and end-to-end demo.

### Shared contract before parallel work

Agree and commit: 1. `Item`, `Vendor`, `Source`, `CheckResult`,
`Decision`, `Approval`, `Payment`, `LedgerEntry`. 2. State and outcome
enums. 3. Config keys. 4. Database table names and transaction
boundaries. 5. Function signatures between pipeline, resolver, checks,
decision engine and payment executor. 6. CLI command/output contract.

One person should own integration and resolve conflicts; do not let
multiple coding agents independently rewrite shared files.

------------------------------------------------------------------------

## 15. Build priorities and time management

The official build window is seven hours, with a code freeze at 6:00 PM
IST in the team's event notes. The timeline below is a suggested
sequencing, not a reason to delay an end-to-end vertical slice.

### P0 --- must work

-   Locate and read the kickoff kit; implement its exact I/O format.
-   Load vendor master and the records actually supplied.
-   Process the four official example lines.
-   Deterministic payee resolution and account mapping.
-   Duplicate detection.
-   `HOLD` / `CANNOT_VERIFY` with reasons and source references.
-   SQLite persistence and resume.
-   Human approval before payment.
-   FakeProvider with persistent idempotency and crash reconciliation.
-   Cost/time limits and a usable cost ledger.
-   README, architecture diagram, logs and reproducible demo.

### P1 --- add after P0 is stable

-   Live Crustdata Identify by domain for an appropriate unknown vendor.
-   Web Fetch for a specific citable company-site fact.
-   Razorpay IFSC lookup.
-   PO matching if the kit supplies POs.
-   Stripe test-mode simulation, accurately labelled.
-   Expanded failure-mode and kill-loop tests.
-   Measured finance report.

### P2 --- cut first if time is short

-   Dashboard / web UI.
-   Multi-tenant production enforcement.
-   Distributed workers and Postgres.
-   RazorpayX production adapter.
-   LLM parsing if the kit is structured.
-   Elaborate fuzzy matching beyond candidate generation.
-   Optional report formatting and non-essential enrichment.

### Never cut

-   Official kit I/O compatibility.
-   Correct official four-line outcomes.
-   No guessing for lookalikes/unanswerable questions.
-   Exact evidence for claims.
-   Human approval before payment.
-   Persistent state and restart recovery.
-   Stable payment idempotency and provider reconciliation.
-   Cost/time limits and per-call ledger.
-   Secrets hygiene.

### Recommended execution sequence

**First 30 minutes** - Locate kickoff kit and confirm actual
input/output formats and files. - Commit shared schemas/config
skeleton. - Create SQLite schema and CLI stubs. - Build a vertical slice
with fake clients.

**Next 60--90 minutes** - Get official four-line test working
end-to-end. - Implement exact/alias matching, duplicate logic and
deterministic decision engine. - Generate source-linked report.

**Next 60--90 minutes** - Implement state persistence, resume, approval
gate, FakeProvider and outbox. - Add crash points and the first
kill/restart test. - Apply the hour-three requirement change immediately
when announced.

**Next 60--90 minutes** - Add live Crustdata/IFSC calls only where they
materially support a decision. - Add PO/bank checks if the kit contains
those records. - Implement cost/time enforcement and retry/cache
behaviour.

**Final 60--90 minutes** - Expand hidden-case tests. - Measure actual
per-item costs. - Finish README and architecture diagram. - Rehearse the
3-minute demo and 5-minute live fix. - Freeze with a clean, reproducible
repository.

The exact schedule should be adjusted to the team's remaining time. The
critical milestone is a working end-to-end CLI early, not a large
collection of unfinished modules.

------------------------------------------------------------------------

## 16. Demo narrative

The demo should prove the service's safety properties, not merely show
successful records.

Suggested sequence:

1.  **Normal item:** process the Swiggy line; show the vendor-master
    source and account mapping.
2.  **Correct legal entity:** process AWS EMEA; show why the EMEA
    vendor-master record is used rather than the generic AWS parent
    brand.
3.  **Duplicate:** process the repeated AWS line; show the earlier line
    reference and `HELD` outcome.
4.  **Cannot verify:** process PAYTM Sharma; show no guessed payee, zero
    unnecessary external lookup cost, and the missing evidence.
5.  **Payment approval:** use a separate invoice/PO test fixture; show
    `AWAITING_APPROVAL`, explicit human approval, and a test-provider
    payment.
6.  **Crash:** kill the process around payment execution; restart; show
    reconciliation and no second successful payment.
7.  **Cost:** show actual ledger usage, cost per item, cache hits and
    remaining budget.
8.  **Change request:** lower a cost limit or add an available output
    field through config/adapter and rerun.

Do not make a real payment, contact a real person, or present a
card-charge simulation as a bank payout.

------------------------------------------------------------------------

## 17. Finance, ethics and judge Q&A

### 17.1 Finance

**Buyer:** 3PL finance controller / AP team, or outsourced AP provider.

**Revenue model:** per invoice or transaction processed, potentially
with a tiered plan for volume.

**Evidence to show:** - Measured cost from the cost ledger. - Cost per
item and cost per completed item. - Share of cases resolved from
existing vendor-master data without paid lookups. - Actual external API
and LLM usage. - A sourced market benchmark before proposing a rupee
price.

Do not make up a per-invoice price. Research and cite an actual
benchmark; explain that your proposed price is a hypothesis until
validated with customers.

### 17.2 Ethics

-   No real payments or external contact.
-   Human approval before payment.
-   Bank changes never auto-approved.
-   No unsupported identity claims.
-   No guessing when sources conflict or are absent.
-   Minimal personal/financial data retention.
-   Mask bank details and secrets.
-   Exact source evidence and audit trail.
-   Approval bound to a decision hash.
-   Fail closed on external-source failure and uncertain payment
    outcome.

### 17.3 Likely judge questions

**Why not let the LLM decide?**\
Because the payment decision must be reproducible and explainable. The
model may extract messy text; deterministic rules decide based on
normalized facts and sourced evidence.

**How do you know the vendor is real?**\
The client vendor master is the primary source. Crustdata and company
websites provide supplementary evidence. The system does not treat a
brand match or self-published website claim as authoritative legal
verification.

**How do you prevent duplicate payments after a crash?**\
A persistent outbox, stable idempotency key, provider-side idempotency,
provider reconciliation on resume and an uncertain state that blocks
blind retries.

**What if the provider is down after accepting a payment?**\
The item remains `PAYMENT_UNCERTAIN`. We reconcile using the same
provider reference/key and do not create a new payment until the outcome
is established.

**What if the vendor changes bank details?**\
The item is held. IFSC lookup may validate bank/branch metadata, but it
does not verify account ownership. A trusted vendor-master update is
required through the client's separate verification process.

**What if Crustdata returns the parent brand?**\
We do not infer the invoicing legal entity from it. We require an exact
vendor-master record or other adequate evidence; otherwise
`CANNOT_VERIFY`.

**How would you scale 100×?**\
Keep the deterministic stages and contracts; move persistence to
PostgreSQL, use workers/queues and a shared rate limiter/cache, and
preserve the outbox/idempotency/reconciliation semantics. This is a
future architecture, not something built in the hackathon.

**How would you support a second client?**\
Separate client data and config, enforce tenant scoping at the
persistence boundary, and use composite foreign keys/unique constraints.
The hackathon implementation is single-client unless time permits fully
tested tenant isolation.

------------------------------------------------------------------------

## 18. Open questions and assumptions to resolve at kickoff

The official problem material says the kickoff kit contains the client
name, records, requests and exact input/output formats. The team notes
say the kit was not yet located when the initial plan was written.
Therefore, the following are assumptions---not confirmed facts:

1.  Does the kit provide bank lines, invoices, or both?
2.  Does it provide a vendor master? Which columns and identifiers?
3.  Are POs, payment history, bank statements and chart of accounts
    included?
4.  What is the exact input and output schema?
5.  Does the request explicitly state `POST`, `PAY` or `RECONCILE`
    intent?
6.  Which actions count as posting versus payment in the official task?
7.  What data is updated by organizers during the build?
8.  Which external Crustdata endpoints are enabled and what is the
    current credit price?
9.  Is a test payment provider account already configured and usable?
10. What is the required cost/time ceiling?

**Rule:** implement adapters and optional checks around the actual kit.
Do not build elaborate parsing, PO matching or bank-statement
reconciliation around files that are not present. The official example
is the minimum supported path until the kit proves otherwise.

------------------------------------------------------------------------

## 19. Approval checklist for the team

Before implementation, agree on:

-   [ ] Product name and buyer story.
-   [ ] CLI-first scope; dashboard is optional.
-   [ ] Official kit I/O is the source of truth.
-   [ ] Shared schemas, states, outcomes and config contract.
-   [ ] Vendor master is the primary payee source.
-   [ ] Crustdata is supplementary and domain-first.
-   [ ] Website evidence is labelled self-published, not authoritative
    verification.
-   [ ] IFSC validity is not account-ownership verification.
-   [ ] LLM is limited to unstructured extraction; deterministic code
    decides.
-   [ ] Duplicate override does not change payment idempotency identity.
-   [ ] Provider uncertainty never triggers a blind new payment.
-   [ ] FakeProvider is used for repeatable crash tests.
-   [ ] Stripe PaymentIntent, if used, is described as a test charge
    simulation---not vendor payout.
-   [ ] All payments require human approval.
-   [ ] Cost/time limits and per-call ledger are implemented.
-   [ ] Three-person work allocation is agreed.
-   [ ] Required hidden cases and crash tests are in the acceptance
    suite.

------------------------------------------------------------------------

## 20. Source basis and change log

### Source basis

This unified proposal was prepared from: - The team's
`HACKATHON_INFORMATION.md`, especially Part A (binding challenge
requirements), Part B (Buying & Paying decision and intended pipeline),
Part C (verified Crustdata/IFSC observations) and Part D (API
reference). - The team's `PLAN.md`, especially its state machine, SQLite
WAL design, module boundaries, evidence model, duplicate/PO/bank checks,
decision rules, cost ledger, CLI, crash-injection plan and test
matrix. - The earlier PayPilot product proposal, which emphasized a
finance-team buyer, invoice-to-payment workflow, a dashboard concept, a
modular monolith, approval gate, audit trail, data model and a staged
P0/P1 implementation plan.

### What was consolidated or deliberately changed

1.  Retained the friend's CLI-first modular monolith rather than
    requiring a dashboard. The dashboard remains optional after core
    acceptance tests pass.
2.  Retained the detailed state machine, SQLite WAL, compare-and-swap
    transitions, outbox, cost ledger, config-driven rules, structured
    logs and kill/restart tests.
3.  Consolidated the team into three builders, consistent with the
    stated team size.
4.  Removed the proposal to append approval ID to a payment idempotency
    key. Approval must not create a new payment identity.
5.  Strengthened the provider-expiry rule: after the provider
    idempotency window, unresolved outcomes require manual
    reconciliation; a missing search result is not permission to create
    another payment.
6.  Clarified that Stripe PaymentIntent is a test card-charge
    simulation, not a vendor bank payout.
7.  Replaced `NEW_VENDOR_VERIFIED` from a company-website passage with a
    weaker self-published identity claim.
8.  Clarified that IFSC lookup does not verify account ownership and
    that HTTP `Date` is not a record update date.
9.  Separated input occurrence identity from logical transaction
    identity.
10. Made PO matching, invoice parsing, bank-statement reconciliation and
    other data-dependent features conditional on the actual kickoff kit.
11. Treated multi-tenancy and scale-out as future design answers, not
    promised current capabilities.
12. Added explicit `PAYMENT_UNCERTAIN` state and an
    at-most-one-successful-payment safety definition.

**This document should be treated as the team's unified review draft.
Once the kickoff kit is confirmed, update the data model, adapter, test
fixtures and acceptance criteria to match the exact files and output
contract.**
