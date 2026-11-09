"""Caller-ID health (C5): detect a degrading DID from pickup, rotate away, keep probing so recovery is noticed.

Why peer comparison: a call's DID is drawn independently of the lead and the hour, so every DID sees the same
mix of people and times. A DID's pickup rate is therefore directly comparable with its peers' in the same window;
no model of hour-of-day or lead mix is needed and a workflow-wide dip (holiday, bad hour) does not flag anything.
"""
from __future__ import annotations

from . import analytics as A
from . import config as C
from .context import Ctx
from .stats import normalise, two_prop_z


def window_stats(ctx: Ctx, wf: str, now: int, hours: float) -> dict:
    opts = ctx.meta[wf].options["caller_id"]
    raw = A.did_window(ctx, wf, int(now - hours * 60), now)
    return {opts[i]: v for i, v in raw.items() if i < len(opts)}


def _ratio(st: dict, did: str):
    s = st.get(did)
    peers_d = sum(v["dials"] for k, v in st.items() if k != did)
    peers_c = sum(v["conn"] for k, v in st.items() if k != did)
    if not s or s["dials"] < 1 or peers_d < 1 or peers_c == 0:
        return None
    return s["dials"], s["conn"], peers_d, peers_c


def detect(ctx: Ctx, wf: str, now: int, state: dict) -> dict:
    """Flag a DID whose pickup STEPPED down against its peers (spam flag / carrier block) and put recovered ones back.

    A step, not a level: a DID that is merely overloaded declines gradually and is handled by the continuous
    pickup-proportional reweighting; flagging it would rotate violently and back. So the flag needs both a low ratio
    now and a healthy ratio over the 6-30h before."""
    st = window_stats(ctx, wf, now, 3)
    prev = {k: v for k, v in _prev_window(ctx, wf, now).items()}
    flagged = state.setdefault("flagged", {})
    prob = state.setdefault("probation", {})
    ev = {"new": [], "recovered": []}
    for did in [d for d, f in flagged.items() if now - f["since"] >= C.DID_FLAG_TTL_H * 60]:
        del flagged[did]
        prob[did] = now                      # probation: a few % of traffic so a recovery can be seen, never more
    st24 = window_stats(ctx, wf, now, C.DID_RECOVERY_WINDOW_H)
    for did in list(prob):
        r24 = _ratio(st24, did)
        if r24 and r24[0] >= 100 and r24[2] >= 100:
            ratio24 = (r24[1] / r24[0]) / max(r24[3] / r24[2], 1e-9)
            if ratio24 >= 0.85 and two_prop_z(r24[1], r24[0], r24[3], r24[2]) > -1.0:
                del prob[did]
                ev["recovered"].append(did)
    for did, s in st.items():
        r = _ratio(st, did)
        if not r or r[0] < C.DID_FLAG_MIN_N or r[2] < C.DID_FLAG_MIN_N:
            continue
        d, c, pd, pc = r
        z = two_prop_z(c, d, pc, pd)
        ratio = (c / d) / (pc / pd)
        if did in flagged:
            continue
        if did in prob and ratio >= 0.85 and z > -1.0 and d >= C.DID_FLAG_MIN_N:
            del prob[did]
            ev["recovered"].append(did)
            continue
        if z <= -C.DID_FLAG_Z and ratio <= C.DID_FLAG_RATIO:
            pr = _ratio(prev, did)
            known = pr is not None and pr[0] >= 100 and pr[2] >= 100
            stepped = ((pr[1] / pr[0]) / max(pr[3] / pr[2], 1e-9) >= 0.8) if known else (z <= -5.0 and ratio <= 0.5)
            if stepped:
                flagged[did] = {"since": now, "z": round(z, 2), "ratio": round(ratio, 2), "n": d}
                prob.pop(did, None)
                ev["new"].append(did)
    return ev


def _prev_window(ctx: Ctx, wf: str, now: int) -> dict:
    opts = ctx.meta[wf].options["caller_id"]
    raw = A.did_window(ctx, wf, now - 30 * 60, now - 6 * 60)
    return {opts[i]: v for i, v in raw.items() if i < len(opts)}


def factors(ctx: Ctx, wf: str, now: int, hours: float = C.DID_WINDOW_H) -> dict:
    """Shrunk relative health factor per DID: observed pickup / pooled pickup, shrunk to 1 for thin data."""
    st = window_stats(ctx, wf, now, hours)
    D = sum(v["dials"] for v in st.values())
    Cn = sum(v["conn"] for v in st.values())
    out = {}
    pooled = Cn / D if D else None
    for did in ctx.meta[wf].options["caller_id"]:
        s = st.get(did)
        if not s or not pooled:
            out[did] = {"n": s["dials"] if s else 0, "pickup": None, "f": 1.0}
            continue
        m = C.DID_PEER_PRIOR_N
        f = (s["conn"] + m * pooled) / ((s["dials"] + m) * pooled)
        out[did] = {"n": s["dials"], "pickup": round(s["conn"] / s["dials"], 4), "f": round(f, 3)}
    return out


def target(ctx: Ctx, wf: str, now: int, live: dict, state: dict) -> tuple[dict, dict]:
    """Desired default caller_id distribution and the evidence it came from."""
    fac = factors(ctx, wf, now)
    flagged = state.get("flagged", {})
    opts = ctx.meta[wf].options["caller_id"]
    w = {}
    for d in opts:
        f = max(fac[d]["f"], 0.05)
        w[d] = f ** C.DID_GAMMA
        if d in flagged:
            w[d] *= 0.05
    t = normalise(w)
    prob = state.get("probation", {})
    floor = {d: (0.02 if d in flagged else C.DID_FLOOR) for d in opts}
    ceil = {d: C.DID_PROBATION_CAP for d in prob if d not in flagged}
    for _ in range(4):  # enforce floors, renormalise the remainder
        low = {d for d in opts if t.get(d, 0) < floor[d]}
        if not low:
            break
        rest = 1.0 - sum(floor[d] for d in low)
        tot = sum(t.get(d, 0) for d in opts if d not in low)
        t = {d: (floor[d] if d in low else t.get(d, 0) * rest / max(tot, 1e-9)) for d in opts}
    # unmeasured DIDs are explored, but at a capped share (their health is unknown and may be bad)
    unmeasured = [d for d in opts if fac[d]["n"] < C.DID_MEASURED_N]
    concentrated = max(live.values(), default=0) >= 0.8
    cap = C.DID_EXPLORE_CAP_CONCENTRATED if concentrated else C.DID_EXPLORE_CAP
    if unmeasured and len(unmeasured) < len(opts):
        for _ in range(3):
            over = {d: t[d] - cap for d in unmeasured if t.get(d, 0) > cap}
            if not over:
                break
            measured = [d for d in opts if d not in unmeasured]
            excess = sum(over.values())
            tot = sum(t.get(d, 0) for d in measured) or 1.0
            for d in over:
                t[d] = cap
            for d in measured:
                t[d] = t.get(d, 0) + excess * t.get(d, 0) / tot
    for d, c in ceil.items():                 # probation cap: excess goes to the others, proportionally
        if t.get(d, 0) > c:
            excess, others = t[d] - c, [o for o in opts if o != d and o not in ceil]
            tot = sum(t.get(o, 0) for o in others) or 1.0
            t[d] = c
            for o in others:
                t[o] = t.get(o, 0) + excess * t.get(o, 0) / tot
    t = {d: round(v, 4) for d, v in t.items() if v > 0}
    return t, {"factors": fac, "flagged": flagged, "probation": prob, "unmeasured": unmeasured}


def step_toward(live: dict, tgt: dict, max_tv: float, damping: float) -> dict:
    """Move `damping` of the way to the target, but never more than max_tv in total variation."""
    ks = set(live) | set(tgt)
    new = {k: live.get(k, 0.0) + damping * (tgt.get(k, 0.0) - live.get(k, 0.0)) for k in ks}
    tvd = 0.5 * sum(abs(new[k] - live.get(k, 0.0)) for k in ks)
    if tvd > max_tv > 0:
        a = max_tv / tvd
        new = {k: live.get(k, 0.0) + a * (new[k] - live.get(k, 0.0)) for k in ks}
    new = {k: round(v, 4) for k, v in new.items() if v > 1e-4}
    return normalise(new)
