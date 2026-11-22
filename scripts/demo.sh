#!/usr/bin/env bash
# PayPilot demo storyboard (A14). Runs unattended; DEMO_PAUSE=1 waits for Enter between scenes.
# Uses the FakeProvider (no real money). A throwaway demo root (.demo/) is built from clients/northbay with REAL IFSC codes
# substituted for the ‹IFSC-x› placeholders, so the payee bank checks can pass. Nothing under clients/ is modified.
set -uo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
[ -d .venv ] && source .venv/bin/activate
PP="python -m paypilot"
export PAYPILOT_ROOT="$REPO/.demo"
export BANK_HMAC_KEY="${BANK_HMAC_KEY:-demo-hmac-key-not-a-secret}"
unset PAYPILOT_CRASH_AT PAYPILOT_FAKE_FAULT

scene() { echo; echo "════════════════════════════════════════════════════════════════"; echo "  $*"; echo "════════════════════════════════════════════════════════════════"; [ "${DEMO_PAUSE:-0}" = 1 ] && read -r -p "  [Enter] " _ ; true; }
run()   { echo "\$ $*"; "$@"; local rc=$?; echo "  (exit $rc)"; return 0; }
sub()   { sed -e 's/‹IFSC-A›/HDFC0000001/g; s/‹IFSC-B›/ICIC0000001/g; s/‹IFSC-C›/UTIB0000001/g; s/‹IFSC-D›/HDFC0000001/g' "$1"; }

# ---- set up a clean demo root ----
rm -rf .demo && mkdir -p .demo/clients/northbay .demo/scenarios
cp config.yaml .demo/config.yaml
[ -f .env ] && ln -s ../.env .demo/.env      # real keys (BANK_HMAC_KEY, RAZORPAYX_*) are picked up if present
for f in clients/northbay/*; do
  case "$f" in *.csv|*.yaml) sub "$f" > ".demo/$f" ;; esac
done
cp -r scripts/demo_data/pay-invoices .demo/scenarios/
sub scripts/demo_data/pay-invoices/invoices.json > .demo/scenarios/pay-invoices/invoices.json
cp -r scenarios/SC-01 .demo/scenarios/ 2>/dev/null || true

scene "1/8  Bookkeeping: post October's bank lines (no payments yet)"
run $PP run --scenario .demo/scenarios/SC-01

scene "2/8  Payables: three invoices. A duplicate is HELD, the others wait for a human"
run $PP run --scenario .demo/scenarios/pay-invoices
run $PP approvals
run $PP show INV-202

scene "3/8  Refusals: a never-overridable flag cannot be approved; an unknown approver is refused"
run $PP approve INV-202 --by "Finance Controller"
run $PP approve INV-201 --by "Some Stranger"

scene "4/8  Approve INV-201 -> paid once through the provider; a second approve is a no-op"
run $PP approve INV-201 --by "Finance Controller"
run $PP approve INV-201 --by "AP Manager"

scene "5/8  kill -9 in the middle of a payment: the provider already created it, the local record never saw the reference"
PAYPILOT_CRASH_AT=after_create_response_before_ref $PP approve INV-203 --by "AP Manager"; echo "  (exit $? : killed, like kill -9)"
run $PP resume
run $PP fake-provider list
echo "  -> INV-203 was reconciled with the SAME key: exactly one provider payment, no double pay."

scene "6/8  The bank returns INV-201's money AFTER the provider confirmed it"
REF=$(python - <<PY
import sqlite3, os
c = sqlite3.connect(os.path.join(os.environ["PAYPILOT_ROOT"], "var/paypilot.db"))
print(c.execute("SELECT a.provider_ref FROM payment_attempts a JOIN payments p ON p.payment_id=a.payment_id JOIN items i ON i.item_id=p.item_id WHERE i.source_ref LIKE '%INV-201'").fetchone()[0])
PY
)
run $PP fake-provider set "$REF" RETURNED
run $PP resume
run $PP exceptions --open
run $PP approve INV-201 --by "Finance Controller"

scene "7/8  Reconcile the bank statement: matched, duplicate debit, returned payment, unknown debit"
mkdir -p .demo/scenarios/reconcile
python - <<PY
import json, os, sqlite3
root = os.environ["PAYPILOT_ROOT"]
c = sqlite3.connect(os.path.join(root, "var/paypilot.db"))
ref = c.execute("SELECT a.provider_ref FROM payment_attempts a JOIN payments p ON p.payment_id=a.payment_id JOIN items i ON i.item_id=p.item_id WHERE i.source_ref LIKE '%INV-203' AND a.provider_ref IS NOT NULL").fetchone()[0]
line = lambda i, d, desc, amt, ref: {"line_id": i, "date": d, "description": desc, "amount_inr": amt, "direction": "DEBIT", "currency": "INR", "bank_reference": ref}
json.dump([line("ST-701", "2026-11-22", "VRL LOGISTICS PAYOUT", "60000.00", ref),
           line("ST-702", "2026-11-22", "VRL LOGISTICS PAYOUT", "60000.00", ref),
           line("ST-703", "2026-11-12", "BLUE DART RETURNED", "22500.00", "hist-0091"),
           line("ST-704", "2026-11-14", "UNKNOWN BENEFICIARY", "999.00", "UTR-UNMATCHED")],
          open(os.path.join(root, "scenarios/reconcile/statement.json"), "w"), indent=1)
json.dump({"schema_version": "1.0", "request_id": "northbay-nov-recon-001", "client_id": "northbay", "operation": "RECONCILE",
           "instruction": "Reconcile the November bank statement to payments.", "period": {"from": "2026-11-01", "to": "2026-11-30"},
           "inputs": {"bank_statement": "statement.json"}}, open(os.path.join(root, "scenarios/reconcile/request.json"), "w"), indent=1)
PY
run $PP run --scenario .demo/scenarios/reconcile
run $PP exceptions --open

scene "8/8  The books must replay: decisions, approvals, payments, journal, audit chain"
run $PP audit verify
run $PP ledger
echo; echo "Outputs: $PAYPILOT_ROOT/out/ (results.jsonl, report.md, journal.csv, payment_instructions.csv, audit.jsonl ...)"
