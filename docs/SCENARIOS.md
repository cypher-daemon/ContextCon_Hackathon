# PayPilot: Demo World & Scenarios

> We own the test world. These scenarios are the acceptance suite, the screening demo, and the video script's data.
> Each scenario lives in `scenarios/<id>/` with `request.json`, batch files and `expected.jsonl` (format in INTERFACES §2–3). `tests/integration/test_scenarios.py` runs every scenario and diffs against `expected.jsonl`, ignoring timestamps.
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
| INV-201 | Delhivery Limited, `DLV/2026-27/00418`, ₹48,000, PO-NB-1042, qty 12, remit = master account | **AWAITING_APPROVAL** → `approve --by "Finance Controller"` → **PAID** (Stripe test) · bank_status: ifsc_valid=PASS (live), account_matches_master=PASS, ownership_verified=NOT_VERIFIED |
| INV-202 | Blue Dart, `BDE-26-27-07781` (reformatted `BDE/26-27/7781`), ₹22,500 | **HELD**: ALREADY_PAID (payment history `hist-0091`), not overridable |
| INV-203 | VRL Logistics, `VRL/11/2026/0332`, ₹64,800 vs PO-NB-1043 ₹60,000 | **HELD**: PO_AMOUNT_MISMATCH (+8.0%, over 1.0% tolerance), overridable |
| INV-204 | "Delhivery Logistics Pvt", `DLP-5521`, ₹31,000, no GSTIN/domain | **CANNOT_VERIFY**: LOOKALIKE (candidate Delhivery Limited; differs by "LOGISTICS") |
| INV-205 | Indian Oil, no invoice number, ₹1,20,000 | **CANNOT_VERIFY**: MISSING_FIELD invoice_no_raw |

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
| `resume` | "reconciled attempt pp_…:a1: PaymentIntent pi_… found via same idempotency key; 0 new payments"; item PAID |
| Stripe test dashboard | **exactly one** PaymentIntent for that invoice |

### SC-06 Provider timeout → uncertain → reconciled (FakeProvider fault injection)
| Step | Expected |
|---|---|
| Fake provider times out on confirm | item PAYMENT_UNCERTAIN; exception PROVIDER_OUTCOME_UNKNOWN (HIGH) |
| `resume` with the provider back | retrieve → SUCCEEDED → PAID; exception RESOLVED with the provider ref as evidence |
| Variant: idempotency window expired + search empty | stays PAYMENT_UNCERTAIN; **no new create** |

### SC-07 Post-payment reconciliation, `RECONCILE` (statement for 1–20 Nov)
| Statement line | Expected |
|---|---|
| Debit matching INV-201's payment (ref + amount) | MATCHED; settlement MATCHED |
| A **second** debit with the same ref/amount | CONFLICT → DUPLICATE_DEBIT **CRITICAL** + recovery case OPEN (action "contact bank/provider; request return"); Delhivery contained |
| A debit to Blue Dart ₹22,500 marked RETURNED | PAYMENT_RETURNED (HIGH); the history payment is reopened for review |
| A debit with no matching instruction | UNMATCHED (MEDIUM exception) |
| Then `resolve-exception … --resolution RECOVERED` **without** evidence | refused |

### SC-08 Unknown vendors with live lookups (P1), `PAY_INVOICES`
| Invoice | Input | Expected |
|---|---|---|
| INV-401 | "Gati Express & Supply Chain", domain gati.com, not in master | Crustdata identify(gati.com) + web fetch → if the passage has a legal name + CIN: **HELD** VENDOR_ONBOARDING_REQUIRED + WEBSITE_IDENTITY_CLAIM_ONLY (cited passage labelled self-published); otherwise HELD VENDOR_ONBOARDING_REQUIRED. Never PAID |
| INV-402 | "Amazon Web Services India Private Limited", domain aws.amazon.com, not in master | Crustdata returns the AWS parent brand → **CANNOT_VERIFY** PARENT_BRAND |

### SC-09 Cost limit (finance + hour-3 style change)
Run SC-08 with `limits.max_cost_inr: 0.5`. Expected: the first paid call is reserved; the next reservation fails → the remaining items are HELD with COST_LIMIT; run `STOPPED_COST`; the ledger shows every call, including any UNCONFIRMED.

### SC-10 Unsupported request
`request.json` without an operation; instruction "Is Sharma Traders a registered company?". Expected: one run-level **CANNOT_VERIFY** result, UNSUPPORTED_REQUEST, missing ["supported operation"], exit code 0.

## 4. Acceptance map
| Requirement (Part A) | Proven by |
|---|---|
| Duplicate record | SC-01 BL-003, SC-03 INV-202, SC-07 duplicate debit |
| Lookalike name | SC-03 INV-204, SC-02 BL-104 (tie) |
| Unsettleable → cannot verify | SC-01 BL-004, SC-10, SC-08 INV-402 |
| Every fact sourced | every `expected.jsonl` row asserts its `sources[]` |
| Live data in a decision | SC-03 INV-201 / SC-04 IFSC (P0); SC-08 Crustdata (P1) |
| Approval before an irreversible action | SC-03 INV-201; SC-04 shows no payment row without approval |
| Test-mode payment | SC-03 / SC-05 (Stripe test) |
| Time and cost limits, per-call cost | SC-09; `ledger` after every scenario |
| Kill and restart, each action once | SC-05 + `tests/crash/test_kill_loop.py` (30 random kills) |
| Exception management | SC-04, SC-06, SC-07 |
