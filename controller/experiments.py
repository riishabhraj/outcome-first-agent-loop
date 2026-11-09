"""Welcome-message / voice experimentation, winner promotion, decay detection and rollback (C6, C7).

Design (details and numbers in SUBMISSION.md §5)
 * Per call the simulator draws welcome and voice independently, so each dimension is analysed on its own marginal
   (the other dimension is balanced noise). Comparisons are always arm-vs-CONTROL, stratified by (config version,
   cohort) because allocation probabilities differ across strata (see stats.mh_lift).
 * Hypotheses are slices: ALL and each single feature value holding >= MIN_SLICE_SHARE of connected calls.
 * Stage 1 flags a candidate (z >= family-corrected threshold AND relative lift >= MIN_LIFT, min sample per arm).
   Stage 2 confirms it on FRESH data collected only after the candidate allocation went live: one-sided z >= 1.28.
   A very strong stage-1 result (z >= Z_STRONG, lift >= 6%) is promoted directly. This is two-stage testing, so the
   chance a null arm is promoted is ~ P(stage1) x P(stage2) rather than P(stage1).
 * Winners keep a control holdout so their lift keeps being measured; a winner whose recent lift falls well below
   its promotion lift on two consecutive checks is decayed and its traffic returns to control (rollback).
 * Arms clearly worse than control in a slice are eliminated (harm control while exploring).
"""
from __future__ import annotations

from math import sqrt

import numpy as np

from . import analytics as A
from . import config as C
from .context import Ctx
from .feat import FEATURES, Slice
from .stats import mh_lift, norm_sf, two_prop_z

DIMS = ["welcome_message", "voice"]
Z_STRONG = 3.8
STRONG_LIFT = 0.06


def z_for_alpha(alpha: float) -> float:
    lo, hi = 0.0, 8.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if norm_sf(mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def key(sid: str, opt: str) -> str:
    return f"{sid}||{opt}"


def enabled(ctx: Ctx, wf: str, state: dict | None = None) -> bool:
    """Welcome/voice move engagement. Primary=engagement: always. Primary=pickup: only to defend an engagement
    guardrail that is eroding (see main.guardrail_defense), never to chase a secondary KPI."""
    return ctx.meta[wf].primary == "engagement_rate" or bool(state and state.get("guardrail_defense"))


def ensure_state(ctx: Ctx, wf: str, live_cfg: dict, now: int, exp: dict, version: int) -> bool:
    """Create per-dim state; returns True if anything was (re)initialised."""
    changed = False
    for dim in DIMS:
        opts = ctx.meta[wf].options[dim]
        dist = live_cfg["allocations"]["default"][dim]
        control = max(dist, key=dist.get)
        s = exp.get(dim)
        if s is None:
            exp[dim] = {"control": control, "epoch_version": version, "epoch_minute": now, "arms": {},
                        "epochs": [], "log": []}
            changed = True
        s = exp[dim]
        s["options"] = list(opts)
    return changed


def reset_epoch(exp: dict, dim: str, new_control: str, version: int, now: int, why: str):
    s = exp[dim]
    s["epochs"].append({"control": s["control"], "epoch_version": s["epoch_version"], "ended": now, "why": why,
                        "arms": s["arms"]})
    s["control"], s["epoch_version"], s["epoch_minute"], s["arms"] = new_control, version, now, {}


def candidate_slices(ctx: Ctx, wf: str, rows: np.ndarray) -> list[Slice]:
    if len(rows) == 0:
        return [Slice(())]
    cell = rows[:, A.COL["cell"]]
    conn = rows[:, A.COL["conn"]]
    tot = max(int(conn.sum()), 1)
    out = [Slice(())]
    for f, vals in FEATURES.items():
        if f == "lang":
            vals = [v for v in vals if v in ctx.meta[wf].languages]
        for v in vals:
            m = Slice.make({f: [v]}).mask()
            if conn[m[cell]].sum() / tot >= C.MIN_SLICE_SHARE:
                out.append(Slice.make({f: [v]}))
    return out


def _recent_ok(rows, dim, sl, oi, ci, overall_lift, now, *, version_from=None) -> bool:
    """Don't flag/promote something that is already fading. Two checks on the evidence window:
    (1) the most recent RECENCY_H still shows most of the lift; (2) the late half of the window is not significantly
    below the early half (a variant that decays *before* we promote it has no later trend for the decay detector)."""
    rec = A.arm_lift(rows, dim, sl, oi, ci, slot_from=(now - C.RECENCY_H * 60) // C.SLOT_MIN, version_from=version_from)
    if rec["n_arm"] >= C.MIN_CONN_PER_ARM and rec["n_ctrl"] >= 40 and rec["lift"] < 0.5 * overall_lift:
        return False
    m = sl.mask()[rows[:, A.COL["cell"]]]
    if version_from is not None:
        m &= rows[:, A.COL["version"]] >= version_from
    r = rows[m]
    opt = r[:, A.COL["w"] if dim == "welcome_message" else A.COL["v"]]
    slots = r[:, A.COL["slot"]][(opt == oi)]
    if len(slots) == 0:
        return True
    lo, hi = int(slots.min()), int(slots.max())
    if hi - lo < 4:
        return True
    mid = (lo + hi) // 2
    early = A.arm_lift(rows, dim, sl, oi, ci, slot_from=lo, slot_to=mid, version_from=version_from)
    late = A.arm_lift(rows, dim, sl, oi, ci, slot_from=mid + 1, slot_to=hi, version_from=version_from)
    if min(early["n_arm"], early["n_ctrl"], late["n_arm"], late["n_ctrl"]) < 40:
        return True
    z = (late["lift"] - early["lift"]) / max(sqrt(early["se_lift"] ** 2 + late["se_lift"] ** 2), 1e-6)
    return not (z <= -1.0 and late["lift"] < 0.6 * overall_lift)


def _guard_ok(rows, dim, sl, a, c, drop, *, version_from=None) -> tuple[bool, dict]:
    """Guardrail: don't promote an arm with significant evidence of hurting pickup (primary=engagement)."""
    g = A.arm_lift(rows, dim, sl, a, c, metric="conn", version_from=version_from)
    bad = g["n_arm"] >= 200 and g["z"] <= -2.5 and g["lift"] <= -drop
    return (not bad), g


def update(ctx: Ctx, wf: str, now: int, state: dict, live_version: int) -> list[dict]:
    """Advance every (dim, slice, arm) hypothesis. Returns audit events (dicts)."""
    ev: list[dict] = []
    meta = ctx.meta[wf]
    exp = state.setdefault("exp", {})
    for dim in DIMS:
        s = exp[dim]
        opts = meta.options[dim]
        if len(opts) < 2:
            continue
        ci = opts.index(s["control"])
        rows = A.exp_rows(ctx, wf, min_version=s["epoch_version"])
        if len(rows) == 0:
            continue
        slices = candidate_slices(ctx, wf, rows)
        arms = s["arms"]
        # ---- stage 1: scan open hypotheses
        todo = []
        for sl in slices:
            for oi, o in enumerate(opts):
                if oi == ci or key(sl.id, o) in arms or o in s.get("banned", {}):
                    continue
                todo.append((sl, oi, o, A.arm_lift(rows, dim, sl, oi, ci)))
        tested = [t for t in todo if t[3]["n_arm"] >= C.MIN_CONN_PER_ARM and t[3]["n_ctrl"] >= C.MIN_CONN_PER_ARM]
        z_first = max(C.Z_FIRST, z_for_alpha(0.2 / max(len(tested), 1)))
        for sl, oi, o, r in tested:
            k = key(sl.id, o)
            guard_ok, g = _guard_ok(rows, dim, sl, oi, ci, meta.guardrail_drop)
            # hierarchy: a slice only gets its own decision if it differs from the decision already in force for
            # the whole population (otherwise it just fragments traffic into cohorts that behave like the parent)
            parent = arms.get(key("ALL", o)) if sl.where else None
            parent_pos = bool(parent and parent["stage"] in ("winner", "candidate"))
            if parent_pos and r["lift"] > C.MAX_ELIM_LIFT:
                continue
            if parent_pos and not (r["z"] <= -2.5):
                continue
            if r["z"] <= -C.Z_ELIMINATE and r["lift"] <= C.MAX_ELIM_LIFT:
                arms[k] = {"stage": "eliminated", "slice": sl.id, "opt": o, "t": now, "lift": r["lift"], "z": r["z"],
                           "n": [r["n_arm"], r["n_ctrl"]]}
                ev.append({"kind": "arm_eliminated", "dim": dim, "slice": sl.id, "opt": o, "stats": _brief(r)})
            elif r["z"] >= z_first and r["lift"] >= C.MIN_LIFT:
                if not _recent_ok(rows, dim, sl, oi, ci, r["lift"], now):
                    continue
                if not guard_ok:
                    arms[k] = {"stage": "eliminated", "slice": sl.id, "opt": o, "t": now, "lift": r["lift"],
                               "z": r["z"], "note": "guardrail: pickup harmed", "n": [r["n_arm"], r["n_ctrl"]]}
                    ev.append({"kind": "arm_guardrail_block", "dim": dim, "slice": sl.id, "opt": o,
                               "stats": _brief(r), "guard": _brief(g)})
                elif r["z"] >= Z_STRONG and r["lift"] >= STRONG_LIFT:
                    arms[k] = {"stage": "winner", "slice": sl.id, "opt": o, "t_flag": now, "t_win": now,
                               "lift_win": r["lift"], "z1": r["z"], "lift1": r["lift"], "path": "strong",
                               "decay_hits": 0, "n": [r["n_arm"], r["n_ctrl"]], "z_first": z_first}
                    ev.append({"kind": "winner_promoted", "dim": dim, "slice": sl.id, "opt": o, "path": "strong",
                               "stats": _brief(r), "z_threshold": z_first})
                else:
                    arms[k] = {"stage": "candidate", "slice": sl.id, "opt": o, "t_flag": now, "z1": r["z"],
                               "lift1": r["lift"], "fresh_version": None, "n": [r["n_arm"], r["n_ctrl"]],
                               "z_first": z_first}
                    ev.append({"kind": "candidate_flagged", "dim": dim, "slice": sl.id, "opt": o, "stats": _brief(r),
                               "z_threshold": z_first, "tests_in_family": len(tested)})
        # ---- stage 2: confirm candidates on fresh data
        for k, a in list(arms.items()):
            if a["stage"] == "candidate" and a.get("fresh_version") is not None:
                sl = Slice.make(_slice_where(a["slice"]))
                oi = opts.index(a["opt"])
                r = A.arm_lift(rows, dim, sl, oi, ci, version_from=a["fresh_version"])
                if r["n_arm"] >= C.CONFIRM_MIN_CONN and r["n_ctrl"] >= C.CONFIRM_MIN_CONN:
                    if r["z"] >= C.Z_CONFIRM and r["lift"] >= C.MIN_CONFIRM_LIFT and \
                            _recent_ok(rows, dim, sl, oi, ci, r["lift"], now, version_from=a["fresh_version"]):
                        a.update({"stage": "winner", "t_win": now, "lift_win": r["lift"], "path": "confirmed",
                                  "decay_hits": 0, "z2": r["z"], "n_fresh": [r["n_arm"], r["n_ctrl"]]})
                        ev.append({"kind": "winner_promoted", "dim": dim, "slice": a["slice"], "opt": a["opt"],
                                   "path": "confirmed", "stage1": {"z": a["z1"], "lift": a["lift1"]},
                                   "stats": _brief(r)})
                    elif r["lift"] < 0 or r["z"] < 0:
                        a.update({"stage": "rejected", "t": now, "z2": r["z"], "lift2": r["lift"]})
                        ev.append({"kind": "candidate_rejected", "dim": dim, "slice": a["slice"], "opt": a["opt"],
                                   "stage1": {"z": a["z1"], "lift": a["lift1"]}, "stats": _brief(r),
                                   "note": "did not replicate on fresh data -> treated as a false alarm"})
        # ---- decay check on winners: has the lift CHANGED? recent window vs fresh data since promotion (not the
        # promotion-time estimate, which carries winner's curse). Needs two consecutive hits.
        slot_recent = (now - C.DECAY_WINDOW_H * 60) // C.SLOT_MIN
        for k, a in list(arms.items()):
            if a["stage"] != "winner" or now - a.get("t_check", 0) < 180:
                continue
            sl = Slice.make(_slice_where(a["slice"]))
            oi = opts.index(a["opt"])
            sr = A.arm_strata(rows, dim, sl, oi, ci, slot_from=slot_recent)
            so = A.arm_strata(rows, dim, sl, oi, ci, slot_from=a["t_win"] // C.SLOT_MIN, slot_to=slot_recent - 1)
            rec, old = mh_lift(sr), mh_lift(so)
            tot = lambda st, i: sum(x[i] for x in st)
            na_r, ya_r, nc_r, yc_r = (tot(sr, i) for i in range(4))
            na_o, ya_o, nc_o, yc_o = (tot(so, i) for i in range(4))
            a["t_check"] = now
            a.setdefault("trace", []).append([now, round(rec["lift"], 4), na_r, nc_r])
            a["trace"] = a["trace"][-40:]
            if min(na_r, na_o) < C.DECAY_MIN_ARM or min(nc_r, nc_o) < C.DECAY_MIN_CTRL:
                continue
            # decay = the variant's own rate fell (large n, well powered) while the control's did not (so it is not a
            # calendar/mix effect), and the arm-vs-control lift has dropped to under half of what it was
            z_arm = two_prop_z(ya_r, na_r, ya_o, na_o)
            z_ctl = two_prop_z(yc_r, nc_r, yc_o, nc_o)
            hit = (z_arm <= -C.DECAY_Z and z_ctl >= -1.5 and rec["lift"] <= C.DECAY_FRACTION * max(old["lift"], C.MIN_LIFT))
            a["decay_hits"] = a.get("decay_hits", 0) + 1 if hit else 0
            if a["decay_hits"] >= C.DECAY_CONFIRM_CHECKS:
                a.update({"stage": "decayed", "t_decay": now, "lift_recent": rec["lift"]})
                if a["slice"] == "ALL":
                    s.setdefault("banned", {})[a["opt"]] = now      # a decaying variant is not re-discovered slice by slice
                for k2, b in arms.items():
                    if b is not a and b["stage"] == "winner" and b["opt"] == a["opt"]:
                        b["decay_hits"] = max(b.get("decay_hits", 0), C.DECAY_CONFIRM_CHECKS - 1)
                ev.append({"kind": "winner_decayed", "dim": dim, "slice": a["slice"], "opt": a["opt"],
                           "reference_lift": old["lift"], "recent": _brief(rec), "reference": _brief(old),
                           "z_arm_rate_change": round(z_arm, 2), "z_control_rate_change": round(z_ctl, 2),
                           "urgent": True})
    return ev


def _slice_where(sid: str) -> dict:
    from .feat import parse_slice_id
    return parse_slice_id(sid).as_where()


def _brief(r: dict) -> dict:
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()
            if k in ("n_arm", "n_ctrl", "lift", "z", "p_arm", "p_ctrl", "se_lift")}


def option_states(state: dict, dim: str, slice_ids: list[str]) -> dict:
    """Combine the stage of each option across all slices containing a block. Priority winner > candidate >
    eliminated > unknown; ties broken by lift."""
    s = state["exp"][dim]
    best = {o: ("unknown", 0.0) for o in s["options"]}
    for o in s.get("banned", {}):
        best[o] = ("decayed", 0.0)
    rank = {"unknown": 0, "decayed": 1, "rejected": 1, "eliminated": 2, "candidate": 3, "winner": 4}
    for sid in slice_ids:
        for o in s["options"]:
            a = s["arms"].get(key(sid, o))
            if not a:
                continue
            st = a["stage"]
            lift = a.get("lift_win", a.get("lift1", a.get("lift", 0.0)))
            cur = best[o]
            if rank[st] > rank[cur[0]] or (rank[st] == rank[cur[0]] and lift > cur[1]):
                best[o] = (st, lift)
    # a decayed arm is rolled back (no traffic); a rejected (non-replicating) arm is simply unknown again
    return {o: ("eliminated" if st == "decayed" else st, lift) for o, (st, lift) in best.items()}


def alloc_weights(states: dict, control: str) -> dict:
    elim = {o for o, (st, _) in states.items() if st == "eliminated" and o != control}
    winners = sorted([(l, o) for o, (st, l) in states.items() if st == "winner" and o != control], reverse=True)
    cands = sorted([(l, o) for o, (st, l) in states.items() if st == "candidate" and o != control], reverse=True)
    unknown = [o for o, (st, _) in states.items() if st in ("unknown", "rejected") and o != control]
    w: dict[str, float] = {}
    if winners:
        best = winners[0][1]
        others = [o for _, o in winners[1:]] + [o for _, o in cands] + unknown
        w[best], w[control] = C.WINNER_SHARES[0], C.WINNER_SHARES[1]
        rest = 1.0 - sum(w.values())
        if others:
            for o in others:
                w[o] = rest / len(others)
        else:
            w[best] += rest
    elif cands:
        best = cands[0][1]
        others = [o for _, o in cands[1:]] + unknown
        w[best], w[control] = C.CAND_SHARES
        rest = 1.0 - sum(w.values())
        if others:
            for o in others:
                w[o] = rest / len(others)
        else:
            w[best] += rest * 0.5
            w[control] += rest * 0.5
    else:
        if unknown:
            w[control] = C.EXPLORE_CONTROL_SHARE
            for o in unknown:
                w[o] = (1 - C.EXPLORE_CONTROL_SHARE) / len(unknown)
        else:
            w[control] = 1.0
    s = sum(w.values())
    return {o: round(v / s, 4) for o, v in w.items() if v > 0 and o not in elim}


def decision_vector(state: dict, feat: str, value: str) -> tuple:
    """Per-dim stage signature of a single-feature slice - used to group feature values into cohorts."""
    sid = Slice.make({feat: [value]}).id
    vec = []
    for dim in DIMS:
        s = state.get("exp", {}).get(dim)
        if not s:
            continue
        for o in s["options"]:
            a = s["arms"].get(key(sid, o))
            if a and a["stage"] in ("candidate", "winner", "eliminated", "decayed"):
                vec.append((dim, o, "eliminated" if a["stage"] == "decayed" else a["stage"]))
    return tuple(vec)


def summary(state: dict) -> list[dict]:
    out = []
    for dim in DIMS:
        s = state.get("exp", {}).get(dim)
        if not s:
            continue
        for k, a in s["arms"].items():
            out.append({"dim": dim, "slice": a["slice"], "opt": a["opt"], "stage": a["stage"],
                        "lift": round(a.get("lift_win", a.get("lift1", a.get("lift", 0.0))), 3),
                        "control": s["control"]})
    return out
