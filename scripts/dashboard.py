#!/usr/bin/env python3
"""PayPilot dashboard: a read-only local web view of the ledger, payments, approvals, exceptions and audit trail.

    python scripts/dashboard.py                      # reads $PAYPILOT_ROOT/var/paypilot.db (else ./var/paypilot.db)
    python scripts/dashboard.py --db .demo/var/paypilot.db --port 8765

Stdlib only. The DB is opened read-only (SQLite URI mode=ro), so this can never persist, approve or pay anything.
Binds to 127.0.0.1. Account numbers are never stored in the DB (fingerprint + last 4 only), so none can appear here.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
STATIC = HERE / "dashboard"
PAGES = {"/", "/payments", "/exceptions", "/approvals", "/books"}      # every page is the same shell; app.js picks the view
ASSETS = {"/static/app.css": ("app.css", "text/css; charset=utf-8"),
          "/static/app.js": ("app.js", "text/javascript; charset=utf-8")}
sys.path.insert(0, str(REPO))  # so `python scripts/dashboard.py` can import the project's own definitions

from paypilot.ledger.store import summarize  # noqa: E402  (cost: one definition of KNOWN / PARTIAL / UNKNOWN)
from paypilot.schemas import ExceptionStatus, ItemState, PaymentStatus  # noqa: E402

# Terminal exception statuses and payment outcomes come from the contract, not from guesses.
CLOSED_EXCEPTION = {ExceptionStatus.RESOLVED, ExceptionStatus.FALSE_POSITIVE, ExceptionStatus.ACCEPTED_RISK,
                    ExceptionStatus.CLOSED_NO_ACTION}
RETURNED = {PaymentStatus.RETURNED, PaymentStatus.REVERSED}
IN_FLIGHT = {PaymentStatus.CREATED, PaymentStatus.SUBMITTED, PaymentStatus.PAYMENT_UNCERTAIN}
OP_LABEL = {"POST_BANK_LINES": "Post bank lines", "PAY_INVOICES": "Pay invoices", "RECONCILE": "Reconcile statement"}


def label_runs(runs: list[dict], stats: list[dict], open_exc: list[dict]) -> list[dict]:
    """Name every run by what it did. Two runs of the same kind are numbered in the order they started."""
    states: dict[str, dict] = {}
    for r in stats:
        states.setdefault(r["run_id"], {})[r["state"]] = r["n"]
    opened = {r["run_id"]: r["n"] for r in open_exc}
    by_op: dict[str, int] = {}
    for r in runs:
        by_op[r["operation"]] = by_op.get(r["operation"], 0) + 1
    seen: dict[str, int] = {}
    out = []
    for r in sorted(runs, key=lambda x: x["started_at"]):          # oldest first so "#1" is the first one run
        op = r["operation"]
        seen[op] = seen.get(op, 0) + 1
        base = OP_LABEL.get(op, "Unsupported request")
        st = states.get(r["run_id"], {})
        out.append({**r, "label": base + (f" #{seen[op]}" if by_op[op] > 1 else ""), "items": sum(st.values()),
                    "states": st, "open_exceptions": opened.get(r["run_id"], 0)})
    return sorted(out, key=lambda x: x["started_at"], reverse=True)    # newest first


def default_db() -> Path:
    root = Path(os.environ.get("PAYPILOT_ROOT") or REPO)
    return root / "var" / "paypilot.db"


def connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5, isolation_level=None)  # we run our own BEGIN
    con.row_factory = sqlite3.Row
    return con


def j(text, default=None):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def rows(con, sql, *args):
    return [dict(r) for r in con.execute(sql, args)]


def empty_state(db: Path) -> dict:
    """Nothing has run yet (no database, or no tables): every figure is zero and every list is empty."""
    zero = {"items": 0, "paid": 0, "held": 0, "uncertain": 0, "open_exceptions": 0, "blocking": 0, "pending_approvals": 0}
    return {"db": str(db), "runs": [], "run_id": None, "counts": zero,
            "money": {"paid_paise": 0, "held_paise": 0, "returned_paise": 0, "inflight_paise": 0, "awaiting_paise": 0},
            "items": [], "exceptions": [], "approvals": [], "audit": [],
            "cost": {"status": "KNOWN", "total": 0.0, "unconfirmed": 0.0, "calls": 0, "cache_hits": 0, "providers": []},
            "trial_balance": [], "entries": [], "journal": [], "payment_providers": []}


def build_state(db: Path, run_id: str | None) -> dict:
    if not db.exists():
        return empty_state(db)
    try:
        return _build_state(db, run_id)
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):         # the file exists but the first run has not created the schema yet
            return empty_state(db)
        raise


def _build_state(db: Path, run_id: str | None) -> dict:
    con = connect(db)
    try:
        # One read transaction = one point-in-time snapshot (WAL). Without it a payment committing between two of the
        # queries below could show e.g. a confirmed payment with no journal entry. Closing the connection ends it.
        con.execute("BEGIN")
        runs = rows(con, "SELECT run_id, client_id, request_id, operation, status, started_at FROM runs ORDER BY started_at DESC")
        where, args = ("WHERE run_id=?", (run_id,)) if run_id else ("", ())
        items_raw = rows(con, f"SELECT * FROM items {where} ORDER BY run_id, row_no", *args)
        item_ids = {i["item_id"] for i in items_raw}

        pay = {p["item_id"]: p for p in rows(con, "SELECT * FROM payments")}
        appr = {}
        for a in rows(con, "SELECT * FROM approvals ORDER BY approval_seq"):
            appr[a["item_id"]] = a  # latest seq wins
        attempts = {}
        for a in rows(con, "SELECT * FROM payment_attempts ORDER BY attempt_no"):
            attempts.setdefault(a["payment_id"], []).append(a)

        items = []
        for it in items_raw:
            payload = j(it["payload_json"], {}) or {}
            decision = j(it["decision_json"], {}) or {}
            p = pay.get(it["item_id"])
            a = appr.get(it["item_id"])
            name = payload.get("vendor_name_raw") or payload.get("descriptor_raw") or it["vendor_id"] or "Unresolved payee"
            ref = payload.get("invoice_id") or payload.get("line_id") or payload.get("message_id") or it["source_ref"].rsplit("#", 1)[-1]
            items.append({
                "item_id": it["item_id"], "run_id": it["run_id"], "kind": it["kind"], "ref": ref,
                "label": name, "vendor_id": it["vendor_id"], "amount_paise": it["amount_paise"],
                "date": it["item_date"], "state": it["state"], "outcome": it["outcome"], "updated_at": it["updated_at"],
                "proposal": decision.get("proposal"), "flags": decision.get("flags", []),
                "reasons": [{"code": r.get("code"), "text": r.get("text"), "rule": r.get("rule_id")}
                            for r in decision.get("reasons", [])],
                "invoice_no": payload.get("invoice_no_raw"), "due_date": payload.get("due_date"),
                "approval": ({"status": a["status"], "reviewer": a["reviewer"], "at": a["decided_at"]} if a else None),
                "payment": ({"payment_id": p["payment_id"], "status": p["status"], "settlement": p["settlement_status"], "provider": p["provider"],
                             "attempts": [{"no": x["attempt_no"], "outcome": x["outcome"], "step": x["step"],
                                           "error": x["last_error"]} for x in attempts.get(p["payment_id"], [])]}
                            if p else None),
            })

        exceptions = [e for e in rows(con, "SELECT * FROM exceptions ORDER BY created_at DESC")
                      if not run_id or e["run_id"] == run_id]
        for e in exceptions:
            e["evidence_count"] = len(j(e.pop("evidence_refs_json"), []))
            e["related"] = j(e.pop("related_refs_json"), [])
            e.pop("origin_key", None)

        approvals = [a for a in rows(con, "SELECT * FROM approvals ORDER BY requested_at DESC") if a["item_id"] in item_ids]
        for a in approvals:
            a["overrides"] = j(a.pop("overrides_json"), [])
            a["decision_hash"] = a["decision_hash"][:12]

        audit = rows(con, "SELECT event_id, run_id, entity_type, entity_id, previous_state, new_state, actor, reason, ts "
                          f"FROM audit_events {where} ORDER BY event_id DESC LIMIT 600", *args)

        # cost: the ledger's own summary. A missing price is UNKNOWN, never 0 (hard rule 7).
        scope = [run_id] if run_id else [r["run_id"] for r in runs]
        sums = [summarize(con, rid) for rid in scope]
        providers: dict[str, dict] = {}
        for sm in sums:
            for name, v in sm.by_provider.items():
                b = providers.setdefault(name, {"provider": name, "spend": 0.0, "unknown": False})
                if v is None:
                    b["unknown"] = True
                else:
                    b["spend"] += v
        statuses = {sm.cost_status for sm in sums}
        status = "UNKNOWN" if "UNKNOWN" in statuses else "PARTIAL" if "PARTIAL" in statuses else "KNOWN"
        known = [sm.total_inr for sm in sums if sm.total_inr is not None]
        cost = {"status": status, "total": (round(sum(known), 6) if status != "UNKNOWN" else None),
                "unconfirmed": round(sum(sm.unconfirmed_inr for sm in sums), 6),
                "calls": sum(sm.calls for sm in sums), "cache_hits": sum(sm.cache_hits for sm in sums),
                "providers": list(providers.values())}

        # trial balance from journal lines
        in_run = ("AND e.item_id IN (SELECT item_id FROM items WHERE run_id=?)" if run_id else "")
        tb = rows(con, "SELECT l.account, SUM(l.debit_paise) AS debit, SUM(l.credit_paise) AS credit "
                       "FROM journal_lines l JOIN journal_entries e ON e.entry_id=l.entry_id "
                       f"WHERE 1=1 {in_run} GROUP BY l.account ORDER BY l.account", *args)
        entries = rows(con, "SELECT e.kind, COUNT(*) AS n FROM journal_entries e "
                            f"WHERE 1=1 {in_run} GROUP BY e.kind", *args)

        def pstatus(i):
            return i["payment"]["status"] if i["payment"] else None

        journal = rows(con, "SELECT entry_id, item_id, kind, vendor_id, memo, posted_at FROM journal_entries e "
                            f"WHERE 1=1 {in_run} ORDER BY posted_at DESC LIMIT 100", *args)
        lines = {}
        for ln in rows(con, "SELECT entry_id, line_no, account, debit_paise, credit_paise FROM journal_lines ORDER BY entry_id, line_no"):
            lines.setdefault(ln["entry_id"], []).append(ln)
        for e in journal:
            e["lines"] = lines.get(e["entry_id"], [])
        providers = [r["provider"] for r in rows(con, "SELECT DISTINCT provider FROM payments")]

        counts = {
            "items": len(items),
            "paid": sum(1 for i in items if i["state"] == ItemState.PAID),
            "held": sum(1 for i in items if i["state"] == ItemState.HELD),
            "uncertain": sum(1 for i in items if pstatus(i) == PaymentStatus.PAYMENT_UNCERTAIN
                             or i["state"] == ItemState.PAYMENT_UNCERTAIN),
            "open_exceptions": sum(1 for e in exceptions if e["status"] not in CLOSED_EXCEPTION),
            "blocking": sum(1 for e in exceptions if e["blocking"] and e["status"] not in CLOSED_EXCEPTION),
            "pending_approvals": sum(1 for a in approvals if a["status"] == "PENDING"),
        }
        # An invoice is "paid" only when the provider confirmed it. "Held" means stopped before any payment existed.
        # A payment later returned/reversed counts as returned, not paid and not held, so the three never overlap.
        invoices = [i for i in items if i["kind"] == "Invoice"]
        paid = sum(i["amount_paise"] or 0 for i in invoices if pstatus(i) == PaymentStatus.PROVIDER_CONFIRMED)
        inflight = sum(i["amount_paise"] or 0 for i in invoices if pstatus(i) in IN_FLIGHT)
        awaiting_value = sum(i["amount_paise"] or 0 for i in invoices if i["state"] == ItemState.AWAITING_APPROVAL and not i["payment"])
        held = sum(i["amount_paise"] or 0 for i in invoices if i["state"] == ItemState.HELD and not i["payment"])
        returned = sum(i["amount_paise"] or 0 for i in invoices if pstatus(i) in RETURNED)
        closed = tuple(str(x) for x in CLOSED_EXCEPTION)
        runs_out = label_runs(
            runs, rows(con, "SELECT run_id, state, COUNT(*) AS n FROM items GROUP BY run_id, state"),
            rows(con, f"SELECT run_id, COUNT(*) AS n FROM exceptions WHERE status NOT IN ({','.join('?' * len(closed))}) GROUP BY run_id", *closed))
        return {"db": str(db), "runs": runs_out, "run_id": run_id, "counts": counts,
                "money": {"paid_paise": paid, "held_paise": held, "returned_paise": returned,
                          "inflight_paise": inflight, "awaiting_paise": awaiting_value},
                "items": items, "exceptions": exceptions, "approvals": approvals, "audit": audit,
                "cost": cost, "trial_balance": tb, "entries": entries, "journal": journal,
                "payment_providers": providers}
    finally:
        con.close()


def make_handler(db: Path):
    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            if u.path in PAGES:
                return self._send(200, (STATIC / "app.html").read_bytes(), "text/html; charset=utf-8")
            if u.path in ASSETS:                      # fixed allowlist: no user-controlled file paths
                name, ctype = ASSETS[u.path]
                return self._send(200, (STATIC / name).read_bytes(), ctype)
            if u.path == "/api/state":
                run = (parse_qs(u.query).get("run") or [None])[0]
                try:
                    body = json.dumps(build_state(db, run)).encode()
                    return self._send(200, body, "application/json")
                except sqlite3.Error as e:
                    return self._send(503, json.dumps({"error": f"cannot read {db}: {e}"}).encode(), "application/json")
            self._send(404, b"not found", "text/plain")

        def log_message(self, *a):  # quiet
            pass

    return H


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    db = (args.db or default_db()).resolve()
    if not db.exists():
        print(f"No database at {db} yet. Showing zeros until the first run creates it.")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(db))
    print(f"PayPilot dashboard (read-only) on http://127.0.0.1:{args.port}  db={db}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
