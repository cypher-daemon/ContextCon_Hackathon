"""A6 done-when: the cost-limit test stops safely. Ledger rows are written BEFORE calls; an unknown price is UNKNOWN, never 0."""
from datetime import timedelta

import pytest

from paypilot.context import BudgetExceeded, TimeExceeded
from paypilot.core.db import PersistenceFailure, connect, tx
from paypilot.ledger.budget import Budget, effective_cost_limit
from paypilot.ledger.cache import SqliteCache
from paypilot.ledger.store import LedgerError, SqliteLedger, summarize
from paypilot.schemas import CachedResponse, Limits, LedgerEntry
from tests.fakes import fixed_clock

NOW = fixed_clock()


@pytest.fixture
def con(tmp_path):
    c = connect(str(tmp_path / "l.db"))
    with tx(c):
        c.execute("INSERT INTO runs VALUES ('R','c','r','s',NULL,'h','p','d','{}','RUNNING','t','t')")
    return c


def entry(item="i1", est=0.2, provider="crustdata", req="h1", op="identify", stage="resolve"):
    return LedgerEntry(call_key="ignored", run_id="R", session_id="S", item_id=item, stage=stage, provider=provider, operation=op,
                       unit_type="credits", estimated_inr=est, status="RESERVED", request_hash=req, attempt=1, ts=NOW)


class PaidClient:
    """Stands in for lane B's ExtClient: budget -> ledger row -> call -> confirm. `calls` counts real provider calls."""
    def __init__(self, con, budget, actual=0.2):
        self.ledger, self.budget, self.actual, self.calls = SqliteLedger(con, fixed_clock), budget, actual, 0

    def call(self, item, est=0.2, req="h1"):
        self.budget.reserve(est, item_id=item)                       # may raise BudgetExceeded -> NO call, NO row
        key = self.ledger.reserve(entry(item, est, req=req))          # persisted before the call (PersistenceFailure -> no call)
        self.calls += 1                                               # <- the provider call
        self.ledger.confirm(key, 1.0, self.actual, 200, 12)


def mk_budget(con, **kw):
    return Budget(con, "R", cfg_limits=kw.pop("cfg", {"max_cost_inr": 0.5}), **kw)


# ---- the done-when ----
def test_cost_limit_stops_safely(con):
    client = PaidClient(con, mk_budget(con, cfg={"max_cost_inr": 0.5}))
    results = []
    for i in range(5):
        try:
            client.call(f"item{i}"); results.append("ok")
        except BudgetExceeded:
            results.append("blocked")
    assert results == ["ok", "ok", "blocked", "blocked", "blocked"]
    assert client.calls == 2                                          # nothing past the limit reached the provider
    assert con.execute("select count(*) from cost_ledger").fetchone()[0] == 2     # and no phantom rows for blocked calls
    s = summarize(con, "R", limit_inr=0.5)
    assert (s.cost_status, s.total_inr, s.calls, s.limit_inr) == ("KNOWN", 0.4, 2, 0.5)


def test_unknown_price_reserves_against_the_limit_but_reports_unknown(con):
    client = PaidClient(con, mk_budget(con, cfg={"max_cost_inr": 2.5}, unknown_reserve_inr=1.0), actual=None)
    client.call("a", est=None); client.call("b", est=None)           # each reserves ₹1.0 -> 2.0 of 2.5
    with pytest.raises(BudgetExceeded):
        client.call("c", est=None)                                    # a 3rd would reach 3.0
    s = summarize(con, "R")
    assert s.cost_status == "UNKNOWN" and s.total_inr is None and s.by_provider == {"crustdata": None}
    assert s.worst_item_inr is None and s.inr_per_item is None        # never ₹0


def test_partial_when_some_priced_some_unknown(con):
    l = SqliteLedger(con, fixed_clock)
    k1, k2 = l.reserve(entry("a", req="x")), l.reserve(entry("b", req="y"))
    l.confirm(k1, 1, 0.3, 200, 5); l.confirm(k2, 1, None, 200, 5)
    s = summarize(con, "R")
    assert (s.cost_status, s.total_inr) == ("PARTIAL", 0.3)           # a lower bound, flagged as such
    assert summarize(con, "R").by_provider == {"crustdata": None}


def test_free_calls_are_never_blocked_and_known_zero_is_known(con):
    b = mk_budget(con, cfg={"max_cost_inr": 0.1})
    PaidClient(con, b, actual=0.1).call("a", est=0.1)
    b.reserve(0.0)                                                    # free call at the limit: allowed
    l = SqliteLedger(con, fixed_clock); k = l.reserve(entry("z", provider="razorpay_ifsc", est=0.0)); l.confirm(k, 1, 0.0, 200, 3)
    s = summarize(con, "R")
    assert s.cost_status == "KNOWN" and s.by_provider == {"crustdata": 0.1, "razorpay_ifsc": 0.0}


def test_no_calls_at_all_is_known_zero_and_all_items_resolved_free(con):
    s = summarize(con, "R")
    assert (s.cost_status, s.total_inr, s.calls, s.zero_cost_items_pct) == ("KNOWN", 0.0, 0, 0.0)


def test_stricter_of_request_and_config_limit(con):
    assert mk_budget(con, cfg={"max_cost_inr": 25.0}, request_limits=Limits(max_cost_inr=0.5)).max_cost == 0.5
    assert mk_budget(con, cfg={"max_cost_inr": 0.3}, request_limits=Limits(max_cost_inr=5.0)).max_cost == 0.3
    assert mk_budget(con, cfg={}, request_limits=None).max_cost is None            # no limit configured


def test_per_item_limit(con):
    client = PaidClient(con, mk_budget(con, cfg={"max_cost_inr": 100, "max_cost_inr_per_item": 0.5}))
    client.call("a"); client.call("a")
    with pytest.raises(BudgetExceeded):
        client.call("a")                                              # item a would pass ₹0.5
    client.call("b")                                                  # another item is fine


# ---- ledger rules ----
def test_reserve_is_persisted_before_the_call_and_repeats_are_new_attempts(con):
    l = SqliteLedger(con, fixed_clock)
    k1 = l.reserve(entry())
    assert con.execute("select status, attempt from cost_ledger where call_key=?", (k1,)).fetchone()[:] == ("RESERVED", 1)   # visible before any call
    k2 = l.reserve(entry())                                           # same request again: never overwrites earlier spend
    assert k2 != k1 and con.execute("select attempt from cost_ledger where call_key=?", (k2,)).fetchone()[0] == 2


def test_persistence_failure_means_no_provider_call(con):
    client = PaidClient(con, mk_budget(con, cfg={}))
    con.execute("DROP TABLE cost_ledger")
    with pytest.raises(PersistenceFailure):
        client.call("a")
    assert client.calls == 0


def test_confirm_only_a_reserved_row(con):
    l = SqliteLedger(con, fixed_clock); k = l.reserve(entry())
    l.confirm(k, 1, 0.2, 200, 1)
    with pytest.raises(LedgerError):
        l.confirm(k, 1, 0.2, 200, 1)                                  # twice
    with pytest.raises(LedgerError):
        l.fail("no-such-key", 500, "x")


def test_crash_leaves_reserved_rows_that_become_unconfirmed_and_still_count(con, tmp_path):
    l = SqliteLedger(con, fixed_clock)
    l.reserve(entry("a", est=0.4))                                    # process dies here: no confirm
    con2 = connect(str(tmp_path / "l.db"))                            # a new process
    assert SqliteLedger(con2, fixed_clock).mark_unconfirmed("R") == 1
    assert con2.execute("select status from cost_ledger").fetchone()[0] == "UNCONFIRMED"
    assert Budget(con2, "R", cfg_limits={"max_cost_inr": 0.5}).spent() == pytest.approx(0.4)     # still counts: spend never under-reported
    with pytest.raises(BudgetExceeded):
        Budget(con2, "R", cfg_limits={"max_cost_inr": 0.5}).reserve(0.2)                          # 0.4 + 0.2 > 0.5
    s = summarize(con2, "R")
    assert s.unconfirmed_inr == pytest.approx(0.4) and s.calls == 1 and s.total_inr == 0.0     # shown separately, not as confirmed spend


def test_failed_with_status_costs_nothing_but_a_timeout_may_have(con):
    l = SqliteLedger(con, fixed_clock)
    l.fail(l.reserve(entry("a", est=0.3, req="x")), 402, "credits")
    assert Budget(con, "R", cfg_limits={}).spent() == 0.0
    l.fail(l.reserve(entry("b", est=0.3, req="y")), None, "timeout")  # no response: the provider may have charged
    assert Budget(con, "R", cfg_limits={}).spent() == pytest.approx(0.3) and summarize(con, "R").unconfirmed_inr == pytest.approx(0.3)


def test_cache_hits_are_logged_at_zero_and_not_counted_as_calls(con):
    l = SqliteLedger(con, fixed_clock)
    l.cache_hit(entry()); l.cache_hit(entry())                        # the same hit twice does not collide
    s = summarize(con, "R")
    assert (s.cache_hits, s.calls, s.total_inr) == (2, 0, 0.0)


# ---- time ----
def test_time_limit_is_per_session(con):
    t = [0.0]
    b = Budget(con, "R", cfg_limits={"max_runtime_seconds": 600}, request_limits=Limits(max_runtime_seconds=10), monotonic=lambda: t[0])
    b.check_time(); t[0] = 9.9; b.check_time()
    t[0] = 10.0
    with pytest.raises(TimeExceeded):
        b.check_time()
    Budget(con, "R", cfg_limits={"max_runtime_seconds": 600}, request_limits=Limits(max_runtime_seconds=10),
           monotonic=lambda: t[0]).check_time()                       # resume: a fresh window, not an instant stop
    Budget(con, "R", cfg_limits={}, monotonic=lambda: 1e9).check_time()                           # no limit -> never raises


# ---- cache ----
def test_cache_roundtrip_expiry_and_replace(con):
    now = [NOW]
    c = SqliteCache(con, lambda: now[0])
    r = CachedResponse(status=200, headers={"x": "1"}, body="{}", retrieved_at=NOW)
    assert c.get("p", "o", "h") is None
    c.put("p", "o", "h", r, ttl_h=1)
    assert c.get("p", "o", "h").body == "{}" and c.get("p", "o", "h").headers == {"x": "1"}
    now[0] = NOW + timedelta(hours=1, seconds=1)
    assert c.get("p", "o", "h") is None                               # expired entries are never served
    c.put("p", "o", "h", r.model_copy(update={"body": "new"}), ttl_h=None)
    now[0] = NOW + timedelta(days=999)
    assert c.get("p", "o", "h").body == "new"                         # ttl None = no expiry


def test_request_limit_is_read_from_the_run_snapshot(con):
    with tx(con):
        con.execute("UPDATE runs SET policy_snapshot_json=?", ('{"_request": {"limits": {"max_cost_inr": 0.5}}}',))
    assert effective_cost_limit(con, "R", {"max_cost_inr": 25.0}) == 0.5
    assert effective_cost_limit(con, "R", {}) == 0.5
