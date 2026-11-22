"""A3 done-when: loaders parse exactly; no raw account in any model, error or hash. Uses tmp fixtures + a patched normalizer."""
import json
import shutil
from pathlib import Path

import pytest

from paypilot.io import batch
from paypilot.io.client_data import ClientDataError, load_client_data
from paypilot.io.money import parse_amount_paise
from paypilot.io.request import RequestError, load_request
from paypilot.resolve import normalize
from paypilot.schemas import BankLine, Invoice, Operation, ParseFailure, RunConfig, VendorMessage
from tests.fakes import make_ctx

ROOT = Path(__file__).resolve().parents[2]
RAW = "50100123456789"
NOW_CFG = RunConfig(limits={}, payments={}, approvals={}, costs={}, output={}, logging={}, storage={})


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("BANK_HMAC_KEY", "test-key-0123456789")
    monkeypatch.setenv("PAYPILOT_ROOT", str(tmp_path))
    shutil.copy(ROOT / "config.yaml", tmp_path / "config.yaml")
    monkeypatch.setattr(normalize, "descriptor_key", lambda s, p: s.upper().replace("RAZ*", "").strip())
    monkeypatch.setattr(normalize, "name_key", lambda s, p: s.upper().replace("LIMITED", "").strip())
    monkeypatch.setattr(normalize, "invoice_canonical", lambda s, p: "".join(c for c in s.upper() if c.isalnum()))


def write(p: Path, obj) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    return p


def req(tmp_path, inputs, **extra):
    doc = {"schema_version": "1.0", "request_id": "r1", "client_id": "northbay", "operation": "POST_BANK_LINES",
           "inputs": inputs, **extra}
    return load_request(write(tmp_path / "s" / "request.json", doc))


# ---- money ----
def test_money_is_exact_and_rejects_floats():
    assert parse_amount_paise("1240.00") == 124000 and parse_amount_paise("0.10") == 10 and parse_amount_paise("19.99") == 1999
    for bad in (1240.0, 12, True, None, "1,240.00", "1.234", "", "abc", " "):
        with pytest.raises(ValueError):
            parse_amount_paise(bad)


# ---- request ----
def test_request_ok_and_unsupported_operation_is_not_an_error(tmp_path):
    r = req(tmp_path, {"bank_lines": "bank_lines.json"}, period={"from": "2026-10-01", "to": "2026-10-31"})
    assert r.operation == Operation.POST_BANK_LINES and r.period.from_.isoformat() == "2026-10-01" and len(r.source_sha) == 64
    for op in (None, "ANSWER_QUESTION"):
        doc = {"schema_version": "1.0", "request_id": "r", "client_id": "c", "operation": op}
        assert load_request(write(tmp_path / "x.json", doc)).operation is None


@pytest.mark.parametrize("doc", [
    {"schema_version": "1.0", "request_id": "r"},                                              # no client_id
    {"schema_version": "1.0", "request_id": "r", "client_id": "c", "inputs": {"bank_lines": "../../etc/passwd"}},
    {"schema_version": "1.0", "request_id": "r", "client_id": "c", "inputs": {"bank_lines": "/etc/passwd"}},
    {"schema_version": "1.0", "request_id": "r", "client_id": "c", "inputs": {"surprise": "x.json"}},
    {"schema_version": "1.0", "request_id": "r", "client_id": "c", "period": {"from": "2026-11-01", "to": "2026-10-01"}},
])
def test_bad_requests_refused(tmp_path, doc):
    with pytest.raises(RequestError):
        load_request(write(tmp_path / "bad.json", doc))
    with pytest.raises(RequestError):
        load_request(write(tmp_path / "bad.json", "{not json"))


# ---- batch ----
def test_bank_lines_parse_exactly_and_bad_rows_become_parse_failures(tmp_path):
    write(tmp_path / "s" / "bank_lines.json", [
        {"line_id": "BL-001", "date": "2026-10-03", "description": "RAZ*SWIGGY BLR", "amount_inr": "1240.00",
         "direction": "debit", "currency": "inr", "bank_reference": None},
        {"line_id": "BL-002", "date": "2026-10-04", "description": "X", "amount_inr": 99.5, "direction": "DEBIT", "currency": "INR"},
        {"line_id": "BL-003", "date": "03/10/2026", "description": "X", "amount_inr": "1.00", "direction": "SIDEWAYS", "currency": "INR"},
        "not-an-object",
    ])
    r = req(tmp_path, {"bank_lines": "bank_lines.json"})
    items = batch.load_items(r, make_ctx())
    a, b, c, d = items
    assert isinstance(a, BankLine) and a.amount_paise == 124000 and a.direction == "DEBIT" and a.currency == "INR"
    assert a.descriptor_key == "SWIGGY BLR" and a.source_ref == "bank_lines.json#BL-001"
    assert isinstance(b, ParseFailure) and any("amount_inr" in e for e in b.errors)
    assert isinstance(c, ParseFailure) and len(c.errors) == 2
    assert isinstance(d, ParseFailure)
    assert [i.item_id for i in items] == [i.item_id for i in batch.load_items(r, make_ctx())]     # deterministic ids
    assert len({i.item_id for i in items}) == 4


def test_same_bank_reference_gives_same_fingerprint(tmp_path):
    row = lambda i, d: {"line_id": i, "date": d, "description": "A", "amount_inr": "1.00", "direction": "DEBIT",
                        "currency": "INR", "bank_reference": "UTR123"}
    write(tmp_path / "s" / "bank_lines.json", [row("a", "2026-10-01"), row("b", "2026-10-09")])
    a, b = batch.load_items(req(tmp_path, {"bank_lines": "bank_lines.json"}), make_ctx())
    assert a.transaction_fingerprint == b.transaction_fingerprint


def test_invoices_and_messages_never_keep_the_raw_account(tmp_path):
    write(tmp_path / "s" / "invoices.json", [
        {"invoice_id": "INV-1", "vendor_name": "Delhivery Limited", "vendor_domain": "Delhivery.com",
         "invoice_number": "DLV/2026-27/00418", "invoice_date": "2026-11-03", "amount_inr": "48000.00", "qty": 12,
         "remit_bank_account": RAW, "remit_ifsc": "abcd0123456"},
        {"invoice_id": "INV-2", "vendor_name": "Bad Date Ltd", "invoice_date": "03-11-2026", "remit_bank_account": RAW},
        {"invoice_id": "INV-3", "vendor_name": "No Number", "amount_inr": "1.00"},
    ])
    write(tmp_path / "s" / "vendor_messages.json", [
        {"message_id": "M-1", "received_at": "2026-11-05T10:12:00+05:30", "from_domain": "delhivery.com",
         "vendor_name": "Delhivery Limited", "kind": "BANK_CHANGE", "new_bank_account": RAW, "new_ifsc": "abcd0999999"},
        {"message_id": "M-2", "received_at": "2026-11-05T10:12:00", "vendor_name": "X", "kind": "OTHER"},
    ])
    r = req(tmp_path, {"invoices": "invoices.json", "vendor_messages": "vendor_messages.json"}, operation="PAY_INVOICES")
    items = batch.load_items(r, make_ctx())
    inv, bad, nonum, msg, badmsg = items
    assert isinstance(inv, Invoice) and inv.account_last4 == "6789" and inv.account_fingerprint and inv.ifsc == "ABCD0123456"
    assert inv.domain == "delhivery.com" and inv.invoice_no_canonical == "DLV20262700418"
    assert isinstance(bad, ParseFailure) and "invoice_date" in " ".join(bad.errors)
    assert isinstance(nonum, Invoice) and nonum.invoice_no_canonical is None and nonum.amount_paise == 100   # C flags MISSING_FIELD
    assert isinstance(msg, VendorMessage) and msg.new_account_last4 == "6789" and isinstance(badmsg, ParseFailure)
    dump = " ".join(i.model_dump_json() for i in items)
    assert RAW not in dump and RAW[:-4] not in dump                                  # nowhere, including error text


def test_statement_is_not_a_work_item_and_bad_rows_are_refused(tmp_path):
    write(tmp_path / "s" / "bank_statement.json", [{"line_id": "ST-1", "date": "2026-11-01", "description": "A",
                                                    "amount_inr": "5.00", "direction": "DEBIT", "currency": "INR"}])
    r = req(tmp_path, {"bank_statement": "bank_statement.json"}, operation="RECONCILE")
    assert batch.load_items(r, make_ctx()) == [] and len(batch.load_statement(r, make_ctx())) == 1
    write(tmp_path / "s" / "bank_statement.json", [{"line_id": "ST-1", "amount_inr": 5.0}])
    with pytest.raises(RequestError):
        batch.load_statement(r, make_ctx())
    assert batch.load_statement(req(tmp_path, {}), make_ctx()) is None


# ---- client data ----
VENDOR_HDR = "vendor_id,legal_name,aliases,cin,gstin,domain,bank_account,ifsc,default_account,category,last_updated\n"


def client(tmp_path, vendors: str, **files):
    d = tmp_path / "clients" / "northbay"
    write(d / "vendor_master.csv", VENDOR_HDR + vendors)
    for name, text in files.items():
        write(d / name, text)
    return d


def test_client_data_loads_fingerprints_and_scans(tmp_path):
    client(tmp_path,
           f"V1,Swiggy Limited,Swiggy|Bundl,,29AAA,swiggy.com,{RAW},ABCD0123456,Staff meals,food,2026-09-30\n"
           f"V2,Om Sai Roadways Pvt,OSR,,29AAA,omsai.in,,,,transport,\n"
           f"V3,Om Sai Roadways Ltd,osr,,,,{RAW},,,transport,2026-01-02\n",
           **{"purchase_orders.csv": "po_number,vendor_id,amount_inr,qty,status,issued_on\nPO1,V1,500.00,3,OPEN,2026-10-01\n",
              "chart_of_accounts.csv": "account_code,account_name,category\n6100,Staff meals,food\n"})
    data, h1 = load_client_data("northbay", NOW_CFG)
    v1 = data.vendors[0]
    assert v1.account_last4 == "6789" and v1.record_date.isoformat() == "2026-09-30" and v1.aliases == ["Swiggy", "Bundl"]
    assert v1.record_ref == "clients/northbay/vendor_master.csv#V1" and v1.name_key == "SWIGGY"
    assert data.pos[0].amount_paise == 50000 and data.history is None and data.accounts == {"food": "Staff meals"}
    dq = " ".join(data.data_quality)
    assert "DUPLICATE_GSTIN:29AAA:V1,V2" in dq and "DUPLICATE_ACCOUNT" in dq and "DUPLICATE_ALIAS:OSR:V2,V3" in dq
    assert RAW not in data.model_dump_json() and RAW not in dq and RAW not in h1
    (tmp_path / "clients/northbay/vendor_master.csv").write_text(
        (tmp_path / "clients/northbay/vendor_master.csv").read_text() + "V4,New,,,,,,,,,\n")
    assert load_client_data("northbay", NOW_CFG)[1] != h1                       # data change -> new data_hash


def test_client_data_errors(tmp_path):
    with pytest.raises(ClientDataError):
        load_client_data("nobody", NOW_CFG)
    d = tmp_path / "clients" / "northbay"; d.mkdir(parents=True)
    with pytest.raises(ClientDataError):
        load_client_data("northbay", NOW_CFG)                                   # no vendor_master.csv
    client(tmp_path, "V1,A,,,,,,,,,not-a-date\n")
    with pytest.raises(ClientDataError):
        load_client_data("northbay", NOW_CFG)
    client(tmp_path, "V1,A,,,,,,,,,\nV1,B,,,,,,,,,\n")
    with pytest.raises(ClientDataError):
        load_client_data("northbay", NOW_CFG)                                   # duplicate vendor_id


def test_normalizers_receive_the_normalize_section_not_the_whole_policy(tmp_path, monkeypatch):
    """Lane B's normalizers read processor_prefixes/city_tokens off the object they get; a wrong object silently yields []."""
    from paypilot.core.config import load_config
    from paypilot.policy_config import NormalizePolicy
    seen = []
    monkeypatch.setattr(normalize, "descriptor_key", lambda s, p: seen.append(p) or s.upper())
    _, policy, _ = load_config("northbay", root=ROOT)
    write(tmp_path / "s" / "bank_lines.json", [{"line_id": "B", "date": "2026-10-01", "description": "x", "amount_inr": "1.00",
                                                "direction": "DEBIT", "currency": "INR"}])
    batch.load_items(req(tmp_path, {"bank_lines": "bank_lines.json"}), make_ctx(policy=policy))
    assert seen and all(isinstance(p, NormalizePolicy) for p in seen)
