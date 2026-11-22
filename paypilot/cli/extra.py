"""More CLI commands (INTERFACES §10): exceptions, exception, resolve-exception, audit verify, demo reset, fake-provider. Owner: lane A."""
from __future__ import annotations

import json
import shutil
from contextlib import closing
from pathlib import Path
from typing import List, Optional

import typer
from rich.table import Table

from paypilot.audit.verify import verify_run
from paypilot.cli import runtime as rt
from paypilot.cli.app import _RUN_OPT, _now_session, _s, app, console
from paypilot.cli.selector import resolve_run
from paypilot.core import pipeline
from paypilot.core.exceptions_repo import ExceptionRefused, resolve_exception
from paypilot.core.lock import RunLock
from paypilot.approvals.service import check_reviewer

_SEV = "CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END"
_CLOSED = ("RESOLVED", "FALSE_POSITIVE", "ACCEPTED_RISK", "CLOSED_NO_ACTION")


@app.command()
def exceptions(run_id: Optional[str] = _RUN_OPT, only_open: bool = typer.Option(False, "--open", help="Only exceptions that are not closed."),
               severity: Optional[str] = typer.Option(None, "--severity", help="CRITICAL | HIGH | MEDIUM | LOW")) -> None:
    """Exceptions, sorted by severity then age."""
    with rt.guarded() as stack:
        root = rt.start()
        con = stack.enter_context(closing(rt.open_existing_db(root)))
        run_id = resolve_run(con, run_id)
        sql, args = f"SELECT * FROM exceptions WHERE run_id=?", [run_id]
        if only_open:
            sql += " AND status NOT IN (" + ",".join("?" * len(_CLOSED)) + ")"
            args += list(_CLOSED)
        if severity:
            sql, args = sql + " AND severity=?", args + [severity.upper()]
        t = Table(title=f"Exceptions · run {run_id}")
        for c in ("Id", "Severity", "Type", "Status", "Item", "Summary"):
            t.add_column(c, overflow="fold")
        for e in con.execute(sql + f" ORDER BY {_SEV}, created_at, exception_id", args):
            ref = con.execute("SELECT source_ref FROM items WHERE item_id=?", (e["item_id"],)).fetchone() if e["item_id"] else None
            t.add_row(e["exception_id"], _s(e["severity"]), _s(e["type"]), _s(e["status"]), _s(ref["source_ref"].split("#", 1)[-1] if ref else "(run)"),
                      _s(e["summary"]))
        console.print(t)


@app.command()
def exception(exc_id: str = typer.Argument(..., help="Exception id.")) -> None:
    """One exception in full: what happened, evidence, recovery case, and its audit trail."""
    with rt.guarded() as stack:
        root = rt.start()
        con = stack.enter_context(closing(rt.open_existing_db(root)))
        e = con.execute("SELECT * FROM exceptions WHERE exception_id=?", (exc_id,)).fetchone()
        if e is None:
            raise rt.CliError(f"no exception {exc_id!r}")
        for k in ("exception_id", "run_id", "item_id", "payment_id", "vendor_id", "type", "severity", "status", "summary", "details",
                  "recommended_action", "resolution_code", "resolution_note", "resolved_by", "resolved_at"):
            console.print(f"[bold]{k}[/bold]: {_s(e[k])}")
        console.print("[bold]evidence[/bold]: " + _s(", ".join(json.loads(e["evidence_refs_json"])) or "-"))
        rec = con.execute("SELECT * FROM recovery_cases WHERE exception_id=?", (exc_id,)).fetchone()
        if rec:
            console.print(f"[bold]recovery case[/bold] {rec['recovery_id']} {_s(rec['status'])}: {_s(rec['requested_action'])}")
        for a in con.execute("SELECT * FROM audit_events WHERE entity_type='exception' AND entity_id=? ORDER BY event_id", (exc_id,)):
            console.print(f"  #{a['event_id']} {_s(a['ts'])} {_s(a['previous_state'] or '-')} -> {_s(a['new_state'])} by {_s(a['actor'])}: {_s(a['reason'] or '')}")


@app.command("resolve-exception")
def resolve_exception_cmd(exc_id: str = typer.Argument(..., help="Exception id."),
                          by: str = typer.Option(..., "--by", help="Who (must be in the approver allowlist, if one is set)."),
                          resolution: str = typer.Option(..., "--resolution", help="RESOLVED | RECOVERED | FALSE_POSITIVE | ACCEPTED_RISK | CLOSED_NO_ACTION"),
                          note: str = typer.Option(..., "--note", help="Why (required)."),
                          evidence: Optional[List[str]] = typer.Option(None, "--evidence", help="Source id or external reference (repeatable)."),
                          ) -> None:
    """Close an exception. RESOLVED / RECOVERED need evidence; closing a CRITICAL one lifts the vendor containment."""
    with rt.guarded() as stack:
        root = rt.start()
        paths = rt.storage_paths(root)
        with RunLock(paths["lock_path"], wait_s=float(rt.runtime_setting(root, "approvals", "lock_wait_s", 10))):
            con = stack.enter_context(closing(rt.open_existing_db(root)))
            row = con.execute("SELECT run_id FROM exceptions WHERE exception_id=?", (exc_id,)).fetchone()
            if row is None:
                raise rt.CliError(f"no exception {exc_id!r}")
            client_id = con.execute("SELECT client_id FROM runs WHERE run_id=?", (row["run_id"],)).fetchone()["client_id"]
            cfg, _, _ = rt.load_cfg(client_id, root)
            try:
                who = check_reviewer(by, cfg.approvals.get("approvers"))
                sid = _now_session(con, row["run_id"], "resolve-exception")
                try:
                    status, rec = resolve_exception(con, exception_id=exc_id, by=who, resolution=resolution, note=note,
                                                    evidence=list(evidence or []), now=rt.utcnow(), session_id=sid)
                finally:
                    pipeline.close_session(con, sid, 0, rt.utcnow())
            except ExceptionRefused as e:
                raise rt.CliError(str(e))
            console.print(f"{exc_id}: {status}" + (f" · recovery case {rec}" if rec else ""))


audit_app = typer.Typer(help="Audit commands.", no_args_is_help=True)
app.add_typer(audit_app, name="audit")


@audit_app.command("verify")
def audit_verify(run_id: Optional[str] = _RUN_OPT) -> None:
    """Replay the run from the database. Exit 0 = the books verify; 1 = a discrepancy (each one is named)."""
    code = 0
    with rt.guarded() as stack:
        root = rt.start()
        con = stack.enter_context(closing(rt.open_existing_db(root)))
        run_id = resolve_run(con, run_id)
        client_id = con.execute("SELECT client_id FROM runs WHERE run_id=?", (run_id,)).fetchone()["client_id"]
        cfg, _, _ = rt.load_cfg(client_id, root)
        rep = verify_run(run_id, cfg)
        console.print(f"Audit verify · run {run_id}: {rep.items_checked} items, {rep.decisions_replayed} decisions replayed")
        console.print("Trial balance: " + (", ".join(f"{a} {n}" for a, n in rep.trial_balance.items()) or "empty") + f" · Σ = {sum(rep.trial_balance.values())}")
        if rep.ok:
            console.print("[bold green]BOOKS VERIFIED[/bold green]: decisions replay, links hold, entries balance, the audit chain is unbroken.")
        else:
            code = rt.EXIT_FAILED
            console.print(f"[bold red]{len(rep.discrepancies)} DISCREPANCY(IES)[/bold red]")
            for d in rep.discrepancies:
                console.print(f"  {_s(d['kind'])} · {_s(d['entity'])}: {_s(d['detail'])}")
    raise typer.Exit(code)


demo_app = typer.Typer(help="Demo helpers.", no_args_is_help=True)
app.add_typer(demo_app, name="demo")


@demo_app.command("reset")
def demo_reset(yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask.")) -> None:
    """Wipe var/ and out/ (demo and test only)."""
    with rt.guarded():
        root = rt.start()
        targets = [root / "var", root / "out"]
        if not yes and not typer.confirm("This deletes " + ", ".join(str(t) for t in targets) + ". Continue?"):
            raise typer.Exit(1)
        for t in targets:
            shutil.rmtree(t, ignore_errors=True)
        console.print("demo state wiped (var/, out/).")


fake_app = typer.Typer(help="Drive the FakeProvider like the RazorpayX test Dashboard (demo only).", no_args_is_help=True)
app.add_typer(fake_app, name="fake-provider")


@fake_app.command("set")
def fake_set(provider_ref: str = typer.Argument(..., help="fp_… ref"),
             status: str = typer.Argument(..., help="SUCCEEDED | PROCESSING | DECLINED | RETURNED ...")) -> None:
    """Move a fake payment to a status by hand (e.g. the bank returns the money)."""
    with rt.guarded():
        root = rt.start()
        from paypilot.payments.fake import FakeProvider
        p = FakeProvider(rt.storage_paths(root)["fake_provider_db"])
        try:
            p.force_status(provider_ref, status.upper())
        finally:
            p.close()
        console.print(f"{provider_ref} -> {status.upper()} (provider side only; run `resume` to reconcile)")


@fake_app.command("list")
def fake_list() -> None:
    """Everything the fake provider has been asked to pay."""
    with rt.guarded():
        root = rt.start()
        from paypilot.payments.fake import FakeProvider
        p = FakeProvider(rt.storage_paths(root)["fake_provider_db"])
        try:
            t = Table(title="FakeProvider payments")
            for c in ("ref", "idempotency key", "amount_paise", "status", "successes"):
                t.add_column(c, overflow="fold")
            for r in p.con.execute("SELECT * FROM fake_payments ORDER BY created_at"):
                t.add_row(r["ref"], r["idem_key"], str(r["amount_paise"]), r["status"], str(r["success_count"]))
            console.print(t)
        finally:
            p.close()
