# PayPilot: Interfaces & Data Contracts (frozen at S0)

> The exact contract all three lanes code against. Design rationale is in [`ARCHITECTURE.md`](ARCHITECTURE.md).
> **Change rule:** additive changes go through [`CONTRACT_CHANGES.md`](CONTRACT_CHANGES.md) and one `contract:` commit by lane A. Renames and removals need every human's agreement. Nobody may change the payment-key formula, the approval binding, `NEVER_OVERRIDABLE` (except to add to it), or outcome meanings.

## 1. Conventions
- Pydantic v2, `extra="forbid"` everywhere. Python 3.11+. Package `paypilot`.
- **Money:** integer paise (`amount_paise`). Floats are never used for money. ₹ is shown only in reports.
- **Time:** timezone-aware. Stored in UTC; shown in IST. Business dates are `date`.
- **IDs:** deterministic wherever they represent a logical identity (§5), which makes restarts idempotent.
- **Accounts:** raw bank account numbers exist only inside `io/` while loading. Everywhere else: `account_fingerprint` (HMAC-SHA256 with `BANK_HMAC_KEY`) + `account_last4`.
- **Purity:** lanes B and C never import `sqlite3`, `stripe` or `anthropic`, and never write files. Only `sources/http.py` may do network I/O, and only through A's ports.
- **No LLM:** inputs are structured CSV/JSON and parsed deterministically. There is no LLM dependency in the product.

## 2. Input formats (ours: the product's public batch API)

### 2.1 Layout
```text
clients/<client_id>/
  policy.yaml               # client rules (overrides config.yaml defaults; §7)
  vendor_master.csv         # REQUIRED
  purchase_orders.csv       # optional → PO check SKIPPED if absent
  payment_history.csv       # optional
  chart_of_accounts.csv     # optional
scenarios/<scenario_id>/
  request.json
  bank_lines.json | invoices.json | vendor_messages.json | bank_statement.json   (as referenced by request.json)
  expected.jsonl            # golden outcomes (tests + demo)
```

### 2.2 `request.json`
```json
{
  "schema_version": "1.0",
  "request_id": "northbay-oct-close-001",
  "client_id": "northbay",
  "operation": "POST_BANK_LINES",
  "instruction": "Post these bank lines from October 2026 to the correct accounts.",
  "period": {"from": "2026-10-01", "to": "2026-10-31"},
  "inputs": {"bank_lines": "bank_lines.json"},
  "limits": {"max_runtime_seconds": 600, "max_cost_inr": 25.0}
}
```
- `operation` ∈ `POST_BANK_LINES | PAY_INVOICES | RECONCILE` is **required and explicit**. We don't infer intent from prose.
- A missing or unsupported operation → the run is still processed. Its output is a single run-level `CANNOT_VERIFY` result with flag `UNSUPPORTED_REQUEST` and `missing: ["supported operation"]`. It is never a crash and never a guess.
- `period` is optional; when set, items outside it get `OUT_OF_PERIOD` (HELD).

### 2.3 Batch records
```jsonc
// bank_lines.json  (POST_BANK_LINES; also the statement format for RECONCILE)
[{"line_id": "BL-001", "date": "2026-10-03", "description": "RAZ*SWIGGY BLR", "amount_inr": "1240.00",
  "direction": "DEBIT", "currency": "INR", "bank_reference": null}]
// invoices.json  (PAY_INVOICES)
[{"invoice_id": "INV-001", "vendor_name": "Delhivery Limited", "vendor_gstin": null, "vendor_domain": "delhivery.com",
  "invoice_number": "DLV/2026-27/00418", "invoice_date": "2026-11-03", "due_date": "2026-11-30",
  "amount_inr": "48000.00", "currency": "INR", "po_number": "PO-NB-1042", "qty": 12,
  "remit_bank_account": "<raw; fingerprinted at load>", "remit_ifsc": "<IFSC>", "document_ref": null}]
// vendor_messages.json  (any operation; e.g. a bank-change email)
[{"message_id": "MSG-001", "received_at": "2026-11-05T10:12:00+05:30", "from_domain": "delhivery.com",
  "vendor_name": "Delhivery Limited", "kind": "BANK_CHANGE", "new_bank_account": "<raw>", "new_ifsc": "<IFSC>",
  "body": "Please update our bank account for all future payments."}]
```
`amount_inr` is a decimal **string**. `io/` converts it to paise exactly; floats are rejected.

### 2.4 Client files (CSV headers)
- `vendor_master.csv`: `vendor_id,legal_name,aliases,cin,gstin,domain,bank_account,ifsc,default_account,category,last_updated`. `aliases` is `|`-separated; `last_updated` is ISO date and becomes `record_date`.
- `purchase_orders.csv`: `po_number,vendor_id,amount_inr,qty,status,issued_on`
- `payment_history.csv`: `ref,vendor_id,invoice_number,amount_inr,currency,paid_on,provider_ref,bank_reference,status`
- `chart_of_accounts.csv`: `account_code,account_name,category`

## 3. Output formats (`out/<run_id>/`)
| File | Content |
|---|---|
| `results.jsonl` | One `ItemResult` view per item. Field order = `output.fields` (§7). |
| `report.md` | A human report: per-item sourced sentences; totals (₹ posted, ₹ awaiting approval, items flagged by type, open exceptions by severity, oldest open item, crash recoveries, ₹ cost per item) |
| `exceptions.jsonl` | Every exception with its recovery case, if any |
| `ledger.csv` | Every external call: provider, operation, status, units, ₹ (or UNKNOWN), duration |
| `audit.jsonl` | The audit events for this run |
| `journal.csv` / `trial_balance.csv` | Every journal line (entry, account, Dr, Cr, item, decision_hash) and the per-account totals (Σ = 0) |
| `payment_instructions.csv` | One row per approved payment: payment_key, vendor, account last 4, amount, approval, provider ref and status (this is the file a finance team would release to its bank) |
| `summary.json` | `RunSummary`: counts by outcome, open exceptions, elapsed, cost and `cost_status` |

`results.jsonl` line (default fields):
```json
{"run_id":"R-7f3a","item_id":"a1b2…","source_ref":"bank_lines.json#BL-001","operation":"POST_BANK_LINES",
 "outcome":"POSTED","payee":{"vendor_id":"V-0001","legal_name":"Swiggy Limited"},"account":"Staff meals",
 "amount_paise":124000,"currency":"INR","flags":[],"reasons":[],
 "bank_status":null,"exception_ids":[],"payment":null,"cost_inr":0.0,
 "sources":[{"source_id":"s-91c2","provider":"vendor_master","record_ref":"clients/northbay/vendor_master.csv#V-0001",
   "fact":"payee.legal_name","value":"Swiggy Limited","field_or_passage":"legal_name",
   "record_date":"2026-09-30","record_date_basis":"RECORD_FIELD","retrieved_at":"2026-11-22T08:33:03Z","limitations":[]}]}
```

## 4. Models (`paypilot/schemas.py`, owner A)
```python
# ---- enums ----
class Operation(StrEnum):      POST_BANK_LINES, PAY_INVOICES, RECONCILE
class ItemState(StrEnum):      NEW, PARSED, RESOLVED, CHECKED, DECIDED, POSTED, AWAITING_APPROVAL, APPROVED,
                               PAYMENT_IN_PROGRESS, PAID, PAYMENT_UNCERTAIN, FAILED, HELD, CANNOT_VERIFY, REJECTED, STALE
class Outcome(StrEnum):        POSTED, AWAITING_APPROVAL, PAID, HELD, CANNOT_VERIFY, REJECTED, PAYMENT_UNCERTAIN, FAILED, RECONCILED
class Proposal(StrEnum):       CANNOT_VERIFY, HELD, AWAITING_APPROVAL, POSTED, RECONCILED   # what decide() may return
class RunStatus(StrEnum):      RUNNING, COMPLETE, PARTIAL, STOPPED_COST, STOPPED_TIME
class CheckStatus(StrEnum):    PASS, FAIL, SKIPPED, ERROR, UNKNOWN
class DateBasis(StrEnum):      RECORD_FIELD, PUBLICATION_DATE, HTTP_DATE_HEADER, FETCH_TIME, FILE_METADATA, UNKNOWN
class PaymentStatus(StrEnum):  CREATED, SUBMITTED, PROVIDER_CONFIRMED, FAILED, PAYMENT_UNCERTAIN, RETURNED, REVERSED
class AttemptStep(StrEnum):    NONE, CREATE_SENT, REF_SAVED, CONFIRM_SENT, DONE
class SettlementStatus(StrEnum): NOT_AVAILABLE, PENDING, MATCHED, POSSIBLE_MATCH, CONFLICT, UNMATCHED
class Severity(StrEnum):       CRITICAL, HIGH, MEDIUM, LOW
class ExceptionStatus(StrEnum): DETECTED, CLASSIFIED, CONTAINED, INVESTIGATING, ACTION_REQUIRED, RESOLVED,
                               FALSE_POSITIVE, ACCEPTED_RISK, CLOSED_NO_ACTION
class RecoveryStatus(StrEnum): OPEN, ACTION_PREPARED, HUMAN_ACTION_REQUIRED, EXTERNAL_RESPONSE_PENDING,
                               RECOVERED, UNRECOVERABLE, CLOSED_NO_ACTION, FALSE_POSITIVE

# ---- request & inputs ----
class Limits(BaseModel):   max_runtime_seconds: int | None = None; max_cost_inr: float | None = None
class Period(BaseModel):   from_: date = Field(alias="from"); to: date
class Request(BaseModel):  schema_version: str; request_id: str; client_id: str; operation: Operation | None
                           instruction: str; period: Period | None; inputs: dict[str, str]; limits: Limits | None
                           source_path: str; source_sha: str
class BankLine(BaseModel): item_id: str; source_ref: str; line_id: str; date: date; descriptor_raw: str; descriptor_key: str
                           amount_paise: int; currency: str; direction: Literal["DEBIT","CREDIT"]; bank_reference: str | None
                           transaction_fingerprint: str
class Invoice(BaseModel):  item_id: str; source_ref: str; invoice_id: str; vendor_name_raw: str; gstin: str | None
                           domain: str | None; invoice_no_raw: str | None; invoice_no_canonical: str | None
                           invoice_date: date | None; due_date: date | None; amount_paise: int | None; currency: str
                           po_number: str | None; qty: float | None; account_fingerprint: str | None
                           account_last4: str | None; ifsc: str | None; document_ref: str | None; transaction_fingerprint: str
class VendorMessage(BaseModel): item_id: str; source_ref: str; message_id: str; received_at: datetime; from_domain: str | None
                           vendor_name_raw: str; kind: Literal["BANK_CHANGE","OTHER"]; new_account_fingerprint: str | None
                           new_account_last4: str | None; new_ifsc: str | None
WorkItem = BankLine | Invoice | VendorMessage
class ParseFailure(BaseModel): item_id: str; source_ref: str; errors: list[str]

# ---- client data ----
class Vendor(BaseModel):   vendor_id: str; legal_name: str; name_key: str; aliases: list[str]; cin: str | None
                           gstin: str | None; domain: str | None; account_fingerprint: str | None; account_last4: str | None
                           ifsc: str | None; default_account: str | None; category: str | None
                           record_date: date | None; record_ref: str
class PurchaseOrder(BaseModel): po_number: str; vendor_id: str; amount_paise: int; qty: float | None; status: str
                           record_date: date | None; record_ref: str
class HistoryRecord(BaseModel): ref: str; vendor_id: str | None; invoice_no_canonical: str | None; amount_paise: int
                           currency: str; paid_on: date; provider_ref: str | None; bank_reference: str | None
                           status: str; record_ref: str
class ClientData(BaseModel): client_id: str; vendors: list[Vendor]; pos: list[PurchaseOrder] | None
                           history: list[HistoryRecord] | None; accounts: dict[str, str] | None   # category → account name
                           statement: list[BankLine] | None; data_quality: list[str]   # e.g. duplicate GSTIN in master

# ---- evidence ----
class Source(BaseModel):   source_id: str; provider: str; record_ref: str; fact: str; value: str; field_or_passage: str
                           record_date: date | None; record_date_basis: DateBasis; retrieved_at: datetime
                           limitations: list[str] = []
# provider ∈ vendor_master | purchase_orders | payment_history | bank_statement | chart_of_accounts | client_policy |
#            batch_input | razorpay_ifsc | crustdata_identify | company_website_claim | stripe | fake_provider

# ---- logic outputs (lanes B, C) ----
class Resolution(BaseModel): status: Literal["RESOLVED_VENDOR_MASTER","RESOLVED_EXACT_IDENTIFIER","FUZZY_CANDIDATE",
                             "LOOKALIKE","TIE","PARENT_BRAND_MISMATCH","WEBSITE_IDENTITY_CLAIM_FOUND","UNRESOLVED","SOURCE_UNAVAILABLE"]
                           vendor_id: str | None; legal_name: str | None
                           method: Literal["gstin","cin","account_fingerprint","exact","alias","fuzzy","website","none"]
                           score: float | None; candidates: list[tuple[str, float]]; flags: list[str]; reasons: list[str]
                           evidence: list[Source]
class CheckResult(BaseModel): check_id: str; status: CheckStatus; flags: list[str]; reasons: list[str]; missing: list[str]
                           details: dict[str, Any]; evidence: list[Source]
                           # bank: details = {ifsc_valid: PASS|FAIL|UNKNOWN, account_matches_master: PASS|FAIL|UNKNOWN,
                           #                  ownership_verified: "NOT_VERIFIED", ifsc_bank, ifsc_branch}
class Reason(BaseModel):   code: str; text: str; rule_id: str | None; evidence_refs: list[str]
class Facts(BaseModel):    operation: Operation; kind: str; vendor_id: str | None; account_fingerprint: str | None
                           amount_paise: int | None; currency: str | None; date: date | None
                           invoice_no_canonical: str | None; account: str | None; flags: list[str]   # sorted
class Decision(BaseModel): item_id: str; proposal: Proposal; flags: list[str]; reasons: list[Reason]; missing: list[str]
                           evidence_refs: list[str]; rule_ids: list[str]; overridable: list[str]; facts: Facts
                           payee_legal_name: str | None; account: str | None; bank_status: dict[str, str] | None
                           decision_hash: str   # sha256(canonical_json(facts) | policy.decision_hash_input)
class ExceptionDraft(BaseModel): type: str; severity: Severity; blocking: bool; summary: str; details: str
                           recommended_action: str; evidence_refs: list[str]; related_refs: list[str]
                           origin_key: str; item_id: str | None; payment_id: str | None; open_recovery_case: bool
class ReconciliationResult(BaseModel): subject_ref: str; status: SettlementStatus; instruction_ref: str | None
                           provider_ref: str | None; statement_refs: list[str]; differences: dict[str, Any]
                           flags: list[str]; evidence: list[Source]

# ---- runtime records (lane A) ----
class Approval(BaseModel): approval_id: str; item_id: str; decision_hash: str
                           status: Literal["PENDING","APPROVED","REJECTED","SUPERSEDED"]; reviewer: str | None
                           reason: str | None; overrides: list[str]; requested_at: datetime; decided_at: datetime | None
class Payment(BaseModel):  payment_id: str; item_id: str; approval_id: str; payment_key: str; provider: str
                           amount_paise: int; currency: str; status: PaymentStatus; settlement_status: SettlementStatus
                           current_attempt: int; created_at: datetime; updated_at: datetime
class PaymentAttempt(BaseModel): attempt_key: str; payment_id: str; attempt_no: int; provider_ref: str | None
                           step: AttemptStep; outcome: Literal["PENDING","SUCCEEDED","DECLINED","UNKNOWN"]
                           last_error: str | None; created_at: datetime; updated_at: datetime
class ExceptionRecord(ExceptionDraft): exception_id: str; run_id: str; status: ExceptionStatus; assigned_to: str | None
                           resolution_code: str | None; resolution_note: str | None; resolved_by: str | None
                           resolved_at: datetime | None; created_at: datetime; updated_at: datetime
class RecoveryCase(BaseModel): recovery_id: str; exception_id: str; payment_id: str; reason: str; requested_action: str
                           status: RecoveryStatus; external_ref: str | None; evidence_refs: list[str]
                           recovered_amount_paise: int | None
class JournalLine(BaseModel): line_no: int; account: str; debit_paise: int; credit_paise: int
class JournalEntry(BaseModel): entry_id: str; item_id: str
                           kind: Literal["BANK_LINE_POSTING","INVOICE_ACCRUAL","INVOICE_PAYMENT","REVERSAL"]
                           vendor_id: str | None; decision_hash: str; approval_id: str | None; attempt_key: str | None
                           currency: str; memo: str; posted_at: datetime; lines: list[JournalLine]   # Σ debit == Σ credit (validator)
class AuditVerifyReport(BaseModel): run_id: str; ok: bool; items_checked: int; decisions_replayed: int
                           discrepancies: list[dict[str, Any]]; trial_balance: dict[str, int]
class AuditEvent(BaseModel): event_id: int; run_id: str; session_id: str; entity_type: str; entity_id: str
                           previous_state: str | None; new_state: str | None; actor: str; reason: str | None
                           metadata: dict[str, Any]; ts: datetime
class LedgerEntry(BaseModel): call_key: str; run_id: str; session_id: str; item_id: str | None; stage: str
                           provider: str; operation: str; unit_type: Literal["credits","tokens_in","tokens_out","call"]
                           units: float; estimated_inr: float | None; actual_inr: float | None
                           status: Literal["RESERVED","CONFIRMED","FAILED","CACHE_HIT","UNCONFIRMED"]
                           http_status: int | None; duration_ms: int | None; request_hash: str; attempt: int; ts: datetime
class LedgerSummary(BaseModel): run_id: str; total_inr: float | None; cost_status: Literal["KNOWN","PARTIAL","UNKNOWN"]
                           unconfirmed_inr: float; by_provider: dict[str, float | None]; calls: int; cache_hits: int
                           items: int; finished_items: int; inr_per_item: float | None; worst_item_inr: float | None
                           limit_inr: float; zero_cost_items_pct: float
class ItemResult(BaseModel): run_id: str; item: WorkItem | ParseFailure; state: ItemState; outcome: Outcome | None
                           decision: Decision | None; approval: Approval | None; payment: Payment | None
                           attempts: list[PaymentAttempt]; journal: list[JournalEntry]; exceptions: list[ExceptionRecord]; evidence: list[Source]
                           cost_inr: float | None
class RunSummary(BaseModel): run_id: str; request_id: str; client_id: str; operation: Operation | None; status: RunStatus
                           counts: dict[str, int]; open_exceptions: dict[str, int]; sessions: int; recovered_from_crash: bool
                           elapsed_ms: int; cost: LedgerSummary
```

**Outcome vs proposal:** `decide()` returns a `Proposal`. The runtime turns it into the item's `Outcome` after the matching side effect: POSTED after the posting transaction, PAID after provider confirmation. `REJECTED` comes only from a human. `PARTIAL` and `STOPPED_*` are **run** statuses, never item outcomes.

## 5. Identity formulas (`core/ids.py`, A)
| ID | Formula |
|---|---|
| `run_id` | `R-` + sha256(client_id \| request_sha)[:8] (same request file → same run) |
| `item_id` | sha256(client_id \| request_sha \| input_name \| row_no)[:20] |
| `transaction_fingerprint` | With a bank reference/UTR: sha256("ref" \| bank_reference). Else bank line: sha256(descriptor_key \| amount_paise \| currency \| date \| direction). Invoice: sha256(vendor_name_key \| invoice_no_canonical). A signal, not proof, unless UTR-based |
| `payment_key` (logical) | `pp_` + sha256(client_id \| vendor_id \| invoice_no_canonical \| amount_paise \| currency)[:32]. **Immutable.** A PAY item without an invoice number is CANNOT_VERIFY (`MISSING_FIELD`) |
| `attempt_key` (provider) | `{payment_key}:a{n}`; Stripe keys `{attempt_key}:create` / `{attempt_key}:confirm`. n+1 only after a provider-confirmed DECLINED / RETURNED / REVERSED **and** a new approval |
| `decision_hash` | sha256(canonical_json(Facts) \| policy.decision_hash_input). The input covers only decision-relevant config (rules, thresholds, mappings, tolerances) + data_hash. **Never timestamps, source ids or output/limit/logging config** |
| `approval_id` | sha256(item_id \| decision_hash)[:16] |
| `source_id` | sha256(provider \| record_ref \| fact \| field_or_passage)[:16] (retrieval time excluded) |
| `exception_id` | sha256(item_id \| payment_id \| type \| origin_key)[:16] |
| `call_key` | sha256(run_id \| item_id \| stage \| provider \| operation \| request_hash \| attempt) |

## 6. Database (`db/schema.sql`, owner A, C reviews)
```sql
PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;
CREATE TABLE runs (run_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, request_id TEXT NOT NULL, request_sha TEXT NOT NULL,
  operation TEXT, config_hash TEXT NOT NULL, policy_hash TEXT NOT NULL, data_hash TEXT NOT NULL,
  policy_snapshot_json TEXT NOT NULL,   -- exact merged config + policy used; audit verify replays decisions from it
  status TEXT NOT NULL,
  started_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(client_id, request_sha));
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs, command TEXT NOT NULL,
  pid INTEGER, started_at TEXT NOT NULL, ended_at TEXT, exit_code INTEGER);
CREATE TABLE items (item_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs,
  kind TEXT NOT NULL, input_name TEXT NOT NULL, row_no INTEGER NOT NULL, source_ref TEXT NOT NULL, operation TEXT NOT NULL,
  state TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0, outcome TEXT, payload_json TEXT, parse_errors_json TEXT NOT NULL DEFAULT '[]',
  transaction_fingerprint TEXT, invoice_no_canonical TEXT, descriptor_key TEXT, vendor_id TEXT, amount_paise INTEGER,
  currency TEXT, item_date TEXT, resolution_json TEXT, checks_json TEXT, decision_json TEXT, decision_hash TEXT,
  updated_at TEXT NOT NULL, UNIQUE(run_id, input_name, row_no));
CREATE INDEX ix_items_state ON items(client_id, state);
CREATE INDEX ix_items_inv   ON items(client_id, vendor_id, invoice_no_canonical);
CREATE INDEX ix_items_txn   ON items(client_id, vendor_id, amount_paise, item_date);
CREATE INDEX ix_items_desc  ON items(client_id, descriptor_key, amount_paise, item_date);
CREATE INDEX ix_items_fp    ON items(client_id, transaction_fingerprint);
CREATE TABLE sources (source_id TEXT PRIMARY KEY, provider TEXT NOT NULL, record_ref TEXT NOT NULL, fact TEXT NOT NULL,
  value TEXT NOT NULL, field_or_passage TEXT NOT NULL, record_date TEXT, record_date_basis TEXT NOT NULL,
  retrieved_at TEXT NOT NULL, limitations_json TEXT NOT NULL DEFAULT '[]');
CREATE TABLE item_evidence (item_id TEXT NOT NULL REFERENCES items, decision_hash TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES sources, check_id TEXT NOT NULL DEFAULT '', rule_id TEXT, flag TEXT,
  PRIMARY KEY(item_id, decision_hash, source_id, check_id));
CREATE TABLE audit_events (event_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, session_id TEXT NOT NULL,
  entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, previous_state TEXT, new_state TEXT, actor TEXT NOT NULL,
  reason TEXT, metadata_json TEXT NOT NULL, ts TEXT NOT NULL);
CREATE INDEX ix_audit_entity ON audit_events(entity_type, entity_id, event_id);
CREATE TABLE journal_entries (entry_id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items,
  kind TEXT NOT NULL CHECK(kind IN('BANK_LINE_POSTING','INVOICE_ACCRUAL','INVOICE_PAYMENT','REVERSAL')),
  vendor_id TEXT, decision_hash TEXT NOT NULL, approval_id TEXT, attempt_key TEXT, currency TEXT NOT NULL,
  memo TEXT NOT NULL, posted_at TEXT NOT NULL, UNIQUE(item_id, kind));
CREATE TABLE journal_lines (entry_id TEXT NOT NULL REFERENCES journal_entries, line_no INTEGER NOT NULL,
  account TEXT NOT NULL, debit_paise INTEGER NOT NULL DEFAULT 0 CHECK(debit_paise >= 0),
  credit_paise INTEGER NOT NULL DEFAULT 0 CHECK(credit_paise >= 0), PRIMARY KEY(entry_id, line_no));
-- balance (sum of debits = sum of credits per entry) is enforced in the same txn by core and re-checked by `audit verify`
CREATE TABLE approvals (approval_id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items, decision_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('PENDING','APPROVED','REJECTED','SUPERSEDED')), reviewer TEXT, reason TEXT,
  overrides_json TEXT NOT NULL DEFAULT '[]', requested_at TEXT NOT NULL, decided_at TEXT, UNIQUE(item_id, decision_hash));
CREATE TABLE payments (payment_id TEXT PRIMARY KEY, item_id TEXT NOT NULL UNIQUE REFERENCES items,
  approval_id TEXT NOT NULL REFERENCES approvals, payment_key TEXT NOT NULL UNIQUE, vendor_id TEXT NOT NULL,
  provider TEXT NOT NULL, amount_paise INTEGER NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL,
  settlement_status TEXT NOT NULL DEFAULT 'NOT_AVAILABLE', current_attempt INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE payment_attempts (attempt_key TEXT PRIMARY KEY, payment_id TEXT NOT NULL REFERENCES payments,
  attempt_no INTEGER NOT NULL, provider_ref TEXT UNIQUE, step TEXT NOT NULL DEFAULT 'NONE', outcome TEXT NOT NULL DEFAULT 'PENDING',
  last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(payment_id, attempt_no));
CREATE INDEX ix_attempts_open ON payment_attempts(outcome) WHERE outcome IN('PENDING','UNKNOWN');
CREATE TABLE exceptions (exception_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, item_id TEXT REFERENCES items,
  payment_id TEXT REFERENCES payments, vendor_id TEXT, type TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL,
  blocking INTEGER NOT NULL, summary TEXT NOT NULL, details TEXT NOT NULL, recommended_action TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL, related_refs_json TEXT NOT NULL, origin_key TEXT NOT NULL, assigned_to TEXT,
  resolution_code TEXT, resolution_note TEXT, resolved_by TEXT, resolved_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(item_id, payment_id, type, origin_key));
CREATE INDEX ix_exc_open   ON exceptions(run_id, status, severity);
CREATE INDEX ix_exc_vendor ON exceptions(vendor_id, severity, status);   -- containment check
CREATE TABLE recovery_cases (recovery_id TEXT PRIMARY KEY, exception_id TEXT NOT NULL UNIQUE REFERENCES exceptions,
  payment_id TEXT NOT NULL REFERENCES payments, reason TEXT NOT NULL, requested_action TEXT NOT NULL, status TEXT NOT NULL,
  external_ref TEXT, evidence_refs_json TEXT NOT NULL DEFAULT '[]', recovered_amount_paise INTEGER,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE cost_ledger (call_key TEXT PRIMARY KEY, run_id TEXT NOT NULL, session_id TEXT NOT NULL, item_id TEXT,
  stage TEXT NOT NULL, provider TEXT NOT NULL, operation TEXT NOT NULL, unit_type TEXT NOT NULL, units REAL NOT NULL DEFAULT 0,
  estimated_inr REAL, actual_inr REAL, status TEXT NOT NULL CHECK(status IN('RESERVED','CONFIRMED','FAILED','CACHE_HIT','UNCONFIRMED')),
  http_status INTEGER, duration_ms INTEGER, request_hash TEXT NOT NULL, attempt INTEGER NOT NULL, ts TEXT NOT NULL);
CREATE INDEX ix_ledger_run ON cost_ledger(run_id, provider);
CREATE TABLE http_cache (provider TEXT NOT NULL, operation TEXT NOT NULL, request_hash TEXT NOT NULL, status INTEGER NOT NULL,
  headers_json TEXT, body TEXT NOT NULL, retrieved_at TEXT NOT NULL, expires_at TEXT, PRIMARY KEY(provider, operation, request_hash));
CREATE TABLE domain_blocklist (domain TEXT PRIMARY KEY, reason TEXT NOT NULL, added_at TEXT NOT NULL);
```

## 7. Configuration
**Two layers**, deep-merged and validated strictly:
- `config.yaml` holds product defaults.
- `clients/<client_id>/policy.yaml` holds client rules and may override any key **in the policy sections only**.

Both are re-read and hashed on every `run`/`resume`/`approve`. `policy_hash` covers only the sections marked `[policy]`; those, plus `data_hash`, feed `decision_hash`.

```yaml
# ===== owner A: runtime =====
limits: {max_runtime_seconds: 600, max_cost_inr: 25.0, max_cost_inr_per_item: 5.0, call_timeout_s: 20}  # request limits: the stricter wins
payments: {provider: fake, currency: INR, test_payment_method: pm_card_visa, idem_window_h: 23,   # stripe = optional extension
           refuse_live_keys: true, approve_executes_inline: true}
approvals: {approvers: [], lock_wait_s: 10}               # [] = any name (declared identity); policy.yaml sets the allowlist
costs:
  crustdata: {inr_per_credit: null, estimates: {identify: 0.2, web_fetch: 1}}
  infra_inr_per_item: null
  unknown_reserve_inr: 1.0                                # budget reservation when the price is unknown; reported cost stays UNKNOWN
output:
  dir: out/{run_id}/
  fields: [run_id, item_id, source_ref, operation, outcome, payee, account, amount_paise, currency,
           flags, reasons, bank_status, exception_ids, payment, cost_inr, sources]     # a new field = one line here
logging: {dir: var/logs, mask_accounts: true}
storage: {db_path: var/paypilot.db, lock_path: var/paypilot.lock, fake_provider_db: var/fake_provider.db}
# ===== owner B: identity & evidence =====
sources:
  ifsc: {enabled: true, base_url: https://ifsc.razorpay.com, retries: 2, backoff_base_s: 0.5, cache_ttl_h: 720}   # optional check; disabled → ifsc_valid = NOT_CHECKED
  crustdata: {base_url: https://api.crustdata.com, api_version: "2025-11-01", retries: 3, backoff_base_s: 1.0,
              rate_per_min: {identify: 30, web_fetch: 10}, identify_by: domain, cache_ttl_h: 168, enabled: true}
  webfetch: {paths: ["/", "/contact-us", "/about-us"], skip_domains: [zaubacorp.com, filesure.in],
             cf_markers: ["Just a moment", "cf-chl"], passage_window: 200, cache_ttl_h: 24, enabled: true}
normalize:                                                # [policy]
  processor_prefixes: ['^RAZ\*', '^PAYTM\*', '^NEFT[-/ ]', '^IMPS[-/ ]', '^UPI[-/ ]', '^POS ', '^ACH ', '^BIL/']
  city_tokens: [BLR, BANGALORE, BENGALURU, MUM, MUMBAI, DEL, DELHI, GGN, GURGAON, CHN, CHENNAI, HYD, PUNE, KOL]
  legal_suffixes: [PVT, PRIVATE, LTD, LIMITED, LLP, INC, SARL, GMBH, CO]
  abbreviations: {TRD: TRADERS, LOGS: LOGISTICS, SVCS: SERVICES}
  invoice_noise_tokens: [INV, INVOICE, BILL, NO, NUM]
match: {fuzzy_min_score: 92, fuzzy_min_margin: 5, lookalike_floor: 75, aliases_extra: {}}   # [policy]
# ===== owner C: checks, decisions, exceptions =====
required_fields:                                          # [policy]
  BankLine: {POST_BANK_LINES: [date, descriptor_raw, amount_paise, direction]}
  Invoice:  {PAY_INVOICES: [vendor_name_raw, invoice_no_raw, invoice_date, amount_paise]}
checks_order: [required, period, duplicate, po, account, bank]   # [policy]
period: {enforce: true}                                   # [policy]
posting: {allowed_directions: [DEBIT], allowed_currencies: [INR]}   # [policy]
duplicate: {window_days: 3, compare_against: [this_run, db_history, payment_history]}     # [policy]
po: {required_for: [PAY_INVOICES], tolerance_pct: 1.0, tolerance_abs_paise: 10000, qty_tolerance_pct: 0}   # [policy]
bank: {verify_master_ifsc_before_pay: true}               # [policy]
accounts: {by_category: {}}                               # [policy]; policy.yaml / chart_of_accounts fills it
reconcile: {window_days: 3, amount_tolerance_paise: 0, settlement_pending_days: 2}   # [policy]
decision:                                                 # [policy]; every Flag except PERSISTENCE_FAILURE (fail-closed exit, never decided) must be covered by exactly one rule (validated at load)
  rules:
    - {id: R01, flags_any: [PAYEE_UNRESOLVED], proposal: CANNOT_VERIFY, reason: "No source identifies the payee for '{descriptor}'", missing: "vendor-master record or a GSTIN/domain"}
    - {id: R02, flags_any: [PAYEE_TIE, LOOKALIKE], proposal: CANNOT_VERIFY, reason: "Payee identity ambiguous: {candidates}"}
    - {id: R03, flags_any: [PARENT_BRAND], proposal: CANNOT_VERIFY, reason: "Source resolves to {found_entity}, not billed entity {billed_entity}"}
    - {id: R04, flags_any: [MISSING_FIELD, PARSE_FAILED, UNSUPPORTED_REQUEST], proposal: CANNOT_VERIFY, reason: "Required input missing or unreadable: {fields}", missing: "{fields}"}
    - {id: R10, flags_any: [DUPLICATE_INVOICE, ALREADY_PAID, DUPLICATE_PAYMENT], proposal: HELD, reason: "Already seen/paid as {dup_ref}"}
    - {id: R11, flags_any: [DUPLICATE_TRANSACTION], proposal: HELD, reason: "Possible duplicate of {dup_ref}", overridable: true}
    - {id: R12, flags_any: [BANK_CHANGED, BANK_CHANGE_REQUEST, IFSC_INVALID], proposal: HELD, reason: "Bank details differ or are invalid ({detail}); vendor master must be updated through the client's verification process"}
    - {id: R13, flags_any: [PO_MISSING, PO_NOT_FOUND, PO_VENDOR_MISMATCH, PO_AMOUNT_MISMATCH, PO_QTY_MISMATCH, PO_EXHAUSTED], proposal: HELD, reason: "PO check: {po_detail}", overridable: true}
    - {id: R14, flags_any: [FUZZY_MATCH, ACCOUNT_UNMAPPED], proposal: HELD, reason: "{detail}; confirm", overridable: true}
    - {id: R15, flags_any: [VENDOR_ONBOARDING_REQUIRED, WEBSITE_IDENTITY_CLAIM_ONLY, VENDOR_MASTER_CONFLICT], proposal: HELD, reason: "{detail}; vendor onboarding / master clean-up required"}
    - {id: R16, flags_any: [OUT_OF_PERIOD, CREDIT_LINE, UNSUPPORTED_CURRENCY, NON_POSITIVE_AMOUNT], proposal: HELD, reason: "{detail}", overridable: true}
    - {id: R17, flags_any: [SOURCE_UNAVAILABLE, CREDITS_EXHAUSTED, PROVIDER_TIMEOUT, COST_LIMIT, TIME_LIMIT], proposal: HELD, reason: "{provider} unavailable or run limit reached; not guessing"}
    - {id: R18, flags_any: [PAYMENT_UNCERTAIN, WRONG_PAYEE_SUSPECTED, AMOUNT_MISMATCH, SETTLEMENT_MISMATCH, DUPLICATE_DEBIT, RETURNED_PAYMENT, REVERSED_PAYMENT, STALE_APPROVAL, VENDOR_CONTAINED], proposal: HELD, reason: "Payment exception: {detail}"}
exceptions: {map: {}}                                     # [policy]; flag → {type, severity_pre, severity_post, blocking, recovery_case}; values in FAILURE_MODES.md §2
```
**Rehearsed live changes:**
- Lower the cost limit: `limits.max_cost_inr` (1 line).
- New output field that is already computed: add it to `output.fields` (1 line). New computed field: one `FIELD_GETTERS` entry in `reports/writer.py` + the config line.
- New rule: append to `decision.rules`. A new flag = 1 line in `flags.py` + an emit in one check + an `exceptions.map` row.
- Client tolerance: edit `clients/northbay/policy.yaml`.

## 8. Ports and stage context (`paypilot/context.py`, A)
```python
@dataclass(frozen=True)
class StageContext:
    run_id: str; session_id: str; client_id: str; operation: Operation | None; period: Period | None
    cfg: RunConfig; policy: PolicyConfig          # PolicyConfig carries decision_hash_input (policy_hash + data_hash)
    data: ClientData
    ext: ExtClient; history: HistoryView; clock: Callable[[], datetime]

class LedgerPort(Protocol):      # A
    def reserve(self, e: LedgerEntry) -> str: ...
    def confirm(self, call_key: str, units: float, actual_inr: float | None, http_status: int | None, duration_ms: int) -> None: ...
    def fail(self, call_key: str, http_status: int | None, error: str) -> None: ...
    def cache_hit(self, e: LedgerEntry) -> None: ...
class CachePort(Protocol):       # A
    def get(self, provider: str, operation: str, request_hash: str) -> CachedResponse | None: ...
    def put(self, provider: str, operation: str, request_hash: str, resp: CachedResponse, ttl_h: int | None) -> None: ...
class BudgetPort(Protocol):      # A; raises BudgetExceeded / TimeExceeded
    def reserve(self, estimated_inr: float | None) -> None: ...
    def check_time(self) -> None: ...
class ExtClient(Protocol):       # B implements in sources/http.py over Ledger/Cache/Budget ports
    def get_json(self, provider, operation, url, *, params=None, item_id, stage) -> ExtResponse: ...
    def post_json(self, provider, operation, url, body, *, item_id, stage) -> ExtResponse: ...
    def fetch_page(self, url, *, item_id, stage) -> ExtResponse: ...
# ExtResponse(ok, status, data, headers, error, cache_hit, retrieved_at, request_hash). It NEVER raises for network or budget problems:
# error ∈ TIMEOUT | RATE_LIMITED | CREDITS_EXHAUSTED | AUTH | FORBIDDEN | NOT_FOUND | SERVER | BAD_BODY | BUDGET | BLOCKED
class HistoryView(Protocol):     # A implements over SQLite + payment_history; read-only
    def prior_by_invoice(self, vendor_id, invoice_no_canonical, *, before_item) -> list[PriorRecord]: ...
    def prior_by_txn(self, key, amount_paise, currency, date_from, date_to, *, before_item) -> list[PriorRecord]: ...
    def prior_by_fingerprint(self, fingerprint, *, before_item) -> list[PriorRecord]: ...
    def payments_for_invoice(self, vendor_id, invoice_no_canonical) -> list[Payment]: ...
    def po_billed_paise(self, po_number, *, before_item) -> int: ...
    def open_critical_for_vendor(self, vendor_id) -> list[str]: ...                  # containment
# before_item: order = (date, input_name, row_no). The first occurrence proceeds; later ones are held.
class PaymentProvider(Protocol): # A: payments/fake.py (default, P0); payments/stripe_provider.py (optional extension)
    def create(self, key: str, amount_paise: int, currency: str, metadata: dict) -> ProviderPayment: ...
    def confirm(self, provider_ref: str, key: str) -> ProviderPayment: ...
    def retrieve(self, provider_ref: str) -> ProviderPayment: ...
    def find_by_attempt_key(self, attempt_key: str) -> ProviderPayment | None: ...   # metadata search
    def refund(self, provider_ref: str, key: str) -> ProviderPayment: ...           # only if demonstrated + labelled
# ProviderPayment(ref, status: REQUIRES_CONFIRMATION|PROCESSING|SUCCEEDED|DECLINED|CANCELED|RETURNED, amount_paise, currency, metadata, raw_ref)
```

## 9. Function boundaries between lanes
```python
# A: io + runtime
io.request.load_request(path: Path) -> Request
io.client_data.load_client_data(client_id: str, cfg: RunConfig) -> tuple[ClientData, str]      # fingerprints at load; data_hash
io.batch.load_items(request: Request, ctx: StageContext) -> list[WorkItem | ParseFailure]
core.pipeline.drive(run_id: str, ctx: StageContext, provider: PaymentProvider) -> RunSummary
approvals.service.approve(item_id, reviewer, reason, overrides) -> Approval          # binds hash; enqueues; executes inline
payments.executor.execute(payment: Payment, provider, ctx) -> Payment
payments.reconcile.reconcile_open(provider, ctx) -> list[Payment]                     # first thing on every command
reports.writer.write_outputs(run_id, ctx) -> list[Path]
core.journal.post_entry(conn, entry: JournalEntry) -> None                            # same txn as the state change; asserts balance
audit.verify.verify_run(run_id: str, cfg: RunConfig) -> AuditVerifyReport             # A; re-calls C's decide() on stored facts + snapshot
# B
resolve.normalize.descriptor_key(s, policy) -> str; name_key(s, policy) -> str; invoice_canonical(s, policy) -> str
resolve.matcher.resolve_vendor(item: WorkItem, ctx: StageContext) -> Resolution
sources.ifsc.lookup(ctx, ifsc: str, item_id: str) -> IfscResult                       # VALID | NOT_FOUND | UNAVAILABLE + Source
sources.crustdata.identify_by_domain(ctx, domain: str, item_id: str) -> IdentifyResult # P1
sources.webfetch.fetch_identity_claim(ctx, domain: str, item_id: str) -> IdentityClaim | None  # P1
# C
checks.runner.run_checks(item: WorkItem, resolution: Resolution, ctx: StageContext) -> list[CheckResult]
decide.engine.decide(item: WorkItem, resolution: Resolution, checks: list[CheckResult], policy: PolicyConfig) -> Decision
exceptions.classify.classify(item: WorkItem | None, decision: Decision | None, recon: ReconciliationResult | None,
                             payment: Payment | None, policy: PolicyConfig) -> list[ExceptionDraft]   # pure; A upserts
exceptions.lifecycle.can_transition(frm: ExceptionStatus, to: ExceptionStatus, evidence_refs: list[str]) -> bool
exceptions.recovery.prepare_case(exc: ExceptionRecord, payment: Payment) -> RecoveryCase
reconciliation.match.three_way(decision: Decision | None, payment: Payment | None, provider_view: ProviderPayment | None,
                               statement: list[BankLine] | None, history: list[HistoryRecord] | None, policy) -> ReconciliationResult
```

## 10. CLI (`python -m paypilot …`; exit codes 0 ok · 2 invalid input · 3 lock held · 70 persistence failure · 75 stopped at a limit)
| Command | Args | Behaviour |
|---|---|---|
| `run` | `--scenario DIR` or `--request PATH` `[--provider stripe\|fake]` | the same request file is the same run (resume semantics) |
| `resume` | `[--run RUN_ID]` | marks RESERVED ledger rows UNCONFIRMED → reconciles payments → continues |
| `approvals` | `[--run RUN_ID]` | queue: payee + source, amount, account last 4, three bank statuses, flags, reasons, evidence, open exceptions |
| `approve` | `ITEM_ID --by NAME [--reason TEXT] [--override FLAG…]` | takes the lock (waits `lock_wait_s`); binds the current decision_hash; refuses NEVER_OVERRIDABLE; an override needs a reason; executes the payment inline |
| `reject` | `ITEM_ID --by NAME --reason TEXT` | → REJECTED |
| `show` | `ITEM_ID` | decision → rule → flag → check → evidence → source; audit timeline; exceptions; payment and attempts |
| `exceptions` | `[--run RUN_ID] [--open] [--severity S]` | sorted by severity, then age |
| `exception` / `resolve-exception` | `EXC_ID` / `EXC_ID --by NAME --resolution CODE --note TEXT [--evidence SOURCE_ID…]` | RESOLVED / RECOVERED require evidence |
| `report` / `ledger` / `export` | `[--run RUN_ID] [--json] [--out PATH]` | `ledger` shows cost_status KNOWN / PARTIAL / UNKNOWN |
| `audit verify` | `[--run RUN_ID]` | replays the run from the DB: re-derives every decision_hash from stored facts + policy snapshot; checks approval ↔ payment ↔ journal links, one confirmed attempt per payment key, balanced entries and trial balance, unbroken audit chain, every output fact → stored source. Exit 0 = books verified |
| `demo reset` | — | wipes `var/` and `out/` (demo/test only; asks for confirmation) |

`PAYPILOT_TEST_AUTOAPPROVE=1` is honoured only with `--provider fake`. `PAYPILOT_CRASH_AT=<point>` is used by the crash tests (FAILURE_MODES §3).
