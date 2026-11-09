"""Call-time optimisation (C4): which cohort gets dialled in which hour.

Business owns cadence/window/capacity. We only set `schedule.hour_weights` per cohort: the dialler's capacity per
hour is fixed, and the weights decide WHO fills it. Per-person preferred hours (`lead_preferred_hour`) were cut:
people make <= 8 attempts in total, so a per-person pickup profile is mostly noise (see SUBMISSION §2).
"""
from __future__ import annotations

import numpy as np

from . import analytics as A
from . import config as C
from .context import Ctx
from .feat import FEATURES, Slice

CAND_FEATURES = ["age_band", "city_tier", "lang"]
MAX_PRIOR_DIALS = 30.0


def _hours(ctx: Ctx, wf: str) -> list[int]:
    lo, hi = ctx.meta[wf].window
    return list(range(lo, hi))


def _window(ctx: Ctx, wf: str, now: int):
    now_h = now // 60
    return max(now_h - C.PROFILE_DAYS * 24, -10**9), now_h


def build_partition(ctx: Ctx, wf: str, now: int) -> dict:
    """Which features split callers into groups with *significantly different hour-of-day pickup shapes*.
    Agglomerative merge of feature values whose shapes are statistically indistinguishable, so we only create a
    cohort split when the data supports it (avoids fragmenting traffic)."""
    hours = _hours(ctx, wf)
    H = len(hours)
    h0, h1 = _window(ctx, wf, now)
    D, Cn = A.pick_matrix(ctx, wf, h0, h1, hours)
    out = {"features": {}, "hetero": {}}
    if Cn.sum() < 500:
        return out
    thr = H + 3.0 * (2 * H) ** 0.5
    tot_p = Cn.sum() / max(D.sum(), 1)
    values = {"age_band": FEATURES["age_band"], "city_tier": FEATURES["city_tier"],
              "lang": [l for l in FEATURES["lang"] if l in ctx.meta[wf].languages]}
    for f in CAND_FEATURES:
        vals = values[f]
        prof = {}
        for v in vals:
            m = A.pcell_mask(Slice.make({f: [v]}))
            dv, cv = D[:, m].sum(1), Cn[:, m].sum(1)
            if cv.sum() < 50:
                continue
            prof[v] = (dv, cv)
        groups = [[v] for v in prof]
        if len(groups) < 2:
            continue

        def shape(g):
            dv = sum(prof[v][0] for v in g)
            cv = sum(prof[v][1] for v in g)
            r = (cv / np.maximum(dv, 1)) / max(cv.sum() / max(dv.sum(), 1), 1e-9)
            return np.log(np.maximum(r, 1e-3)), cv

        def dist(a, b):
            la, ca = shape(a)
            lb, cb = shape(b)
            return float((((la - lb) ** 2) / (1.0 / np.maximum(ca, 1) + 1.0 / np.maximum(cb, 1))).sum())

        while len(groups) > 1:
            best = min(((dist(groups[i], groups[j]), i, j) for i in range(len(groups))
                        for j in range(i + 1, len(groups))), key=lambda t: t[0])
            if best[0] >= thr:
                break
            _, i, j = best
            groups[i] = groups[i] + groups[j]
            del groups[j]
        if len(groups) > 1:
            out["features"][f] = [sorted(g, key=FEATURES[f].index) for g in groups]
            out["hetero"][f] = round(max(dist(a, b) for a in groups for b in groups if a is not b), 1)
    return out


def hour_table(ctx: Ctx, wf: str, now: int, mask: np.ndarray) -> dict:
    """Hour weights for a population given as a mask over pcells (lang|tier|age codes)."""
    hours = _hours(ctx, wf)
    h0, h1 = _window(ctx, wf, now)
    D, Cn = A.pick_matrix(ctx, wf, h0, h1, hours)
    allD, allC = D.sum(1), Cn.sum(1)
    if allC.sum() < 200:
        return {str(h): 1.0 for h in hours}
    pbar_all = allC.sum() / allD.sum()
    Rg = (allC / np.maximum(allD, 1)) / pbar_all
    m = mask
    Dh, Ch = D[:, m].sum(1), Cn[:, m].sum(1)
    pbar = max(Ch.sum() / max(Dh.sum(), 1), 1e-4) if Dh.sum() > 0 else pbar_all
    phat = (Ch + MAX_PRIOR_DIALS * pbar * Rg) / (Dh + MAX_PRIOR_DIALS)
    w = (phat / pbar) ** C.HOUR_GAMMA
    w = np.maximum(w, C.HOUR_FLOOR * w.max())
    w = w / w.mean()
    return {str(h): round(float(x), 3) for h, x in zip(hours, w)}


def table_distance(a: dict, b: dict) -> float:
    ks = sorted(set(a) | set(b), key=int)
    x = np.array([a.get(k, 0.0) for k in ks], dtype=float)
    y = np.array([b.get(k, 0.0) for k in ks], dtype=float)
    if x.sum() == 0 or y.sum() == 0:
        return 1.0
    return float(0.5 * np.abs(x / x.sum() - y / y.sum()).sum())


# ---------------------------------------------------------------- historical replay model (dry runs)
def build_model(ctx: Ctx, wf: str) -> dict | None:
    """Static model from the pre-D0 history (flat hour weights there, so dial mix == lead pool mix)."""
    hours = _hours(ctx, wf)
    D, Cn = A.pick_matrix(ctx, wf, -10**9, 0, hours)
    if D.sum() < 500:
        return None
    pool = D.sum(0) / D.sum()
    p = (Cn + 20 * (Cn.sum() / D.sum())) / (D + 20)
    return {"hours": hours, "pool": pool, "p": p, "base": float(Cn.sum() / D.sum())}


def _weights_for(cfg: dict, hours: list[int]):
    """Return fn(pcell_mask_list...) -> weight matrix (H x 512) under cfg's schedule/cohorts."""
    hw = cfg.get("schedule", {}).get("hour_weights", {})
    W = np.ones((len(hours), 512))
    default_t = hw.get("default")
    if default_t:
        W[:] = np.array([default_t.get(str(h), 0.0) for h in hours])[:, None]
    assigned = np.zeros(512, dtype=bool)
    for c in cfg.get("cohorts", []):
        m = A.pcell_mask(Slice.make(c["where"])) & ~assigned
        assigned |= m
        t = hw.get(c["id"])
        if t:
            W[:, m] = np.array([t.get(str(h), 0.0) for h in hours])[:, None]
        elif default_t is None:
            W[:, m] = 1.0
    return W


def replay_pickup(model: dict, cfg: dict) -> float:
    hours, pool, p = model["hours"], model["pool"], model["p"]
    W = _weights_for(cfg, hours) * pool[None, :]
    den = W.sum(1)
    ok = den > 0
    if not ok.any():
        return model["base"]
    return float(((W * p).sum(1)[ok] / den[ok]).mean())
