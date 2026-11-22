# PayPilot: Business Case, Unit Economics & Ethics

> Finance and ethics are 50 of 100 finals points. Every number here must come from our ledger or a cited source. **No invented figures.** Lines marked ⟨TODO⟩ are filled by task H3/H4 before the video.

## 1. Buyer and the job
| | |
|---|---|
| Primary buyer | Finance controller / head of AP at a mid-size Indian 3PL. They own vendor payments, month-end close, and losses from duplicate or fraudulent payments. |
| Secondary buyer | Outsourced AP / finance BPO firms that serve many 3PLs. PayPilot multiplies their staff capacity; `clients/<id>/` is built for them. |
| Job today | AP clerks match bank lines and invoices to vendors, check POs, spot duplicates, chase bank-change emails, get approvals, pay, and investigate errors. |
| Why they'd pay | (a) Fewer duplicate or wrong payments, (b) bank-change fraud blocked by default, (c) faster close, (d) an audit trail where every decision has a reason and a source. |

## 2. Pricing and unit economics (measured, not guessed)
- **Pricing model:** a per-item fee (each bank line posted or invoice processed), with volume tiers for BPO firms.
- **Measured cost per job:** from `python -m paypilot ledger --run <id>` across the SC-01…SC-09 runs:

| Metric | Value | Source |
|---|---|---|
| Items processed | ⟨from ledger⟩ | `summary.json` |
| % items resolved at ₹0 (vendor master, no external call) | ⟨from ledger⟩ | `ledger` |
| Avg ₹ / item (external calls) | ⟨from ledger⟩ | `ledger` (Crustdata credits × ₹/credit; IFSC free; payment provider ₹0 in test mode: the RazorpayX test fee comes out of a dummy balance and is recorded as provider metadata, not as real spend) |
| Worst-case ₹ / item | ⟨from ledger⟩ | `ledger` |
| ₹ per Crustdata credit | ⟨ask the Crustdata team; cite⟩ | — |
| LLM ₹ / item | ₹0: no LLM is used | design |

- **Price benchmark:** ⟨TODO H3: one or two cited sources for outsourced AP cost per invoice in India, with URL + date⟩. The proposed price = a multiple of measured cost, set clearly below the cited manual cost per invoice. **State it as a hypothesis to validate with customers.**
- **Value argument:** one prevented duplicate or fraudulent carrier payment (e.g. the ₹48,000 freight invoice in SC-03) covers many months of fees for a mid-size client. Show this with our own scenario numbers, not industry statistics we can't cite.

## 3. Ethics by design (each point maps to a feature and a test)
| Principle | Feature | Proof |
|---|---|---|
| Minimal personal data | No data about people; vendor master loaded by an allowlist of columns; emails/phones redacted before any LLM | fixtures contain no personal data; `tests/unit/io` |
| Financial data protection | Bank accounts → HMAC fingerprint + last 4 at load; never logged or stored raw | `tests/unit/io/test_fingerprint.py`, log-masking test |
| Human approval before irreversible actions | Every payment needs a named approver from the client allowlist; approval bound to `decision_hash` | FAILURE_MODES #30–33 |
| No harmful automation | Bank changes are never auto-applied; identity, duplicate and uncertain-payment flags can't be overridden; the vendor is contained on CRITICAL | SC-04, SC-07 |
| Explainability | Every outcome lists all reasons, rules and sources; `show` gives the full chain; decisions are reproducible | SC-01 `show`, consistency test |
| Honesty about evidence | Website claims labelled self-published; IFSC ≠ ownership; provider-confirmed ≠ settled; recovery requested ≠ recovered; unknown cost ≠ ₹0 | SC-04, SC-06, SC-07, SC-09 |
| Fail closed | Source down, credits out, limits hit, provider silent → HOLD / UNCERTAIN, never a guess | FAILURE_MODES #10, #11, #23, #25, #34 |
| Test mode only | Only `fake` or `razorpayx_test` providers exist; non-`rzp_test_` keys refused at startup; no fallback to live; no real people contacted | FAILURE_MODES #40 |
| Disclosed limitations | MCA registry not usable (Cloudflare/login); Crustdata has no legal-name field; payments run through RazorpayX Payouts **test mode** (no real money; not bank settlement) or, offline and in tests, through our FakeProvider simulation | README "Data sources" |

## 4. Data sources the service calls (README copies this table once implemented)
| Source | Used for | Cost | Record date basis | Limitation |
|---|---|---|---|---|
| Client vendor master / POs / payment history / chart of accounts | Payee identity, bank, account, PO, history | ₹0 | `last_updated` column / file date | Only as good as the client's master |
| Razorpay IFSC API (`GET ifsc.razorpay.com/{IFSC}`), optional; a free lookup, not RazorpayX | IFSC validity, bank and branch | free | `UNKNOWN` (no record date; `retrieved_at` from the HTTP `Date` header) | Doesn't prove account ownership |
| **Crustdata Company Identify (by domain): the live-data source** | The company behind an unknown vendor's domain | ~0.2 credit / match | `RECORD_FIELD` only if the response carries a dated field; else `UNKNOWN` (dates came back null in probes) | Often the parent brand; no legal name |
| Crustdata Web Fetch | Citable legal-name / CIN passage on the vendor's site | ~1 credit / page | `PUBLICATION_DATE` if the page states one; else `UNKNOWN` | Self-published; Cloudflare sites skipped |
| Payment provider: RazorpayX Payouts test mode (`razorpayx_test`); FakeProvider (`fake`, tests/offline) | Vendor payout execution and status through `PaymentProvider` | ₹0 (test mode) | `RECORD_FIELD` (payout `created_at`) | Test mode: dummy balance, no real money, not bank settlement; payouts move past `processing` only when changed by hand in the test Dashboard. FakeProvider is our own simulation |

`retrieved_at` (when we fetched it) is recorded for every source and is never presented as the date the record was last updated.
