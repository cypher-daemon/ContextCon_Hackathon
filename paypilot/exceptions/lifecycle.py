"""Allowed exception state transitions (C7b)."""
from __future__ import annotations

from paypilot.schemas import ExceptionStatus

_TRANSITIONS = {
    ExceptionStatus.DETECTED: {ExceptionStatus.CLASSIFIED},
    ExceptionStatus.CLASSIFIED: {ExceptionStatus.CONTAINED},
    ExceptionStatus.CONTAINED: {ExceptionStatus.INVESTIGATING},
    ExceptionStatus.INVESTIGATING: {ExceptionStatus.ACTION_REQUIRED, ExceptionStatus.FALSE_POSITIVE,
                                    ExceptionStatus.ACCEPTED_RISK},
    ExceptionStatus.ACTION_REQUIRED: {ExceptionStatus.RESOLVED, ExceptionStatus.CLOSED_NO_ACTION},
}


def can_transition(frm: ExceptionStatus, to: ExceptionStatus, evidence_refs: list[str]) -> bool:
    """Validate the documented lifecycle edge; resolution always needs evidence.

    The caller must additionally require an override reason and overridable
    cause for ACCEPTED_RISK; that information is not part of this contract.
    """
    try:
        source = frm if isinstance(frm, ExceptionStatus) else ExceptionStatus(frm)
    except (TypeError, ValueError):
        return False
    try:
        target = to if isinstance(to, ExceptionStatus) else ExceptionStatus(to)
    except (TypeError, ValueError):
        return False
    if target not in _TRANSITIONS.get(source, set()):
        return False
    if target == ExceptionStatus.RESOLVED and not evidence_refs:
        return False
    return True
