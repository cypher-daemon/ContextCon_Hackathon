"""CLI (INTERFACES §10): run, resume, report, show. Owner: lane A.

Exit codes: 0 ok · 2 invalid input · 3 lock held · 70 persistence failure · 75 stopped at a limit.
Commands that write take the process lock. Every output is regenerated from the database only.
"""
from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from paypilot.cli import runtime as rt
from paypilot.cli.selector import resolve_item, resolve_run
from paypilot.context import StageContext
from paypilot.core import pipeline
from paypilot.core.db import connect, tx
from paypilot.core.lock import RunLock
from paypilot.core.null_ext import NullExtClient
from paypilot.io import batch
from paypilot.io.request import load_request
from paypilot.reports import writer
from paypilot.reports.writer import fmt_inr, load_views
from paypilot.schemas import Operation, Period, RunSummary

app = typer.Typer(add_completion=False, no_args_is_help=True, help="PayPilot: pay every invoice at most once, never guess a payee.")
console = Console(width=max(shutil.get_terminal_size((120, 20)).columns, 110))
_RUN_OPT = typer.Option(None, "--run", help="Run id (default: the most recent run).")


# ---------------- rendering ----------------
def render_summary(con, run_id: str, summary: RunSummary, out_dir: Path | None) -> None:
    t = Table(title=f"Run {run_id} · {summary.status}", show_lines=False)
    for col in ("Item", "Outcome", "Payee", "Amount", "Flags"):
        t.add_column(col, overflow="fold")
    for v in load_views(con, run_id):
        d = v.decision
        t.add_row(v.row["source_ref"].split("#", 1)[-1], v.row["outcome"] or v.row["state"],
                  (d or {}).get("payee_legal_name") or "-", fmt_inr(v.row["amount_paise"]), ", ".join((d or {}).get("flags", [])) or "-")
    console.print(t)
    console.print("Outcomes: " + (", ".join(f"{k} {n}" for k, n in sorted(summary.counts.items())) or "none") +
                  f" · open exceptions: {sum(summary.open_exceptions.values())} · cost: {summary.cost.cost_status}"
                  + (" · recovered from a crash" if summary.recovered_from_crash else ""))
    if out_dir:
        console.print(f"Outputs: {out_dir}")


def _ctx(loaded: rt.Loaded, run_id: str, session_id: str, client_id: str, operation, period) -> StageContext:
    # history is bound to the run's connection inside drive(); ext fails closed until sources/http.py is wired (A6)
    return StageContext(run_id=run_id, session_id=session_id, client_id=client_id, operation=operation, period=period,
                        cfg=loaded.cfg, policy=loaded.policy, data=loaded.data, ext=NullExtClient(), history=None,  # type: ignore[arg-type]
                        clock=rt.utcnow)


def _finish(con, ctx: StageContext, run_id: str) -> RunSummary:
    summary = pipeline.drive(run_id, ctx, provider=None)
    writer.write_run_outputs(con, run_id, ctx.cfg, now=ctx.clock(), session_id=ctx.session_id, operation=ctx.operation)
    return summary


def _exit_for(summary: RunSummary) -> int:
    return rt.EXIT_LIMIT if summary.status.value.startswith("STOPPED") else rt.EXIT_OK


# ---------------- commands ----------------
@app.command()
def run(scenario: Optional[Path] = typer.Option(None, "--scenario", help="Directory holding request.json"),
        request: Optional[Path] = typer.Option(None, "--request", help="Path to a request.json"),
        provider: Optional[str] = typer.Option(None, "--provider", help="fake | razorpayx_test")) -> None:
    """Run a request. The same request file is the same run (resume semantics)."""
    code = rt.EXIT_OK
    with rt.guarded():
        root = rt.start()
        if (scenario is None) == (request is None):
            raise rt.CliError("give exactly one of --scenario DIR or --request PATH")
        req_path = (scenario / "request.json") if scenario else request
        req = load_request(req_path)
        loaded = rt.load_all(req.client_id, root)
        provider_name = rt.check_provider(provider, loaded.cfg)
        console.print(f"Payment provider: {'FakeProvider (no real money)' if provider_name == 'fake' else 'RazorpayX TEST MODE (no real money)'}")
        with RunLock(loaded.cfg.storage["lock_path"], wait_s=0):
            con = connect(loaded.cfg.storage["db_path"])
            ctx0 = _ctx(loaded, "", "", req.client_id, req.operation, req.period)
            items = batch.load_items(req, ctx0)
            snapshot = {**loaded.snapshot, "_request": {"period": req.period.model_dump(mode="json", by_alias=True) if req.period else None,
                                                         "limits": req.limits.model_dump() if req.limits else None,
                                                         "source_path": str(req_path)}}
            run_id, created = pipeline.ingest_run(con, request=req, cfg=loaded.cfg, snapshot=snapshot, data_hash=loaded.data_hash,
                                                  items=items, now=rt.utcnow())
            sid = pipeline.open_session(con, run_id, "run", rt.utcnow())
            try:
                rt.begin_recovery(con, run_id)
                ctx = dataclasses.replace(ctx0, run_id=run_id, session_id=sid)
                summary = _finish(con, ctx, run_id)
                code = _exit_for(summary)
            finally:
                pipeline.close_session(con, sid, code, rt.utcnow())
        if not created:
            console.print("[dim]This request was already run: continuing the same run.[/dim]")
        render_summary(con, run_id, summary, Path(loaded.cfg.output["dir"].format(run_id=run_id)))
    raise typer.Exit(code)


@app.command()
def resume(run_id: Optional[str] = _RUN_OPT) -> None:
    """Continue a run from its persisted state (after a crash, a stop, or new data)."""
    code = rt.EXIT_OK
    with rt.guarded():
        root = rt.start()
        paths = rt.storage_paths(root)
        with RunLock(paths["lock_path"], wait_s=0):
            con = connect(paths["db_path"])
            run_id = resolve_run(con, run_id)
            run = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            loaded = rt.load_all(run["client_id"], root)
            req = json.loads(run["policy_snapshot_json"]).get("_request") or {}
            period = Period(**req["period"]) if req.get("period") else None
            operation = Operation(run["operation"]) if run["operation"] else None
            sid = pipeline.open_session(con, run_id, "resume", rt.utcnow())
            try:
                rt.begin_recovery(con, run_id)
                summary = _finish(con, _ctx(loaded, run_id, sid, run["client_id"], operation, period), run_id)
                code = _exit_for(summary)
            finally:
                pipeline.close_session(con, sid, code, rt.utcnow())
        render_summary(con, run_id, summary, Path(loaded.cfg.output["dir"].format(run_id=run_id)))
    raise typer.Exit(code)


@app.command()
def report(run_id: Optional[str] = _RUN_OPT,
           as_json: bool = typer.Option(False, "--json", help="Print the run summary as JSON."),
           out: Optional[Path] = typer.Option(None, "--out", help="Write the output files here instead of out/<run_id>/.")) -> None:
    """Regenerate every output file for a run from the database, and show the summary."""
    with rt.guarded():
        root = rt.start()
        paths = rt.storage_paths(root)
        con = connect(paths["db_path"], apply_schema=False)
        run_id = resolve_run(con, run_id)
        client_id = con.execute("SELECT client_id FROM runs WHERE run_id=?", (run_id,)).fetchone()["client_id"]
        cfg, _, _ = rt.load_cfg(client_id, root)
        if out is not None:
            cfg = cfg.model_copy(update={"output": {**cfg.output, "dir": str(out.resolve())}})
        with RunLock(paths["lock_path"], wait_s=0):          # outputs are written atomically; the lock keeps two writers apart
            written = writer.write_run_outputs(con, run_id, cfg, now=rt.utcnow())
        out_dir = written[0].parent
        summary = pipeline.build_summary(con, run_id, SimpleNamespace(session_id="", cfg=cfg, clock=rt.utcnow, operation=None))
        if as_json:
            print(summary.model_dump_json(indent=2))
        else:
            render_summary(con, run_id, summary, out_dir)


def _j(text):
    return json.loads(text) if text else None


@app.command()
def show(item: str = typer.Argument(..., help="Exact item_id, or a unique invoice_id / line_id / message_id in the run."),
         run_id: Optional[str] = _RUN_OPT) -> None:
    """Why this item got its outcome: decision, rules, flags, checks, evidence, audit timeline, exceptions, payment."""
    with rt.guarded():
        root = rt.start()
        con = connect(rt.storage_paths(root)["db_path"], apply_schema=False)
        run_id = resolve_run(con, run_id)
        row = resolve_item(con, run_id, item)
        d, res, checks = _j(row["decision_json"]), _j(row["resolution_json"]), _j(row["checks_json"]) or []
        head = Table.grid(padding=(0, 2))
        for k, v in (("Item", row["item_id"]), ("Source", row["source_ref"]), ("State", f"{row['state']} (v{row['version']})"),
                     ("Outcome", row["outcome"] or "-"), ("Amount", fmt_inr(row["amount_paise"])),
                     ("Decision hash", row["decision_hash"] or "-")):
            head.add_row(f"[bold]{k}[/bold]", str(v))
        console.print(Panel(head, title=f"Item {row['source_ref'].split('#', 1)[-1]}"))
        if row["kind"] == "ParseFailure":
            console.print("Parse errors: " + "; ".join(json.loads(row["parse_errors_json"])))
        if res:
            console.print(f"[bold]Resolution[/bold] {res['status']} via {res['method']}"
                          + (f" -> {res['legal_name']} ({res['vendor_id']})" if res.get("vendor_id") else "")
                          + (f" · candidates {[c[0] for c in res['candidates']]}" if res.get("candidates") else ""))
        if checks:
            t = Table(title="Checks")
            for c in ("Check", "Status", "Flags", "Reasons"):
                t.add_column(c, overflow="fold")
            for c in checks:
                t.add_row(c["check_id"], c["status"], ", ".join(c["flags"]) or "-", "; ".join(c["reasons"]) or "-")
            console.print(t)
        if d:
            console.print(f"[bold]Decision[/bold] {d['proposal']} · flags {d['flags'] or '-'} · rules {d['rule_ids'] or '-'}")
            for r in d["reasons"]:
                console.print(f"  - [{r.get('rule_id') or '-'}] {r['code']}: {r['text']}")
            if d.get("missing"):
                console.print("  missing: " + ", ".join(d["missing"]))
        srcs = con.execute("SELECT DISTINCT s.* FROM item_evidence e JOIN sources s ON s.source_id=e.source_id "
                           "WHERE e.item_id=? AND e.decision_hash=? ORDER BY s.source_id", (row["item_id"], row["decision_hash"] or "")).fetchall()
        if srcs:
            t = Table(title="Evidence")
            for c in ("Source", "Provider", "Fact", "Value", "Record", "Record date"):
                t.add_column(c, overflow="fold")
            for s_ in srcs:
                t.add_row(s_["source_id"], s_["provider"], s_["fact"], s_["value"], s_["record_ref"],
                          f"{s_['record_date'] or 'unknown'} ({s_['record_date_basis']})")
            console.print(t)
        ev = con.execute("SELECT * FROM audit_events WHERE entity_type='item' AND entity_id=? ORDER BY event_id", (row["item_id"],)).fetchall()
        t = Table(title="Audit timeline")
        for c in ("#", "Time", "From", "To", "Actor", "Reason"):
            t.add_column(c, overflow="fold")
        for e in ev:
            t.add_row(str(e["event_id"]), e["ts"], e["previous_state"] or "-", e["new_state"] or "-", e["actor"], e["reason"] or "-")
        console.print(t)
        for e in con.execute("SELECT * FROM exceptions WHERE item_id=? ORDER BY exception_id", (row["item_id"],)):
            console.print(f"[bold]Exception[/bold] {e['exception_id']} {e['severity']} {e['type']} ({e['status']}): {e['summary']} -> {e['recommended_action']}")
        pay = con.execute("SELECT * FROM payments WHERE item_id=?", (row["item_id"],)).fetchone()
        if pay:
            console.print(f"[bold]Payment[/bold] {pay['payment_key']} {pay['status']} settlement {pay['settlement_status']} via {pay['provider']}")
            for a in con.execute("SELECT * FROM payment_attempts WHERE payment_id=? ORDER BY attempt_no", (pay["payment_id"],)):
                console.print(f"  attempt {a['attempt_no']} {a['attempt_key']} step {a['step']} outcome {a['outcome']} ref {a['provider_ref'] or '-'}")
