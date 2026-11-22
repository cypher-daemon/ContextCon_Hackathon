"""A6 x B3: lane B's REAL ExtClient over lane A's REAL ledger/cache/budget; only the network is faked (httpx.request)."""
import httpx
import pytest

from paypilot.core.config import load_config
from paypilot.core.db import connect, tx
from paypilot.ledger.budget import Budget
from paypilot.ledger.cache import SqliteCache
from paypilot.ledger.store import SqliteLedger, summarize
from paypilot.sources import http as b_http
from paypilot.sources.http import make_ext_client
from tests.fakes import fixed_clock

ID = "https://api.crustdata.com/identify"
IFSC = "https://ifsc.razorpay.com/ABCD0123456"


@pytest.fixture
def net(monkeypatch):
    class Net:
        calls, script = [], []               # script: list of callables/Responses consumed in order; default 200 {}
    n = Net(); n.calls = []; n.script = []

    def fake(method, url, **kw):
        n.calls.append((method, url))
        out = n.script.pop(0) if n.script else httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))
        if isinstance(out, BaseException):
            raise out
        return out
    monkeypatch.setattr(b_http.httpx, "request", fake)
    monkeypatch.setattr(b_http.time, "sleep", lambda s: None)          # retries must not really sleep
    return n


def build(tmp_path, *, inr_per_credit=None, max_cost=None, estimate=0.2, db="x.db", prior=None):
    con = connect(str(tmp_path / db))
    with tx(con):
        con.execute("INSERT OR IGNORE INTO runs VALUES ('R','northbay','r','s',NULL,'h','p','d','{}','RUNNING','t','t')")
    cfg, _, _ = load_config("northbay")
    costs = {**cfg.costs, "crustdata": {"inr_per_credit": inr_per_credit, "estimates": {"identify": estimate, "web_fetch": 1}}}
    cfg = cfg.model_copy(update={"costs": costs, "limits": {**cfg.limits, "max_cost_inr": max_cost}})
    ledger = SqliteLedger(con, fixed_clock, run_id="R", session_id="S1", costs=costs)
    budget = Budget(con, "R", cfg_limits=cfg.limits, unknown_reserve_inr=cfg.costs["unknown_reserve_inr"])
    return con, cfg, ledger, make_ext_client(cfg, ledger, SqliteCache(con, fixed_clock), budget)


def test_rows_land_in_the_real_run_and_are_priced_from_config(tmp_path, net):
    con, cfg, ledger, ext = build(tmp_path, inr_per_credit=None)
    assert ext.post_json("crustdata", "identify", ID, {"domain": "a.com"}, item_id="i1", stage="resolve").ok
    assert ext.get_json("ifsc", "lookup", IFSC, item_id="i1", stage="check").ok
    rows = {r["provider"]: r for r in con.execute("select * from cost_ledger")}
    assert {r["run_id"] for r in rows.values()} == {"R"} and {r["session_id"] for r in rows.values()} == {"S1"}   # not "unknown"
    assert rows["crustdata"]["status"] == "CONFIRMED" and rows["crustdata"]["actual_inr"] is None       # price null -> UNKNOWN, not 0
    assert rows["ifsc"]["actual_inr"] == 0.0                                                            # public API -> known free
    assert summarize(con, "R").cost_status == "UNKNOWN"          # the only paid call has no price; the free ifsc call does not make it PARTIAL
    assert summarize(con, "R").total_inr is None and summarize(con, "R").by_provider == {"crustdata": None, "ifsc": 0.0}


def test_known_price_converts_credits_to_rupees(tmp_path, net):
    con, cfg, ledger, ext = build(tmp_path, inr_per_credit=0.5)
    ext.post_json("crustdata", "identify", ID, {"domain": "a.com"}, item_id="i1", stage="resolve")
    s = summarize(con, "R")
    assert (s.cost_status, s.total_inr, s.by_provider["crustdata"]) == ("KNOWN", 0.5, 0.5)


def test_cost_limit_through_the_real_client_stops_before_the_provider_is_called(tmp_path, net):
    con, cfg, ledger, ext = build(tmp_path, inr_per_credit=0.2, max_cost=0.5, estimate=0.2)
    out = [ext.post_json("crustdata", "identify", ID, {"domain": f"d{i}.com"}, item_id=f"i{i}", stage="resolve") for i in range(5)]
    assert [(r.ok, r.error) for r in out] == [(True, None), (True, None), (False, "BUDGET"), (False, "BUDGET"), (False, "BUDGET")]
    assert len(net.calls) == 2                                                    # nothing past the limit reached the network
    assert con.execute("select count(*) from cost_ledger").fetchone()[0] == 2     # and no phantom rows


def test_http_402_is_recorded_as_failed_and_costs_nothing(tmp_path, net):
    con, cfg, ledger, ext = build(tmp_path, inr_per_credit=0.2)
    net.script = [httpx.Response(402, json={"error": "no credits"}, request=httpx.Request("POST", ID))]
    r = ext.post_json("crustdata", "identify", ID, {"domain": "a.com"}, item_id="i1", stage="resolve")
    assert (r.ok, r.error) == (False, "CREDITS_EXHAUSTED") and len(net.calls) == 1             # no retry on 402
    row = con.execute("select status, http_status from cost_ledger").fetchone()
    assert (row["status"], row["http_status"]) == ("FAILED", 402) and Budget(con, "R", cfg_limits={}).spent() == 0.0


def test_timeouts_retry_per_the_sources_config_and_still_count_as_possible_spend(tmp_path, net):
    con, cfg, ledger, ext = build(tmp_path, inr_per_credit=0.2, estimate=0.2)
    assert cfg.sources["crustdata"]["retries"] == 3                               # B reads cfg.sources: it must reach the client
    net.script = [httpx.ConnectTimeout("t")] * 10
    r = ext.post_json("crustdata", "identify", ID, {"domain": "a.com"}, item_id="i1", stage="resolve")
    assert (r.ok, r.error) == (False, "TIMEOUT") and len(net.calls) == 4          # 1 + 3 retries
    rows = con.execute("select status, http_status from cost_ledger").fetchall()
    assert len(rows) == 4 and {(x["status"], x["http_status"]) for x in rows} == {("FAILED", None)}
    s = summarize(con, "R")
    assert s.unconfirmed_inr == pytest.approx(0.8) and s.total_inr == 0.0         # no response: spend may have happened, shown separately


def test_kill_then_retry_of_the_same_request_does_not_collide(tmp_path, net):
    con, cfg, ledger, ext = build(tmp_path, inr_per_credit=0.2, estimate=0.2)
    net.script = [SystemExit(137)]                                                # the process dies inside the call
    with pytest.raises(SystemExit):
        ext.post_json("crustdata", "identify", ID, {"domain": "a.com"}, item_id="i1", stage="resolve")
    assert con.execute("select status from cost_ledger").fetchone()[0] == "RESERVED"        # persisted before the call
    # --- new process ---
    con2, cfg2, ledger2, ext2 = build(tmp_path, inr_per_credit=0.2, estimate=0.2)
    assert ledger2.mark_unconfirmed("R") == 1
    r = ext2.post_json("crustdata", "identify", ID, {"domain": "a.com"}, item_id="i1", stage="resolve")
    assert r.ok
    assert sorted(x["status"] for x in con2.execute("select status from cost_ledger")) == ["CONFIRMED", "UNCONFIRMED"]
    s = summarize(con2, "R")
    assert (s.total_inr, s.unconfirmed_inr, s.calls) == (0.2, 0.2, 2)             # both calls visible; the lost one still counts
