"""Deterministic statistics. No LLM and no randomness anywhere in winner selection (constraint 4)."""
from __future__ import annotations

from math import erf, sqrt

import numpy as np


def norm_sf(z: float) -> float:
    return 0.5 * (1.0 - erf(z / sqrt(2.0)))


def two_prop_z(x1: int, n1: int, x0: int, n0: int) -> float:
    if n1 <= 0 or n0 <= 0:
        return 0.0
    p = (x1 + x0) / (n1 + n0)
    se = sqrt(max(p * (1 - p), 1e-12) * (1 / n1 + 1 / n0))
    return (x1 / n1 - x0 / n0) / se


def prop_z(x: int, n: int, p0: float) -> float:
    if n <= 0:
        return 0.0
    se = sqrt(max(p0 * (1 - p0), 1e-9) / n)
    return (x / n - p0) / se


def mh_lift(strata: list[tuple[int, int, int, int]]) -> dict:
    """Stratified (Mantel-Haenszel style) comparison of arm vs control.

    strata: [(n_arm, y_arm, n_ctrl, y_ctrl), ...] one per (config version, cohort) stratum, inside which the
    allocation is constant and independent of the lead, so arm vs control is unbiased. Pooling WITHOUT the strata
    would confound arm with cohort (e.g. V3 sent mostly to seniors) - Simpson's paradox.
    Returns relative lift = (p_arm - p_ctrl)/p_ctrl and its z (on the difference scale).
    """
    sw = sd = var = pc_w = po_w = 0.0
    n_o = n_c = 0
    for no, yo, nc, yc in strata:
        if no <= 0 or nc <= 0:
            continue
        po, pc = yo / no, yc / nc
        w = no * nc / (no + nc)
        sw += w
        sd += w * (po - pc)
        pc_w += w * pc
        po_w += w * po
        var += w * w * (max(po * (1 - po), 1e-4) / no + max(pc * (1 - pc), 1e-4) / nc)
        n_o += no
        n_c += nc
    if sw <= 0:
        return {"n_arm": 0, "n_ctrl": 0, "lift": 0.0, "se_lift": 9.0, "z": 0.0, "diff": 0.0, "p_arm": 0.0,
                "p_ctrl": 0.0}
    diff = sd / sw
    se = sqrt(var) / sw
    pc = max(pc_w / sw, 1e-6)
    return {"n_arm": n_o, "n_ctrl": n_c, "diff": diff, "p_arm": po_w / sw, "p_ctrl": pc, "lift": diff / pc,
            "se_lift": se / pc, "z": diff / max(se, 1e-9)}


def binom_sf(k: int, n: int, p: float) -> float:
    """Exact P(X >= k) for X ~ Binomial(n, p). The normal approximation badly understates tails when n*p is ~1-3
    (our hallucination-flag rates), which is exactly where false incidents come from."""
    from math import lgamma, log, exp
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    p = min(max(p, 1e-9), 1 - 1e-9)
    lp, lq = log(p), log(1 - p)
    tot = 0.0
    for i in range(k, n + 1):
        t = lgamma(n + 1) - lgamma(i + 1) - lgamma(n - i + 1) + i * lp + (n - i) * lq
        tot += exp(t)
        if t < -40 and i > n * p:
            break
    return min(tot, 1.0)


def tv(a: dict, b: dict) -> float:
    ks = set(a) | set(b)
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in ks)


def normalise(d: dict) -> dict:
    s = float(sum(d.values()))
    return {k: v / s for k, v in d.items() if v > 0} if s > 0 else d
