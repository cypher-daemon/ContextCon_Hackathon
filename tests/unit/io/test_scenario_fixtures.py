from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

from paypilot.core.config import load_config
from paypilot.schemas import Outcome, Request


ROOT = Path(__file__).resolve().parents[3]
CLIENT = ROOT / "clients" / "northbay"


def _csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)


def test_northbay_client_policy_validates_through_a1() -> None:
    cfg, policy, _ = load_config("northbay", root=ROOT)

    assert cfg.approvals["approvers"] == ["Finance Controller", "AP Manager"]
    assert policy.accounts.by_category == {
        "food": "Staff meals",
        "cloud": "Cloud hosting",
        "freight": "Freight - carriers",
        "fuel": "Fuel",
    }
    assert policy.po.tolerance_pct == 1.0
    assert policy.duplicate.window_days == 3


def test_northbay_client_csv_files_match_contract_headers() -> None:
    vendor_header, vendors = _csv_rows(CLIENT / "vendor_master.csv")
    assert vendor_header == [
        "vendor_id", "legal_name", "aliases", "cin", "gstin", "domain", "bank_account", "ifsc",
        "default_account", "category", "last_updated",
    ]
    assert len(vendors) == 8
    assert all(row["last_updated"] == "2026-09-30" for row in vendors)
    assert all(row["bank_account"].isdigit() for row in vendors)
    assert {row["vendor_id"] for row in vendors} >= {"V-0001", "V-0002", "V-0101", "V-0102"}
    om_sai = [row for row in vendors if row["vendor_id"] in {"V-0101", "V-0102"}]
    assert len(om_sai) == 2 and {row["aliases"] for row in om_sai} == {"OM SAI ROADWAYS"}
    delhivery = next(row for row in vendors if row["vendor_id"] == "V-0003")
    assert delhivery["cin"] == "L63090DL2011PLC221234"

    po_header, pos = _csv_rows(CLIENT / "purchase_orders.csv")
    assert po_header == ["po_number", "vendor_id", "amount_inr", "qty", "status", "issued_on"]
    assert {row["po_number"] for row in pos} == {"PO-NB-1042", "PO-NB-1043", "PO-NB-1044", "PO-NB-1045"}
    assert all(Decimal(row["amount_inr"]) > 0 for row in pos)
    assert {row["vendor_id"] for row in pos} <= {row["vendor_id"] for row in vendors}

    history_header, history = _csv_rows(CLIENT / "payment_history.csv")
    assert history_header == [
        "ref", "vendor_id", "invoice_number", "amount_inr", "currency", "paid_on", "provider_ref",
        "bank_reference", "status",
    ]
    assert len(history) == 1
    assert history[0]["invoice_number"] == "BDE/26-27/7781"

    chart_header, chart = _csv_rows(CLIENT / "chart_of_accounts.csv")
    assert chart_header == ["account_code", "account_name", "category"]
    assert {row["category"] for row in chart} == {"food", "cloud", "freight", "fuel"}


def test_sc01_and_sc02_requests_batches_and_expected_outcomes_are_well_formed() -> None:
    scenario_expectations = {
        "SC-01": {
            "BL-001": ("POSTED", []),
            "BL-002": ("POSTED", []),
            "BL-003": ("HELD", ["DUPLICATE_TRANSACTION"]),
            "BL-004": ("CANNOT_VERIFY", ["PAYEE_UNRESOLVED"]),
        },
        "SC-02": {
            "BL-101": ("POSTED", []),
            "BL-102": ("HELD", ["OUT_OF_PERIOD"]),
            "BL-103": ("HELD", ["CREDIT_LINE"]),
            "BL-104": ("CANNOT_VERIFY", ["PAYEE_TIE"]),
        },
    }
    for scenario_id, expected_by_ref in scenario_expectations.items():
        expected_refs = set(expected_by_ref)
        scenario = ROOT / "scenarios" / scenario_id
        request_data = json.loads((scenario / "request.json").read_text(encoding="utf-8"))
        request = Request.model_validate(request_data)
        assert request.operation == "POST_BANK_LINES"
        assert request.client_id == "northbay"

        lines = json.loads((scenario / "bank_lines.json").read_text(encoding="utf-8"))
        assert {line["line_id"] for line in lines} == expected_refs
        for line in lines:
            assert isinstance(line["amount_inr"], str)
            assert Decimal(line["amount_inr"]) > 0
            assert line["direction"] in {"DEBIT", "CREDIT"}

        expectations = [
            json.loads(line)
            for line in (scenario / "expected.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert {item["item_ref"] for item in expectations} == expected_refs
        assert all(item["outcome"] in {outcome.value for outcome in Outcome} for item in expectations)
        for item in expectations:
            assert (item["outcome"], item["flags"]) == expected_by_ref[item["item_ref"]]
