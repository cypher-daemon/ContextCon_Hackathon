"""A1 done-when: hashes stable; lock refuses a 2nd process; JSONL masks accounts; audit is atomic."""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from paypilot.core import ids
from paypilot.core.audit import append_event
from paypilot.core.config import ConfigError, load_config, merge_config, with_data_hash
from paypilot.core.db import PersistenceFailure, connect, tx
from paypilot.core.lock import RunLock
from paypilot.core.log import JsonlLogger, mask_text
from paypilot.schemas import Facts, Operation

NOW = datetime(2026, 11, 22, tzinfo=timezone.utc)


def test_config_hashes_stable_and_data_bound():
    c1, p1, _ = load_config("northbay")
    c2, p2, _ = load_config("northbay")
    assert (c1.config_hash, c1.policy_hash) == (c2.config_hash, c2.policy_hash)
    assert with_data_hash(p1, "d1").decision_hash_input != with_data_hash(p1, "d2").decision_hash_input


def test_runtime_limit_change_keeps_policy_hash_but_changes_config_hash(tmp_path):
    import yaml
    c1, _, _ = load_config("northbay")
    cfg = yaml.safe_load(Path("config.yaml").read_text()); cfg["limits"]["max_cost_inr"] = 1.0
    p = tmp_path / "config.yaml"; p.write_text(yaml.safe_dump(cfg))
    c2, _, _ = load_config("northbay", config_path=p)
    assert c2.policy_hash == c1.policy_hash          # decisions (and approvals) are NOT staled by a limit change
    assert c2.config_hash != c1.config_hash


def test_client_cannot_override_runtime_sections_or_provider():
    with pytest.raises(ConfigError):
        merge_config({"limits": {}}, {"limits": {"max_cost_inr": 999}})


def test_bad_provider_refused(tmp_path):
    import yaml
    cfg = yaml.safe_load(Path("config.yaml").read_text()); cfg["payments"]["provider"] = "razorpay_live"
    p = tmp_path / "config.yaml"; p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ConfigError):
        load_config("northbay", config_path=p)


def test_ids_deterministic_and_payment_key_immutable_shape():
    assert ids.run_id("c", "s") == ids.run_id("c", "s") and ids.run_id("c", "s").startswith("R-")
    k = ids.payment_key("c", "V1", "INV1", 100, "INR")
    assert k == ids.payment_key("c", "V1", "INV1", 100, "INR") and k.startswith("pp_") and len(k) == 35
    with pytest.raises(ValueError):
        ids.payment_key("c", "V1", "", 100, "INR")
    a = ids.attempt_key(k, 1)
    assert a == f"{k}:a1" and len(ids.razorpayx_idempotency_key(a)) == 36
    assert ids.razorpayx_idempotency_key(a) == ids.razorpayx_idempotency_key(a)
    assert ids.approval_id("i", "h", 1) != ids.approval_id("i", "h", 2)


def test_decision_hash_ignores_flag_order():
    f = lambda fl: Facts(operation=Operation.POST_BANK_LINES, kind="BankLine", flags=fl)
    assert ids.decision_hash(f(["A", "B"]), "x") == ids.decision_hash(f(["B", "A"]), "x")


def test_fingerprint_hides_raw_number():
    fp = ids.fingerprint_account("1234 5678 9012", "k")
    assert "123456789012" not in fp and fp == ids.fingerprint_account("123456789012", "k")


def test_lock_refuses_second_process(tmp_path):
    lock = tmp_path / "l.lock"
    with RunLock(lock):
        code = ("from paypilot.core.lock import RunLock,LockHeld\n"
                f"try:\n RunLock({str(lock)!r}).acquire()\nexcept LockHeld:\n raise SystemExit(3)\n")
        assert subprocess.run([sys.executable, "-c", code]).returncode == 3
    RunLock(lock).acquire().release()                # free again after release


def test_log_masks_accounts_and_secrets(tmp_path):
    JsonlLogger(tmp_path).log("x", acct="pay 123456789012 now", key_secret="s3", h="Basic QWxhZGRpbjpvcGVu")
    line = (tmp_path / "paypilot.jsonl").read_text()
    assert "123456789012" not in line and "9012" in line and "s3" not in line and "QWxhZGRpbjpvcGVu" not in line
    assert mask_text("rzp_test_abc123") == "[REDACTED]"


def _item(con):
    con.execute("INSERT INTO runs VALUES ('R','c','r','s',NULL,'h','p','d','{}','RUNNING','t','t')")


def test_audit_event_is_atomic_with_state_change():
    con = connect(":memory:")
    with tx(con):
        _item(con)
    with pytest.raises(RuntimeError):
        append_event(con, run_id="R", session_id="S", entity_type="item", entity_id="i", previous_state=None,
                     new_state="NEW", actor="t", ts=NOW)            # outside a txn -> refused
    with pytest.raises(PersistenceFailure):
        with tx(con):
            append_event(con, run_id="R", session_id="S", entity_type="item", entity_id="i", previous_state=None,
                         new_state="NEW", actor="t", ts=NOW)
            con.execute("INSERT INTO items(item_id) VALUES ('bad')")  # violates NOT NULL -> whole txn rolls back
    assert con.execute("select count(*) from audit_events").fetchone()[0] == 0
    with tx(con):
        append_event(con, run_id="R", session_id="S", entity_type="item", entity_id="i", previous_state=None,
                     new_state="NEW", actor="t", ts=NOW)
    assert con.execute("select count(*) from audit_events").fetchone()[0] == 1


def test_sha256_parts_are_unambiguous():
    assert ids.sha256_hex("a|b", "c") != ids.sha256_hex("a", "b|c")
    assert ids.run_id("c|x", "s") != ids.run_id("c", "x|s")


def test_log_masks_separated_int_and_glued_accounts(tmp_path):
    fp = "ab" * 32
    JsonlLogger(tmp_path).log("x", a="1234-5678-9012", b=123456789012, c="acct123456789012", fp=fp, my_token="t0k", amount=4200)
    rec = json.loads((tmp_path / "paypilot.jsonl").read_text())
    assert rec["a"] == "****9012" and rec["b"] == "****9012" and rec["c"].endswith("****9012")
    assert rec["fp"] == fp and rec["my_token"] == "[REDACTED]" and rec["amount"] == 4200
    assert (tmp_path / "paypilot.jsonl").stat().st_mode & 0o077 == 0


def test_client_may_set_approver_allowlist_only():
    m = merge_config({"approvals": {"approvers": [], "lock_wait_s": 10}, "limits": {}}, {"approvals": {"approvers": ["Asha"]}})
    assert m["approvals"] == {"approvers": ["Asha"], "lock_wait_s": 10}
    with pytest.raises(ConfigError):
        merge_config({"approvals": {"approvers": [], "lock_wait_s": 10}}, {"approvals": {"lock_wait_s": 0}})


def test_approvers_in_policy_yaml_do_not_change_policy_hash(tmp_path):
    root = tmp_path; (root / "clients/northbay").mkdir(parents=True)
    import shutil; shutil.copy("config.yaml", root / "config.yaml")
    c1, _, _ = load_config("northbay", root=root)
    (root / "clients/northbay/policy.yaml").write_text("approvals:\n  approvers: [Asha, Ravi]\n")
    c2, _, _ = load_config("northbay", root=root)
    assert c2.approvals["approvers"] == ["Asha", "Ravi"] and c2.policy_hash == c1.policy_hash   # allowlist never stales decisions
