# PayPilot Technical Reference

This document describes the repository as it exists in this checkout. The design contracts in `docs/` include planned behavior beyond what is currently implemented; those differences are called out explicitly. The product is a Python CLI prototype for deterministic accounts-payable operations, with an intended SQLite-backed state machine and separately owned pure decision modules.

## 1. Runtime and dependency profile

- Python `>=3.11` (`pyproject.toml`); Pydantic v2 for strict models.
- PyYAML loads product/client configuration; `httpx` is used only by `paypilot/sources/http.py`; RapidFuzz performs fuzzy vendor-name scoring; Typer and Rich provide the CLI.
- SQLite is the durable runtime store. The schema is in `db/schema.sql`; connections use the project DB layer and transaction helpers.
- Pytest is the `dev` extra. Test fakes are in `tests/fakes.py` and cover external client, history, cache, budget and ledger ports.
- **Platform limitation:** `paypilot/core/lock.py` imports Unix `fcntl`. The CLI cannot currently start on native Windows without a lane A portability change.
- **Provider limitation:** `paypilot/payments/executor.py` and `paypilot/payments/reconcile.py` are stubs. No `payments/fake.py` or `payments/razorpayx_test.py` exists in this checkout. Payment execution and its exactly-once guarantee are therefore not complete or verified here.

## 2. Component map

| Package | Responsibility | Implementation state |
|---|---|---|
| `paypilot/io/` | Parse request/batch/client CSVs; decimal-string INR to integer paise; account fingerprinting; deterministic item IDs | Implemented modules present |
| `paypilot/resolve/` | Normalize descriptors, names and invoice numbers; match to client vendor master | Implemented; evidence-bearing vendor-master match |
| `paypilot/sources/` | HTTP external client; Crustdata domain identify; website CIN claim; IFSC lookup | Modules present; identity source functions are not yet called by `resolve_vendor` |
| `paypilot/checks/` | Required fields, period, posting scope, duplicate, PO, account and bank checks | Implemented modules present; check runner fails closed if configured check missing |
| `paypilot/decide/` | Turn resolution and check flags into proposal, reasons and decision hash | Implemented modules present |
| `paypilot/exceptions/` | Classify flags and prepare recovery data | Classification/lifecycle/recovery modules present; full persistent workflow incomplete |
| `paypilot/reconciliation/` | Compare instruction/provider/statement facts | Pure matching function present; operational invocation is incomplete |
| `paypilot/core/` | Config, IDs, DB transactions, item state, audit events, journals, pipeline and process lock | Substantial runtime primitives present; CLI portability and downstream integration gaps remain |
| `paypilot/ledger/` | Reserve and finalize call costs, cache, apply budget and summarize | SQLite ports and budget/cache modules present; external client integration has outstanding issues listed below |
| `paypilot/cli/` | Typer command handlers, loading, locking and output orchestration | `run`, `resume`, `report`, `show`, `ledger`, `approvals`, `approve`, and `reject` handlers exist; some routes reach incomplete payment/provider code |
| `paypilot/reports/` | Generate result and audit-support output files | Writer module present |
| `paypilot/audit/` | Audit replay verification | `audit/verify.py` is still a stub |

## 3. Data flow

1. `io.request.load_request` validates request JSON, explicit operation, client and limits.
2. `core.config.load_config` reads product `config.yaml` and client policy, applies permitted policy overrides, validates typed source/policy sections, and computes config/policy hashes.
3. `io.client_data.load_client_data` loads vendor, PO, payment-history and chart-of-accounts files; raw accounts are converted at the boundary to HMAC-SHA256 fingerprints and last-four display values. A data hash binds decisions to the loaded records.
4. `io.batch.load_items` parses input rows. Money is represented as integer paise. Malformed rows become `ParseFailure` records rather than silently disappearing.
5. `core.pipeline.ingest_run` assigns deterministic run/item identifiers and inserts rows idempotently. The same request source hash maps back to the same run.
6. The item state machine advances through `NEW → PARSED → RESOLVED → CHECKED → DECIDED`, then to a terminal/queue outcome. Lane B resolution and lane C checks/decisions return structured models; lane A persists transitions and evidence.
7. Eligible bank lines use the journal posting path. Eligible invoices are intended to enter an approval/outbox/provider path, but the executor is currently a stub.
8. Output writer serializes database views into configured output files; report/ledger commands read stored state.

The actual resolver currently consults client vendor data and returns an unresolved/tie/lookalike/fuzzy outcome with vendor-master evidence. Although Crustdata and website fetch functions are defined, there is no call site from the matcher to those functions at present. The IFSC adapter is called by `checks/bank.py` for the relevant check path.

## 4. Input and persisted models

### Request operations

- `POST_BANK_LINES`
- `PAY_INVOICES`
- `RECONCILE` (contracted; runtime orchestration is incomplete)

Request operation is explicit. The model includes client ID, optional date period and request cost/time limits. `amount_inr` inputs are strings; `io/money.py` rejects floating point amounts and converts exactly to paise.

### Input records

- `BankLine`: source and line IDs, date, raw and normalized descriptor, paise amount, currency, debit/credit, optional bank reference and transaction fingerprint.
- `Invoice`: source and invoice IDs, vendor name, optional GSTIN/domain, raw and canonical invoice number, dates, amount/currency, PO and quantity, account fingerprint/last four, IFSC and transaction fingerprint.
- `VendorMessage`: source/message IDs, received time, optional sender domain, vendor name, message type, new account fingerprint/last four and IFSC.
- `Vendor`, `PurchaseOrder`, `HistoryRecord`, `ClientData`: typed client records and data-quality warnings.

All Pydantic models forbid undeclared fields. The central model definitions and enums are in `paypilot/schemas.py`.

### Evidence model

`Source` stores `source_id`, provider, record reference, fact name, value, exact field/passage, optional `record_date`, `record_date_basis`, `retrieved_at` and limitations. Retrieval time is not a record date. Source providers include vendor master, IFSC, Crustdata, company website claim, batch input, payment history and provider records.

Evidence limitations are intentionally narrow:

- Vendor master proves what the client record states, dated by its record field where available.
- IFSC proves IFSC/bank/branch metadata, not bank-account ownership.
- Crustdata domain identification proves a domain association only, not the legal entity issuing the invoice.
- Website text is a self-published claim, not registry verification.
- `ownership_verified` must remain `NOT_VERIFIED`.

## 5. Identity normalization and matching

`paypilot/resolve/normalize.py` contains three deterministic functions, configured by `normalize` policy:

- `descriptor_key`: removes configured processor prefixes and city tokens, expands configured abbreviations, uppercases and joins tokens. Examples include `RAZ*`, `PAYTM*`, UPI/NEFT/IMPS markers and Bengaluru/BLR variants.
- `name_key`: tokenizes/uppercases, expands abbreviations and removes only configured legal suffixes. Meaningful tokens such as `EMEA` remain.
- `invoice_canonical`: tokenizes, removes configured invoice noise terms and normalizes numeric segments to remove leading zeroes. Separators become a stable `|` representation.

`resolve_vendor` uses this order:

1. exact GSTIN, CIN and account fingerprint (only an unambiguous single master row resolves);
2. exact normalized legal name or alias;
3. fuzzy scoring with `rapidfuzz`, configured floor, threshold and margin.

Multiple equal candidates produce `TIE`/`PAYEE_TIE`. Near-name and partial-name results are `LOOKALIKE` or `FUZZY_CANDIDATE`, never a resolution. No match yields `UNRESOLVED`/`PAYEE_UNRESOLVED`. Resolved master facts carry a `vendor_master` Source.

**Current gap:** domain is read from invoice/message records but is not used to call Crustdata from the resolver. There is no integrated parent-brand comparison in the matcher. The source adapters alone do not satisfy the live-data-in-a-decision requirement.

## 6. External source client

`StageContext` passes `ext`, `history`, configuration, data and a clock into lane B/C functions. `ExtClient` exposes:

- `get_json(provider, operation, url, params, item_id, stage)`
- `post_json(provider, operation, url, body, item_id, stage)`
- `fetch_page(url, item_id, stage)`

The HTTP client uses `httpx` and is intended to couple source calls to `CachePort`, `LedgerPort` and `BudgetPort`. It has endpoint token buckets, status-to-error mapping, retries, cache serialization, retrieved-at handling and response-header filtering. Crustdata calls use configured API version and domain-only request body. HTTP 402 maps to credits exhausted; 401/403 map to typed failures; failed calls do not become passes.

Source adapters:

- `sources.crustdata.identify_by_domain`: posts `{"domains": [domain]}` to `/company/identify`, interprets company matches, preserves ties, and marks domain association as insufficient evidence of the billed legal entity.
- `sources.webfetch.fetch_identity_claim`: visits configured website paths through `ctx.ext`, applies the domain skip list and challenge markers, extracts a CIN-containing passage, and returns it as `company_website_claim` with self-published limitation.
- `sources.ifsc.lookup`: calls the configured IFSC endpoint, distinguishes valid/not-found/unavailable, and provides evidence that explicitly does not prove ownership.

**External client integration risks identified in the current code:** `sources/http.py` derives run/session IDs from `stage`, but the ExtClient contract passes `stage` as a string; those IDs therefore fall back to `unknown`. It also uses random retry jitter despite the project hard rule forbidding randomness, imports an unused query helper, and the checked-in unit suite does not test the HTTP client’s 200/429/402/403/timeout/budget paths. These should be fixed before claiming source-call accounting and deterministic retry behavior are production-ready.

## 7. Checks and decision policy

`checks.runner.run_checks` executes configured checks in order and rejects an unimplemented configured check rather than silently passing. Check implementations cover:

- required fields;
- period and posting scope (direction, currency, positive amount);
- duplicate bank transactions and invoices using normalized keys/history;
- purchase-order existence, vendor, amount, quantity and exhaustion/tolerance;
- chart/default account mapping;
- bank details against vendor master, bank-change requests, IFSC metadata and vendor containment.

Each check returns `CheckResult` with `PASS`, `FAIL`, `SKIPPED`, `ERROR` or `UNKNOWN`, flags, reasons, missing data, details and evidence. A missing optional file should be `SKIPPED` with a reason, never treated as PASS.

`decide.engine.decide` collects all flags and reasons, applies validated config rules and computes a deterministic `decision_hash` over normalized facts and policy/data hash input. The design gives `CANNOT_VERIFY` precedence over `HELD`; unsafe identity, changed bank, duplicate, missing required data, payment uncertainty and persistence flags are not overrideable.

## 8. Persistence, journal and lifecycle

`db/schema.sql` defines durable runs, sessions, items, sources/item evidence, journal entries/lines, approvals, payments/attempts, exceptions/recovery cases, cost ledger and audit events. The database runs through `core/db.py` transaction helpers. State transitions use compare-and-swap on state/version and append audit information in the transaction.

Deterministic IDs and canonical hashes live in `core/ids.py`:

- run ID from client and request source hash;
- item ID from client, request hash, input file and row number;
- bank/invoice transaction fingerprints;
- logical payment key from client/vendor/canonical invoice/amount/currency;
- attempt and step keys, approval IDs, source IDs, exception IDs and call keys.

`core/journal.py` constructs and validates balanced entries for bank-line posting, invoice accrual, invoice payment and reversals. The invoice payment and reversal paths are present as primitives, but the payment executor that should invoke them is not implemented.

## 9. Limits, cache and cost accounting

Every external request should reserve budget and create a cost-ledger row before the request, then confirm/fail it. The ledger distinguishes `KNOWN`, `PARTIAL` and `UNKNOWN`; unknown cost is never zero. Unconfirmed work after a crash retains its estimate. Cache entries are keyed by provider/operation/request hash and carry retrieved time and expiry metadata.

Product config includes runtime, cost, per-item and call-time limits. Client policy may override policy sections and the approver allowlist only. Cost configuration for Crustdata has no ₹/credit conversion at this time. A successful authenticated `GET /account/credits` was recorded in `docs/DECISIONS.md`, but the dollar/product rate card does not resolve the INR cost of the Company Identify API call.

**Implementation caveat:** the HTTP client currently calls both `budget.reserve()` and `ledger.reserve()`, while the SQLite ledger’s `reserve()` can itself invoke a pre-reserve budget callback. Confirm the runtime wiring to avoid double-reserving budget before external calls. Also verify that successful live sources return correct provider costs/units before using ledger totals as measured unit economics.

## 10. CLI and outputs

`python -m paypilot` is exposed through Typer. The code declares handlers for:

- `run --scenario DIR` or `run --request PATH`;
- `resume [--run RUN_ID]`;
- `report [--run RUN_ID] [--json] [--out PATH]`;
- `show ITEM [--run RUN_ID]`;
- `ledger [--run RUN_ID] [--json]`;
- `approvals [--run RUN_ID]`;
- `approve ITEM --by NAME [--reason TEXT] [--override FLAG…]`;
- `reject ITEM --by NAME --reason TEXT`.

The contract also describes `exceptions`, `exception`, `resolve-exception`, `export`, `audit verify` and `demo reset`; those commands are not all present in the current CLI file. CLI output is designed to include a rich summary and durable files under `out/<run_id>/`: results JSONL, report Markdown, exceptions, ledger, audit, journal and summary.

Output fields are controlled by config. The payment instructions CSV, when emitted by the complete design, is an audit/reconciliation export marked `do_not_execute=TRUE`, never a manual payment file.

## 11. Tests and scenarios in this checkout

Present tests include:

- contract tests for schema/config, imports, fakes, A1/A2/A3/A4/A5/A6/A7 contracts;
- unit tests for checks, decisions, exception lifecycle/classification, I/O policy/scenario fixtures, reconciliation, normalization/matching and source adapters.

Scenario fixtures are present for `SC-01` through `SC-11` (some contain expected files/test profiles). The task documentation also describes SC-12 auditor replay and SC-13 RazorpayX, but those scenario directories are absent. No `tests/integration/` or `tests/crash/` folders are present in this checkout. Test fixture presence is not proof that the expected scenario outputs currently pass.

## 12. Security and privacy boundaries

- `.env` is for local secrets and is not committed. Never paste secrets into source, logs or reports. The current checkout has no `.env.example` file.
- A test provider must reject live credentials and must never silently fall back to live mode. The RazorpayX adapter is absent in this checkout.
- Raw bank accounts are restricted to ingestion and the explicitly designed provider handoff; elsewhere use HMAC fingerprints and last four digits.
- No personal data is needed in the NorthBay fixtures. Do not add personal contact information.
- Do not treat a company website claim as registry evidence, IFSC as ownership verification, provider acceptance as terminal payment success, or provider success as bank settlement.

## 13. Known submission gaps (current checkout)

1. Native Windows startup fails because `fcntl` is imported by the process lock.
2. Payment executor/reconciler and FakeProvider/RazorpayX adapters are absent; approvals cannot complete the payment lifecycle.
3. `audit.verify_run` is a stub; the audit-replay acceptance test is not present.
4. Operational reconciliation and exception CLI routes are incomplete relative to the interface contract.
5. Crustdata and Web Fetch adapters are not wired into matching; the live-data decision requirement is therefore not met by the current pipeline.
6. IFSC fixture codes remain placeholders; live fixture checks and the RazorpayX test payout dependency are unresolved.
7. HTTP source-client ledger/budget/retry behavior needs correction and focused tests.
8. No integration/crash test directories are present; written scenario expectations have not all been demonstrated.
9. Crustdata price per credit in INR is unknown, so measured external cost cannot yet be fully reported in INR.
10. The external organizer kickoff kit is not in this repository; no adapter to its input/output contract is included.

These gaps should be resolved or explicitly disclosed before claiming the final pass mark.
