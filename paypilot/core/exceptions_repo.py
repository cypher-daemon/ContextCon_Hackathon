"""Exception + recovery-case persistence helpers (A11). Owner: lane A. All functions run inside the caller's transaction."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from paypilot.core import ids
from paypilot.core.audit import append_event
from paypilot.core.pipeline import upsert_exceptions
from paypilot.schemas import ExceptionDraft, Severity


def add_exception(con: sqlite3.Connection, *, run_id: str, session_id: str, item_id: str, vendor_id: str | None, now: datetime,
                  type_: str, severity: Severity, summary: str, details: str, action: str, payment_id: str | None = None,
                  blocking: bool = True, recovery: bool = False, origin_key: str = "") -> str:
    """Upsert one exception (deterministic id) and, if asked, its recovery case. Returns the exception_id."""
    draft = ExceptionDraft(type=type_, severity=severity, blocking=blocking, summary=summary, details=details, recommended_action=action,
                           origin_key=origin_key or (payment_id or item_id), item_id=item_id, payment_id=payment_id,
                           open_recovery_case=recovery)
    upsert_exceptions(con, run_id=run_id, session_id=session_id, item_id=item_id, vendor_id=vendor_id, drafts=[draft], now=now)
    eid = ids.exception_id(item_id, payment_id, type_, draft.origin_key)
    if recovery and payment_id:
        rid = ids.sha256_hex("recovery", eid)[:16]
        cur = con.execute("INSERT OR IGNORE INTO recovery_cases(recovery_id,exception_id,payment_id,reason,requested_action,status,external_ref,"
                          "evidence_refs_json,recovered_amount_paise,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (rid, eid, payment_id, summary, action, "OPEN", None, "[]", None, now.isoformat(), now.isoformat()))
        if cur.rowcount == 1:
            append_event(con, run_id=run_id, session_id=session_id, entity_type="recovery_case", entity_id=rid, previous_state=None,
                         new_state="OPEN", actor="system", ts=now, reason=summary, metadata={"exception_id": eid, "payment_id": payment_id})
    return eid


# ---------------- lifecycle (A11) ----------------
CLOSED = frozenset({"RESOLVED", "FALSE_POSITIVE", "ACCEPTED_RISK", "CLOSED_NO_ACTION"})
NEEDS_EVIDENCE = frozenset({"RESOLVED"})                     # RESOLVED (and a RECOVERED recovery case) need evidence
RESOLUTION_CODES = {"RESOLVED": "RESOLVED", "RECOVERED": "RESOLVED", "FALSE_POSITIVE": "FALSE_POSITIVE", "ACCEPTED_RISK": "ACCEPTED_RISK",
                    "CLOSED_NO_ACTION": "CLOSED_NO_ACTION", "INVESTIGATING": "INVESTIGATING", "ACTION_REQUIRED": "ACTION_REQUIRED"}


class ExceptionRefused(Exception):
    """A refused exception transition (exit 2)."""


def can_move(frm: str, to: str, evidence_refs: list[str]) -> bool:
    """Lane C's lifecycle rule when it exists; otherwise the same rule here: a closed exception never reopens, RESOLVED needs evidence."""
    try:
        from paypilot.exceptions.lifecycle import can_transition
        from paypilot.schemas import ExceptionStatus
        return bool(can_transition(ExceptionStatus(frm), ExceptionStatus(to), evidence_refs))
    except NotImplementedError:
        if frm in CLOSED:
            return False
        return bool(evidence_refs) if to in NEEDS_EVIDENCE else True


def resolve_exception(con: sqlite3.Connection, *, exception_id: str, by: str, resolution: str, note: str, evidence: list[str],
                      now: datetime, session_id: str) -> tuple[str, str | None]:
    """Move an exception (and its recovery case) along its lifecycle. Returns (new exception status, recovery status or None)."""
    from paypilot.core.db import tx
    code = resolution.upper()
    if code not in RESOLUTION_CODES:
        raise ExceptionRefused(f"--resolution must be one of {sorted(RESOLUTION_CODES)}")
    if not note.strip():
        raise ExceptionRefused("a written --note is required")
    evidence = [e.strip() for e in evidence if e and e.strip()]
    with tx(con):
        e = con.execute("SELECT * FROM exceptions WHERE exception_id=?", (exception_id,)).fetchone()
        if e is None:
            raise ExceptionRefused(f"no exception {exception_id}")
        new = RESOLUTION_CODES[code]
        if not can_move(e["status"], new, evidence):
            why = ("a closed exception cannot be reopened" if e["status"] in CLOSED else f"{new} needs evidence (--evidence SOURCE_ID_OR_REF)")
            raise ExceptionRefused(f"{e['status']} -> {new} refused: {why}")
        rec = con.execute("SELECT * FROM recovery_cases WHERE exception_id=?", (exception_id,)).fetchone()
        rec_status = None
        if rec is not None:
            rec_status = {"RECOVERED": "RECOVERED", "FALSE_POSITIVE": "FALSE_POSITIVE", "CLOSED_NO_ACTION": "CLOSED_NO_ACTION"}.get(code)
            if code == "RESOLVED" and rec["status"] != "RECOVERED":
                raise ExceptionRefused("this exception has a recovery case: close it with --resolution RECOVERED (with evidence), "
                                       "FALSE_POSITIVE or CLOSED_NO_ACTION")
            if rec_status is None and new in CLOSED:
                rec_status = "CLOSED_NO_ACTION"
        con.execute("UPDATE exceptions SET status=?, resolution_code=?, resolution_note=?, resolved_by=?, resolved_at=?, updated_at=?, "
                    "evidence_refs_json=? WHERE exception_id=?",
                    (new, code, note.strip(), by, now.isoformat() if new in CLOSED else None, now.isoformat(),
                     json.dumps(sorted(set(json.loads(e["evidence_refs_json"])) | set(evidence))), exception_id))
        append_event(con, run_id=e["run_id"], session_id=session_id, entity_type="exception", entity_id=exception_id, previous_state=e["status"],
                     new_state=new, actor=by, ts=now, reason=note.strip(), metadata={"resolution": code, "evidence": evidence})
        if rec is not None and rec_status:
            con.execute("UPDATE recovery_cases SET status=?, evidence_refs_json=?, updated_at=? WHERE recovery_id=?",
                        (rec_status, json.dumps(evidence), now.isoformat(), rec["recovery_id"]))
            append_event(con, run_id=e["run_id"], session_id=session_id, entity_type="recovery_case", entity_id=rec["recovery_id"],
                         previous_state=rec["status"], new_state=rec_status, actor=by, ts=now, reason=note.strip(), metadata={"evidence": evidence})
    return new, rec_status
