# PayPilot — Shared Interfaces & Data Contracts

**Purpose:** the shared contract all three builders code against.  
**Status:** provisional internal contract until the organizer kickoff kit is inspected.  
**Rule:** the official kit schema wins. Only `paypilot/kitio/` translates organizer formats into these internal models.

## 1. Contract rules

- Use Python type hints and Pydantic v2 models (or the team's agreed equivalent).
- Money is integer paise internally (`amount_paise`); never use float for calculations.
- Datetimes are timezone-aware; store UTC, display IST.
- IDs are stable and deterministic where they represent logical identity.
- Every result carries `run_id`, `item_id`, outcome, reasons and evidence references.
- Optional file absent → check status `SKIPPED` with reason. Required file absent → input validation error.
- Do not log secrets or full bank-account numbers.
- Do not silently change a shared model. Update this file, tests and `docs/CONTRACT_CHANGES.md` first.

## 2. Provisional input directory

```text
data/runs/<run-name>/
  request.json
  bank_lines.json          # for POST_BANK_LINES
  invoices.json            # for PAY_INVOICES
  vendor_master.csv        # required for vendor resolution
  purchase_orders.csv      # optional, if kit provides it
  payment_history.csv      # optional
  bank_statement.json      # optional
  chart_of_accounts.csv    # optional
```

The organizer kit may use entirely different names/formats. The adapter maps them.

### request.json

```json
{
  "schema_version": "1.0",
  "request_id": "demo-invoice-payment-001",
  "client_id": "client01",
  "operation": "PAY_INVOICES",
  "instruction": "Validate these invoices and prepare approved payments.",
  "input_files": {
    "invoices": "invoices.json",
    "vendor_master": "vendor_master.csv",
    "purchase_orders": "purchase_orders.csv",
    "payment_history": "payment_history.csv",
    "bank_statement": "bank_statement.json"
  },
  "limits": {
    "max_runtime_seconds": 600,
    "max_cost_inr": 25.0
  }
}
```

Allowed internal operations: `POST_BANK_LINES`, `PAY_INVOICES`, `RECONCILE`. Explicit organizer intent takes precedence; ambiguous intent is refused rather than inferred unsafely.

### Bank line

```json
{
  "line_id": "BL-001",
  "date": "2026-10-03",
  "description": "RAZ*SWIGGY BLR",
  "amount_paise": 124000,
  "direction": "DEBIT",
  "currency": "INR",
  "bank_reference": null
}
```

### Invoice

```json
{
  "invoice_id": "INV-001",
  "vendor_name": "Example Logistics Limited",
  "invoice_number": "EL-2026-104",
  "invoice_date": "2026-11-01",
  "due_date": "2026-11-30",
  "amount_paise": 1250000,
  "currency": "INR",
  "po_number": "PO-991",
  "bank_account": "raw input only; fingerprint before persistence",
  "ifsc": "ABCD0123456",
  "document_ref": "fixtures/invoice-001.pdf"
}
```

Do not persist the raw bank account in the normalized model or logs. The example field illustrates the incoming source only; normalize to HMAC/fingerprint + last four.

### Vendor master CSV (provisional columns)

`vendor_id,legal_name,aliases,cin,gstin,domain,bank_account,ifsc,default_account,category,last_updated`

If the master contains bank details, immediately fingerprint/HMAC them and avoid retaining raw account numbers beyond the ingestion boundary.

## 3. Shared internal models

All modules should agree on these concepts before implementation:

| Model | Minimum fields | Owner |
|---|---|---|
| `Request` | request_id, client_id, operation, instruction, input_refs, limits | Integrator |
| `BankLine` | item_id, source_ref, date, descriptor_raw, descriptor_key, amount_paise, currency, direction, bank_reference | Integrator |
| `Invoice` | item_id, source_ref, vendor_name_raw, invoice_no_raw, invoice_no_canonical, invoice_date, amount_paise, currency, PO ref | Integrator |
| `Vendor` | vendor_id, legal_name, aliases, identifiers, domain, account_fingerprint, IFSC, default_account | Resolver |
| `Source` | source_id, provider, record_id/url, field_or_passage, record_date, record_date_basis, retrieved_at, limitations | Resolver |
| `CheckResult` | check_id, status, flags[], details, evidence_refs[] | Checks |
| `Decision` | outcome, flags[], reasons[], missing[], evidence_refs[], decision_hash | Policy |
| `Approval` | item_id, decision_hash, reviewer, status, reason, timestamp | Runtime |
| `PaymentAttempt` | payment_id, item_id, idempotency_key, provider, provider_ref, amount_paise, status | Payments |
| `Exception` | exception_id, item_id, type, severity, status, evidence_refs[], recommended_action, resolution | Exceptions |
| `RecoveryCase` | recovery_id, payment_id, reason, requested_action, status, external_ref, evidence_refs[] | Exceptions |
| `AuditEvent` | event_id, entity_type/id, previous_state, new_state, actor, reason, timestamp, metadata | Runtime |
| `LedgerEntry` | call_key, provider, operation, item_id, status, estimated/actual cost, duration | Runtime |

## 4. Outcomes, flags and check statuses

### Item outcomes
- `POSTED` — local journal/export entry created once.
- `AWAITING_APPROVAL` — eligible payment proposed; no provider side effect yet.
- `PAID` — configured provider action confirmed; provider semantics must be stated.
- `HELD` — known issue blocks the action pending review.
- `CANNOT_VERIFY` — required fact cannot be established; missing evidence is named.
- `REJECTED` — human rejected the proposed action.
- `PAYMENT_UNCERTAIN` — provider may have acted, but result is unknown; reconcile, never blindly retry.
- `FAILED` — known provider failure; retry only if classified safe.
- `PARTIAL` — run stopped at a limit with remaining work resumable.

### Check statuses
`PASS`, `FAIL`, `SKIPPED`, `ERROR`, `UNKNOWN`.

An `ERROR` never counts as `PASS`.

### Initial flags
`MISSING_FIELD`, `PARSE_FAILED`, `PAYEE_UNRESOLVED`, `PAYEE_TIE`, `LOOKALIKE`, `PARENT_BRAND`, `DUPLICATE_INVOICE`, `DUPLICATE_TRANSACTION`, `ALREADY_PAID`, `PO_MISSING`, `PO_NOT_FOUND`, `PO_VENDOR_MISMATCH`, `PO_AMOUNT_MISMATCH`, `PO_QTY_MISMATCH`, `ACCOUNT_UNMAPPED`, `BANK_CHANGED`, `BANK_CHANGE_REQUEST`, `IFSC_INVALID`, `SOURCE_UNAVAILABLE`, `CREDITS_EXHAUSTED`, `COST_LIMIT`, `TIME_LIMIT`, `PROVIDER_TIMEOUT`, `PAYMENT_UNCERTAIN`, `AMOUNT_MISMATCH`, `WRONG_PAYEE_SUSPECTED`, `SETTLEMENT_MISMATCH`, `RETURNED_PAYMENT`, `REVERSED_PAYMENT`.

`NEVER_OVERRIDABLE` should include at least unresolved identity, identity tie/lookalike, bank-change/fraud flags, duplicate invoice, and uncertain provider outcome. Team must explicitly review this list before enabling overrides.

## 5. Exception and recovery contract

```json
{
  "exception_id": "EX-001",
  "run_id": "RUN-001",
  "item_id": "INV-001",
  "payment_id": "PAY-001",
  "type": "DUPLICATE_PAYMENT",
  "severity": "CRITICAL",
  "status": "CONTAINED",
  "summary": "Two provider payment records appear to reference the same invoice.",
  "details": "Matching vendor, invoice identity and amount.",
  "recommended_action": "STOP_RELATED_PAYMENTS_AND_OPEN_RECOVERY_CASE",
  "evidence_refs": ["SRC-PROVIDER-001", "SRC-HISTORY-004"],
  "created_at": "2026-11-22T10:00:00Z",
  "updated_at": "2026-11-22T10:00:00Z",
  "assigned_to": null,
  "resolution_code": null,
  "resolution_note": null,
  "resolved_by": null,
  "resolved_at": null
}
```

Recovery case:

```json
{
  "recovery_id": "REC-001",
  "payment_id": "PAY-001",
  "reason": "WRONG_PAYEE",
  "requested_action": "HUMAN_CONTACT_PROVIDER_AND_REQUEST_RETURN",
  "status": "OPEN",
  "external_ref": null,
  "evidence_refs": ["SRC-PROVIDER-001"],
  "recovered_amount_paise": null
}
```

A case may be closed as `RECOVERED` only with evidence of the returned/credited funds. `REQUESTED`, `ACKNOWLEDGED` or `PROMISED` are not recovery completion.

### Exception statuses
`DETECTED`, `CLASSIFIED`, `CONTAINED`, `INVESTIGATING`, `ACTION_REQUIRED`, `RESOLVED`, `FALSE_POSITIVE`, `CLOSED_NO_ACTION`.

### Severity
`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`.

## 6. Decision precedence

Suggested deterministic precedence:

1. `CANNOT_VERIFY` — required identity/fact cannot be established.
2. `HELD` — known risk, mismatch, duplicate, unavailable source, or unresolved exception.
3. `AWAITING_APPROVAL` — all required checks pass and payment is eligible.
4. `POSTED` — local posting policy passes.
5. `REJECTED` — human explicitly rejects.

Collect all applicable reasons; do not stop at the first flag. Never allow an overridable flag to override a non-overridable flag.

## 7. Provisional output shape

Per-item result:

```json
{
  "run_id": "RUN-001",
  "item_id": "INV-001",
  "operation": "PAY_INVOICES",
  "outcome": "HELD",
  "payee": {
    "vendor_id": "V-001",
    "legal_name": "Example Logistics Limited"
  },
  "amount_paise": 1250000,
  "currency": "INR",
  "flags": ["BANK_CHANGE_REQUEST"],
  "reasons": ["Remittance details differ from the approved vendor master."],
  "checks": {
    "duplicate": "PASS",
    "po": "PASS",
    "ifsc_valid": "PASS",
    "account_matches_master": "FAIL",
    "ownership_verified": "NOT_VERIFIED"
  },
  "exception_ids": ["EX-001"],
  "evidence_refs": ["SRC-001", "SRC-002"],
  "payment_id": null
}
```

Run report:

```json
{
  "run_id": "RUN-001",
  "status": "PARTIAL",
  "counts": {
    "POSTED": 1,
    "PAID": 0,
    "HELD": 2,
    "CANNOT_VERIFY": 1,
    "PAYMENT_UNCERTAIN": 0
  },
  "open_exceptions": 2,
  "elapsed_ms": 1234,
  "cost_inr": null,
  "cost_status": "UNKNOWN",
  "outputs": ["out/RUN-001/results.jsonl", "out/RUN-001/report.md"]
}
```

Organizer output format always wins over this internal shape. Keep a normalized internal result and render through `kitio/`.

## 8. Shared function boundaries

Use one context object rather than circular imports. Proposed signatures:

```python
def resolve_vendor(item: BankLine | Invoice, ctx: StageContext) -> Resolution: ...
def run_checks(item: BankLine | Invoice, resolution: Resolution, ctx: StageContext) -> list[CheckResult]: ...
def decide(item: BankLine | Invoice, resolution: Resolution, checks: list[CheckResult], policy: PolicyConfig) -> Decision: ...
def create_or_update_exceptions(item, decision, ctx: StageContext) -> list[ExceptionRecord]: ...
def reconcile_payment(payment: PaymentAttempt, provider: PaymentProvider, ctx: StageContext) -> ReconciliationResult: ...
def execute_payment(item, approval: Approval, ctx: StageContext) -> PaymentAttempt: ...
```

Pure logic modules must not write to SQLite or call provider APIs directly. Runtime owns persistence and side effects. If team chooses a different signature, update this document and contract tests before parallel implementation.

## 9. Contract-change process

1. Propose change in `docs/CONTRACT_CHANGES.md`.
2. Explain why it is needed and which modules/tests are affected.
3. Integrator approves.
4. Update Pydantic models and contract tests.
5. Merge contract change before dependent work.
