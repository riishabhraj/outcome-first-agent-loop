"""Read-side queries over the aggregate tables. Everything here is a pure function of the store."""
from __future__ import annotations

import numpy as np

from . import feat as F
from .context import Ctx
from .feat import Slice
from .stats import mh_lift


def exp_rows(ctx: Ctx, wf: str, min_slot: int = -10**9, min_version: int = 0, max_slot: int = 10**9) -> np.ndarray:
    rows = ctx.store.q("SELECT slot,version,cohort,cell,w,v,dials,conn,eng FROM exp_stats "
                       "WHERE wf=? AND slot>=? AND slot<=? AND version>=?", (wf, min_slot, max_slot, min_version))
    return np.array(rows, dtype=np.int64).reshape(-1, 9)


COL = {"slot": 0, "version": 1, "cohort": 2, "cell": 3, "w": 4, "v": 5, "dials": 6, "conn": 7, "eng": 8}


def arm_strata(rows: np.ndarray, dim: str, sl: Slice, arm: int, ctrl: int, metric: str = "eng",
               slot_from: int | None = None, version_from: int | None = None, slot_to: int | None = None) -> list:
    """Strata [(n_arm, y_arm, n_ctrl, y_ctrl)] for one (slice, arm vs control) comparison.
    metric 'eng' -> engagement (denominator = connected calls); 'conn' -> pickup (denominator = dials)."""
    if len(rows) == 0:
        return []
    m = sl.mask()[rows[:, COL["cell"]]]
    if slot_from is not None:
        m &= rows[:, COL["slot"]] >= slot_from
    if slot_to is not None:
        m &= rows[:, COL["slot"]] <= slot_to
    if version_from is not None:
        m &= rows[:, COL["version"]] >= version_from
    r = rows[m]
    if len(r) == 0:
        return []
    opt = r[:, COL["w"] if dim == "welcome_message" else COL["v"]]
    den = r[:, COL["conn"]] if metric == "eng" else r[:, COL["dials"]]
    num = r[:, COL["eng"]] if metric == "eng" else r[:, COL["conn"]]
    strat = r[:, COL["version"]] * 100000 + r[:, COL["cohort"]]
    out = []
    for s in np.unique(strat):
        ms = strat == s
        a, c = ms & (opt == arm), ms & (opt == ctrl)
        na, nc = int(den[a].sum()), int(den[c].sum())
        if na > 0 and nc > 0:
            out.append((na, int(num[a].sum()), nc, int(num[c].sum())))
    return out


def arm_lift(rows, dim, sl, arm, ctrl, metric="eng", slot_from=None, version_from=None, slot_to=None) -> dict:
    return mh_lift(arm_strata(rows, dim, sl, arm, ctrl, metric, slot_from, version_from, slot_to))


def kpi_window(ctx: Ctx, wf: str, minute_from: int, minute_to: int) -> dict:
    """Workflow-level pickup/engagement over [from, to) using the 15-minute did_stats table."""
    r = ctx.store.q("SELECT sum(dials),sum(conn),sum(eng) FROM did_stats WHERE wf=? AND tidx>=? AND tidx<?",
                    (wf, minute_from // 15, minute_to // 15))[0]
    d, c, e = (x or 0 for x in r)
    return {"dials": d, "conn": c, "eng": e, "pickup": c / d if d else None, "engagement": e / c if c else None}


def did_window(ctx: Ctx, wf: str, minute_from: int, minute_to: int) -> dict:
    out = {}
    for caller, d, c, e in ctx.store.q(
            "SELECT caller,sum(dials),sum(conn),sum(eng) FROM did_stats WHERE wf=? AND tidx>=? AND tidx<? "
            "GROUP BY caller", (wf, minute_from // 15, minute_to // 15)):
        out[int(caller)] = {"dials": int(d), "conn": int(c), "eng": int(e)}
    return out


def pick_matrix(ctx: Ctx, wf: str, hour_from: int, hour_to: int, hours: list[int]):
    """dials[h_index, pcell], conn[...] aggregated by hour-of-day over absolute hours [hour_from, hour_to)."""
    rows = ctx.store.q("SELECT hidx,pcell,dials,conn FROM pick_stats WHERE wf=? AND hidx>=? AND hidx<?",
                       (wf, hour_from, hour_to))
    hmap = {h: i for i, h in enumerate(hours)}
    D = np.zeros((len(hours), 512))
    C = np.zeros((len(hours), 512))
    for hidx, pc, d, c in rows:
        i = hmap.get(hidx % 24)
        if i is not None:
            D[i, pc] += d
            C[i, pc] += c
    return D, C


def pcell_mask(sl: Slice) -> np.ndarray:
    """Mask over pcell codes (lang|tier<<3|age<<5) for a slice; value_band is not part of the pickup model."""
    codes = np.arange(512)
    dec = {"lang": codes & 7, "city_tier": (codes >> 3) & 3, "age_band": (codes >> 5) & 3}
    m = (dec["lang"] < 7) & (dec["city_tier"] < 3)
    for f, vs in sl.where:
        if f in dec:
            m &= np.isin(dec[f], [F._IDX[f][v] for v in vs])
    return m


def qa_window(ctx: Ctx, wf: str, minute_from: int, minute_to: int, prompt: int | None = None) -> dict:
    sql = "SELECT sum(dials),sum(conn),sum(flags),sum(eng),sum(adh_milli) FROM qa_stats WHERE wf=? AND tidx>=? AND tidx<?"
    args = [wf, minute_from // 15, minute_to // 15]
    if prompt is not None:
        sql += " AND prompt=?"
        args.append(prompt)
    d, c, f, e, a = (x or 0 for x in ctx.store.q(sql, tuple(args))[0])
    return {"dials": d, "conn": c, "flags": f, "eng": e, "flag_rate": f / c if c else None,
            "engagement": e / c if c else None, "adherence": a / 1000 / c if c else None}


def qa_by_prompt(ctx: Ctx, wf: str, minute_from: int, minute_to: int) -> dict:
    out = {}
    for p, d, c, f, e in ctx.store.q(
            "SELECT prompt,sum(dials),sum(conn),sum(flags),sum(eng) FROM qa_stats WHERE wf=? AND tidx>=? AND tidx<? "
            "GROUP BY prompt", (wf, minute_from // 15, minute_to // 15)):
        out[int(p)] = {"dials": d, "conn": c, "flags": f, "eng": e}
    return out


def active_leads_mask(ctx: Ctx, wf: str, today: int) -> np.ndarray:
    """Feature codes of leads still being worked: inside their cadence window and not concluded."""
    la = ctx.leads[wf]
    n = la.n
    day = la.day[:n].astype(np.int64)
    win = len(ctx.meta[wf].cadence)
    act = (day >= today - win + 1) & (day <= today) & (la.concl[:n] == 0)
    return la.feat[:n][act]
