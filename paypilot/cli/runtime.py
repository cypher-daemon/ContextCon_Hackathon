"""CLI runtime helpers: exit codes, path rooting, provider check, session start. Owner: lane A."""
from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from paypilot.core import crash
from paypilot.core.config import ConfigError, load_config, with_data_hash
from paypilot.core.db import PersistenceFailure
from paypilot.ledger.store import SqliteLedger
from paypilot.core.env import load_dotenv
from paypilot.core.lock import LockHeld
from paypilot.io.client_data import ClientDataError, load_client_data
from paypilot.io.request import RequestError
from paypilot.cli.selector import SelectorError
from paypilot.policy_config import PolicyConfig
from paypilot.reports.writer import UnknownOutputField
from paypilot.schemas import ClientData, RunConfig

EXIT_OK, EXIT_FAILED, EXIT_INVALID, EXIT_LOCK, EXIT_PERSIST, EXIT_LIMIT = 0, 1, 2, 3, 70, 75
PROVIDERS = ("fake", "razorpayx_test")


class CliError(Exception):
    """Invalid input -> exit 2."""


def root_dir() -> Path:
    return Path(os.environ.get("PAYPILOT_ROOT", ".")).resolve()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_LANES = {"resolve": "B", "sources": "B", "checks": "C", "decide": "C", "exceptions": "C", "reconciliation": "C",
          "flags": "C", "policy_config": "C"}


def _unbuilt_module(tb) -> tuple[str, str]:
    """(module, lane) of the innermost paypilot frame, so the message names what is not built yet."""
    import traceback
    mod = "paypilot"
    for f in traceback.extract_tb(tb):
        if f.filename.replace("\\", "/").find("/paypilot/") >= 0:
            mod = f.filename.replace("\\", "/").split("/paypilot/")[-1].removesuffix(".py").replace("/", ".")
    return "paypilot." + mod, _LANES.get(mod.split(".")[0], "A")


@contextlib.contextmanager
def guarded() -> Iterator[contextlib.ExitStack]:
    """Map domain errors to the documented exit codes; nothing here ever hides a failure.
    Yields an ExitStack: register database connections on it and they are closed however the command ends."""
    with contextlib.ExitStack() as stack:
        try:
            yield stack
        except (CliError, ConfigError, RequestError, ClientDataError, SelectorError, UnknownOutputField, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            raise SystemExit(EXIT_INVALID)
        except NotImplementedError as e:        # a lane's module is still a stub: say which one, not a traceback
            module, lane = _unbuilt_module(e.__traceback__)
            print(f"error: not built yet: {module} (lane {lane}): {e or 'NotImplementedError'}\n"
                  f"       nothing was lost: the run is saved and `python -m paypilot resume` continues it once the module lands.",
                  file=sys.stderr)
            raise SystemExit(EXIT_FAILED)
        except LockHeld as e:
            print(f"error: {e}", file=sys.stderr)
            raise SystemExit(EXIT_LOCK)
        except PersistenceFailure as e:
            print(f"error: persistence failure, stopped before any provider call: {e}", file=sys.stderr)
            raise SystemExit(EXIT_PERSIST)


def start() -> Path:
    """Common start: root, .env, crash-env sanity. Returns the root."""
    root = root_dir()
    os.environ["PAYPILOT_ROOT"] = str(root)
    load_dotenv(root / ".env")
    crash.validate_env()                      # a typo must not silently disable a crash test
    return root


def rooted(cfg: RunConfig, root: Path) -> RunConfig:
    """Anchor relative storage/output paths at the root (config hashes were computed before; paths are not policy)."""
    def p(v: str) -> str:
        return v if os.path.isabs(v) else str(root / v)
    storage = {k: p(v) if k.endswith(("_path", "_db", "dir")) else v for k, v in cfg.storage.items()}
    output = {**cfg.output, "dir": p(cfg.output["dir"])}
    logging = {**cfg.logging, "dir": p(cfg.logging["dir"])}
    return cfg.model_copy(update={"storage": storage, "output": output, "logging": logging})


@dataclass
class Loaded:
    cfg: RunConfig
    policy: PolicyConfig
    snapshot: dict
    data: ClientData
    data_hash: str


def load_cfg(client_id: str, root: Path) -> tuple[RunConfig, PolicyConfig, dict]:
    cfg, policy, snap = load_config(client_id, root=root)
    return rooted(cfg, root), policy, snap


def load_all(client_id: str, root: Path) -> Loaded:
    cfg, policy, snap = load_cfg(client_id, root)
    data, dh = load_client_data(client_id, cfg)
    return Loaded(cfg, with_data_hash(policy, dh), snap, data, dh)


def check_provider(name: str | None, cfg: RunConfig) -> str:
    """Only `fake` or `razorpayx_test`; no live option, no fallback. razorpayx_test needs test keys (exit 2 before any work)."""
    provider = name or cfg.payments.get("provider", "fake")
    if provider not in PROVIDERS:
        raise CliError(f"--provider must be one of {list(PROVIDERS)} (there is no live option), got {provider!r}")
    if provider == "razorpayx_test":
        key = os.environ.get("RAZORPAYX_KEY_ID", "")
        if not key.startswith("rzp_test_"):
            raise CliError("razorpayx_test needs RAZORPAYX_KEY_ID starting with rzp_test_ (live keys are refused)")
        if not os.environ.get("RAZORPAYX_KEY_SECRET") or not os.environ.get("RAZORPAYX_ACCOUNT_NUMBER"):
            raise CliError("razorpayx_test needs RAZORPAYX_KEY_SECRET and RAZORPAYX_ACCOUNT_NUMBER in .env")
        raise CliError("the razorpayx_test adapter is not built yet (task A9); use --provider fake")
    return provider


def begin_recovery(con: sqlite3.Connection, run_id: str) -> None:
    """Resume protocol steps 3-4 (FAILURE_MODES §3). RESERVED ledger rows -> UNCONFIRMED (they still count against the budget).
    Open payments are reconciled with the provider BEFORE any new work; payments land in A8, so until then any existing
    payment row fails loudly instead of being ignored."""
    SqliteLedger(con, utcnow).mark_unconfirmed(run_id)
    if con.execute("SELECT COUNT(*) FROM payments p JOIN items i ON i.item_id=p.item_id WHERE i.run_id=?", (run_id,)).fetchone()[0]:
        raise NotImplementedError("open payments must be reconciled first (payments.reconcile, task A8)")


def storage_paths(root: Path) -> dict[str, str]:
    """Storage paths are runtime config and cannot be overridden per client, so they are readable before the client is known."""
    import yaml
    st = (yaml.safe_load((root / "config.yaml").read_text()) or {}).get("storage") or {}
    if not st.get("db_path") or not st.get("lock_path"):
        raise CliError("config.yaml storage.db_path / storage.lock_path are required")
    return {k: v if os.path.isabs(v) else str(root / v) for k, v in st.items()}


def open_existing_db(root: Path):
    """Read-side commands (report/show/ledger): a missing or empty database is a clean 'nothing to show', not a crash."""
    from paypilot.core.db import connect
    db = storage_paths(root)["db_path"]
    if not Path(db).exists():
        raise CliError("no database yet: run a request first (python -m paypilot run --scenario DIR)")
    con = connect(db, apply_schema=False)
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'").fetchone() is None:
        raise CliError("the database has no runs yet: run a request first")
    return con
