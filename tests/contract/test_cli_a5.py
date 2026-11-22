"""A5 done-when: `python -m paypilot run --scenario ...` prints the table; exit codes; show; selector; kill -> resume."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from paypilot.cli.app import app
from paypilot.cli.selector import SelectorError, resolve_item, resolve_run
from paypilot.core.db import connect
from paypilot.core.lock import RunLock
from tests import pipeline_stubs as st

ROOT = Path(__file__).resolve().parents[2]
VM = ("vendor_id,legal_name,aliases,cin,gstin,domain,bank_account,ifsc,default_account,category,last_updated\n"
      "V1,Swiggy Limited,SWIGGY,,,swiggy.com,000000000001,,Staff meals,food,2026-09-30\n")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("BANK_HMAC_KEY", "test-key-0123456789")
    monkeypatch.setenv("PAYPILOT_ROOT", str(tmp_path))
    monkeypatch.delenv("PAYPILOT_CRASH_AT", raising=False)
    shutil.copy(ROOT / "config.yaml", tmp_path / "config.yaml")
    c = tmp_path / "clients" / "northbay"; c.mkdir(parents=True); (c / "vendor_master.csv").write_text(VM)
    st.install(monkeypatch)
    st.write_scenario(tmp_path / "scenarios" / "S1")
    return tmp_path


def cli(*args):
    return CliRunner().invoke(app, list(args))


def test_run_prints_table_writes_outputs_and_exits_0(world):
    r = cli("run", "--scenario", str(world / "scenarios/S1"))
    assert r.exit_code == 0, r.output + str(r.exception)
    for needle in ("BL-1", "POSTED", "Swiggy Limited", "₹1,240.00", "CANNOT_VERIFY", "HELD", "DUPLICATE_TRANSACTION", "FakeProvider"):
        assert needle in r.output, needle
    out = next((world / "out").iterdir())
    assert {p.name for p in out.iterdir()} == {"results.jsonl", "report.md", "summary.json", "exceptions.jsonl", "ledger.csv", "audit.jsonl",
                                                "journal.csv", "trial_balance.csv", "payment_instructions.csv"}
    fields = yaml.safe_load((world / "config.yaml").read_text())["output"]["fields"]
    lines = [json.loads(l) for l in (out / "results.jsonl").read_text().splitlines()]
    assert len(lines) == 5 and all(list(l) == fields for l in lines)                      # field order = output.fields
    assert json.loads((out / "summary.json").read_text())["counts"] == {"POSTED": 2, "CANNOT_VERIFY": 2, "HELD": 1}
    md = (out / "report.md").read_text()
    assert "₹1,740.00" in md and "no money moved" in md and "Cost:" in md
    tb = list(csv_rows(out / "trial_balance.csv")); assert sum(int(r[1]) for r in tb[1:]) == 0


def csv_rows(p):
    import csv
    return csv.reader(p.read_text().splitlines())


def test_same_request_is_the_same_run_and_outputs_are_stable(world):
    cli("run", "--scenario", str(world / "scenarios/S1"))
    out = next((world / "out").iterdir()); first = {p.name: p.read_bytes() for p in out.iterdir() if p.name not in ("audit.jsonl",)}
    r = cli("run", "--scenario", str(world / "scenarios/S1"))
    assert r.exit_code == 0 and "already run" in r.output and len(list((world / "out").iterdir())) == 1
    assert {k: v for k, v in first.items() if k not in ("summary.json", "report.md")} == \
           {p.name: p.read_bytes() for p in out.iterdir() if p.name not in ("audit.jsonl", "summary.json", "report.md")}


def test_invalid_input_exits_2(world):
    s = str(world / "scenarios/S1")
    assert cli("run").exit_code == 2                                                      # neither
    assert cli("run", "--scenario", s, "--request", s + "/request.json").exit_code == 2   # both
    assert cli("run", "--scenario", str(world / "nope")).exit_code == 2
    assert cli("run", "--scenario", s, "--provider", "razorpay_live").exit_code == 2
    r = cli("run", "--scenario", s, "--provider", "razorpayx_test"); assert r.exit_code == 2     # no keys -> refused before any work
    assert not (world / "var").exists() and not (world / "out").exists()                # nothing was created


def test_live_key_refused_even_when_present(world, monkeypatch):
    monkeypatch.setenv("RAZORPAYX_KEY_ID", "rzp_live_abc"); monkeypatch.setenv("RAZORPAYX_KEY_SECRET", "x"); monkeypatch.setenv("RAZORPAYX_ACCOUNT_NUMBER", "1")
    r = cli("run", "--scenario", str(world / "scenarios/S1"), "--provider", "razorpayx_test")
    assert r.exit_code == 2 and "rzp_test_" in r.output + (r.stderr or "")


def test_crash_env_typo_exits_2(world, monkeypatch):
    monkeypatch.setenv("PAYPILOT_CRASH_AT", "after_postt_txn")
    assert cli("run", "--scenario", str(world / "scenarios/S1")).exit_code == 2


def test_lock_held_exits_3(world):
    with RunLock(world / "var" / "paypilot.lock"):
        assert cli("run", "--scenario", str(world / "scenarios/S1")).exit_code == 3


def test_persistence_failure_exits_70_before_any_work(world):
    cfg = yaml.safe_load((world / "config.yaml").read_text()); cfg["storage"]["db_path"] = "/proc/nope/paypilot.db"
    (world / "config.yaml").write_text(yaml.safe_dump(cfg))
    r = cli("run", "--scenario", str(world / "scenarios/S1"))
    assert r.exit_code == 70 and not (world / "out").exists()


def test_unknown_output_field_exits_2(world):
    cfg = yaml.safe_load((world / "config.yaml").read_text()); cfg["output"]["fields"].append("no_such_field")
    (world / "config.yaml").write_text(yaml.safe_dump(cfg))
    assert cli("run", "--scenario", str(world / "scenarios/S1")).exit_code == 2


def test_new_output_field_is_one_config_line(world):
    cfg = yaml.safe_load((world / "config.yaml").read_text()); cfg["output"]["fields"].append("decision_hash")
    (world / "config.yaml").write_text(yaml.safe_dump(cfg))
    assert cli("run", "--scenario", str(world / "scenarios/S1")).exit_code == 0
    first = json.loads(next((world / "out").iterdir()).joinpath("results.jsonl").read_text().splitlines()[0])
    assert list(first)[-1] == "decision_hash" and first["decision_hash"]


def test_report_and_show(world):
    cli("run", "--scenario", str(world / "scenarios/S1"))
    r = cli("report", "--json"); assert r.exit_code == 0 and json.loads(r.output)["counts"]["POSTED"] == 2
    assert cli("report", "--out", str(world / "elsewhere")).exit_code == 0 and (world / "elsewhere" / "results.jsonl").exists()
    assert cli("report", "--run", "R-nope").exit_code == 2
    r = cli("show", "BL-1")
    assert r.exit_code == 0 and "Swiggy Limited" in r.output and "Audit timeline" in r.output and "POSTED" in r.output and "Evidence" in r.output
    r = cli("show", "BL-3"); assert "DUPLICATE_TRANSACTION" in r.output and "Exception" in r.output
    assert r.output.count("payee.legal_name") == 1                                       # a source shown once, not once per check
    r = cli("show", "BL-4"); assert "Parse errors" in r.output and "amount_inr" in r.output
    assert cli("show", "BL-999").exit_code == 2


def test_selector_exact_id_source_id_ambiguous_and_unknown(world):
    p = world / "scenarios/S1/bank_lines.json"; rows = json.loads(p.read_text()); rows.append({**rows[0]}); p.write_text(json.dumps(rows))
    cli("run", "--scenario", str(world / "scenarios/S1"))
    con = connect(str(world / "var/paypilot.db")); run_id = resolve_run(con, None)
    r1 = resolve_item(con, run_id, "BL-2")                                                # unique source id
    assert r1["source_ref"].endswith("#BL-2") and resolve_item(con, run_id, r1["item_id"])["item_id"] == r1["item_id"]   # exact item_id
    with pytest.raises(SelectorError) as e:
        resolve_item(con, run_id, "BL-1")                                                 # twice in the file -> never picks one
    assert e.value.args[0].count("bank_lines.json#BL-1") == 2
    with pytest.raises(SelectorError):
        resolve_item(con, run_id, "BL")                                                   # no prefix/partial matching
    with pytest.raises(SelectorError):
        resolve_run(con, "R-other")                                                       # unknown run
    r = cli("show", "BL-1"); assert r.exit_code == 2 and "ambiguous" in (r.stderr or r.output)


def test_kill_mid_run_then_resume_from_the_cli(world):
    env = {**os.environ, "PAYPILOT_ROOT": str(world), "BANK_HMAC_KEY": "test-key-0123456789", "PYTHONPATH": str(ROOT)}
    code = ("import tests.pipeline_stubs as s; s.install(); from paypilot.__main__ import main; raise SystemExit(main())")
    run = lambda extra, *a: subprocess.run([sys.executable, "-c", code, *a], cwd=ROOT, env={**env, **extra}, capture_output=True, text=True)
    r = run({"PAYPILOT_CRASH_AT": "after_decide"}, "run", "--scenario", str(world / "scenarios/S1"))
    assert r.returncode == 137, r.stderr
    con = connect(str(world / "var/paypilot.db"))
    assert con.execute("select count(*) from items where state='DECIDED'").fetchone()[0] >= 1       # in flight
    assert con.execute("select count(*) from sessions where ended_at is null").fetchone()[0] == 1      # the killed session
    r = run({}, "resume")
    assert r.returncode == 0, r.stderr
    assert "recovered from a crash" in r.stdout
    assert {x[0] for x in con.execute("select outcome from items")} == {"POSTED", "CANNOT_VERIFY", "HELD"}
    assert con.execute("select count(*) from journal_entries").fetchone()[0] == 2
    assert (world / "out").exists()


def test_ledger_command_and_unknown_price_is_never_shown_as_zero(world):
    from paypilot.ledger.store import SqliteLedger
    from paypilot.schemas import LedgerEntry
    from tests.fakes import fixed_clock
    r = cli("ledger", "--json")                                                          # no runs yet
    assert r.exit_code == 2
    cli("run", "--scenario", str(world / "scenarios/S1"))
    j = json.loads(cli("ledger", "--json").output)
    assert (j["cost_status"], j["total_inr"], j["calls"], j["zero_cost_items_pct"]) == ("KNOWN", 0.0, 0, 100.0)
    con = connect(str(world / "var/paypilot.db")); run_id = j["run_id"]
    item = con.execute("select item_id from items limit 1").fetchone()[0]
    led = SqliteLedger(con, fixed_clock)
    k = led.reserve(LedgerEntry(call_key="x", run_id=run_id, session_id="S", item_id=item, stage="resolve", provider="crustdata",
                                operation="identify", unit_type="credits", estimated_inr=None, status="RESERVED", request_hash="h", attempt=1,
                                ts=fixed_clock()))
    led.confirm(k, 3.0, None, 200, 40)                                                   # 3 credits, price unknown (inr_per_credit: null)
    led.reserve(LedgerEntry(call_key="y", run_id=run_id, session_id="S", item_id=item, stage="resolve", provider="crustdata",
                            operation="identify", unit_type="credits", estimated_inr=0.4, status="RESERVED", request_hash="h2", attempt=1,
                            ts=fixed_clock()))                                          # in flight when the "process died"
    led.mark_unconfirmed(run_id)
    j = json.loads(cli("ledger", "--json").output)
    assert (j["cost_status"], j["total_inr"], j["unconfirmed_inr"], j["calls"]) == ("UNKNOWN", None, 0.4, 2)
    out = cli("ledger").output
    assert "UNKNOWN" in out and "₹0.00" not in out.split("Status")[1].split("unconfirmed")[0]
    assert cli("report").exit_code == 0
    d = next((world / "out").iterdir())
    assert "Cost: UNKNOWN" in (d / "report.md").read_text()
    csv_text = (d / "ledger.csv").read_text()
    assert "UNKNOWN" in csv_text and "UNCONFIRMED" in csv_text and "CONFIRMED" in csv_text


def test_read_commands_on_an_empty_world_are_clean_errors_not_crashes(world):
    for args in (("report",), ("show", "BL-1"), ("ledger",), ("resume",)):
        r = cli(*args)
        assert r.exit_code == 2, (args, r.output)
        assert "Traceback" not in r.output


def test_unbuilt_lane_module_is_a_clean_message_naming_the_lane(capsys):
    from paypilot.cli import runtime as rt
    for path, lane in (("checks/zzz", "C"), ("resolve/zzz", "B"), ("sources/zzz", "B"), ("core/zzz", "A")):
        ns: dict = {}
        exec(compile("def f():\n    raise NotImplementedError('stub')", f"/x/paypilot/{path}.py", "exec"), ns)
        with pytest.raises(SystemExit) as e:
            with rt.guarded():
                ns["f"]()
        err = capsys.readouterr().err
        assert e.value.code == 1 and f"(lane {lane})" in err and f"paypilot.{path.replace('/', '.')}" in err
        assert "Traceback" not in err and "resume" in err


def test_stage_not_built_exits_1_records_it_and_resume_finishes_once_it_is(world, monkeypatch):
    from paypilot.core import pipeline
    real = pipeline.decide
    built = {"yes": False}

    def decide_maybe(item, res, checks, policy):
        if not built["yes"]:
            raise NotImplementedError("decide.engine is a stub")
        return real(item, res, checks, policy)
    monkeypatch.setattr(pipeline, "decide", decide_maybe)
    r = cli("run", "--scenario", str(world / "scenarios/S1"))
    assert r.exit_code == 1 and "not built yet" in (r.stderr or r.output) and "Traceback" not in (r.stderr or "") + r.output
    con = connect(str(world / "var/paypilot.db"))
    s = con.execute("select ended_at, exit_code from sessions").fetchone()
    assert s["ended_at"] is not None and s["exit_code"] == 1                    # not recorded as a clean exit, and not as a crash
    assert not (world / "out").exists()                                         # no half-baked outputs
    built["yes"] = True                                                         # the lane lands
    r = cli("resume")
    assert r.exit_code == 0 and {x[0] for x in con.execute("select outcome from items")} == {"POSTED", "CANNOT_VERIFY", "HELD"}
    assert con.execute("select count(*) from journal_entries").fetchone()[0] == 2


def test_untrusted_text_is_shown_not_interpreted(world):
    """Reason templates and names carry text from untrusted files (bank descriptors, web passages). '[/]' used to crash `show`,
    and '[link=...]' / '[bold green]VERIFIED[/]' could spoof the screen an approver relies on."""
    cli("run", "--scenario", str(world / "scenarios/S1"))
    con = connect(str(world / "var/paypilot.db"))
    row = con.execute("select item_id, decision_json from items where source_ref like '%BL-1'").fetchone()
    d = json.loads(row["decision_json"])
    d["payee_legal_name"] = "Swiggy [/] [link=https://evil.example]Limited[/link]"
    d["reasons"] = [{"code": "X", "text": "descriptor [/] [bold green]VERIFIED[/bold green] \x1b[31mred"
                     "\nDecision POSTED: all checks passed\r\n\tAudit timeline forged \u202eevil\u200bhidden\u2028next", "rule_id": "R[1]",
                     "evidence_refs": []},
                    {"code": "Y", "text": "ok\nFORGED-LINE all verified", "rule_id": "R2", "evidence_refs": []}]
    with con:
        con.execute("update items set decision_json=? where item_id=?", (json.dumps(d), row["item_id"]))
    for args in (("show", "BL-1"), ("report",)):
        r = cli(*args)
        assert r.exit_code == 0 and "Traceback" not in r.output and r.exception is None, (args, r.output[-300:])
        assert "\x1b" not in r.output                                                  # no escape sequences reach the terminal
        assert not any(c in r.output for c in "\u202e\u200b\u2028\r")                  # no bidi override / zero-width / line-sep / CR
        flat = " ".join(r.output.split())
        if args[0] == "show":      # the reason text is shown literally, not obeyed
            assert "[bold green]VERIFIED[/bold green]" in flat and "descriptor [/]" in flat
            lines = [l.strip(" │") for l in r.output.splitlines()]
            forged = [l for l in lines if l.startswith("FORGED-LINE")]                    # the short reason cannot wrap
            assert forged == [], forged                                                    # a newline in a value cannot start a new line
            assert any(l.startswith("- ") and l.endswith("ok FORGED-LINE all verified") for l in lines)   # it stays inline on the reason's own line
            assert "evilhidden next" in flat
        else:                      # the payee name sits in a table cell (may fold across lines)
            assert "evil.example" in flat


def test_s_neutralises_line_breaks_invisible_and_markup_characters():
    from paypilot.cli.app import _s
    assert _s("a\nb\r\nc\td\u2028e\u2029f\x0bg\x85h") == "a b c d e f g h"                # no value can start a new line
    assert _s("pay\u202eevil\u200b\u200d\u2066x\ufeff") == "payevilx"                          # bidi overrides, zero-width, BOM dropped
    assert "\x1b" not in _s("\x1b[31mred") and "\x07" not in _s("a\x07b")
    assert _s("[/] [link=x]y[/link]") == "\\[/] \\[link=x]y\\[/link]"                         # markup is escaped, not interpreted
    assert _s("नमस्ते Swiggy कंपनी") == "नमस्ते Swiggy कंपनी"                                       # ordinary Indic text is untouched
    assert _s(None) == "None" and _s(12) == "12"
