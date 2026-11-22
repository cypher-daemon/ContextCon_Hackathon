"""io.batch (A3): parse batch files into work items. Owner: lane A.

A malformed row becomes a ParseFailure (never a crash, never a guess). Decimal strings -> exact paise; floats rejected.
Bank accounts are fingerprinted here and the raw value is dropped; error text never echoes an account value.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from paypilot.core import ids
from paypilot.core.env import bank_hmac_key
from paypilot.io.fingerprint import fingerprint
from paypilot.io.money import parse_amount_paise
from paypilot.io.request import RequestError, safe_input_path
from paypilot.resolve import normalize as _norm
from paypilot.context import StageContext
from paypilot.schemas import BankLine, Invoice, Operation, ParseFailure, Request, VendorMessage

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ACCOUNT_FIELDS = {"remit_bank_account", "new_bank_account"}


class _Row:
    """Collects errors for one row; never includes the value of an account field."""

    def __init__(self, rec: dict):
        self.rec, self.errors = rec, []

    def s(self, k: str, required: bool = False) -> str | None:
        v = self.rec.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            if required:
                self.errors.append(f"{k}: required")
            return None
        if not isinstance(v, str):
            self.errors.append(f"{k}: must be a string")
            return None
        return v.strip()

    def date(self, k: str, required: bool = False) -> date | None:
        v = self.s(k, required)
        if v is None:
            return None
        try:
            if not _DATE.match(v):
                raise ValueError
            return date.fromisoformat(v)
        except ValueError:
            self.errors.append(f"{k}: must be an ISO date YYYY-MM-DD")
            return None

    def amount(self, k: str, required: bool = False) -> int | None:
        if self.rec.get(k) is None:
            if required:
                self.errors.append(f"{k}: required")
            return None
        try:
            return parse_amount_paise(self.rec[k])
        except ValueError as e:
            self.errors.append(f"{k}: {e}")
            return None

    def number(self, k: str) -> float | None:
        v = self.rec.get(k)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            self.errors.append(f"{k}: must be a number")
            return None
        return float(v)


def _read_rows(path: Path) -> list[Any]:
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise RequestError(f"cannot read batch file {path.name}: {e}") from e
    if not isinstance(doc, list):
        raise RequestError(f"{path.name}: top level must be a JSON array")
    return doc


def _parse_bank_line(r: _Row, item_id: str, ref: str, ctx: StageContext) -> BankLine | None:
    line_id = r.s("line_id", True)
    d, desc, amt = r.date("date", True), r.s("description", True), r.amount("amount_inr", True)
    direction, cur = r.s("direction", True), r.s("currency", True)
    if direction is not None:
        direction = direction.upper()
        if direction not in ("DEBIT", "CREDIT"):
            r.errors.append("direction: must be DEBIT or CREDIT"); direction = None
    bank_ref = r.s("bank_reference")
    if r.errors:
        return None
    dkey = _norm.descriptor_key(desc, ctx.policy.normalize)
    return BankLine(item_id=item_id, source_ref=ref, line_id=line_id, date=d, descriptor_raw=desc, descriptor_key=dkey,
                    amount_paise=amt, currency=cur.upper(), direction=direction, bank_reference=bank_ref,
                    transaction_fingerprint=ids.txn_fingerprint_bank_line(
                        bank_reference=bank_ref, descriptor_key=dkey, amount_paise=amt, currency=cur.upper(),
                        date=d, direction=direction))


def _parse_invoice(r: _Row, item_id: str, ref: str, ctx: StageContext, key: str) -> Invoice | None:
    inv_id, name = r.s("invoice_id", True), r.s("vendor_name", True)
    gstin, dom, inv_no = r.s("vendor_gstin"), r.s("vendor_domain"), r.s("invoice_number")
    inv_date, due = r.date("invoice_date"), r.date("due_date")
    amt, cur, po, qty = r.amount("amount_inr"), r.s("currency"), r.s("po_number"), r.number("qty")
    fp, last4 = fingerprint(r.s("remit_bank_account"), key)
    ifsc, docref = r.s("remit_ifsc"), r.s("document_ref")
    if r.errors:
        return None
    canon = _norm.invoice_canonical(inv_no, ctx.policy.normalize) if inv_no else None
    return Invoice(item_id=item_id, source_ref=ref, invoice_id=inv_id, vendor_name_raw=name, gstin=gstin,
                   domain=dom.lower() if dom else None, invoice_no_raw=inv_no, invoice_no_canonical=canon,
                   invoice_date=inv_date, due_date=due, amount_paise=amt, currency=(cur or "INR").upper(),
                   po_number=po, qty=qty, account_fingerprint=fp, account_last4=last4,
                   ifsc=ifsc.upper() if ifsc else None, document_ref=docref,
                   transaction_fingerprint=ids.txn_fingerprint_invoice(_norm.name_key(name, ctx.policy.normalize), canon))


def _parse_message(r: _Row, item_id: str, ref: str, ctx: StageContext, key: str) -> VendorMessage | None:
    mid, name = r.s("message_id", True), r.s("vendor_name", True)
    got, dom, kind = r.s("received_at", True), r.s("from_domain"), r.s("kind", True)
    ts = None
    if got:
        try:
            ts = datetime.fromisoformat(got)
            if ts.tzinfo is None:
                raise ValueError
        except ValueError:
            r.errors.append("received_at: must be an ISO timestamp with a timezone")
    if kind and kind not in ("BANK_CHANGE", "OTHER"):
        r.errors.append("kind: must be BANK_CHANGE or OTHER")
    fp, last4 = fingerprint(r.s("new_bank_account"), key)
    ifsc = r.s("new_ifsc")
    if r.errors:
        return None
    return VendorMessage(item_id=item_id, source_ref=ref, message_id=mid, received_at=ts,
                         from_domain=dom.lower() if dom else None, vendor_name_raw=name, kind=kind,
                         new_account_fingerprint=fp, new_account_last4=last4, new_ifsc=ifsc.upper() if ifsc else None)


_PARSERS = {"bank_lines": "line_id", "bank_statement": "line_id", "invoices": "invoice_id", "vendor_messages": "message_id"}


def load_items(request: Request, ctx: StageContext) -> list[BankLine | Invoice | VendorMessage | ParseFailure]:
    """Items in file order (inputs in request order), row numbers 1-based. `bank_statement` is not a work item: see load_statement."""
    base = Path(request.source_path).parent
    key = bank_hmac_key() if any(k in ("invoices", "vendor_messages") for k in request.inputs) else ""
    out: list[BankLine | Invoice | VendorMessage | ParseFailure] = []
    for input_key, fname in request.inputs.items():
        if input_key == "bank_statement" and request.operation is not Operation.RECONCILE:
            continue                      # a statement is only a work item in a RECONCILE run (each line is reconciled)
        for row_no, rec in enumerate(_read_rows(safe_input_path(base, fname)), start=1):
            iid = ids.item_id(request.client_id, request.source_sha, fname, row_no)
            if not isinstance(rec, dict):
                out.append(ParseFailure(item_id=iid, source_ref=f"{fname}#row{row_no}", errors=["row must be an object"]))
                continue
            sid = rec.get(_PARSERS[input_key])
            ref = f"{fname}#{sid if isinstance(sid, str) and sid else 'row' + str(row_no)}"
            r = _Row(rec)
            item = (_parse_bank_line(r, iid, ref, ctx) if input_key in ("bank_lines", "bank_statement") else
                    _parse_invoice(r, iid, ref, ctx, key) if input_key == "invoices" else
                    _parse_message(r, iid, ref, ctx, key))
            out.append(item if item is not None else ParseFailure(item_id=iid, source_ref=ref, errors=r.errors))
    return out


def load_statement(request: Request, ctx: StageContext) -> list[BankLine] | None:
    """The RECONCILE statement (same format as bank_lines.json). None if the request supplies none (settlement NOT_AVAILABLE)."""
    fname = request.inputs.get("bank_statement")
    if fname is None:
        return None
    base = Path(request.source_path).parent
    lines: list[BankLine] = []
    for row_no, rec in enumerate(_read_rows(safe_input_path(base, fname)), start=1):
        iid = ids.item_id(request.client_id, request.source_sha, fname, row_no)
        r = _Row(rec if isinstance(rec, dict) else {})
        b = _parse_bank_line(r, iid, f"{fname}#{rec.get('line_id', row_no) if isinstance(rec, dict) else row_no}", ctx)
        if b is None:      # a bad statement row must not silently shrink the statement
            raise RequestError(f"{fname} row {row_no}: {'; '.join(r.errors)}")
        lines.append(b)
    return lines
