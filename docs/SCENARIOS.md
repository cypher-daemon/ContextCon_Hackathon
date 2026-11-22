# PayPilot: Demo World & Scenarios

> We own the test world. These scenarios are the acceptance suite, the screening demo, and the video script's data.
> Each scenario lives in `scenarios/<id>/` with `request.json`, batch files and `expected.jsonl` (format in INTERFACES §2–3), plus `expected_db.json`.
> `tests/integration/test_scenarios.py` runs every scenario and asserts on **three things, not just return values**:
> 1. **Output files:** `results.jsonl` == `expected.jsonl`, ignoring timestamps; `journal.csv` / `payment_instructions.csv` row counts.
> 2. **Database:** `expected_db.json` gives row counts and key fields per table (items by state, journal entries and lines balanced, approvals, payments + attempts, exceptions by type and severity, recovery cases, ledger rows by provider).
> 3. **Audit log:** the expected sequence of `audit_events` transitions per item, and `audit verify` exits 0.
>
> SC-01…SC-12 need no provider credentials, real payment data or LLM: payments go through the durable **FakeProvider** (our own simulation, labelled as such). SC-13 runs the same payment through **RazorpayX Payouts test mode** (`payments.provider: razorpayx_test`) and is opt-in (`pytest -m razorpayx`, needs test credentials).
> All dates are after **1 Sep 2026**. Today's demo date is 22 Nov 2026.

## 1. Data honesty rules (ethics)
- **The client is fictional:** **NorthBay Logistics Pvt Ltd** (`client_id: northbay`), a mid-size Bengaluru-based 3PL. The name is invented for the demo and labelled "fictional" in the README and video.
- **Vendors are real companies**, as the brief requires. Their vendor-master rows are *the fictional client's records*. We **never invent identifiers for real companies**:
  - CIN and GSTIN stay empty unless we have a citable source. The one exception is Delhivery's CIN `L63090DL2011PLC221234`, which we verified from delhivery.com's footer (HACKATHON_INFORMATION Part C).
  - Bank account numbers are **fictional test numbers**, marked as such.
  - IFSC codes are real public branch codes that lane B **verifies live** with Razorpay IFSC before committing them (task B4). The "invalid IFSC" case uses a well-formed code that B confirms returns 404.
- No personal data: no contact names, phones or emails in any fixture.

## 2. Client records (`clients/northbay/`)
**`vendor_master.csv`** (`last_updated` 2026-09-30 for all rows unless noted)

| vendor_id | legal_name | aliases | cin | domain | bank_account (fictional) | ifsc | default_account | category |
|---|---|---|---|---|---|---|---|---|
| V-0001 | Swiggy Limited | SWIGGY | – | swiggy.com | 000000000001 | ‹IFSC-A› | Staff meals | food |
| V-0002 | Amazon Web Services EMEA SARL | AWS EMEA | – | aws.amazon.com | 000000000002 | ‹IFSC-A› | Cloud hosting | cloud |
| V-0003 | Delhivery Limited | DELHIVERY | L63090DL2011PLC221234 | delhivery.com | 000000000003 | ‹IFSC-B› | Freight - carriers | freight |
| V-0004 | Blue Dart Express Limited | BLUE DART\|BLUEDART | – | bluedart.com | 000000000004 | ‹IFSC-B› | Freight - carriers | freight |
| V-0005 | VRL Logistics Limited | VRL | – | vrlgroup.in | 000000000005 | ‹IFSC-C› | Freight - carriers | freight |
| V-0006 | Indian Oil Corporation Limited | INDIAN OIL\|IOCL | – | iocl.com | 000000000006 | ‹IFSC-C› | Fuel | fuel |
| V-0101 | Om Sai Roadways Private Limited | OM SAI ROADWAYS | – | – | 000000000101 | ‹IFSC-A› | Freight - carriers | freight |
| V-0102 | Om Sai Roadways LLP | OM SAI ROADWAYS | – | – | 000000000102 | ‹IFSC-A› | Freight - carriers | freight |

> There is **no** Sharma Traders vendor, so the official `PAYTM*SHARMA TRD` line is genuinely unresolvable (expanding `TRD→TRADERS` still finds nothing). The deliberate **tie** is the two "Om Sai Roadways" entities, whose name keys are identical once legal suffixes are dropped.

**`purchase_orders.csv`**: PO-NB-1042 (Delhivery, ₹48,000.00, qty 12, open) · PO-NB-1043 (VRL, ₹60,000.00, qty 10, open) · PO-NB-1044 (Indian Oil, ₹1,20,000.00, open) · PO-NB-1045 (Blue Dart, ₹22,500.00, open).

**`payment_history.csv`**: BD invoice `BDE/26-27/7781` ₹22,500.00 paid 2026-10-28 (provider_ref `hist-0091`).

**`policy.yaml`**:
- `approvers: ["Finance Controller", "AP Manager"]`
- `accounts.by_category`: food → Staff meals, cloud → Cloud hosting, freight → Freight - carriers, fuel → Fuel
- `po.tolerance_pct: 1.0`
- `duplicate.window_days: 3`

## 3. Scenarios and expected outcomes

### SC-01 Month-end bank posting (official example), `POST_BANK_LINES`, period Oct 2026
| Line | Input | Expected | Key evidence / reason |
|---|---|---|---|
| BL-001 | 3 Oct, `RAZ*SWIGGY BLR`, ₹1,240 | **POSTED**: Swiggy Limited, Staff meals | vendor_master V-0001 `legal_name` + `default_account` |
| BL-002 | 7 Oct, `AWS EMEA`, ₹18,500 | **POSTED**: Amazon Web Services EMEA SARL, Cloud hosting | alias "AWS EMEA" → the exact EMEA entity, not the AWS parent |
| BL-003 | 7 Oct, `AWS EMEA`, ₹18,500 | **HELD**: DUPLICATE_TRANSACTION of BL-002 (HIGH, overridable) | same vendor, amount, date |
| BL-004 | 9 Oct, `PAYTM*SHARMA TRD`, ₹4,000 | **CANNOT_VERIFY**: PAYEE_UNRESOLVED; missing "vendor-master record or GSTIN/domain"; **₹0, 0 external calls** | no master match, no identifier |

### SC-02 Posting edge cases, `POST_BANK_LINES`, period Oct 2026
| Line | Input | Expected |
|---|---|---|
| BL-101 | 14 Oct, `UPI/SWIGGY/BENGALURU`, ₹1,240 | POSTED, Swiggy. **Same `decision_hash` facts as BL-001 apart from the date** (consistency) |
| BL-102 | 2 Nov, `RAZ*SWIGGY BLR`, ₹980 | HELD: OUT_OF_PERIOD |
| BL-103 | 15 Oct, `NEFT/DELHIVERY`, ₹2,000 **CREDIT** | HELD: CREDIT_LINE (payee resolves to Delhivery; credits are not posted as expenses) |
| BL-104 | 18 Oct, `IMPS/OM SAI ROADWAYS`, ₹6,500 | CANNOT_VERIFY: PAYEE_TIE (V-0101, V-0102) |

### SC-03 Carrier invoice run, `PAY_INVOICES`
| Invoice | Input | Expected |
|---|---|---|
| INV-201 | Delhivery Limited, `DLV/2026-27/00418`, ₹48,000, PO-NB-1042, qty 12, remit = master account | **AWAITING_APPROVAL** → `approve --by "Finance Controller"` → **PAID** once the provider returns its terminal success (FakeProvider here; RazorpayX test mode in SC-13) · journal: accrual (Dr Freight - carriers / Cr AP-Delhivery) + payment (Dr AP-Delhivery / Cr Bank clearing) · bank_status: ifsc_valid=PASS (live; NOT_CHECKED if IFSC disabled), account_matches_master=PASS, ownership_verified=NOT_VERIFIED |
| INV-202 | Blue Dart, `BDE-26-27-07781` (reformatted `BDE/26-27/7781`), ₹22,500 | **HELD**: ALREADY_PAID (payment history `hist-0091`), not overridable |
| INV-203 | VRL Logistics, `VRL/11/2026/0332`, ₹64,800 vs PO-NB-1043 ₹60,000 | **HELD**: PO_AMOUNT_MISMATCH (+8.0%, over 1.0% tolerance), overridable |
| INV-204 | "Delhivery Logistics Pvt", `DLP-5521`, ₹31,000, no GSTIN/domain | **CANNOT_VERIFY**: LOOKALIKE (candidate Delhivery Limited; differs by "LOGISTICS") |
| INV-205 | Indian Oil, no invoice number, ₹1,20,000 | **CANNOT_VERIFY**: MISSING_FIELD invoice_no_raw |
| INV-206 | VRL Logistics, `VRL/11/2026/0340`, ₹18,000, **no PO number** | **HELD**: PO_MISSING (PO required for PAY_INVOICES), overridable with a reason |

### SC-04 Bank-change fraud attempt, `PAY_INVOICES` + vendor message
| Item | Input | Expected |
|---|---|---|
| MSG-301 | Email "from" delhivery.com: please update our bank account, new IFSC ‹IFSC-D› | **HELD**: BANK_CHANGE_REQUEST (HIGH); vendor V-0003 **contained**; IFSC lookup shows bank/branch; ownership NOT_VERIFIED |
| INV-302 | Delhivery invoice `DLV/2026-27/00431`, ₹12,000, remit = the *new* account | **HELD**: BANK_CHANGED + VENDOR_CONTAINED; not overridable; **no payment row created** |
| INV-303 | Indian Oil invoice `IOC/NB/2026/1189`, ₹1,20,000, remit IFSC ‹IFSC-INVALID› | **HELD**: BANK_CHANGED + IFSC_INVALID (live 404) |

### SC-05 Crash drill (robustness), runs SC-03 INV-201 with a kill
| Step | Expected |
|---|---|
| `approve INV-201` with `PAYPILOT_CRASH_AT=after_create_response_before_ref` (or the judge's random kill) | process dies |
| `resume` | "reconciled attempt pp_…:a1: provider payment found via same idempotency key; 0 new payments"; item PAID |
| Provider record (FakeProvider DB; the RazorpayX test Dashboard in SC-13) | **exactly one** successful payment for that invoice; `payment_instructions.csv` has one row (`do_not_execute=TRUE`, provider ref + status); journal has one payment entry; `audit verify` passes |

### SC-06 Provider timeout → uncertain → reconciled (FakeProvider fault injection)
| Step | Expected |
|---|---|
| FakeProvider **accepts** the confirm, then the response times out (accepted just before we lose contact) | item PAYMENT_UNCERTAIN; exception PROVIDER_OUTCOME_UNKNOWN (HIGH); **no journal payment entry yet**; `payment_instructions.csv` row with `status=PAYMENT_UNCERTAIN`, `provider_status=UNKNOWN`, `do_not_execute=TRUE` |
| `resume` with the provider back | retrieve → SUCCEEDED → PAID; exception RESOLVED with the provider ref as evidence |
| Variant: idempotency window expired + search empty | stays PAYMENT_UNCERTAIN; **no new create** |
| Variant: FakeProvider answers `PROCESSING` (acknowledged, not terminal) | payment SUBMITTED, item PAYMENT_IN_PROGRESS, **not PAID**, no payment journal entry; the next `resume` after the fault clears → PAID |

### SC-07 Post-payment reconciliation, `RECONCILE` (statement for 1–20 Nov)
| Statement line | Expected |
|---|---|
| Debit matching INV-201's payment (ref + amount) | MATCHED; settlement MATCHED |
| A **second** debit with the same ref/amount | CONFLICT → DUPLICATE_DEBIT **CRITICAL** + recovery case OPEN (action "contact bank/provider; request return"); Delhivery contained |
| A debit to Blue Dart ₹22,500 marked RETURNED | PAYMENT_RETURNED (HIGH); the history payment is reopened for review |
| A debit with no matching instruction | UNMATCHED (MEDIUM exception) |
| Then `resolve-exception … --resolution RECOVERED` **without** evidence | refused |

### SC-08 Unknown vendors with live lookups (P0: the live-data decision), `PAY_INVOICES`
| Invoice | Input | Expected |
|---|---|---|
| INV-401 | "Gati Express & Supply Chain", domain gati.com, not in master | Crustdata identify(gati.com) + web fetch → if the passage has a legal name + CIN: **HELD** VENDOR_ONBOARDING_REQUIRED + WEBSITE_IDENTITY_CLAIM_ONLY (cited passage labelled self-published); otherwise HELD VENDOR_ONBOARDING_REQUIRED. Never PAID |
| INV-402 | "Amazon Web Services India Private Limited", domain aws.amazon.com, not in master | Crustdata returns the AWS parent brand → **CANNOT_VERIFY** PARENT_BRAND |

### SC-09 Cost limit (finance + hour-3 style change)
Run SC-08 with `limits.max_cost_inr: 0.5`. Expected: the first paid call is reserved; the next reservation fails → the remaining items are HELD with COST_LIMIT; run `STOPPED_COST`; the ledger shows every call, including any UNCONFIRMED.

### SC-10 Unsupported request
`request.json` without an operation; instruction "Is Sharma Traders a registered company?". Expected: one run-level **CANNOT_VERIFY** result, UNSUPPORTED_REQUEST, missing ["supported operation"], exit code 0.

### SC-11 Same invoice re-sent in a different file, `PAY_INVOICES`
After SC-03 (INV-201 PAID), a new request `invoices_resent_2026-11-12.json` contains the same Delhivery invoice as `DLV-2026-27-418` (different filename, different formatting).

| Expected | Asserted on |
|---|---|
| **HELD**: DUPLICATE_INVOICE, citing the PAID item from SC-03 (canonical `DLV\|2026\|27\|418` matches across runs via the DB) | `results.jsonl`; **no new payments/attempts row**; exceptions table +1 DUPLICATE_INVOICE (HIGH); audit shows NEW→…→HELD only |

### SC-12 Auditor replay
After SC-01…SC-11, run `audit verify` over every run.

| Expected | Asserted on |
|---|---|
| Exit 0. Every decision_hash re-derives from the stored facts + policy snapshot; every PAID item ↔ one approval ↔ one confirmed attempt ↔ one payment journal entry; trial balance Σ = 0; every output fact → a stored source | the `audit verify` report |
| Tamper test: edit one journal line's amount in a copy of the DB → `audit verify` exits non-zero and names the entry | `tests/integration/test_audit_verify.py` |

### SC-13 RazorpayX test-mode vendor payout (opt-in: `--provider razorpayx_test`, needs H7)
Runs SC-03 INV-201 against RazorpayX Payouts **test mode**: a dummy balance, no real money, no bank settlement. The vendor's account number is the fictional fixture account. **P0 fixture dependency:** ‹IFSC-B› (V-0003) must be a real public IFSC: 11 characters, `^[A-Z]{4}0[A-Z0-9]{6}$`, and verified live by lane B (task B4) with a 200 from the IFSC API before SC-13 runs. SC-13 is blocked while it is still a placeholder; we don't invent a code or relax validation.

| Step | Expected | Asserted on |
|---|---|---|
| 1. Startup | prints `Payment provider: RazorpayX TEST MODE (no real money)`; a non-`rzp_test_` key → exit 2 | stdout / exit code |
| 2. `approve INV-201 --by "Finance Controller"` | one `POST /v1/payouts` with `X-Payout-Idempotency`; `provider_ref = pout_…` persisted; RazorpayX status `processing` → payment SUBMITTED, item **PAYMENT_IN_PROGRESS (not PAID)**; no payment journal entry | DB, `payment_instructions.csv` (`provider_status=processing`), ledger row `razorpayx_test` |
| 3. Move the payout to `processed` in the test Dashboard → `resume` | retrieve → `processed` → PROVIDER_CONFIRMED / **PAID**; INVOICE_PAYMENT journal entry; settlement NOT_AVAILABLE | DB, audit, `audit verify` |
| 4. Variant: move it to `failed` (or `reversed`) instead | FAILED → item HELD; the same key is never re-sent as a new payout | DB, exceptions |
| 5. Crash drill: `PAYPILOT_CRASH_AT=after_create_response_before_ref` then `resume` | the same idempotency key + body is replayed → the same `pout_…`; the Dashboard shows **one** payout | Dashboard, DB |

## 4. Coverage of the team's required test cases
| Required case | Scenario |
|---|---|
| Normal invoice | SC-03 INV-201 |
| Duplicate invoice with a different filename | SC-11 |
| Same invoice with slightly different formatting | SC-03 INV-202 (vs payment history), SC-11 |
| Vendor requesting a different bank account | SC-04 MSG-301, INV-302 |
| Lookalike vendor names | SC-03 INV-204; tie SC-02 BL-104 |
| Unknown vendor with insufficient evidence | SC-01 BL-004; SC-08 INV-401 / INV-402 |
| Missing PO | SC-03 INV-206 |
| Invoice amount mismatch | SC-03 INV-203 |
| Duplicate bank transaction | SC-01 BL-003; SC-07 duplicate debit |
| Crash during payment execution | SC-05 + the kill loop (30 random kills) |
| Timeout after the provider accepted | SC-06 |
| Real provider test-mode payout | SC-13 |
| The four official bank lines | SC-01 |

## 5. Acceptance map (Part A rules)
| Requirement (Part A) | Proven by |
|---|---|
| Duplicate record | SC-01 BL-003, SC-03 INV-202, SC-07 duplicate debit |
| Lookalike name | SC-03 INV-204, SC-02 BL-104 (tie) |
| Unsettleable → cannot verify | SC-01 BL-004, SC-10, SC-08 INV-402 |
| Every fact sourced | every `expected.jsonl` row asserts its `sources[]` |
| Live data in a decision | SC-08: Crustdata identify by domain + web fetch (P0). IFSC lookup in SC-03 / SC-04 is an optional extra check |
| Approval before an irreversible action | SC-03 INV-201; SC-04 shows no payment row without approval |
| Payment provider test mode | SC-13: RazorpayX Payouts test mode (P0, needs H7). FakeProvider (durable, fault-injecting, labelled as a simulation) for every deterministic scenario. Without H7 the requirement is **not met**, and we say so |
| Time and cost limits, per-call cost | SC-09; `ledger` after every scenario |
| Kill and restart, each action once | SC-05 + `tests/crash/test_kill_loop.py` (30 random kills) |
| Exception management | SC-04, SC-06, SC-07, SC-11 |
| Books an auditor can replay | SC-12 (`audit verify`) |
