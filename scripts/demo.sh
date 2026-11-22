#!/usr/bin/env bash
# PayPilot live demo (A14). One storyboard, 21 scenes, built to be filmed next to the dashboard.
#
#   python scripts/dashboard.py --db .demo/var/paypilot.db     # terminal 1: open http://127.0.0.1:8765 first, it reads zero
#   DEMO_PAUSE=1 bash scripts/demo.sh                          # terminal 2: press Enter to start each scene
#
#   DEMO_PAUSE=1   wait for Enter before each scene (default: run on a timer)
#   DEMO_BEAT=4    seconds to hold after each scene so the dashboard (it polls every 2s) catches the change. 0 = no wait
#
# Uses the FakeProvider (our own simulation, no real money). A throwaway root (.demo/) is built from clients/northbay and the
# official scenarios, with REAL IFSC codes substituted for the placeholders so the live bank check can pass. Nothing under
# clients/ or scenarios/ is modified.
set -uo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
[ -d .venv ] && source .venv/bin/activate
PP="python -m paypilot"
export PAYPILOT_ROOT="$REPO/.demo"
export BANK_HMAC_KEY="${BANK_HMAC_KEY:-demo-hmac-key-not-a-secret}"
unset PAYPILOT_CRASH_AT PAYPILOT_FAKE_FAULT
BEAT="${DEMO_BEAT:-4}"
DB="$PAYPILOT_ROOT/var/paypilot.db"

# ---------- helpers ----------
scene() {   # scene "n/N  Title" "what to watch on the dashboard"
  echo; echo "════════════════════════════════════════════════════════════════════════"
  echo "  $1"; echo "════════════════════════════════════════════════════════════════════════"
  [ -n "${2:-}" ] && echo "  DASHBOARD  $2"
  [ "${DEMO_PAUSE:-0}" = 1 ] && read -r -p "  [Enter to run] " _
  true
}
run()  { echo "\$ $*"; "$@"; local rc=$?; echo "  (exit $rc)"; return 0; }
beat() { [ "$BEAT" -gt 0 ] && sleep "$BEAT"; true; }
say()  { echo "  -> $*"; }
sub()  { sed -e 's/‹IFSC-A›/HDFC0000001/g; s/‹IFSC-B›/ICIC0000001/g; s/‹IFSC-C›/UTIB0000001/g; s/‹IFSC-D›/HDFC0000001/g; s/‹IFSC-INVALID›/ABCD0999999/g' "$1"; }
q()    { python - "$@" <<'PY'
import os, sqlite3, sys
c = sqlite3.connect(os.path.join(os.environ["PAYPILOT_ROOT"], "var/paypilot.db"))
r = c.execute(sys.argv[1], sys.argv[2:]).fetchone()
print(r[0] if r and r[0] is not None else "")
PY
}
provider_ref() { q "SELECT a.provider_ref FROM payment_attempts a JOIN payments p ON p.payment_id=a.payment_id JOIN items i ON i.item_id=p.item_id WHERE i.source_ref LIKE ? AND a.provider_ref IS NOT NULL ORDER BY a.attempt_no DESC LIMIT 1" "%#$1"; }
open_exception() { q "SELECT e.exception_id FROM exceptions e JOIN items i ON i.item_id=e.item_id WHERE i.source_ref LIKE ? AND e.type=? AND e.status NOT IN ('RESOLVED','FALSE_POSITIVE','ACCEPTED_RISK','CLOSED_NO_ACTION') LIMIT 1" "%#$1" "$2"; }

# ---------- a clean demo root ----------
echo "PayPilot live demo. Open the dashboard first:  python scripts/dashboard.py --db .demo/var/paypilot.db"
rm -rf .demo && mkdir -p .demo/clients/northbay .demo/scenarios
cp config.yaml .demo/config.yaml
[ -f .env ] && ln -s ../.env .demo/.env      # real keys (BANK_HMAC_KEY, RAZORPAYX_*) are picked up if present
for f in clients/northbay/*; do case "$f" in *.csv|*.yaml) sub "$f" > ".demo/$f" ;; esac; done
# extra purchase orders for the payment-failure scenes (the fictional client's records; clients/ itself is untouched)
cat >> .demo/clients/northbay/purchase_orders.csv <<'CSV'
PO-NB-2001,V-0003,15000.00,,OPEN,2026-11-02
PO-NB-2002,V-0005,18500.00,,OPEN,2026-11-02
PO-NB-2003,V-0004,9800.00,,OPEN,2026-11-02
PO-NB-2004,V-0006,41000.00,,OPEN,2026-11-02
PO-NB-2005,V-0006,27500.00,,OPEN,2026-11-02
PO-NB-2006,V-0003,33000.00,,OPEN,2026-11-02
CSV
for sc in SC-01 SC-02 SC-03 SC-04 SC-10 SC-11; do
  mkdir -p ".demo/scenarios/$sc"
  for f in scenarios/$sc/*; do case "$f" in */expected*) ;; *) sub "$f" > ".demo/scenarios/$sc/$(basename "$f")" ;; esac; done
done
mkdir -p .demo/scenarios/lab
for f in scripts/demo_data/lab/*; do sub "$f" > ".demo/scenarios/lab/$(basename "$f")"; done
mkdir -p .demo/var; : > /dev/null
echo "  The dashboard should now read zero. Hold on that for the camera."
beat

# ======================= ACT I: the books =======================
scene "1/21  Post October's bank lines" \
      "Live board fills with 4 bank lines (2 Posted, 1 Held, 1 Cannot verify). A run appears: Post bank lines. Books shows 2 journal entries."
run $PP run --scenario .demo/scenarios/SC-01
beat

scene "2/21  Bank-line edge cases: wrong month, a credit, and two vendors with the same name" \
      "A second Post bank lines run. One more Posted, two Held (out of period, a credit) and one Cannot verify (tie between two vendors)."
run $PP run --scenario .demo/scenarios/SC-02
beat

# ======================= ACT II: paying invoices =======================
scene "3/21  Six carrier invoices arrive" \
      "A Pay invoices run. Only INV-201 needs approval. Already-paid, price mismatch, no PO, look-alike vendor and missing number are all stopped. Approvals badge becomes 1."
run $PP run --scenario .demo/scenarios/SC-03
beat

scene "4/21  Why was the look-alike vendor stopped?" \
      "No change. Open Payments, then INV-204, to see the same reasoning."
run $PP show INV-204
run $PP approvals

scene "5/21  Refusals: a never-overridable flag, a stranger, and an item that cannot be verified" \
      "No change at all. Refusing is safe, and nothing moves."
run $PP approve INV-202 --by "Finance Controller"
run $PP approve INV-201 --by "Some Stranger"
run $PP approve INV-204 --by "Finance Controller"
beat

scene "6/21  Approve INV-201: paid once. A second approval changes nothing" \
      "INV-201 flips Needs approval to Paid. Paid to vendors rises by 48,000. Books gains an accrual and a payment entry."
run $PP approve INV-201 --by "Finance Controller"
run $PP approve INV-201 --by "AP Manager"
beat

scene "7/21  Override a price mismatch with a written reason" \
      "INV-203 flips Held to Paid. Approvals lists the override and who made it."
run $PP approve INV-203 --by "Finance Controller" --override PO_AMOUNT_MISMATCH --reason "Fuel surcharge agreed by phone; PO to be amended"
beat

scene "8/21  Reject an invoice outright" \
      "INV-206 flips Held to Rejected. The audit trail records the reason."
run $PP reject INV-206 --by "AP Manager" --reason "No purchase order on file"
beat

# ======================= ACT III: when payments go wrong =======================
scene "9/21  A second batch: six more invoices for the failure scenes" \
      "Another Pay invoices run. Six new rows, all Needs approval. Waiting for approval jumps by 144,800."
run $PP run --scenario .demo/scenarios/lab
LAB=$(q "SELECT run_id FROM runs WHERE request_id='northbay-nov-payment-lab-001'")   # later runs become "most recent", so pin this one
beat

scene "10/21  The process is killed mid-payment (LAB-01)" \
      "LAB-01 shows In flight while the process is dead, then Paid after resume. Exactly one provider payment."
PAYPILOT_CRASH_AT=after_create_response_before_ref $PP approve LAB-01 --by "Finance Controller"; echo "  (exit $? : killed, like kill -9)"
beat
run $PP resume
run $PP fake-provider list
say "LAB-01 was reconciled with the SAME key: one provider payment, no double pay."
beat

scene "11/21  The provider accepts the payment, then the reply is lost (LAB-02)" \
      "LAB-02 shows Outcome unknown, with a HIGH exception, then Paid after resume. The system never guessed."
PAYPILOT_FAKE_FAULT=TIMEOUT_AFTER_CREATE run $PP approve LAB-02 --by "Finance Controller"
beat
run $PP resume
beat

scene "12/21  The provider says 'still processing' (LAB-03)" \
      "LAB-03 shows In flight and does not count as paid. It turns Paid only after the provider confirms."
PAYPILOT_FAKE_FAULT=PROCESSING run $PP approve LAB-03 --by "AP Manager"
beat
REF=$(provider_ref LAB-03)
run $PP fake-provider set "$REF" SUCCEEDED
run $PP resume
beat

scene "13/21  The provider declines, and a fresh approval is needed to retry (LAB-04)" \
      "LAB-04 shows Declined with a PAYMENT_FAILED exception. After a second approval it turns Paid on attempt 2."
PAYPILOT_FAKE_FAULT=DECLINE run $PP approve LAB-04 --by "Finance Controller"
beat
run $PP approve LAB-04 --by "Finance Controller"
beat

scene "14/21  The bank returns LAB-04's money after it was confirmed" \
      "LAB-04 flips Paid to Returned. A CRITICAL exception appears and Indian Oil is contained. Returned and Exceptions both rise."
REF=$(provider_ref LAB-04)
run $PP fake-provider set "$REF" RETURNED
run $PP resume
run $PP exceptions --open
beat

scene "15/21  Containment: Indian Oil's next invoice is refused until the return is resolved (LAB-05)" \
      "Refusal changes nothing. Then, once the exception is resolved with evidence, LAB-05 flips Needs approval to Paid."
run $PP approve LAB-05 --by "Finance Controller"
EXC=$(open_exception LAB-04 PAYMENT_RETURNED)
run $PP resolve-exception "$EXC" --by "Finance Controller" --resolution RECOVERED --note "Bank refunded the returned payout" --evidence "BANK-REFUND-UTR-88231"
beat
run $PP approve LAB-05 --by "Finance Controller"
beat

# ======================= ACT IV: duplicates and fraud =======================
scene "16/21  The same invoice arrives again in a different file" \
      "A new Pay invoices run with one row that flips straight to Held. No payment is created."
run $PP run --scenario .demo/scenarios/SC-11
beat

scene "17/21  A vendor 'changes its bank account': the classic fraud" \
      "A new run, three Held rows, and Delhivery is contained. Exceptions and the Exceptions badge climb."
run $PP run --scenario .demo/scenarios/SC-04
beat

scene "18/21  Delhivery is now contained: even an approved invoice cannot be paid (LAB-06)" \
      "Refused. LAB-06 flips Needs approval to Held. Nothing is paid to a vendor under suspicion."
run $PP approve LAB-06 --by "Finance Controller" --run "$LAB"
beat

scene "19/21  A request the system cannot do" \
      "A run called Unsupported request with 0 items and one exception. It says so instead of guessing."
run $PP run --scenario .demo/scenarios/SC-10
beat

# ======================= ACT V: reconcile and prove =======================
scene "20/21  Reconcile the bank statement against what we paid" \
      "A Reconcile statement run: one Matched, one Duplicate debit, one Returned, one Unmatched. Two more CRITICAL exceptions."
mkdir -p .demo/scenarios/reconcile
python - <<PY
import json, os, sqlite3
root = os.environ["PAYPILOT_ROOT"]
c = sqlite3.connect(os.path.join(root, "var/paypilot.db"))
ref = c.execute("SELECT a.provider_ref FROM payment_attempts a JOIN payments p ON p.payment_id=a.payment_id JOIN items i ON i.item_id=p.item_id WHERE i.source_ref LIKE '%INV-201' AND a.provider_ref IS NOT NULL").fetchone()[0]
line = lambda i, d, desc, amt, ref: {"line_id": i, "date": d, "description": desc, "amount_inr": amt, "direction": "DEBIT", "currency": "INR", "bank_reference": ref}
json.dump([line("ST-701", "2026-11-22", "DELHIVERY PAYOUT", "48000.00", ref),
           line("ST-702", "2026-11-22", "DELHIVERY PAYOUT", "48000.00", ref),
           line("ST-703", "2026-11-12", "BLUE DART RETURNED", "22500.00", "hist-0091"),
           line("ST-704", "2026-11-14", "UNKNOWN BENEFICIARY", "999.00", "UTR-UNMATCHED")],
          open(os.path.join(root, "scenarios/reconcile/statement.json"), "w"), indent=1)
json.dump({"schema_version": "1.0", "request_id": "northbay-nov-recon-001", "client_id": "northbay", "operation": "RECONCILE",
           "instruction": "Reconcile the November bank statement to payments.", "period": {"from": "2026-11-01", "to": "2026-11-30"},
           "inputs": {"bank_statement": "statement.json"}}, open(os.path.join(root, "scenarios/reconcile/request.json"), "w"), indent=1)
PY
run $PP run --scenario .demo/scenarios/reconcile
run $PP exceptions --open
beat

scene "21/21  The books must replay: decisions, approvals, payments, journal, audit chain" \
      "Books: Trial balance reads Debits equal credits. Walk Payments, Exceptions, Approvals and Books to show every case."
run $PP audit verify
run $PP ledger
echo; echo "Outputs: $PAYPILOT_ROOT/out/ (results.jsonl, report.md, journal.csv, payment_instructions.csv, audit.jsonl ...)"
