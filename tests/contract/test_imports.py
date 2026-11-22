"""Contract: every module in INTERFACES §9 imports and exposes the frozen signature."""
import importlib
import inspect

import pytest

SIGNATURES = {
    "paypilot.io.request": {"load_request": ["path"]},
    "paypilot.io.client_data": {"load_client_data": ["client_id", "cfg"]},
    "paypilot.io.batch": {"load_items": ["request", "ctx"]},
    "paypilot.core.pipeline": {"drive": ["run_id", "ctx", "provider"]},
    "paypilot.core.journal": {"post_entry": ["conn", "entry"]},
    "paypilot.approvals.service": {"approve": ["item_id", "reviewer", "reason", "overrides"]},
    "paypilot.payments.executor": {"execute": ["payment", "provider", "ctx"]},
    "paypilot.payments.reconcile": {"reconcile_open": ["provider", "ctx"]},
    "paypilot.reports.writer": {"write_outputs": ["run_id", "ctx"]},
    "paypilot.audit.verify": {"verify_run": ["run_id", "cfg"]},
    "paypilot.resolve.normalize": {"descriptor_key": ["s", "policy"], "name_key": ["s", "policy"], "invoice_canonical": ["s", "policy"]},
    "paypilot.resolve.matcher": {"resolve_vendor": ["item", "ctx"]},
    "paypilot.sources.ifsc": {"lookup": ["ctx", "ifsc", "item_id"]},
    "paypilot.sources.crustdata": {"identify_by_domain": ["ctx", "domain", "item_id"]},
    "paypilot.sources.webfetch": {"fetch_identity_claim": ["ctx", "domain", "item_id"]},
    "paypilot.checks.runner": {"run_checks": ["item", "resolution", "ctx"]},
    "paypilot.decide.engine": {"decide": ["item", "resolution", "checks", "policy"]},
    "paypilot.exceptions.classify": {"classify": ["item", "decision", "recon", "payment", "policy"]},
    "paypilot.exceptions.lifecycle": {"can_transition": ["frm", "to", "evidence_refs"]},
    "paypilot.exceptions.recovery": {"prepare_case": ["exc", "payment"]},
    "paypilot.reconciliation.match": {"three_way": ["decision", "payment", "provider_view", "statement", "history", "policy"]},
}


@pytest.mark.parametrize("module,fns", SIGNATURES.items())
def test_signatures(module, fns):
    mod = importlib.import_module(module)
    for name, params in fns.items():
        got = list(inspect.signature(getattr(mod, name)).parameters)
        assert got == params, f"{module}.{name}: {got} != {params}"


def test_core_contract_imports():
    import paypilot.context, paypilot.schemas  # noqa: F401
    assert paypilot.schemas.Operation.PAY_INVOICES == "PAY_INVOICES"
