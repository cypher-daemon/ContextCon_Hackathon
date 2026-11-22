"""Pure deterministic decision engine (C6)."""
from __future__ import annotations

from typing import Any

from paypilot.core.ids import decision_hash as make_decision_hash
from paypilot.decide.rules import load_rules
from paypilot.schemas import (BankLine, CheckResult, Decision, Facts, Invoice, Proposal, Reason, Resolution,
                              VendorMessage, WorkItem)

_PRECEDENCE = {
    Proposal.CANNOT_VERIFY: 0,
    Proposal.HELD: 1,
    Proposal.AWAITING_APPROVAL: 2,
    Proposal.POSTED: 3,
    Proposal.RECONCILED: 4,
}


class _FormatValues(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _operation(item: WorkItem):
    from paypilot.schemas import Operation
    if isinstance(item, BankLine):
        return Operation.POST_BANK_LINES
    if isinstance(item, Invoice):
        return Operation.PAY_INVOICES
    return Operation.PAY_INVOICES


def _all_evidence(resolution: Resolution, checks: list[CheckResult]) -> list[str]:
    return sorted({source.source_id for source in [*resolution.evidence,
                                                    *(source for check in checks for source in check.evidence)]})


def _context(item: WorkItem, resolution: Resolution, checks: list[CheckResult], flags: list[str]) -> dict[str, str]:
    details = {key: value for check in checks for key, value in check.details.items()}
    missing_fields = sorted({field for check in checks for field in check.missing})
    candidates = ", ".join(f"{vendor_id} ({score:g})" for vendor_id, score in
                            sorted(resolution.candidates, key=lambda pair: (-pair[1], pair[0])))
    duplicate_refs = sorted({str(ref) for check in checks for ref in check.details.get("duplicate_refs", [])})
    raw_descriptor = getattr(item, "descriptor_raw", None)
    raw_name = getattr(item, "vendor_name_raw", None)
    return _FormatValues({
        "descriptor": raw_descriptor or raw_name or "",
        "billed_entity": raw_name or "",
        "found_entity": resolution.legal_name or "",
        "candidates": candidates,
        "fields": ", ".join(missing_fields),
        "missing": ", ".join(missing_fields),
        "dup_ref": ", ".join(duplicate_refs),
        "po_detail": "; ".join(check.reasons[0] for check in checks if check.check_id == "po" and check.reasons),
        "detail": "; ".join(reason for check in checks for reason in check.reasons) or
                  "; ".join(f"{key}={value}" for key, value in sorted(details.items())),
        "provider": str(details.get("provider", "external source")),
        "flags": ", ".join(flags),
    })


def decide(item: BankLine | Invoice | VendorMessage, resolution: Resolution, checks: list[CheckResult], policy: Any) -> Decision:
    """Collect every flag/reason, apply the strictest matching proposal, and hash decision facts."""
    rules = load_rules(policy)
    rule_for = {flag: rule for rule in rules for flag in rule.flags_any}
    flags = sorted({str(flag) for flag in [*resolution.flags, *(flag for check in checks for flag in check.flags)]})
    unknown = sorted(set(flags) - {flag.value for flag in rule_for})
    if unknown:
        raise ValueError(f"decision received flags without rules: {unknown}")

    fmt = _context(item, resolution, checks, flags)
    reason_by_flag = {str(flag): text for flag, text in zip(resolution.flags, resolution.reasons)}
    for check in checks:
        for flag, text in zip(check.flags, check.reasons):
            reason_by_flag.setdefault(str(flag), text)

    reasons: list[Reason] = []
    missing: set[str] = set()
    matched_rule_ids = {rule_for[flag].id for flag in flags}
    ordered_rules = [rule for rule in rules if rule.id in matched_rule_ids]
    proposals = [rule.proposal for rule in ordered_rules]
    for flag in flags:
        rule = rule_for[flag]
        reason_text = reason_by_flag.get(flag) or rule.reason.format_map(fmt)
        reasons.append(Reason(code=flag, text=reason_text, rule_id=rule.id, evidence_refs=_all_evidence(resolution, checks)))
        if rule.missing:
            rendered = rule.missing.format_map(fmt).strip()
            if rendered:
                missing.update(part.strip() for part in rendered.split(",") if part.strip())

    if proposals:
        proposal = min(proposals, key=lambda value: _PRECEDENCE[value])
    elif isinstance(item, BankLine):
        proposal = Proposal.POSTED
    elif isinstance(item, Invoice):
        proposal = Proposal.AWAITING_APPROVAL
    else:
        proposal = Proposal.HELD

    account = next((check.details.get("account") for check in checks
                    if check.check_id == "account" and check.details.get("account")), None)
    bank_check = next((check for check in checks if check.check_id == "bank"), None)
    bank_status = ({key: str(value) for key, value in bank_check.details.items()
                   if key in {"ifsc_valid", "account_matches_master", "ownership_verified"}} if bank_check else None)
    canonical = getattr(item, "invoice_no_canonical", None)
    if isinstance(item, Invoice) and not canonical:
        canonical = next((check.details.get("invoice_no_canonical") for check in checks
                          if check.details.get("invoice_no_canonical")), None)
    facts = Facts(
        operation=_operation(item), kind=type(item).__name__, vendor_id=resolution.vendor_id,
        account_fingerprint=getattr(item, "account_fingerprint", None) or
                            getattr(item, "new_account_fingerprint", None),
        amount_paise=getattr(item, "amount_paise", None), currency=getattr(item, "currency", None),
        date=getattr(item, "date", None) or getattr(item, "invoice_date", None),
        invoice_no_canonical=canonical, account=account, flags=flags,
    )
    hash_input = getattr(policy, "decision_hash_input", "")
    return Decision(
        item_id=item.item_id, proposal=proposal, flags=flags, reasons=reasons,
        missing=sorted(missing), evidence_refs=_all_evidence(resolution, checks),
        rule_ids=[rule.id for rule in ordered_rules],
        overridable=sorted(flag for flag in flags if rule_for[flag].overridable), facts=facts,
        payee_legal_name=resolution.legal_name, account=account, bank_status=bank_status,
        decision_hash=make_decision_hash(facts, hash_input),
    )
