"""io.client_data (A3): load a client's vendor master, POs, history and chart of accounts. Owner: lane A.

Bank accounts are fingerprinted at load (HMAC + last 4); the raw number is dropped. A load-time scan reports
master-data conflicts (duplicate GSTIN / CIN / alias / account) in `data_quality`; items that hit them are held by lane C.
Root directory: $PAYPILOT_ROOT (default: cwd) -> <root>/clients/<client_id>/.
"""
from __future__ import annotations

import csv
import hashlib
import os
from collections import defaultdict
from datetime import date
from pathlib import Path

from paypilot.core.config import load_config
from paypilot.core.env import bank_hmac_key
from paypilot.io.fingerprint import fingerprint
from paypilot.io.money import parse_amount_paise
from paypilot.resolve import normalize as _norm
from paypilot.schemas import ClientData, HistoryRecord, PurchaseOrder, RunConfig, Vendor

VENDOR_COLS = ["vendor_id", "legal_name", "aliases", "cin", "gstin", "domain", "bank_account", "ifsc", "default_account",
               "category", "last_updated"]
PO_COLS = ["po_number", "vendor_id", "amount_inr", "qty", "status", "issued_on"]
HISTORY_COLS = ["ref", "vendor_id", "invoice_number", "amount_inr", "currency", "paid_on", "provider_ref", "bank_reference", "status"]
COA_COLS = ["account_code", "account_name", "category"]


class ClientDataError(Exception):
    """Bad or missing client data -> CLI exit 2."""


def _root() -> Path:
    return Path(os.environ.get("PAYPILOT_ROOT", "."))


def _rows(path: Path, cols: list[str], required: bool) -> tuple[list[dict], bytes]:
    if not path.exists():
        if required:
            raise ClientDataError(f"missing required file {path}")
        return [], b""
    raw = path.read_bytes()
    reader = csv.DictReader(raw.decode("utf-8-sig").splitlines())
    missing = [c for c in cols if c not in (reader.fieldnames or [])]
    if missing:
        raise ClientDataError(f"{path.name}: missing columns {missing}")
    return [{k: (v or "").strip() for k, v in row.items()} for row in reader], raw


def _date(v: str, where: str) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError as e:
        raise ClientDataError(f"{where}: bad date {v!r}") from e


def _opt(v: str) -> str | None:
    return v or None


def _scan(vendors: list[Vendor]) -> list[str]:
    """Master-data conflicts. Format: KIND:value:V1,V2 (accounts shown as a fingerprint prefix, never the number)."""
    out: list[str] = []
    seen: dict[str, dict[str, list[str]]] = {k: defaultdict(list) for k in ("GSTIN", "CIN", "ACCOUNT", "ALIAS", "NAME")}
    for v in vendors:
        for kind, val in (("GSTIN", v.gstin), ("CIN", v.cin),
                          ("ACCOUNT", v.account_fingerprint[:12] if v.account_fingerprint else None), ("NAME", v.name_key)):
            if val:
                seen[kind][val].append(v.vendor_id)
        for a in {a.strip().upper() for a in v.aliases}:
            seen["ALIAS"][a].append(v.vendor_id)
    for kind, groups in seen.items():
        for val, vids in sorted(groups.items()):
            uniq = sorted(set(vids))
            if len(uniq) > 1:
                out.append(f"DUPLICATE_{kind}:{val}:{','.join(uniq)}")
    return out


def load_client_data(client_id: str, cfg: RunConfig) -> tuple[ClientData, str]:
    """Returns (ClientData, data_hash). data_hash covers the raw bytes of every client file."""
    root = _root()
    cdir = root / "clients" / client_id
    if not cdir.is_dir():
        raise ClientDataError(f"unknown client {client_id!r}: {cdir} not found")
    _, policy, _ = load_config(client_id, root=root)
    key = bank_hmac_key(root)
    h = hashlib.sha256()

    vrows, raw = _rows(cdir / "vendor_master.csv", VENDOR_COLS, True); h.update(b"vendor_master:" + raw)
    vendors: list[Vendor] = []
    for r in vrows:
        fp, last4 = fingerprint(r["bank_account"], key)       # raw number dropped right here
        vendors.append(Vendor(
            vendor_id=r["vendor_id"], legal_name=r["legal_name"], name_key=_norm.name_key(r["legal_name"], policy),
            aliases=[a.strip() for a in r["aliases"].split("|") if a.strip()], cin=_opt(r["cin"]), gstin=_opt(r["gstin"]),
            domain=_opt(r["domain"].lower()), account_fingerprint=fp, account_last4=last4,
            ifsc=_opt(r["ifsc"].upper()), default_account=_opt(r["default_account"]), category=_opt(r["category"]),
            record_date=_date(r["last_updated"], f"vendor {r['vendor_id']}"),
            record_ref=f"clients/{client_id}/vendor_master.csv#{r['vendor_id']}"))
    if len({v.vendor_id for v in vendors}) != len(vendors):
        raise ClientDataError("vendor_master.csv: duplicate vendor_id")

    pos = None
    rows, raw = _rows(cdir / "purchase_orders.csv", PO_COLS, False); h.update(b"po:" + raw)
    if (cdir / "purchase_orders.csv").exists():
        try:
            pos = [PurchaseOrder(po_number=r["po_number"], vendor_id=r["vendor_id"], amount_paise=parse_amount_paise(r["amount_inr"]),
                                 qty=float(r["qty"]) if r["qty"] else None, status=r["status"],
                                 record_date=_date(r["issued_on"], r["po_number"]),
                                 record_ref=f"clients/{client_id}/purchase_orders.csv#{r['po_number']}") for r in rows]
        except ValueError as e:
            raise ClientDataError(f"purchase_orders.csv: {e}") from e

    history = None
    rows, raw = _rows(cdir / "payment_history.csv", HISTORY_COLS, False); h.update(b"history:" + raw)
    if (cdir / "payment_history.csv").exists():
        try:
            history = [HistoryRecord(
                ref=r["ref"], vendor_id=_opt(r["vendor_id"]),
                invoice_no_canonical=_norm.invoice_canonical(r["invoice_number"], policy) if r["invoice_number"] else None,
                amount_paise=parse_amount_paise(r["amount_inr"]), currency=r["currency"].upper() or "INR",
                paid_on=_date(r["paid_on"], r["ref"]), provider_ref=_opt(r["provider_ref"]),
                bank_reference=_opt(r["bank_reference"]), status=r["status"],
                record_ref=f"clients/{client_id}/payment_history.csv#{r['ref']}") for r in rows]
        except (ValueError, TypeError) as e:
            raise ClientDataError(f"payment_history.csv: {e}") from e

    accounts = None
    rows, raw = _rows(cdir / "chart_of_accounts.csv", COA_COLS, False); h.update(b"coa:" + raw)
    if (cdir / "chart_of_accounts.csv").exists():
        accounts = {r["category"]: r["account_name"] for r in rows if r["category"]}

    data = ClientData(client_id=client_id, vendors=vendors, pos=pos, history=history, accounts=accounts,
                      statement=None, data_quality=_scan(vendors))
    return data, h.hexdigest()
