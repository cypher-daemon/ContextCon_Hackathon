"""HistoryView over SQLite + the client's payment_history file (INTERFACES §8). Owner: lane A. Read-only.

Ordering rule: items of the SAME run are compared by (item_date, input_name, row_no): the first occurrence proceeds, later ones
see it as a prior. Items of OTHER runs of the client are always "earlier". `PriorRecord.source` tells lane C which history it came
from (this_run | db_history | payment_history) so `duplicate.compare_against` can select.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from paypilot.core import ids
from paypilot.schemas import ClientData, Payment, PaymentStatus, PriorRecord, SettlementStatus

CLOSED_EXCEPTION = ("RESOLVED", "FALSE_POSITIVE", "ACCEPTED_RISK", "CLOSED_NO_ACTION")
BILLED_STATES = ("AWAITING_APPROVAL", "APPROVED", "PAYMENT_IN_PROGRESS", "PAYMENT_UNCERTAIN", "PAID")  # invoices that count against a PO


class SqliteHistory:
    def __init__(self, con: sqlite3.Connection, data: ClientData, client_id: str, run_id: str):
        self.con, self.data, self.client_id, self.run_id = con, data, client_id, run_id

    # -- helpers --
    def _before(self, before_item: str | None) -> tuple[str, list]:
        """SQL fragment limiting same-run rows to those ordered before `before_item` (and never the item itself)."""
        if before_item is None:
            return "", []
        pos = self.con.execute("SELECT item_date, input_name, row_no FROM items WHERE item_id=?", (before_item,)).fetchone()
        if pos is None:
            return " AND item_id != ?", [before_item]
        d = pos["item_date"] or ""
        return (" AND item_id != ? AND (run_id != ? OR COALESCE(item_date,'') < ? OR (COALESCE(item_date,'') = ? AND "
                "(input_name < ? OR (input_name = ? AND row_no < ?))))",
                [before_item, self.run_id, d, d, pos["input_name"], pos["input_name"], pos["row_no"]])

    def _prior(self, r: sqlite3.Row) -> PriorRecord:
        return PriorRecord(ref=r["source_ref"], item_id=r["item_id"], vendor_id=r["vendor_id"],
                           invoice_no_canonical=r["invoice_no_canonical"], amount_paise=r["amount_paise"],
                           currency=r["currency"], date=date.fromisoformat(r["item_date"]) if r["item_date"] else None,
                           state=r["state"], source="this_run" if r["run_id"] == self.run_id else "db_history",
                           record_ref=r["source_ref"])

    # -- HistoryView --
    def prior_by_invoice(self, vendor_id: str, invoice_no_canonical: str, *, before_item: str | None) -> list[PriorRecord]:
        frag, args = self._before(before_item)
        rows = self.con.execute("SELECT * FROM items WHERE client_id=? AND vendor_id=? AND invoice_no_canonical=?" + frag +
                                " ORDER BY COALESCE(item_date,''), input_name, row_no",
                                [self.client_id, vendor_id, invoice_no_canonical, *args]).fetchall()
        out = [self._prior(r) for r in rows]
        for h in self.data.history or []:
            if h.vendor_id == vendor_id and h.invoice_no_canonical == invoice_no_canonical:
                out.append(PriorRecord(ref=h.ref, vendor_id=h.vendor_id, invoice_no_canonical=h.invoice_no_canonical,
                                       amount_paise=h.amount_paise, currency=h.currency, date=h.paid_on, state=h.status,
                                       source="payment_history", record_ref=h.record_ref))
        return out

    def prior_by_txn(self, key: str, amount_paise: int, currency: str, date_from: date, date_to: date, *,
                     before_item: str | None) -> list[PriorRecord]:
        frag, args = self._before(before_item)
        rows = self.con.execute(
            "SELECT * FROM items WHERE client_id=? AND descriptor_key=? AND amount_paise=? AND currency=? "
            "AND item_date BETWEEN ? AND ?" + frag + " ORDER BY COALESCE(item_date,''), input_name, row_no",
            [self.client_id, key, amount_paise, currency, date_from.isoformat(), date_to.isoformat(), *args]).fetchall()
        return [self._prior(r) for r in rows]

    def prior_by_fingerprint(self, fingerprint: str, *, before_item: str | None) -> list[PriorRecord]:
        frag, args = self._before(before_item)
        rows = self.con.execute("SELECT * FROM items WHERE client_id=? AND transaction_fingerprint=?" + frag +
                                " ORDER BY COALESCE(item_date,''), input_name, row_no",
                                [self.client_id, fingerprint, *args]).fetchall()
        out = [self._prior(r) for r in rows]
        for h in self.data.history or []:      # UTR-based match against the client's own payment history
            if h.bank_reference and ids.sha256_hex("ref", h.bank_reference) == fingerprint:
                out.append(PriorRecord(ref=h.ref, vendor_id=h.vendor_id, invoice_no_canonical=h.invoice_no_canonical,
                                       amount_paise=h.amount_paise, currency=h.currency, date=h.paid_on, state=h.status,
                                       source="payment_history", record_ref=h.record_ref))
        return out

    def payments_for_invoice(self, vendor_id: str, invoice_no_canonical: str) -> list[Payment]:
        rows = self.con.execute(
            "SELECT p.* FROM payments p JOIN items i ON i.item_id=p.item_id WHERE i.client_id=? AND p.vendor_id=? "
            "AND i.invoice_no_canonical=? ORDER BY p.created_at, p.payment_id", (self.client_id, vendor_id, invoice_no_canonical)).fetchall()
        return [Payment(payment_id=r["payment_id"], item_id=r["item_id"], approval_id=r["approval_id"],
                        payment_key=r["payment_key"], provider=r["provider"], amount_paise=r["amount_paise"],
                        currency=r["currency"], status=PaymentStatus(r["status"]),
                        settlement_status=SettlementStatus(r["settlement_status"]), current_attempt=r["current_attempt"],
                        created_at=r["created_at"], updated_at=r["updated_at"]) for r in rows]

    def po_billed_paise(self, po_number: str, *, before_item: str | None) -> int:
        """Sum of invoices against this PO that were accepted into the payment flow (never held/rejected ones)."""
        frag, args = self._before(before_item)
        marks = ",".join("?" * len(BILLED_STATES))
        row = self.con.execute(
            f"SELECT COALESCE(SUM(amount_paise),0) AS s FROM items WHERE client_id=? AND kind='Invoice' "
            f"AND json_extract(payload_json,'$.po_number')=? AND state IN ({marks})" + frag,
            [self.client_id, po_number, *BILLED_STATES, *args]).fetchone()
        return int(row["s"])

    def open_critical_for_vendor(self, vendor_id: str) -> list[str]:
        """Containment: open CRITICAL exceptions, or a pending BANK_CHANGE_REQUEST, for this vendor."""
        marks = ",".join("?" * len(CLOSED_EXCEPTION))
        rows = self.con.execute(
            f"SELECT e.exception_id FROM exceptions e JOIN runs r ON r.run_id=e.run_id WHERE r.client_id=? AND e.vendor_id=? "
            f"AND e.status NOT IN ({marks}) AND (e.severity='CRITICAL' OR e.type='BANK_CHANGE_REQUEST') ORDER BY e.exception_id",
            [self.client_id, vendor_id, *CLOSED_EXCEPTION]).fetchall()
        return [r["exception_id"] for r in rows]
