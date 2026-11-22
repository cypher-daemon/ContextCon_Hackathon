"""io.request (A3): load and validate request.json. Owner: lane A."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from paypilot.schemas import Limits, Operation, Period, Request

KNOWN_INPUTS = ("bank_lines", "invoices", "vendor_messages", "bank_statement")


class RequestError(Exception):
    """Unreadable or invalid request -> CLI exit 2."""


def safe_input_path(base: Path, name: str) -> Path:
    """Input files must live inside the request's directory: no absolute paths, no `..`."""
    p = (base / name).resolve()
    if Path(name).is_absolute() or not p.is_relative_to(base.resolve()):
        raise RequestError(f"input path escapes the request directory: {name!r}")
    return p


def load_request(path: Path) -> Request:
    """Missing/unsupported `operation` is NOT an error: operation=None, handled later as a run-level CANNOT_VERIFY."""
    path = Path(path)
    try:
        raw = path.read_bytes()
        doc = json.loads(raw)
    except (OSError, ValueError) as e:
        raise RequestError(f"cannot read request {path}: {e}") from e
    if not isinstance(doc, dict):
        raise RequestError("request must be a JSON object")
    for k in ("schema_version", "request_id", "client_id"):
        if not isinstance(doc.get(k), str) or not doc[k]:
            raise RequestError(f"request is missing required string field {k!r}")

    op_raw = doc.get("operation")
    operation = Operation(op_raw) if op_raw in Operation._value2member_map_ else None

    inputs = doc.get("inputs") or {}
    if not isinstance(inputs, dict) or not all(isinstance(v, str) for v in inputs.values()):
        raise RequestError("inputs must map names to file names")
    unknown = sorted(set(inputs) - set(KNOWN_INPUTS))
    if unknown:
        raise RequestError(f"unknown inputs {unknown}; allowed: {list(KNOWN_INPUTS)}")
    for name in inputs.values():
        safe_input_path(path.parent, name)

    try:
        period = Period(**doc["period"]) if doc.get("period") else None
        limits = Limits(**doc["limits"]) if doc.get("limits") else None
        if period and period.from_ > period.to:
            raise ValueError("period.from is after period.to")
        return Request(schema_version=doc["schema_version"], request_id=doc["request_id"], client_id=doc["client_id"],
                       operation=operation, instruction=str(doc.get("instruction", "")), period=period,
                       inputs=inputs, limits=limits, source_path=str(path), source_sha=hashlib.sha256(raw).hexdigest())
    except (ValidationError, ValueError, TypeError) as e:
        raise RequestError(f"invalid request: {e}") from e
