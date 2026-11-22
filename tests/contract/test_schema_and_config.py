"""Contract: DDL applies, config.yaml has every section, models forbid extras, journal balances."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from paypilot.schemas import JournalEntry, JournalLine, Limits, Operation

ROOT = Path(__file__).resolve().parents[2]
SECTIONS = ["limits", "payments", "approvals", "costs", "output", "logging", "storage", "sources", "normalize", "match",
            "required_fields", "checks_order", "period", "posting", "duplicate", "po", "bank", "accounts", "reconcile",
            "decision", "exceptions"]
TABLES = {"runs", "sessions", "items", "sources", "item_evidence", "audit_events", "journal_entries", "journal_lines",
          "approvals", "payments", "payment_attempts", "exceptions", "recovery_cases", "cost_ledger", "http_cache", "domain_blocklist"}


def test_ddl_applies():
    con = sqlite3.connect(":memory:")
    con.executescript((ROOT / "db/schema.sql").read_text())
    got = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert TABLES <= got


def test_config_has_all_sections():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    assert list(cfg) == SECTIONS
    assert cfg["payments"]["provider"] == "fake"          # FakeProvider is the default
    assert cfg["payments"]["refuse_live_keys"] is True


def test_models_forbid_extras():
    with pytest.raises(ValidationError):
        Limits(max_cost_inr=1.0, surprise=True)


def _entry(dr, cr):
    return JournalEntry(entry_id="e", item_id="i", kind="BANK_LINE_POSTING", decision_hash="h", currency="INR", memo="m",
                        posted_at=datetime.now(timezone.utc),
                        lines=[JournalLine(line_no=1, account="A", debit_paise=dr), JournalLine(line_no=2, account="B", credit_paise=cr)])


def test_journal_must_balance():
    _entry(100, 100)
    with pytest.raises(ValidationError):
        _entry(100, 99)


def test_operation_values():
    assert {o.value for o in Operation} == {"POST_BANK_LINES", "PAY_INVOICES", "RECONCILE"}
