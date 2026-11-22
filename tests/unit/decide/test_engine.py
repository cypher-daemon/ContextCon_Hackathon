from datetime import date

from paypilot.core.config import load_config
from paypilot.decide.engine import decide
from paypilot.decide.rules import load_rules
from paypilot.flags import Flag
from paypilot.schemas import BankLine, CheckResult, Invoice, Resolution


def _policy():
    return load_config("northbay")[1].model_copy(update={"decision_hash_input": "policy-data-hash"})


def _line(line_id="BL-001", day=date(2026, 10, 3)):
    return BankLine(item_id=line_id, source_ref=f"bank_lines.json#{line_id}", line_id=line_id, date=day,
                    descriptor_raw="RAZ*SWIGGY BLR", descriptor_key="SWIGGY", amount_paise=124_000,
                    currency="INR", direction="DEBIT", transaction_fingerprint="fp")


def test_load_rules_covers_every_decision_flag_once():
    rules = load_rules(_policy())

    assert rules
    assert sum(len(rule.flags_any) for rule in rules) == len(Flag) - 1


def test_clean_bank_line_proposes_posted_and_hashes_normalized_facts():
    resolution = Resolution(status="RESOLVED_VENDOR_MASTER", vendor_id="V-0001", legal_name="Swiggy Limited")
    checks = [CheckResult(check_id="account", status="PASS", details={"account": "Staff meals"})]

    result = decide(_line(), resolution, checks, _policy())

    assert result.proposal == "POSTED"
    assert result.flags == []
    assert result.facts.vendor_id == "V-0001"
    assert result.facts.account == "Staff meals"
    assert result.decision_hash
    assert result.decision_hash == decide(_line(), resolution, checks, _policy()).decision_hash


def test_all_flags_reasons_and_most_restrictive_proposal_are_kept():
    item = Invoice(item_id="INV-205", source_ref="invoices.json#INV-205", invoice_id="INV-205",
                   vendor_name_raw="Indian Oil", invoice_no_raw=None, invoice_date=date(2026, 10, 1),
                   amount_paise=120_000_00, transaction_fingerprint="inv-fp")
    resolution = Resolution(status="UNRESOLVED", flags=["PAYEE_UNRESOLVED"],
                            reasons=["No vendor match"])
    checks = [CheckResult(check_id="required", status="FAIL", flags=["MISSING_FIELD"],
                          reasons=["Required field invoice_no_raw is missing"], missing=["invoice_no_raw"]),
              CheckResult(check_id="duplicate", status="FAIL", flags=["DUPLICATE_INVOICE"],
                          reasons=["Duplicate of HIST-1"], details={"duplicate_refs": ["HIST-1"]})]

    result = decide(item, resolution, checks, _policy())

    assert result.proposal == "CANNOT_VERIFY"
    assert result.flags == ["DUPLICATE_INVOICE", "MISSING_FIELD", "PAYEE_UNRESOLVED"]
    assert [reason.code for reason in result.reasons] == result.flags
    assert result.missing == ["invoice_no_raw", "vendor-master record or a GSTIN/domain"]
    assert "HIST-1" in result.reasons[0].text or "HIST-1" in result.reasons[1].text


def test_decision_hash_changes_with_policy_input():
    resolution = Resolution(status="RESOLVED_VENDOR_MASTER", vendor_id="V-0001", legal_name="Swiggy Limited")
    first = decide(_line(), resolution, [], _policy())
    second = decide(_line(), resolution, [], _policy().model_copy(update={"decision_hash_input": "changed"}))

    assert first.decision_hash != second.decision_hash


def test_lookalike_candidate_is_not_emitted_as_resolved_payee():
    resolution = Resolution(status="LOOKALIKE", vendor_id="V-0003", legal_name="Delhivery Limited",
                            candidates=[("V-0003", 0.91)], flags=["LOOKALIKE"])

    result = decide(_line(), resolution, [], _policy())

    assert result.facts.vendor_id is None
    assert result.payee_legal_name is None
