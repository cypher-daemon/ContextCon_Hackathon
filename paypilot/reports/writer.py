"""reports.writer (A5): every output file is derived from DB state only, written atomically. Owner: lane A.

Files (INTERFACES §3): results.jsonl, report.md, summary.json, exceptions.jsonl, ledger.csv, audit.jsonl, journal.csv,
trial_balance.csv, payment_instructions.csv. Re-running regenerates identical content from the database.
Rehearsed live change: a new output field = one line in config `output.fields` (+ one FIELD_GETTERS entry if it is a new computed field).
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from paypilot.context import StageContext
from paypilot.core import journal
from paypilot.core.db import connect
from paypilot.core.fsutil import atomic_write_text
from paypilot.core.pipeline import build_summary
from paypilot.schemas import RunConfig

CLOSED = ("RESOLVED", "FALSE_POSITIVE", "ACCEPTED_RISK", "CLOSED_NO_ACTION")


class UnknownOutputField(Exception):
    """output.fields names a field with no FIELD_GETTERS entry."""


def fmt_inr(paise: int | None) -> str:
    """Integer paise -> '₹1,24,000.00' (Indian grouping). None -> UNKNOWN (never ₹0)."""
    if paise is None:
        return "UNKNOWN"
    sign, p = ("-" if paise < 0 else ""), abs(paise)
    rupees, ps = divmod(p, 100)
    s = str(rupees)
    head, tail = s[:-3], s[-3:]
    if head:
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:]); head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return f"{sign}₹{s}.{ps:02d}"


@dataclass
class ItemView:
    row: sqlite3.Row
    decision: dict | None
    exceptions: list[sqlite3.Row] = field(default_factory=list)
    payment: dict | None = None
    sources: list[dict] = field(default_factory=list)
    cost_inr: float | None = 0.0


def _j(text: str | None) -> Any:
    return json.loads(text) if text else None


def load_views(con: sqlite3.Connection, run_id: str) -> list[ItemView]:
    exc: dict[str, list] = {}
    for r in con.execute("SELECT * FROM exceptions WHERE run_id=? ORDER BY exception_id", (run_id,)):
        exc.setdefault(r["item_id"], []).append(r)
    pay = {r["item_id"]: r for r in con.execute(
        "SELECT p.*, (SELECT attempt_key FROM payment_attempts a WHERE a.payment_id=p.payment_id ORDER BY attempt_no DESC LIMIT 1) AS attempt_key "
        "FROM payments p JOIN items i ON i.item_id=p.item_id WHERE i.run_id=?", (run_id,))}
    cost: dict[str, float | None] = {}
    for r in con.execute("SELECT item_id, status, actual_inr FROM cost_ledger WHERE run_id=? AND item_id IS NOT NULL", (run_id,)):
        cur = cost.get(r["item_id"], 0.0)
        if r["status"] == "CACHE_HIT":
            continue
        cost[r["item_id"]] = None if (cur is None or r["actual_inr"] is None) else cur + r["actual_inr"]
    views = []
    for row in con.execute("SELECT * FROM items WHERE run_id=? ORDER BY input_name, row_no", (run_id,)):
        srcs = [dict(s) for s in con.execute(
            "SELECT DISTINCT s.* FROM item_evidence e JOIN sources s ON s.source_id=e.source_id WHERE e.item_id=? AND e.decision_hash=? "
            "ORDER BY s.source_id", (row["item_id"], row["decision_hash"] or ""))]
        for s in srcs:
            s["limitations"] = json.loads(s.pop("limitations_json"))
        p = pay.get(row["item_id"])
        views.append(ItemView(row=row, decision=_j(row["decision_json"]), exceptions=exc.get(row["item_id"], []),
                              payment=dict(p) if p else None, sources=srcs, cost_inr=cost.get(row["item_id"], 0.0)))
    return views


# field name -> getter(ItemView). Add one entry to expose a new computed field, then list it in config `output.fields`.
FIELD_GETTERS: dict[str, Callable[[ItemView], Any]] = {
    "run_id": lambda v: v.row["run_id"],
    "item_id": lambda v: v.row["item_id"],
    "source_ref": lambda v: v.row["source_ref"],
    "operation": lambda v: v.row["operation"],
    "outcome": lambda v: v.row["outcome"],
    "payee": lambda v: ({"vendor_id": v.decision["facts"]["vendor_id"], "legal_name": v.decision.get("payee_legal_name")}
                        if v.decision and v.decision["facts"].get("vendor_id") else None),
    "account": lambda v: v.decision.get("account") if v.decision else None,
    "amount_paise": lambda v: v.row["amount_paise"],
    "currency": lambda v: v.row["currency"],
    "flags": lambda v: v.decision["flags"] if v.decision else [],
    "reasons": lambda v: [{"code": r["code"], "text": r["text"], "rule_id": r["rule_id"]} for r in v.decision["reasons"]] if v.decision else [],
    "bank_status": lambda v: v.decision.get("bank_status") if v.decision else None,
    "exception_ids": lambda v: [e["exception_id"] for e in v.exceptions],
    "payment": lambda v: ({"payment_key": v.payment["payment_key"], "status": v.payment["status"], "provider": v.payment["provider"],
                           "settlement_status": v.payment["settlement_status"], "attempt_key": v.payment["attempt_key"]}
                          if v.payment else None),
    "cost_inr": lambda v: v.cost_inr,
    "sources": lambda v: v.sources,
    "decision_hash": lambda v: v.row["decision_hash"],
    "vendor_id": lambda v: v.row["vendor_id"],
}


def results_lines(views: list[ItemView], fields: list[str]) -> str:
    unknown = [f for f in fields if f not in FIELD_GETTERS]
    if unknown:
        raise UnknownOutputField(f"output.fields has no FIELD_GETTERS entry for: {unknown}")
    return "".join(json.dumps({f: FIELD_GETTERS[f](v) for f in fields}, ensure_ascii=False, default=str) + "\n" for v in views)


def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO(); w = csv.writer(buf, lineterminator="\n"); w.writerow(header); w.writerows(rows)
    return buf.getvalue()


def _sentence(v: ItemView) -> str:
    r, d = v.row, v.decision
    ref = r["source_ref"].split("#", 1)[-1]
    head = f"**{ref}** `{r['outcome'] or r['state']}`"
    if d is None:
        return f"- {head}: not decided yet (state {r['state']})."
    what = f"{fmt_inr(r['amount_paise'])}" if r["amount_paise"] is not None else "amount unavailable"
    parts = [f"- {head}: {what}"]
    if d.get("payee_legal_name"):
        src = next((s for s in v.sources if s["fact"].startswith("payee")), None)
        parts.append(f"payee {d['payee_legal_name']}" + (f" (source `{src['provider']}` {src['record_ref']}, record date {src['record_date'] or 'unknown'})" if src else ""))
    if d.get("account"):
        parts.append(f"account {d['account']}")
    if d["flags"]:
        parts.append("flags " + ", ".join(d["flags"]))
    if d["reasons"]:
        parts.append("; ".join(x["text"] for x in d["reasons"]))
    if d.get("missing"):
        parts.append("missing: " + ", ".join(d["missing"]))
    return ", ".join(parts[:1]) + (" - " + " | ".join(parts[1:]) if len(parts) > 1 else "") + "."


def _report_md(con: sqlite3.Connection, run_id: str, views: list[ItemView], summary, exclude_session: str) -> str:
    run = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    posted = sum(v.row["amount_paise"] or 0 for v in views if v.row["outcome"] == "POSTED")
    awaiting = sum(v.row["amount_paise"] or 0 for v in views if v.row["outcome"] == "AWAITING_APPROVAL")
    flags = Counter(f for v in views if v.decision for f in v.decision["flags"])
    open_exc = [e for v in views for e in v.exceptions if e["status"] not in CLOSED]
    oldest = min(open_exc, key=lambda e: (e["created_at"], e["exception_id"]), default=None)
    crashes = con.execute("SELECT COUNT(*) FROM sessions WHERE run_id=? AND ended_at IS NULL AND session_id != ?", (run_id, exclude_session)).fetchone()[0]
    c = summary.cost
    cost_line = ("UNKNOWN (call prices missing)" if c.cost_status == "UNKNOWN" else
                 f"₹{c.total_inr:.2f} total" + (" at least, some prices unknown" if c.cost_status == "PARTIAL" else "") +
                 (f", ₹{c.inr_per_item:.2f} per item" if c.inr_per_item is not None else "") +
                 (f"; ₹{c.unconfirmed_inr:.2f} unconfirmed" if c.unconfirmed_inr else ""))
    out = [f"# PayPilot run {run_id}", "",
           f"- Client: `{run['client_id']}` · request `{run['request_id']}` · operation `{run['operation'] or 'UNSUPPORTED'}` · status **{run['status']}**",
           f"- Items: {len(views)} · outcomes: " + (", ".join(f"{k} {n}" for k, n in sorted(summary.counts.items())) or "none"), "",
           "## Totals", "",
           f"- Posted to the books (local journal only, no money moved): {fmt_inr(posted)}",
           f"- Awaiting approval: {fmt_inr(awaiting)}",
           "- Items flagged, by type: " + (", ".join(f"{k} {n}" for k, n in sorted(flags.items())) or "none"),
           "- Open exceptions by severity: " + (", ".join(f"{k} {n}" for k, n in sorted(summary.open_exceptions.items())) or "none"),
           f"- Oldest open exception: " + (f"{oldest['type']} ({oldest['created_at']})" if oldest else "none"),
           f"- Crash recoveries: {crashes}", f"- Cost: {cost_line}", "", "## Items", ""]
    out += [_sentence(v) for v in views] or ["- no items"]
    return "\n".join(out) + "\n"


def write_run_outputs(con: sqlite3.Connection, run_id: str, cfg: RunConfig, *, now: datetime | None = None,
                      session_id: str = "", operation=None) -> list[Path]:
    now = now or datetime.now(timezone.utc)
    out = Path(cfg.output["dir"].format(run_id=run_id))
    views = load_views(con, run_id)
    sctx = SimpleNamespace(session_id=session_id, cfg=cfg, clock=lambda: now, operation=operation)
    summary = build_summary(con, run_id, sctx)
    paths = [atomic_write_text(out / "results.jsonl", results_lines(views, cfg.output["fields"])),
             atomic_write_text(out / "report.md", _report_md(con, run_id, views, summary, session_id)),
             atomic_write_text(out / "summary.json", summary.model_dump_json(indent=2) + "\n")]
    exc_lines = ""
    for e in con.execute("SELECT * FROM exceptions WHERE run_id=? ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 "
                         "WHEN 'MEDIUM' THEN 2 ELSE 3 END, created_at, exception_id", (run_id,)):
        rec = con.execute("SELECT * FROM recovery_cases WHERE exception_id=?", (e["exception_id"],)).fetchone()
        d = dict(e); d["evidence_refs"] = json.loads(d.pop("evidence_refs_json")); d["related_refs"] = json.loads(d.pop("related_refs_json"))
        d["recovery_case"] = dict(rec) if rec else None
        exc_lines += json.dumps(d, ensure_ascii=False, default=str) + "\n"
    paths.append(atomic_write_text(out / "exceptions.jsonl", exc_lines))
    ledger = [[r["call_key"], r["item_id"] or "", r["stage"], r["provider"], r["operation"], r["status"], r["attempt"], r["units"],
               "" if r["estimated_inr"] is None else r["estimated_inr"], "UNKNOWN" if r["actual_inr"] is None else r["actual_inr"],
               r["http_status"] or "", r["duration_ms"] or ""]
              for r in con.execute("SELECT * FROM cost_ledger WHERE run_id=? ORDER BY ts, call_key", (run_id,))]
    paths.append(atomic_write_text(out / "ledger.csv", _csv(["call_key", "item_id", "stage", "provider", "operation", "status", "attempt",
                                                              "units", "estimated_inr", "actual_inr", "http_status", "duration_ms"], ledger)))
    audit = "".join(json.dumps({**dict(r), "metadata": json.loads(r["metadata_json"])} | {"metadata_json": None}, default=str) + "\n"
                    for r in con.execute("SELECT * FROM audit_events WHERE run_id=? ORDER BY event_id", (run_id,)))
    paths.append(atomic_write_text(out / "audit.jsonl", audit))
    paths += journal.write_journal_csv(con, run_id, out)
    pay_rows = []
    for p in con.execute(
            "SELECT p.*, i.payload_json, a.provider_ref, a.outcome AS attempt_outcome, a.attempt_key, a.approval_id AS attempt_approval "
            "FROM payments p JOIN items i ON i.item_id=p.item_id LEFT JOIN payment_attempts a ON a.payment_id=p.payment_id AND a.attempt_no=p.current_attempt "
            "WHERE i.run_id=? ORDER BY p.created_at, p.payment_id", (run_id,)):
        pay_rows.append(["TRUE", p["payment_key"], p["attempt_key"] or "", p["vendor_id"], (_j(p["payload_json"]) or {}).get("account_last4") or "",
                         p["amount_paise"], p["attempt_approval"] or p["approval_id"], p["provider"], p["provider_ref"] or "",
                         p["attempt_outcome"] or "UNKNOWN", p["status"], p["settlement_status"], ""])
    paths.append(atomic_write_text(out / "payment_instructions.csv", _csv(
        ["do_not_execute", "payment_key", "attempt_key", "vendor", "account_last4", "amount_paise", "approval", "provider", "provider_ref",
         "provider_status", "status", "settlement_status", "utr"], pay_rows)))
    return paths


def write_outputs(run_id: str, ctx: StageContext) -> list[Path]:
    """Contract entry point (INTERFACES §9): regenerate every output file for a run from DB state."""
    con = connect(ctx.cfg.storage["db_path"])
    return write_run_outputs(con, run_id, ctx.cfg, now=ctx.clock(), session_id=ctx.session_id, operation=ctx.operation)
