# PayPilot

## Initial Product, Architecture & Dashboard Proposal

**Document status:** Draft for team review and approval\
**Project:** ContextCon 05 --- BITS Pilani\
**Track:** Buying & Paying\
**Version:** 0.1

------------------------------------------------------------------------

## 1. Executive summary

PayPilot is an autonomous accounts-payable service for a mid-sized
Indian logistics company operating in the third-party logistics (3PL)
industry.

The service processes invoices from carriers and suppliers, identifies
payees, checks invoices against available business and financial
records, detects duplicates and suspicious payment details, prepares
payments for human approval, and records an auditable history of its
work.

The central product principle is:

> An invoice should move from receipt to verified payment, with every
> decision traceable and no invoice ever paid twice.

PayPilot is not intended to be a generic accounting application or a
chatbot that merely explains invoices. It should complete the
accounts-payable workflow as far as the available data and authorized
actions allow.

## 2. Problem and customer

### The problem

A logistics company receives invoices from many carriers and suppliers.
Finance staff must:

-   Extract invoice details.
-   Identify the correct legal payee.
-   Check whether the invoice is legitimate and complete.
-   Compare invoices against bank statements and payment history.
-   Detect duplicate invoices and duplicate payments.
-   Investigate suspicious bank-detail changes or mismatches.
-   Approve and execute payments.
-   Maintain records explaining what happened and why.

Manual processing is time-consuming and error-prone. Incorrectly
identifying a payee, paying an invoice twice, or sending money to
changed or fraudulent bank details can cause direct financial loss.

### The customer

The official problem statement defines one fictional client for all
teams: a mid-sized Indian logistics company that operates third-party
logistics (3PL) services. Its carriers, suppliers, candidates, rivals
and other counterparties are real companies.

Our initial customer story is therefore:

> PayPilot helps the finance team of a 3PL company process carrier and
> supplier invoices accurately, detect payment risks, and prepare
> verified payments.

### Who pays and why?

The proposed buyer is the logistics company's finance or
accounts-payable function. The value proposition is reduced manual
processing effort, fewer duplicate or incorrect payments, faster
reconciliation, and a clear audit trail.

Pricing and measured cost per processed invoice must be established
during implementation. We should not invent a price without measuring
the actual cost of API and model calls.

## 3. What we're building

An autonomous invoice-processing service with the following
capabilities:

1.  **Invoice intake:** Accept invoice documents and/or structured
    invoice records.
2.  **Invoice extraction:** Extract vendor, invoice number, amount,
    date, bank details and other relevant fields.
3.  **Vendor identification:** Identify the correct legal business using
    company records and external data.
4.  **Vendor verification:** Gather available external evidence and
    compare it with the invoice and vendor records.
5.  **Invoice reconciliation:** Match invoices against bank-statement
    entries and existing payment records.
6.  **Duplicate and fraud checks:** Detect repeated invoices, duplicate
    transactions, suspicious bank-detail changes and inconsistencies.
7.  **Decision-making:** Apply deterministic rules to decide whether an
    invoice can proceed or must be held.
8.  **Payment preparation and execution:** Request human approval and
    execute approved payments using a payment provider's test mode.
9.  **Audit and recovery:** Persist workflow state, evidence, decisions,
    approvals, payment events, retries and costs.

### Invoice outcomes

Each invoice should receive one of the following operational outcomes:

-   **APPROVE / READY FOR APPROVAL:** Available checks pass and the
    invoice can be presented for human payment approval. This does not
    itself authorize payment.
-   **HOLD:** A duplicate possibility, mismatch, suspicious detail,
    incomplete record or other uncertainty requires investigation.
-   **CANNOT VERIFY:** Available sources are insufficient to establish a
    required fact. The system must state what information is missing
    rather than guess.

Payment execution is a separate step and must require human approval.

## 4. End-to-end workflow

``` mermaid
flowchart TD
    A["Invoice received"] --> B["Extract invoice fields"]
    B --> C{"Extraction valid?"}
    C -->|No| H["HOLD: Manual review"]
    C -->|Yes| D["Identify vendor"]
    D --> E["Verify vendor and bank details"]
    E --> F["Check duplicate invoices"]
    F --> G["Reconcile with bank statement"]
    G --> I{"Risk checks passed?"}
    I -->|No or uncertain| H
    I -->|Yes| J["Prepare payment"]
    J --> K["Human approval"]
    K -->|Rejected| L["Payment cancelled"]
    K -->|Approved| M["Execute in test mode"]
    M --> N["Record payment result"]
    N --> O["Reconcile and close invoice"]
    H --> P["Investigation and evidence log"]
    L --> P
    O --> P
```

### Workflow stages

  -----------------------------------------------------------------------
  Stage                   System responsibility   Expected output
  ----------------------- ----------------------- -----------------------
  Ingestion               Receive invoice and     Raw records
                          bank-statement data     

  Extraction              Extract invoice fields  Structured invoice
                          into a structured       
                          format                  

  Vendor verification     Identify the legal      Vendor identity and
                          company and retrieve    evidence
                          available evidence      

  Duplicate detection     Compare against prior   Duplicate warnings or
                          invoices and payments   clear result

  Reconciliation          Match invoices against  Reconciliation status
                          bank entries and        
                          payment history         

  Risk evaluation         Apply deterministic     Ready for approval or
                          rules                   hold

  Payment                 Obtain human approval   Payment status
                          and execute in test     
                          mode                    

  Audit                   Record sources,         Audit record
                          decisions, costs and    
                          events                  
  -----------------------------------------------------------------------

**Important distinction:** a duplicate invoice and a duplicate bank
transaction are not necessarily the same thing. The system should check
for both.

A similar vendor name is not proof that two records refer to the same
legal business.

## 5. Proposed system architecture

We propose starting with a **modular monolith**, rather than
microservices or a collection of independent agents. This gives the
project clear module boundaries without introducing unnecessary
distributed-system complexity during the hackathon.

``` mermaid
flowchart TB
    U["Finance User"] --> UI["PayPilot Dashboard"]
    UI --> API["Backend API"]

    API --> ING["Ingestion and Extraction"]
    API --> WF["Workflow Orchestrator"]

    ING --> DB[("Persistent Database")]
    WF --> DB

    WF --> VEN["Vendor Verification"]
    WF --> DUP["Duplicate Detection"]
    WF --> REC["Reconciliation Engine"]
    WF --> RISK["Risk and Decision Engine"]

    VEN --> CR["Crustdata API"]
    VEN --> WEB["Web Search and Fetch"]

    DUP --> DB
    REC --> DB
    RISK --> DB

    RISK --> AP["Approval Gate"]
    AP --> PAY["Payment Provider - Test Mode"]
    PAY --> DB

    WF --> LED["Audit and Cost Ledger"]
    VEN --> LED
    DUP --> LED
    REC --> LED
    RISK --> LED
    PAY --> LED

    LED --> DB
    DB --> UI
```

### Proposed modules

  -----------------------------------------------------------------------
  Module                              Responsibility
  ----------------------------------- -----------------------------------
  Frontend                            Dashboard, invoice views, approvals
                                      and audit interface

  Backend API                         Accept requests and coordinate
                                      workflows

  Ingestion and extraction            Parse invoices and bank statements

  Vendor verification                 Company identification and external
                                      evidence collection

  Duplicate detection                 Detect repeated invoices and
                                      transactions

  Reconciliation                      Match invoices, bank entries and
                                      payments

  Decision engine                     Apply deterministic rules

  Approval gate                       Prevent unauthorized payment
                                      execution

  Payment adapter                     Execute approved payments in test
                                      mode

  Persistence                         Store workflow and payment state

  Audit and cost ledger               Record evidence, decisions, API
                                      calls, model calls and costs
  -----------------------------------------------------------------------

### Workflow orchestrator

The workflow orchestrator is the central component. It must know the
current state of every invoice and resume safely after a process
restart.

The workflow must not depend on an LLM conversation remembering what has
already happened. State transitions, payment attempts and approvals must
be persisted.

## 6. Dashboard proposal

The dashboard should function as a finance operations control center.
Its first screen should quickly answer:

-   What needs attention?
-   How much money is waiting to be processed?
-   Are there suspicious or duplicate transactions?
-   Which payments are awaiting approval?
-   What has been processed recently?

### Proposed navigation

  -----------------------------------------------------------------------
  Page                                Purpose
  ----------------------------------- -----------------------------------
  **Overview**                        KPIs, alerts, priority queue and
                                      recent activity

  **Invoices**                        All invoices, statuses, extraction
                                      and reconciliation

  **Vendors**                         Business identities, bank details
                                      and verification records

  **Approvals**                       Payments awaiting human
                                      authorization

  **Audit & Activity**                Sources, decisions, retries,
                                      payment events and costs

  **Settings**                        Rules, limits, integrations and
                                      test-mode configuration
  -----------------------------------------------------------------------

### Overview dashboard --- illustrative layout

The following figures are placeholders for a design mock-up, not actual
client records.

  Metric                 Example
  ------------------ -----------
  Pending invoices            24
  Pending amount       ₹8.4 lakh
  Flagged invoices             3
  Processed today      ₹2.1 lakh

The priority queue could display:

  Vendor                   Invoice           Amount Status
  ------------------------ -------------- --------- --------------------
  ABC Transport Services   INV-2026-104     ₹48,000 Possible duplicate
  Sharma Logistics         INV-2026-108     ₹32,500 Bank mismatch
  NorthStar Carriers       INV-2026-111     ₹76,000 Awaiting approval

### Invoice investigation view

The invoice detail page is the most important operational screen. It
should show:

-   Original invoice and extracted fields.
-   Vendor identity and external company information.
-   Invoice-to-bank-statement reconciliation.
-   Duplicate detection results.
-   Risk flags and the rules that triggered them.
-   Source links and exact supporting fields.
-   Processing timeline and workflow status.
-   Payment approval controls.

The purpose is to demonstrate that PayPilot performed an investigation,
not merely generated a summary.

## 7. Persistence and data model

Workflow state must be stored persistently. It must not exist only in
application memory.

  -----------------------------------------------------------------------
  Entity                              Important fields / purpose
  ----------------------------------- -----------------------------------
  `Invoice`                           ID, vendor ID, invoice number,
                                      amount, date, status

  `Vendor`                            ID, legal name, domain, company ID,
                                      bank-account reference

  `BankTransaction`                   ID, date, amount, description,
                                      payee

  `Payment`                           ID, invoice ID, idempotency key,
                                      provider reference, status

  `Evidence`                          ID, entity ID, source URL,
                                      retrieval date, claim, supporting
                                      data

  `Approval`                          ID, payment ID, reviewer, decision,
                                      timestamp

  `WorkflowRun`                       ID, invoice ID, current stage,
                                      status, retry count

  `AuditEvent`                        ID, workflow ID, event type,
                                      timestamp, details

  `CostRecord`                        ID, workflow ID, API/model name,
                                      usage, estimated cost
  -----------------------------------------------------------------------

The exact schema will be refined after the organizers release the
kickoff input and output formats.

### Crash recovery

Every workflow stage must persist its result and state transition. If
the process crashes during payment execution, the restarted process must
inspect stored state and reconcile with the payment provider before
attempting another payment.

### Idempotency

Every payment attempt must use a stable idempotency key. Retrying the
same logical operation must not create another payment.

The payment adapter should distinguish between: - Payment not yet
attempted. - Payment request sent, outcome unknown. - Payment confirmed
successful. - Payment failed. - Payment held for manual investigation.

An unknown outcome must not automatically trigger another payment
attempt.

## 8. Data sources and integrations

### Crustdata

The proposed Crustdata workflow is:

1.  Company Identify --- resolve a vendor name or domain to a company.
2.  Company Enrich --- retrieve available company information such as
    headcount, funding, leadership and web traffic.
3.  Web Search and Fetch --- gather relevant public information and
    retrieve supporting pages.

Crustdata is an external source of company intelligence. It does not by
itself prove bank-account ownership, invoice validity, payment history
or the legitimacy of a particular transaction.

### Other sources

Depending on the kickoff data and available integrations, the system may
use:

-   Organizer-provided invoice and bank-statement records.
-   Existing vendor and payment records.
-   Public company or government registry records.
-   Public web pages.
-   A payment provider's test-mode API.

Each source must be recorded with the exact relevant field or passage
and its date. If a source cannot establish a fact, the system should say
**cannot verify** and explain what is missing.

## 9. Decision engine and safety rules

The language model may assist with document extraction and interpreting
unstructured evidence. It should not be the authority for payment
safety.

Deterministic application code should enforce:

-   Required-field checks.
-   Vendor identity matching rules.
-   Duplicate invoice detection.
-   Duplicate transaction detection.
-   Bank-detail mismatch checks.
-   Reconciliation rules.
-   Approval requirements.
-   Payment idempotency.
-   Retry and recovery rules.

### Human approval

Payments are irreversible actions and must wait for human approval. The
system can prepare a payment, explain its supporting evidence and
request approval, but must not execute it without authorization.

### Uncertainty

A missing source, ambiguous company match, conflicting record or API
failure must not be silently treated as a successful verification. The
system should hold the invoice or return cannot verify.

## 10. Initial implementation priorities

### P0 --- Core correctness

  -----------------------------------------------------------------------
  Component                           Definition of done
  ----------------------------------- -----------------------------------
  Invoice ingestion                   Load and structure an invoice

  Vendor identification               Identify payee or return cannot
                                      verify

  Duplicate detection                 Catch exact and likely duplicate
                                      invoices

  Reconciliation                      Match invoices against bank entries

  Decision engine                     Produce consistent
                                      ready-for-approval / hold outcomes

  Persistent workflow                 Restart does not lose progress

  Approval gate                       No payment executes without
                                      authorization
  -----------------------------------------------------------------------

### P1 --- Complete service and demonstration

  -----------------------------------------------------------------------
  Component                           Definition of done
  ----------------------------------- -----------------------------------
  Payment test mode                   Approved payment executes once

  Evidence ledger                     Material findings have supporting
                                      sources

  Dashboard                           User can inspect and operate the
                                      workflow

  Cost ledger                         Each API/model call and its cost
                                      are recorded

  Robustness tests                    Process restart and retries are
                                      tested

  README and architecture diagram     Repository explains the service and
                                      its design
  -----------------------------------------------------------------------

We should prioritize a correct, end-to-end workflow over visual polish.

## 11. Proposed team division

The following is a suggested ownership split for three builders. It
should be adjusted based on the team's skills and availability.

### Builder 1 --- Backend and workflow

-   Backend API and database.
-   Invoice lifecycle and workflow orchestration.
-   Persistent state and crash recovery.
-   Payment state and idempotency.

### Builder 2 --- Frontend

-   Dashboard and navigation.
-   Invoice investigation view.
-   Approval queue.
-   Audit and activity interface.

### Builder 3 --- Intelligence and verification

-   Crustdata integration.
-   Invoice extraction.
-   Duplicate detection and reconciliation.
-   Evidence collection and test cases.

These are ownership areas, not isolated systems. All builders must agree
on the shared data structures and API contracts before parallel
implementation.

## 12. Proposed technology stack

These are initial recommendations, not yet final team decisions.

  -----------------------------------------------------------------------
  Layer                   Proposed choice         Reason
  ----------------------- ----------------------- -----------------------
  Frontend                Next.js + React         Dashboard and
                                                  interactive views

  Backend                 Python FastAPI          API and workflow
                                                  implementation

  Database                SQLite                  Simple persistent
                                                  storage for a short
                                                  prototype

  Company intelligence    Crustdata API           Company identification
                                                  and enrichment

  Web evidence            Web Search and Fetch    Public evidence
                                                  collection

  LLM                     Team-selected model     Extraction and
                                                  interpretation of
                                                  unstructured
                                                  information

  Payment                 Payment provider test   Demonstrate controlled
                          mode                    payment execution
  -----------------------------------------------------------------------

The team should confirm the stack before implementation. The kickoff
input/output formats may also affect the ingestion design.

## 13. What success looks like

Given invoices and relevant financial records, PayPilot should:

1.  Correctly identify the payee or explicitly say it cannot verify the
    identity.
2.  Extract invoice details into a structured format.
3.  Detect duplicate invoices and duplicate transactions.
4.  Identify suspicious bank-detail changes and inconsistencies.
5.  Reconcile invoices against available bank records.
6.  Explain each important finding using a source and date.
7.  Hold uncertain or suspicious cases for investigation.
8.  Require human approval before payment.
9.  Execute approved payments exactly once, including across process
    restarts.
10. Record workflow events, API/model calls and processing costs.

### Proposed pass mark

An invoice-processing run should pass only when the system returns the
correct outcome for the supplied case, supports material claims with the
appropriate source records, avoids unauthorized or duplicate payments,
and preserves a recoverable audit trail.

The precise test cases and expected output format must be aligned with
the organizers' kickoff kit.

## 14. Risks and open questions

  -----------------------------------------------------------------------
  Risk / question         Why it matters          Proposed response
  ----------------------- ----------------------- -----------------------
  Kickoff input format    Determines ingestion    Keep ingestion adapters
  unknown                 and schema              separate from the core
                                                  workflow

  Bank-account            Company data does not   Use available
  verification data may   prove account ownership authoritative records;
  be unavailable                                  otherwise hold / cannot
                                                  verify

  Payment provider        Payment execution is    Build a narrow
  integration may take    part of the service     test-mode adapter and
  time                                            prioritize idempotency

  OCR may be unreliable   Bad extraction can      Validate required
                          corrupt downstream      fields and provide
                          decisions               manual review

  Company name ambiguity  Namesakes can cause     Use domain and multiple
                          wrong-payee decisions   identity signals; hold
                                                  ambiguous matches

  API or model failure    Workflow must not       Persist progress, retry
                          silently skip a check   safely, or hold for
                                                  review

  Cost ceiling            External calls and      Record usage and
                          model usage consume     enforce per-run limits
                          budget                  

  Requirement change at   The system must adapt   Keep rules, limits and
  hour 3                  during the build        output schema
                                                  configurable
  -----------------------------------------------------------------------

## 15. Team approval checklist

Before implementation, the team should review and approve:

-   [ ] We agree that PayPilot is an accounts-payable service for a 3PL
    logistics company.
-   [ ] We agree on the invoice lifecycle and the distinction between
    approval and payment execution.
-   [ ] We agree on the proposed modular-monolith architecture.
-   [ ] We agree on the dashboard pages and invoice investigation view.
-   [ ] We agree that workflow state must persist across restarts.
-   [ ] We agree that payment execution must be idempotent and require
    human approval.
-   [ ] We agree on the initial technology stack.
-   [ ] We agree on module ownership and shared data contracts.
-   [ ] We will revise the design against the organizers' kickoff
    input/output formats.

------------------------------------------------------------------------

## References and design basis

This draft is based on the ContextCon 05 official problem statement and
the team's initial PayPilot planning discussions.

The official problem statement requires a service that completes a paid
job end-to-end, uses live data for at least one decision, links facts to
exact source records and dates, requires approval for irreversible
actions, records call costs, and handles hidden test cases including
duplicates, lookalike names and cases where the correct answer is cannot
verify.

It also specifies a seven-hour build, a requirement change at hour
three, process termination and restart during finals, and evaluation
across live run, robustness, design, ethics and financial feasibility.

**This README is a proposal for team review, not a claim that all listed
features have already been implemented.**
