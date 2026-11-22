"""Scripted stand-ins for lane B/C logic + a runnable scenario, usable in-process and in a subprocess (crash tests).
Replaced by the real modules at integration (A-S1). Owner: lane A."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from paypilot.core import ids, pipeline
from paypilot.core.config import load_config, with_data_hash
from paypilot.core.db import connect
from paypilot.io import batch
from paypilot.io.request import load_request
from paypilot.resolve import normalize
from paypilot.schemas import (BankLine, CheckResult, CheckStatus, ClientData, DateBasis, Decision, ExceptionDraft, Facts,
                              Proposal, Reason, Resolution, Severity, Source, Vendor)
from tests.fakes import FakeExtClient, FakeHistory, fixed_clock

ROOT = Path(__file__).resolve().parents[1]
VENDORS = [Vendor(vendor_id="V1", legal_name="Swiggy Limited", name_key="SWIGGY", record_ref="vm#V1", default_account="Staff meals"),
           Vendor(vendor_id="V2", legal_name="Delhivery Limited", name_key="DELHIVERY", record_ref="vm#V2")]


def _src(v: Vendor) -> Source:
    return Source(source_id=ids.source_id("vendor_master", v.record_ref, "payee.legal_name", "legal_name"), provider="vendor_master",
                  record_ref=v.record_ref, fact="payee.legal_name", value=v.legal_name, field_or_passage="legal_name",
                  record_date=date(2026, 9, 30), record_date_basis=DateBasis.RECORD_FIELD, retrieved_at=fixed_clock())


def resolve_stub(item, ctx):
    text = (getattr(item, "descriptor_raw", None) or getattr(item, "vendor_name_raw", "")).upper()
    v = next((v for v in ctx.data.vendors if v.name_key in text), None)
    if v is None:
        return Resolution(status="UNRESOLVED", method="none", flags=["PAYEE_UNRESOLVED"])
    return Resolution(status="RESOLVED_VENDOR_MASTER", vendor_id=v.vendor_id, legal_name=v.legal_name, method="exact", score=100.0,
                      evidence=[_src(v)])


def checks_stub(item, res, ctx):
    return [CheckResult(check_id="stub", status=CheckStatus.PASS, evidence=res.evidence)]


def decide_stub(item, res, checks, policy):
    flags: list[str] = []
    if res.vendor_id is None:
        proposal, flags = Proposal.CANNOT_VERIFY, ["PAYEE_UNRESOLVED"]
    elif "DUP" in (getattr(item, "descriptor_raw", "") or "").upper():
        proposal, flags = Proposal.HELD, ["DUPLICATE_TRANSACTION"]
    elif isinstance(item, BankLine):
        proposal = Proposal.POSTED
    else:
        proposal = Proposal.AWAITING_APPROVAL
    facts = Facts(operation="POST_BANK_LINES" if isinstance(item, BankLine) else "PAY_INVOICES", kind=type(item).__name__,
                  vendor_id=res.vendor_id, amount_paise=getattr(item, "amount_paise", None), currency=getattr(item, "currency", None),
                  account="Staff meals" if res.vendor_id else None, flags=sorted(flags))
    return Decision(item_id=item.item_id, proposal=proposal, flags=flags, reasons=[Reason(code=f, text=f) for f in flags],
                    facts=facts, account=facts.account, payee_legal_name=res.legal_name,
                    decision_hash=ids.decision_hash(facts, policy.decision_hash_input))


def classify_stub(item, decision, recon, payment, policy):
    out = []
    for flag, sev, blocking in (("DUPLICATE_TRANSACTION", Severity.HIGH, True), ("PAYEE_UNRESOLVED", Severity.MEDIUM, False),
                                ("PARSE_FAILED", Severity.MEDIUM, False)):
        if flag in decision.flags:
            out.append(ExceptionDraft(type=flag, severity=sev, blocking=blocking, summary=flag, details=flag,
                                      recommended_action="review", origin_key=decision.decision_hash, item_id=decision.item_id))
    return out


def install(monkeypatch=None) -> None:
    """Patch the lane B/C entry points that pipeline.py imported, plus the normalizers io/ calls."""
    patches = [(pipeline, "resolve_vendor", resolve_stub), (pipeline, "run_checks", checks_stub), (pipeline, "decide", decide_stub),
               (pipeline, "classify", classify_stub),
               (normalize, "descriptor_key", lambda s, p: s.upper().replace("RAZ*", "").strip()),
               (normalize, "name_key", lambda s, p: s.upper().replace("LIMITED", "").strip()),
               (normalize, "invoice_canonical", lambda s, p: "".join(c for c in s.upper() if c.isalnum()))]
    for mod, name, fn in patches:
        monkeypatch.setattr(mod, name, fn) if monkeypatch else setattr(mod, name, fn)


def make_stage_ctx(db_path: str, session_id: str = "S0", operation=None, history=None):
    from paypilot.context import StageContext
    cfg, policy, snap = load_config("northbay", root=ROOT)
    cfg = cfg.model_copy(update={"storage": {**cfg.storage, "db_path": db_path}})
    policy = with_data_hash(policy, "dh")
    data = ClientData(client_id="northbay", vendors=VENDORS)
    ctx = StageContext(run_id="", session_id=session_id, client_id="northbay", operation=operation, period=None, cfg=cfg,
                       policy=policy, data=data, ext=FakeExtClient(), history=history or FakeHistory(), clock=fixed_clock)
    return ctx, cfg, snap


def run(workdir: str, db_path: str):
    """ingest + drive for <workdir>/request.json. Callable repeatedly (resume)."""
    req = load_request(Path(workdir) / "request.json")
    ctx, cfg, snap = make_stage_ctx(db_path, operation=req.operation)
    items = batch.load_items(req, ctx)
    con = connect(db_path)
    run_id, _ = pipeline.ingest_run(con, request=req, cfg=cfg, snapshot=snap, data_hash="dh", items=items, now=fixed_clock())
    sid = pipeline.open_session(con, run_id, "run", fixed_clock())
    import dataclasses
    ctx = dataclasses.replace(ctx, run_id=run_id, session_id=sid)
    summary = pipeline.drive(run_id, ctx, provider=None)
    pipeline.close_session(con, sid, 0, fixed_clock())
    return summary


def write_scenario(dirpath: Path) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "request.json").write_text(json.dumps({
        "schema_version": "1.0", "request_id": "r1", "client_id": "northbay", "operation": "POST_BANK_LINES",
        "inputs": {"bank_lines": "bank_lines.json"}}))
    row = lambda i, d, desc, amt: {"line_id": i, "date": d, "description": desc, "amount_inr": amt, "direction": "DEBIT", "currency": "INR"}
    (dirpath / "bank_lines.json").write_text(json.dumps([
        row("BL-1", "2026-10-03", "RAZ*SWIGGY BLR", "1240.00"), row("BL-2", "2026-10-04", "PAYTM*MYSTERY", "99.00"),
        row("BL-3", "2026-10-05", "SWIGGY DUP", "1240.00"), row("BL-4", "2026-10-06", "SWIGGY", 12.5),
        row("BL-5", "2026-10-07", "SWIGGY BLR", "500.00")]))
    return dirpath


if __name__ == "__main__":      # subprocess entry: python -m tests.pipeline_stubs <workdir> <db>
    install()
    run(sys.argv[1], sys.argv[2])
