# HACKATHON_INFORMATION.md

> For AI coding agents (Claude Code, Codex/GPT) and the team. Read fully before planning.
> **Part A is binding** (official problem statement). Part B is our decision and ideas (raw; organise them into a plan).
> Part C is verified API findings. Part D is API reference.
> Event: ContextCon 05 @ BITS Pilani, 22 Nov 2026. **Code freeze 6:00 PM IST.**

---

# PART A: WHAT IS STRICTLY EXPECTED (official problem statement)

## Challenge
"Services, Replaced." Pick a job a firm earns a fee from today; build the service that does ALL of it, request → finished result.
Each claim has a source. A person approves only actions that cannot be undone.

## Our track: Buying & paying
- Job today: payables teams / outsourced finance firms are paid to pay each invoice correctly.
- Example jobs: **pay each invoice exactly once, through crashes and bank-detail fraud; reconcile against a bank statement.**
- Finished work = "each invoice paid exactly once."

## The client (same for all teams)
- Fictional **mid-size Indian 3PL logistics company**. Its carriers, suppliers, counterparties are **real companies**.
- Client name, records and requests are in the **kickoff kit** (input/output formats too). **Follow the kit's I/O format exactly.** (TODO: team is locating it; add path here.)
- **Organizers may change client info during the build** (new rule, updated carrier record, new price). Never hardcode client data; load from files/config, re-read on each run.

## Requirements for every service (all mandatory)
1. **Live data:** live real-world data for at least one decision (Crustdata, registries, public APIs, web). Mock data only in tests.
2. **Hidden test set:** organizers run hidden test cases for our request, including **a duplicate record, a lookalike name, and an unsettleable question → correct answer "cannot verify"**. All cases dated after 1 Sep 2026. **A wrong answer is worse than "cannot verify".**
3. **Sources:** every fact in the output links to the record it came from (web page, API response, filing), showing **the exact passage or field + the record's date**. The source must state the fact itself. **No facts from model memory.** If unsettled → "cannot verify" + name what's missing.
   Example: "Payee Swiggy Limited, from the legal-name field of a company registry record, retrieved 9 October 2026."
4. **Approval:** irreversible actions (payment, email to a client) wait for a person's approval. **Payments in test mode** (Razorpay or Stripe).
5. **Limits:** every run has a **time limit and a cost limit**; record the cost of each call.

## Official example test case (our track)
Request: "Post these bank lines from October 2026 to the correct accounts."
| Bank line | Correct result |
|---|---|
| 3 Oct 2026, RAZ*SWIGGY BLR, ₹1,240 | Payee: Swiggy Limited. Account: staff meals. |
| 7 Oct 2026, AWS EMEA, ₹18,500 | Payee: Amazon Web Services EMEA SARL. Account: cloud hosting. |
| 7 Oct 2026, AWS EMEA, ₹18,500 | Same payee/amount/date as above → **held for approval as a possible duplicate** |
| 9 Oct 2026, PAYTM*SHARMA TRD, ₹4,000 | No source identifies payee → **cannot verify, held for a person** |
Posting the duplicate or guessing the last payee = FAIL.

## Deliverables (in the ORGANIZER repo from kickoff; commit throughout, since organizers watch history)
- **README:** the job + who pays for it today, **the input, the finished result, and the pass mark**, and **every data source the service calls**.
- **Architecture diagram in the repo:** components, data flow, and **where data calls, approvals, retries and the cost ledger sit**.
- Form factor: agent, agent system, MCP server, or CLI (we're doing a CLI).

## Competition format
1. **Build (7 h).** At **hour 3, one requirement changes for all tracks** (e.g. new output field, lower cost limit). Must be met at screening. Code freeze after build; only change after that = the live fix in finals.
2. **Screening (no score; picks 8–12 finalists):** organizers run an **unseen request + the hidden test set**, **stop and restart the service**, inspect **README, diagram, logs, cost records, repo history**. One member runs it and answers questions. Judged on correctness, robustness after restart, engineering, working demo.
3. **Finals (12-min slot):** 3-min live demo on an unseen request, **including a judge killing the process at a random moment** (restart, each action happens once) · **5-min live fix** of a small change request · 2-min Q&A incl. a system design question (100× volume? second client?) · 2-min changeover.

## Finals scoring (only this decides ranking)
| Area | Judge | Pts | What's checked |
|---|---|---|---|
| Live run | CS (2, averaged) | 20 | Unseen request → correct, sourced result |
| Robustness | CS | 15 | Kill on stage; restart; each action happens once |
| Design | CS | 15 | Live fix + system design question |
| Ethics | Ethics | 25 | Personal data handling, approvals before harmful actions, explaining decisions |
| Financial feasibility | Finance | 25 | The buyer, price in ₹, **measured cost of each job**, would a firm pay |

## Hard rules
- Agents never contact real outside people. No real payments; test mode only.
- Secrets only in `.env` (gitignored). Never commit keys.
- Small, frequent commits with clear messages. Don't rewrite history.

---

# PART B: OUR DECISION + IDEAS (raw; Claude Code: organise into a plan)

## Decision (final): Buying & paying, as a CLI service.
Why: the official example test is this track; the kill-on-stage test is literally "pay exactly once"; little personal data (good for ethics); clear per-invoice pricing (finance); input comes mostly from client records, so we depend less on weak Crustdata coverage.

## Core principle
**The client's vendor master is the source of truth** for payee legal name, CIN, GSTIN, bank account + IFSC. Crustdata only verifies that unknown vendors exist and matches domains. **Decisions are deterministic rules, not the LLM.** The LLM is only for parsing messy input (invoice text, descriptors). Reason: consistency (same case worded differently → same answer) + explainability (ethics).

## Pipeline (per bank line / invoice)
1. **INGEST:** parse request + lines/invoices → `items` table, status NEW. LLM only for messy parsing; validate into the schema.
2. **RESOLVE payee:** normalise descriptor (strip RAZ*, PAYTM*, NEFT, IMPS, UPI, city codes) → vendor master match: exact → alias → fuzzy.
   - Unknown vendor → Crustdata Company Identify **by domain only** + Web Fetch the company site for a citable CIN passage.
   - **Any tie** (multiple matches at the same confidence) → CANNOT_VERIFY.
   - **Domain resolves to a parent brand / different legal entity** (e.g. aws.com → US parent, not EMEA SARL) → CANNOT_VERIFY unless the vendor master has the exact entity.
   - No record → CANNOT_VERIFY, naming what's missing.
3. **CHECK:**
   - Duplicate: same payee + amount + date window, OR same invoice number, incl. "new format" duplicates (different formatting, same invoice) → HOLD.
   - PO match: amount/qty within tolerance (config) → otherwise HOLD with reason.
   - Bank details ≠ vendor master (e.g. "please update our bank account" email) → Razorpay IFSC check (branch real?) → **ALWAYS HOLD, never auto-pay changed bank details.**
   - Source timeout/error → retry with backoff, then HOLD for a person. **Never read silence as an answer.**
4. **DECIDE:** rules from `config.yaml` → POST / PAY | HOLD | CANNOT_VERIFY, plus `reasons[]` and `sources[{source, field_or_passage, record_date, retrieved_at}]`.
5. **APPROVE:** approval queue; CLI `approve <id>` / `reject <id>`; log who, when, why.
6. **PAY:** Stripe or Razorpay TEST MODE. `idempotency_key = hash(vendor_id, invoice_no, amount)`, sent to the provider too.
7. **LEDGER:** every Crustdata call, LLM call (tokens → ₹), and payment call → `cost_ledger`. Enforce per-run time + cost ceilings → stop safely and report if exceeded.

## State and crash safety (kill on stage happens at screening AND finals)
- SQLite (WAL). Each item is a state machine: NEW → RESOLVED → CHECKED → DECIDED → (AWAITING_APPROVAL) → APPROVED → PAID / HELD / CANNOT_VERIFY.
- Write each step's result before any side effect (outbox pattern).
- `payments.idempotency_key` UNIQUE. On restart (`resume`): reload all non-terminal items; before re-paying, query the provider by idempotency key to see if it already went through.
- Test it: `kill -9` at random points in a loop; assert no double payment, no duplicate ledger rows, run completes.

## Config-driven (for the hour-3 change + 5-min live fix)
`config.yaml` holds: time limit, cost limit, output fields/schema, duplicate date window, PO tolerance, account mapping rules, client data file paths. Adding an output field or lowering a limit should be a one-line change. Keep modules small and decoupled.

## CLI
`run --request <file>` · `resume` · `approve <id>` · `reject <id>` · `ledger` · `report`. Output in the kickoff kit's format.

## Logs
Structured, readable (JSON lines + a human summary). Every step, decision, retry, approval, and cost. Organizers read them at screening.

## Team split (by folder, to avoid agent collisions)
| Owner | Owns | Folder |
|---|---|---|
| Me | state machine, resume, idempotency, test-mode payments | `core/`, `pay/` |
| B | vendor-master matching, Crustdata client (rate limit, cache, per-call cost), IFSC, Web Fetch for CIN | `resolve/` |
| C | duplicate / PO / bank-change checks, rules config, fixtures + tests | `checks/`, `tests/` |
| D | approval CLI, README, Mermaid architecture diagram, finance + ethics numbers, rehearsal | `cli/`, `docs/` |
2 people: me = core+pay+docs, other = resolve+checks+tests. 3 people: merge D into me.
**First commit: `schemas.py` (Item, Source, Decision, LedgerEntry) + `config.yaml`, agreed by everyone.**

## Timeline (to 6:00 PM freeze)
- 11:10–11:30 skeleton: schemas, config, SQLite tables, CLI stubs; commit. Locate kickoff kit; match I/O format.
- 11:30–1:00 vendor matching + checks + decision rules; test on the official 4 lines + our own cases.
- ~1:00 **one request end to end on the CLI** (fake payment).
- ~1:15 **hour-3 requirement change**: watch WhatsApp; apply via config.
- 1:00–2:30 test-mode payments + idempotency + kill/restart loop test; Crustdata/Web Fetch for unknown vendors; cost ledger.
- 2:30 checkpoint submit.
- 2:30–4:00 hard cases: duplicate in a new format, lookalike vendor name, bank-change email, timeout → hold, same case worded differently.
- 4:00–5:00 README (job, who pays, input, finished result, pass mark, sources), Mermaid diagram, readable logs.
- 5:00–5:45 rehearse: kill on stage, a mock live fix (add an output field in under 5 min), finance + ethics answers.
- 6:00 freeze.

## Test cases to build (our own; dated after 1 Sep 2026)
- The 4 official lines above.
- A carrier invoice matching a PO exactly → PAY.
- The same invoice re-sent as a PDF with a different number format → duplicate → HOLD.
- A lookalike vendor name (e.g. "Delhivery Logistics Pvt" vs "Delhivery Limited") → don't merge blindly; resolve via the vendor master or CANNOT_VERIFY.
- A polite "we changed our bank account" email from a known carrier → HOLD + IFSC check + reason.
- Invoice amount > PO beyond tolerance → HOLD.
- Crustdata/web timeout → HOLD, not guess.
- An unknown payee with a site showing CIN → verified with the cited passage; unknown payee with no record → CANNOT_VERIFY.
- The same bank line worded two ways → identical result.

## Ethics answers (25 pts)
- No personal data: we deal with companies; don't store people's data. Log only what the job needs.
- Every payment needs human approval; bank-detail changes are never auto-paid and are flagged as possible fraud.
- Every decision shows reasons + sources; "cannot verify" instead of guessing; approval audit trail (who, when, why).
- Wrong-payee risk: guarded by tie → cannot-verify, parent-brand → cannot-verify, bank-change → hold.

## Finance answers (25 pts)
- Buyer: the 3PL's finance team, or an outsourced payables firm serving many 3PLs (second-client answer).
- **Measured** cost per invoice from `cost_ledger`: Crustdata credits → ₹ + LLM tokens → ₹ (+ infra). Show it live.
- Price per invoice processed as a multiple of measured cost; compare to a real, cited outsourced-AP per-invoice price (look it up; don't invent).
- Value: duplicates and bank-detail fraud prevented; faster close.

## System design answers
- 100× volume: queue workers over the same SQLite → Postgres; idempotency keys make retries safe; cache vendor lookups; rate-limit Crustdata per endpoint.
- Second client: per-client vendor master + config; tenant id on every table.

---

# PART C: VERIFIED API FINDINGS (tested today, about 24.6 credits spent)
- Crustdata **has no legal/registered-name field** ("Swiggy", not "Swiggy Limited"; "Delhivery", not "Delhivery Limited"). Record date fields (`updated_at`, `metadata.updated_at`) came back **null**.
- **Domain → parent brand:** aws.com → "Amazon Web Services (AWS)", US HQ, NOT the Amazon Web Services EMEA SARL entity that bills. Never treat the domain match as the legal payee.
- **Name matching unreliable:** 3 different "Sharma Traders" all at confidence 1.0; earlier, wrong companies returned at 1.0 (an ad agency, a Paris office, a Bangladeshi firm). Identify **by domain**, and treat ties as cannot verify.
- **Web Fetch works** (about 9 s, 1 credit): delhivery.com footer gives a citable "CIN: L63090DL2011PLC221234 NSE: DELHIVERY". No page date; cite the fetch time. Cloudflare-protected sites (zaubacorp, filesure) return "Just a moment" and **are still charged**, so skip them.
- **Razorpay IFSC API works:** `GET https://ifsc.razorpay.com/<IFSC>`, free, no login, about 0.45 s; gives bank, branch, address, MICR, NEFT/RTGS/IMPS/UPI flags. No date field; cite the HTTP `Date` header.
- **MCA registry:** not usable (403 / Cloudflare / login). Say so in the README.
- LinkedIn-based headcount badly undercounts Indian factories (not needed for our track).
- Web Search result links were often Google redirects with undated snippets, so prefer Web Fetch of known pages for citations.
- Costs: Identify 0.2 / identifier matched, Enrich about 2 / company, Web Search about 1 / query, Web Fetch about 1 / page, `GET /account/credits` free.
- Maps data group is blocked on our plan.
- Probe logs: `scratch/api_probe/log.jsonl` (gitignored).

---

# PART D: CRUSTDATA API REFERENCE (minimal)
- Base `https://api.crustdata.com`; headers `authorization: Bearer $CRUSTDATA_API_KEY`, `content-type: application/json`, `x-api-version: 2025-11-01`.
- Company Identify `POST /company/identify` body `{"domains": ["delhivery.com"]}` → `[{matched_on, match_type, matches:[{confidence_score, company_data:{crustdata_company_id, basic_info:{name, year_founded}, headcount}}]}]`
- Company Enrich: check exact path in the docs/probe script. Web Search / Web Fetch: check exact paths in `scratch/api_probe/`.
- Rate limits/min: Identify 30, Enrich 15, Web 10. On 429 wait for `x-ratelimit-reset`. 402 = out of credits (don't retry). 401 = bad key. 403 = endpoint not allowed.
- Client must: rate-limit per endpoint, retry with backoff, cache responses, log credits per call to the ledger.
- Docs: https://docs.crustdata.com (agent index: https://docs.crustdata.com/llms.txt). MCP: `https://install.crustdata.com/mcp`.

## Contacts
WhatsApp group (submissions, announcements, **the hour-3 change**) · Crustdata team on the floor (API, credits) · BITS Pilani desk (venue, urgent).
