"""Turns decision state into one desired config per workflow (C2: the cohort object is shared).

The controller is a reconciler: decision modules (caller-ID optimiser, schedule optimiser, experiment engine,
incident handler) only update *targets* in `wf_state`; `compose()` is the single place that renders targets into a
config, and `governance` is the single place that routes the diff. Targets are committed with hysteresis, so noise
alone never produces a diff (and therefore never produces a proposal).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from . import analytics as A
from . import cfgutil as U
from . import config as C
from . import experiments as X
from . import schedule as S
from .context import Ctx
from .feat import FEATURES, Slice


@dataclass
class Block:
    id: str
    where: dict
    default: bool
    share: float
    slice_ids: list
    groups: dict = field(default_factory=dict)


def feature_values(ctx: Ctx, wf: str, f: str) -> list[str]:
    return [v for v in FEATURES[f] if f != "lang" or v in ctx.meta[wf].languages]


def cell_shares(ctx: Ctx, wf: str) -> np.ndarray:
    out = np.zeros(512)
    for cell, d in ctx.store.q("SELECT cell,sum(dials) FROM exp_stats WHERE wf=? GROUP BY cell", (wf,)):
        out[cell] = d
    return out / max(out.sum(), 1)


def build_blocks(ctx: Ctx, wf: str, state: dict, now: int) -> list[Block]:
    sp = state.get("sched", {}).get("partition", {}).get("features", {})
    per_feature = {}
    importance = {}
    for f in FEATURES:
        vals = feature_values(ctx, wf, f)
        groups: dict[tuple, list] = {}
        for v in vals:
            sg = next((i for i, g in enumerate(sp.get(f, [])) if v in g), -1)
            groups.setdefault((sg, X.decision_vector(state, f, v)), []).append(v)
        gl = [g for g in groups.values()]
        per_feature[f] = gl
        dec = [k[1] for k in groups]
        imp = 0.0
        for vec in dec:
            for _, _, st in vec:
                imp = max(imp, {"winner": 100, "candidate": 50, "eliminated": 20}.get(st, 0))
        importance[f] = imp + (state.get("sched", {}).get("partition", {}).get("hetero", {}).get(f, 0) / 1000.0)
    involved = [f for f in FEATURES if len(per_feature[f]) > 1]
    while involved and int(np.prod([len(per_feature[f]) for f in involved])) > C.MAX_COHORTS:
        involved.remove(min(involved, key=lambda f: importance[f]))
    shares = cell_shares(ctx, wf)
    blocks: list[Block] = []
    for combo in itertools.product(*[per_feature[f] for f in involved]) if involved else [()]:
        groups = dict(zip(involved, combo))
        sl = Slice.make({f: g for f, g in groups.items()})
        share = float(shares[sl.mask()].sum()) if groups else 1.0
        sids = ["ALL"] + [Slice.make({f: [per_feature[f][0][0]]}).id if f not in groups else
                          Slice.make({f: [groups[f][0]]}).id for f in FEATURES]
        blocks.append(Block(id=sl.id, where=sl.as_where(), default=False, share=share, slice_ids=sids,
                            groups=groups))
    if not blocks:
        blocks = [Block("default", {}, True, 1.0, ["ALL"])]
    d = max(blocks, key=lambda b: b.share)
    d.default = True
    return blocks


def _carry(live: dict, cohort_id: str, dim: str) -> dict:
    return dict(U.dist(live, cohort_id, dim))


def compose(ctx: Ctx, wf: str, live: dict, state: dict, now: int, *, did_target: dict | None = None) -> tuple:
    """Return (desired_cfg, blocks). Pure function of (live config, targets in state, aggregate tables)."""
    meta = ctx.meta[wf]
    locks = {k for k, t in state.get("locks", {}).items() if t > now}
    if "cohorts" in locks:
        d = U.clone(live)
        if did_target and "caller_id" not in locks:
            d["allocations"]["default"]["caller_id"] = did_target
        return d, []
    blocks = build_blocks(ctx, wf, state, now)
    non_default = [b for b in blocks if not b.default]
    desired = {"cohorts": [{"id": b.id, "where": b.where} for b in non_default],
               "allocations": {"default": {}}, "prompt_version": live["prompt_version"],
               "schedule": {"hour_weights": {}, "lead_preferred_hour": dict(live["schedule"].get(
                   "lead_preferred_hour", {}))}}
    default_b = next(b for b in blocks if b.default)
    live_ids = {c["id"] for c in live["cohorts"]}

    def lookup_live(b: Block, dim: str) -> dict:
        return _carry(live, b.id if b.id in live_ids else "default", dim)

    exp_on = X.enabled(ctx, wf, state) and "exp" in state
    for b in blocks:
        alloc = desired["allocations"].setdefault("default" if b.default else b.id, {})
        for dim in X.DIMS:
            if exp_on and dim not in locks and dim in state["exp"]:
                st = X.option_states(state, dim, b.slice_ids)
                alloc[dim] = X.alloc_weights(st, state["exp"][dim]["control"])
            else:
                alloc[dim] = lookup_live(b, dim)
    # caller id: one global distribution (cohort allocations never override it)
    desired["allocations"]["default"]["caller_id"] = (
        dict(live["allocations"]["default"]["caller_id"]) if "caller_id" in locks or not did_target else did_target)
    # drop per-cohort dims identical to default (keeps the config small and diffs honest)
    for k in list(desired["allocations"]):
        if k == "default":
            continue
        for dim in X.DIMS:
            if desired["allocations"][k].get(dim) == desired["allocations"]["default"].get(dim):
                desired["allocations"][k].pop(dim, None)
        if not desired["allocations"][k]:
            del desired["allocations"][k]
    # schedule
    sc = state.get("sched", {}).get("committed")
    if sc and "schedule" not in locks:
        for b in blocks:
            t = sc["tables"].get(b.id if not b.default else "default")
            if t:
                desired["schedule"]["hour_weights"]["default" if b.default else b.id] = t
    elif "schedule" in locks or not sc:
        hw = live["schedule"].get("hour_weights", {})
        ids = {b.id for b in non_default}
        desired["schedule"]["hour_weights"] = {k: v for k, v in hw.items() if k == "default" or k in ids}
    return desired, blocks


def refresh_schedule(ctx: Ctx, wf: str, state: dict, now: int, blocks: list[Block], *, force=False) -> dict | None:
    """Recompute hour tables for the current block structure; commit only if materially different."""
    sched = state.setdefault("sched", {})
    part = S.build_partition(ctx, wf, now)
    sched["partition"] = part
    sched["partition_at"] = now
    new_blocks = build_blocks(ctx, wf, state, now)
    non_default = [b for b in new_blocks if not b.default]
    masks = [A.pcell_mask(Slice.make(b.where)) for b in non_default]
    valid = A.pcell_mask(Slice(()))
    default_mask = valid.copy()
    for m in masks:
        default_mask &= ~m
    tables = {"default": S.hour_table(ctx, wf, now, default_mask)}
    for b, m in zip(non_default, masks):
        tables[b.id] = S.hour_table(ctx, wf, now, m)
    committed = sched.get("committed")
    if committed and not force:
        same_blocks = set(committed["tables"]) == set(tables)
        moved = max((S.table_distance(committed["tables"][k], tables[k]) for k in tables if k in committed["tables"]),
                    default=1.0)
        if same_blocks and moved < C.SCHED_MIN_CHANGE_L1:
            return None
    sched["committed"] = {"tables": tables, "at": now, "features": sorted(part.get("features", {}))}
    return sched["committed"]
