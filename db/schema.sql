-- PayPilot schema (INTERFACES §6). Owner: lane A. Applied by core/db.py.
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
-- REVERSAL = the mirror of the item's INVOICE_PAYMENT (Dr Bank – payments clearing / Cr AP – vendor), written in the same txn as payment
--   PROVIDER_CONFIRMED → RETURNED/REVERSED. UNIQUE(item_id, kind) allows one per item: there is no retry after a reversal
CREATE TABLE approvals (approval_id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items, decision_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('PENDING','APPROVED','REJECTED','SUPERSEDED')), reviewer TEXT, reason TEXT,
  overrides_json TEXT NOT NULL DEFAULT '[]', requested_at TEXT NOT NULL, decided_at TEXT,
  approval_seq INTEGER NOT NULL DEFAULT 1, UNIQUE(item_id, decision_hash, approval_seq));
CREATE TABLE payments (payment_id TEXT PRIMARY KEY, item_id TEXT NOT NULL UNIQUE REFERENCES items,
  approval_id TEXT NOT NULL REFERENCES approvals,   -- the approval for attempt 1; each attempt records its own
  payment_key TEXT NOT NULL UNIQUE, vendor_id TEXT NOT NULL,
  provider TEXT NOT NULL, amount_paise INTEGER NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL,
  settlement_status TEXT NOT NULL DEFAULT 'NOT_AVAILABLE', current_attempt INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE payment_attempts (attempt_key TEXT PRIMARY KEY, payment_id TEXT NOT NULL REFERENCES payments,
  attempt_no INTEGER NOT NULL, approval_id TEXT NOT NULL REFERENCES approvals, provider_ref TEXT UNIQUE, step TEXT NOT NULL DEFAULT 'NONE', outcome TEXT NOT NULL DEFAULT 'PENDING',
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
