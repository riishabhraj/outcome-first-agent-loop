"""Tests for the controller. Run: python -m pytest -q tests

Where the simulator is imported (cross-checks of the contract, and the integration test) it is TEST code only: the
controller itself never imports `sim` or reads `runs/` or scenario files.
"""
import copy
import json
import random
import socket
import threading
import time
from math import comb

import numpy as np
import pytest

from controller import cfgutil as U
from controller import config as C
from controller import did as DID
from controller import experiments as X
from controller.feat import FEATURES, Slice, pack, parse_slice_id
from controller.stats import binom_sf, mh_lift, two_prop_z, tv


# ------------------------------------------------------------------------------------------------ cohort object
def test_slice_id_is_canonical_and_roundtrips():
    a = Slice.make({"lang": ["ta", "hi"], "age_band": ["60+"]})
    b = Slice.make({"age_band": ["60+"], "lang": ["hi", "ta"]})
    assert a == b and a.id == b.id == "age_band=60+|lang=hi,ta"
    assert parse_slice_id(a.id) == a
    assert Slice(()).id == "ALL"


def test_slice_mask_matches_where_semantics():
    sl = Slice.make({"age_band": ["60+", "46-60"], "lang": ["ta"]})
    m = sl.mask()
    hit = pack({"lang": "ta", "city_tier": "2", "age_band": "60+", "value_band": "mid"})
    miss_lang = pack({"lang": "hi", "city_tier": "2", "age_band": "60+", "value_band": "mid"})
    miss_age = pack({"lang": "ta", "city_tier": "2", "age_band": "18-30", "value_band": "mid"})
    assert m[hit] and not m[miss_lang] and not m[miss_age]


# ------------------------------------------------------------------------------------------------ statistics
def test_binom_sf_exact():
    n, p, k = 105, 0.014, 7
    brute = sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))
    assert binom_sf(k, n, p) == pytest.approx(brute, rel=1e-6)
    # the normal approximation calls this z~4.5 (p~3e-6); the exact tail is ~100x larger -> why we use the exact one
    z = (k / n - p) / (p * (1 - p) / n) ** 0.5
    assert z > 4 and binom_sf(k, n, p) > 1e-4


def test_mh_lift_removes_simpson_confound():
    """Arm is sent mostly to a high-baseline cohort; pooled comparison shows a fake lift, stratified shows none."""
    rng = np.random.default_rng(1)
    strata, pooled = [], [0, 0, 0, 0]
    for base, p_arm in ((0.8, 0.9), (0.3, 0.1)):         # (cohort base rate, share of traffic on the arm)
        n = 4000
        arm = rng.random(n) < p_arm
        y = rng.random(n) < base                            # true effect of the arm is exactly zero
        na, nc = int(arm.sum()), int((~arm).sum())
        ya, yc = int(y[arm].sum()), int(y[~arm].sum())
        strata.append((na, ya, nc, yc))
        pooled = [pooled[0] + na, pooled[1] + ya, pooled[2] + nc, pooled[3] + yc]
    naive = (pooled[1] / pooled[0]) / (pooled[3] / pooled[2]) - 1
    r = mh_lift(strata)
    assert naive > 0.3 and abs(r["lift"]) < 0.08


def test_mh_lift_detects_real_effect():
    rng = np.random.default_rng(2)
    n = 3000
    strata = []
    for base in (0.5, 0.6):
        a, c = rng.random(n) < base * 1.15, rng.random(n) < base
        strata.append((n, int(a.sum()), n, int(c.sum())))
    r = mh_lift(strata)
    assert 0.08 < r["lift"] < 0.22 and r["z"] > 3


def test_two_stage_rule_false_winner_rate_under_null():
    """A/A: no arm has any effect. The chance that a (slice, arm) hypothesis passes stage 1 on cumulative data and then
    replicates on fresh data must be far below the stage-1 rate alone."""
    rng = np.random.default_rng(7)
    reps, m = 150, 40
    flagged = promoted = 0
    for _ in range(reps):
        for _h in range(m):
            n = 200
            a1, c1 = rng.random(n) < 0.55, rng.random(n) < 0.55
            r1 = mh_lift([(n, int(a1.sum()), n, int(c1.sum()))])
            if r1["z"] >= C.Z_FIRST and r1["lift"] >= C.MIN_LIFT:
                flagged += 1
                a2, c2 = rng.random(n) < 0.55, rng.random(n) < 0.55
                r2 = mh_lift([(n, int(a2.sum()), n, int(c2.sum()))])
                if r2["z"] >= C.Z_CONFIRM and r2["lift"] >= C.MIN_CONFIRM_LIFT:
                    promoted += 1
    tests = reps * m
    assert flagged / tests < 0.01 and promoted / tests < 0.0015


def test_z_for_alpha_is_monotone_and_correct():
    assert X.z_for_alpha(0.05) == pytest.approx(1.645, abs=0.01)
    assert X.z_for_alpha(0.2 / 85) > X.z_for_alpha(0.2 / 10)


# ------------------------------------------------------------------------------------------------ allocations
def _st(**kw):
    return {o: kw.get(o, ("unknown", 0.0)) for o in ("V1", "V2", "V3", "V4")}


def test_alloc_weights_exploring_has_control_floor_and_sums_to_one():
    w = X.alloc_weights(_st(), "V1")
    assert w["V1"] == pytest.approx(C.EXPLORE_CONTROL_SHARE, abs=1e-3) and sum(w.values()) == pytest.approx(1, abs=1e-3)


def test_alloc_weights_winner_keeps_holdout_and_eliminated_gets_zero():
    w = X.alloc_weights(_st(V3=("winner", 0.2), V4=("eliminated", -0.1)), "V1")
    assert "V4" not in w and w["V3"] > w["V1"] >= C.WINNER_SHARES[1] - 1e-6 and sum(w.values()) == pytest.approx(1, abs=1e-3)


def test_alloc_weights_all_eliminated_falls_back_to_control():
    w = X.alloc_weights(_st(V2=("eliminated", -.1), V3=("eliminated", -.1), V4=("eliminated", -.1)), "V1")
    assert w == {"V1": 1.0}


# ------------------------------------------------------------------------------------------------ config helpers
def _rand_cfg(rng, cohorts):
    cfg = {"cohorts": [{"id": c, "where": {"age_band": ["60+"]} if c == "a" else {"lang": ["hi"]}} for c in cohorts],
           "allocations": {"default": {}}, "prompt_version": "P1",
           "schedule": {"hour_weights": {}, "lead_preferred_hour": {}}}
    for k in ["default"] + list(cohorts):
        cfg["allocations"].setdefault(k, {})
        for dim, opts in (("welcome_message", ["W1", "W2", "W3"]), ("voice", ["V1", "V2", "V3", "V4"]),
                          ("caller_id", ["D1", "D2", "D3"])):
            if k == "default" or rng.random() < 0.5:
                w = {o: float(rng.random()) + 0.01 for o in opts if rng.random() < 0.8} or {opts[0]: 1.0}
                s = sum(w.values())
                cfg["allocations"][k][dim] = {o: v / s for o, v in w.items()}
    return cfg


def test_cfgutil_matches_simulator_contract():
    from sim.world import changed_dims as sim_changed, max_shift as sim_shift
    rng = np.random.default_rng(3)
    for _ in range(60):
        a, b = _rand_cfg(rng, ["a", "b"]), _rand_cfg(rng, ["a", "b"] if rng.random() < .7 else ["a"])
        assert U.changed_dims(a, b) == sim_changed(a, b)
        assert U.max_shift(a, b) == pytest.approx(sim_shift(a, b))


def test_did_step_toward_respects_tv_limit():
    live, tgt = {"D1": 1.0}, {"D1": 0.2, "D2": 0.4, "D3": 0.4}
    new = DID.step_toward(live, tgt, 0.18, 1.0)
    assert tv(live, new) <= 0.18 + 1e-3 and sum(new.values()) == pytest.approx(1, abs=1e-3)
    assert tv(live, DID.step_toward(live, tgt, 1.0, 1.0)) == pytest.approx(0.8, abs=1e-2)


# ------------------------------------------------------------------------------------------------ governance rules
@pytest.fixture()
def gov(tmp_path):
    from controller.context import Ctx, WfMeta
    from controller.governance import Governance
    from controller.store import Store
    st = Store(str(tmp_path / "t.db"))
    ctx = Ctx(None, st)
    base = {"workflow_id": "w", "tenant_id": "northwind_life", "options": {"welcome_message": ["W1"], "voice": ["V1"],
            "caller_id": ["D1"], "prompt_version": ["P1"]}, "dialling_window": [9, 21], "dials_per_hour": 100,
            "cadence": [2], "objective": {"primary": "engagement_rate", "guardrail": {"pickup_rate_max_drop": 0.02}},
            "governance": {"approver_hours": [10, 19], "approver_latency_minutes": [30, 240], "max_requests_per_24h": 3,
                           "proposal_ttl_hours": 24, "apply_window_hours": 2, "min_validation_pass_rate": 0.97,
                           "auto_apply": {"enabled": False, "allowed_dims": [], "max_shift": 0}},
            "mid_flight": "allowed", "languages": ["hi"], "daily_batch": {"size": 10}, "prompt_catalog": {"P1": ""}}
    ctx.meta["w"] = WfMeta.from_api(base)
    k = dict(base, workflow_id="k", tenant_id="kestrel_finance")
    k["governance"] = dict(base["governance"], max_requests_per_24h=4, approver_latency_minutes=[20, 120])
    ctx.meta["k"] = WfMeta.from_api(k)
    return Governance(ctx), st


def at(day, h, m=0):
    return day * 1440 + h * 60 + m


def test_submission_window_follows_approver_hours_and_latency(gov):
    g, _ = gov
    assert g.can_submit("w", at(1, 3))[0] is False            # night: would sit in a queue
    assert g.can_submit("w", at(1, 10))[0] is True
    assert g.can_submit("w", at(1, 14, 45))[0] is True
    assert g.can_submit("w", at(1, 15))[0] is False           # 15:00 + up to 4h latency would spill past 19:00
    assert g.can_submit("w", at(1, 15), urgent=True)[0] is True   # incidents ignore the window


def test_tenant_quiet_hours_are_honoured(gov):
    g, _ = gov
    assert g.can_submit("k", at(1, 10, 30))[0] is False       # Kestrel supervisor: never before 11:00
    assert g.can_submit("k", at(1, 11))[0] is True


def test_urgent_slot_is_reserved_and_budget_enforced(gov):
    """Kestrel: policy allows 4/24h but the client asked for max 2/day, so hard cap = 2; routine work may use only
    (cap - 1) so an incident fix never queues behind a routine experiment."""
    g, st = gov

    def add(pid, t):
        st.db.execute("INSERT INTO proposals(proposal_id,wf,route,kind,state,created_minute,base_version,dims,updated_minute)"
                      " VALUES(?,?,?,?,?,?,?,?,?)", (pid, "k", "approval", "batch", "applied", t, 1, "[]", t))
    now = at(3, 12)
    add("p1", at(2, 14))                                       # one request inside the last 24h
    assert g.can_submit("k", now)[0] is False                  # routine: its single slot is used (cap 2 minus reserve 1)
    assert g.can_submit("k", now, urgent=True)[0] is True      # ...but the reserved slot is there for an incident
    add("p2", at(3, 11))                                       # second request: hard cap reached
    assert g.can_submit("k", now, urgent=True)[0] is False
    assert g.can_submit("k", at(4, 12), urgent=True)[0] is True    # slots free up as requests age past 24h


def test_no_second_request_while_one_is_in_flight(gov):
    g, st = gov
    st.db.execute("INSERT INTO proposals(proposal_id,wf,route,kind,state,created_minute,base_version,dims,updated_minute)"
                  " VALUES('p','w','approval','batch','pending',0,1,'[]',0)")
    ok, why = g.can_submit("w", at(3, 10))
    assert not ok and "in flight" in why
    assert g.can_submit("w", at(3, 10), urgent=True)[0] is True    # an incident fix never queues behind routine work


def test_auto_apply_policy_check_mirrors_contract(gov):
    g, _ = gov
    g.ctx.meta["w"].governance["auto_apply"] = {"enabled": True, "allowed_dims": ["caller_id", "voice"], "max_shift": 0.2}
    live = _rand_cfg(np.random.default_rng(0), [])
    new = copy.deepcopy(live)
    new["allocations"]["default"]["caller_id"] = {"D1": 0.5, "D2": 0.5}
    ok, _ = g.auto_ok("w", live, new)
    assert not ok                                              # shift 0.5 > max_shift 0.2
    new["allocations"]["default"]["caller_id"] = {k: (v * 0.9 + (0.1 if k == "D1" else 0)) for k, v in live["allocations"]["default"]["caller_id"].items()}
    ok, _ = g.auto_ok("w", live, new)
    assert ok
    new2 = copy.deepcopy(live)
    new2["allocations"]["default"]["voice"] = {"V1": 0.9, "V2": 0.1}
    ok, problems = g.auto_ok("w", live, new2)
    assert not ok and any("customer-facing" in p for p in problems)   # allowed by policy, voluntarily not used


def test_store_audit_is_append_only_and_queryable(tmp_path):
    from controller.store import Store
    st = Store(str(tmp_path / "a.db"))
    st.set_now(90)
    st.audit("proposal_intent", "w", "x", {"k": 1}, proposal_id="p1")
    st.audit("proposal_submitted", "w", "y", {}, proposal_id="p1", approval_id="apr_1")
    rows = st.q("SELECT kind,sim_minute,proposal_id FROM audit WHERE proposal_id='p1' ORDER BY id")
    assert [r[0] for r in rows] == ["proposal_intent", "proposal_submitted"] and rows[0][1] == 90
    assert st.alert("w", "page", "c", "m") is True and st.alert("w", "page", "c", "m") is False   # de-duplicated


# ------------------------------------------------------------------------------------------------ integration
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_end_to_end_small_scenario_and_restart_is_idempotent(tmp_path):
    """Whole loop against a real simulator over HTTP (3 days), then a second controller on the same store+world."""
    import uvicorn
    from sim import server
    from sim.scenario import load_scenario
    from sim.world import World
    sc = load_scenario("scenarios/public_01_dev.yaml")
    sc["days"], sc["history_days"] = 3, 5
    server._world = World(sc, tmp_path / "run")
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    try:
        from controller.main import Controller
        db = str(tmp_path / "c.db")
        Controller(f"http://127.0.0.1:{port}", db, None).run()
        c2 = Controller(f"http://127.0.0.1:{port}", db, None)       # restart on a finished world: must be a clean no-op
        c2.run()
        import sqlite3
        d = sqlite3.connect(db)
        assert d.execute("SELECT count(*) FROM audit").fetchone()[0] > 20
        assert d.execute("SELECT count(*) FROM proposals WHERE state IN ('submitting')").fetchone()[0] == 0
        n_chunks = d.execute("SELECT count(*) FROM chunks").fetchone()[0]
        assert n_chunks == len(server._world.chunks)                 # every chunk ingested exactly once
        ev = d.execute("SELECT sum(events) FROM chunks").fetchone()[0]
        assert ev == server._world.event_seq
        # a proposal was never sent twice
        ids = [r[0] for r in d.execute("SELECT proposal_id FROM proposals")]
        assert len(ids) == len(set(ids))
    finally:
        srv.should_exit = True
