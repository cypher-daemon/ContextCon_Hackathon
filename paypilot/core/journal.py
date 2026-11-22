"""Double-entry journal (ARCHITECTURE §7.1). Owner: lane A.

Every financial effect is ONE balanced entry written in the SAME transaction as the state change it records.
Kinds (one per item, UNIQUE(item_id, kind)):
  BANK_LINE_POSTING  Dr <expense account>        / Cr Bank
  INVOICE_ACCRUAL    Dr <expense account>        / Cr Accounts payable - <vendor>     (at approval: invoice accepted for payment)
  INVOICE_PAYMENT    Dr Accounts payable - <v>   / Cr Bank - payments clearing        (provider-confirmed terminal success)
  REVERSAL           mirror of INVOICE_PAYMENT   (reversal after success; there is no retry)
"""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime
from pathlib import Path

from paypilot.core import ids
from paypilot.core.fsutil import atomic_write_text
from paypilot.schemas import BankLine, Decision, Invoice, JournalEntry, JournalLine

BANK = "Bank"
CLEARING = "Bank - payments clearing"


def ap_account(vendor_id: str) -> str:
    return f"Accounts payable - {vendor_id}"


class JournalError(Exception):
    """A malformed or unbalanced entry. Nothing is written."""


def post_entry(conn: sqlite3.Connection, entry: JournalEntry) -> None:
    """Write an entry + lines. Must run inside the caller's transaction; asserts balance BEFORE anything is committed."""
    if not conn.in_transaction:
        raise RuntimeError("journal.post_entry must run inside the state-change transaction")
    if len(entry.lines) < 2:
        raise JournalError("an entry needs at least two lines")
    for l in entry.lines:
        if (l.debit_paise > 0) == (l.credit_paise > 0):
            raise JournalError(f"line {l.line_no}: exactly one of debit/credit must be positive")
    dr, cr = sum(l.debit_paise for l in entry.lines), sum(l.credit_paise for l in entry.lines)
    if dr != cr:
        raise JournalError(f"entry {entry.entry_id} is unbalanced: Dr {dr} != Cr {cr}")
    conn.execute(
        "INSERT INTO journal_entries(entry_id,item_id,kind,vendor_id,decision_hash,approval_id,attempt_key,currency,memo,posted_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (entry.entry_id, entry.item_id, entry.kind, entry.vendor_id, entry.decision_hash, entry.approval_id,
         entry.attempt_key, entry.currency, entry.memo, entry.posted_at.isoformat()))
    conn.executemany("INSERT INTO journal_lines(entry_id,line_no,account,debit_paise,credit_paise) VALUES (?,?,?,?,?)",
                     [(entry.entry_id, l.line_no, l.account, l.debit_paise, l.credit_paise) for l in entry.lines])


def _entry(item_id: str, kind: str, decision_hash: str, vendor_id: str | None, currency: str, memo: str, now: datetime,
           dr_account: str, cr_account: str, amount: int, approval_id: str | None = None,
           attempt_key: str | None = None) -> JournalEntry:
    if amount is None or amount <= 0:
        raise JournalError(f"{kind}: amount must be positive, got {amount}")
    return JournalEntry(entry_id=ids.sha256_hex("journal", item_id, kind)[:16], item_id=item_id, kind=kind,
                        vendor_id=vendor_id, decision_hash=decision_hash, approval_id=approval_id, attempt_key=attempt_key,
                        currency=currency, memo=memo, posted_at=now,
                        lines=[JournalLine(line_no=1, account=dr_account, debit_paise=amount),
                               JournalLine(line_no=2, account=cr_account, credit_paise=amount)])


def bank_line_posting(item: BankLine, decision: Decision, now: datetime) -> JournalEntry:
    if item.direction != "DEBIT":
        raise JournalError("only DEBIT bank lines are posted (credits are held by the posting check)")
    if not decision.account:
        raise JournalError("cannot post without an account")
    return _entry(item.item_id, "BANK_LINE_POSTING", decision.decision_hash, decision.facts.vendor_id, item.currency,
                  item.descriptor_raw, now, decision.account, BANK, item.amount_paise)


def invoice_accrual(item: Invoice, decision: Decision, approval_id: str, now: datetime) -> JournalEntry:
    if not decision.account or not decision.facts.vendor_id:
        raise JournalError("cannot accrue without an account and a vendor")
    return _entry(item.item_id, "INVOICE_ACCRUAL", decision.decision_hash, decision.facts.vendor_id, item.currency,
                  f"Invoice {item.invoice_no_raw}", now, decision.account, ap_account(decision.facts.vendor_id),
                  item.amount_paise, approval_id=approval_id)


def invoice_payment(item: Invoice, decision: Decision, approval_id: str, attempt_key: str, now: datetime) -> JournalEntry:
    if not decision.facts.vendor_id:
        raise JournalError("cannot pay without a vendor")
    return _entry(item.item_id, "INVOICE_PAYMENT", decision.decision_hash, decision.facts.vendor_id, item.currency,
                  f"Payout for invoice {item.invoice_no_raw}", now, ap_account(decision.facts.vendor_id), CLEARING,
                  item.amount_paise, approval_id=approval_id, attempt_key=attempt_key)


def reversal_of_payment(conn: sqlite3.Connection, item_id: str, now: datetime) -> JournalEntry:
    """Mirror of the item's INVOICE_PAYMENT: Dr Bank-clearing / Cr AP. Same approval and attempt links."""
    row = conn.execute("SELECT * FROM journal_entries WHERE item_id=? AND kind='INVOICE_PAYMENT'", (item_id,)).fetchone()
    if row is None:
        raise JournalError(f"item {item_id} has no INVOICE_PAYMENT entry to reverse")
    lines = conn.execute("SELECT * FROM journal_lines WHERE entry_id=? ORDER BY line_no", (row["entry_id"],)).fetchall()
    mirrored = [JournalLine(line_no=l["line_no"], account=l["account"], debit_paise=l["credit_paise"],
                            credit_paise=l["debit_paise"]) for l in lines]
    return JournalEntry(entry_id=ids.sha256_hex("journal", item_id, "REVERSAL")[:16], item_id=item_id, kind="REVERSAL",
                        vendor_id=row["vendor_id"], decision_hash=row["decision_hash"], approval_id=row["approval_id"],
                        attempt_key=row["attempt_key"], currency=row["currency"], memo="Reversal of " + row["memo"],
                        posted_at=now, lines=mirrored)


def trial_balance(conn: sqlite3.Connection, run_id: str | None = None) -> dict[str, int]:
    """account -> debits - credits (paise). The values always sum to zero."""
    sql = ("SELECT l.account, SUM(l.debit_paise) - SUM(l.credit_paise) AS net FROM journal_lines l "
           "JOIN journal_entries e ON e.entry_id = l.entry_id JOIN items i ON i.item_id = e.item_id")
    args: tuple = ()
    if run_id:
        sql, args = sql + " WHERE i.run_id = ?", (run_id,)
    return {r["account"]: r["net"] for r in conn.execute(sql + " GROUP BY l.account ORDER BY l.account", args)}


def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def write_journal_csv(conn: sqlite3.Connection, run_id: str, out_dir: str | Path) -> list[Path]:
    """journal.csv (every line) + trial_balance.csv (per account; sums to 0). Atomic."""
    rows = conn.execute(
        "SELECT e.entry_id, e.kind, e.item_id, e.vendor_id, e.decision_hash, e.approval_id, e.attempt_key, l.line_no, "
        "l.account, l.debit_paise, l.credit_paise FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.entry_id "
        "JOIN items i ON i.item_id=e.item_id WHERE i.run_id=? ORDER BY e.posted_at, e.entry_id, l.line_no", (run_id,)).fetchall()
    out = Path(out_dir)
    j = atomic_write_text(out / "journal.csv", _csv(
        ["entry_id", "kind", "item_id", "vendor_id", "decision_hash", "approval_id", "attempt_key", "line_no", "account",
         "debit_paise", "credit_paise"], [list(r) for r in rows]))
    tb = trial_balance(conn, run_id)
    t = atomic_write_text(out / "trial_balance.csv", _csv(["account", "net_debit_paise"], [[a, n] for a, n in tb.items()]))
    return [j, t]
