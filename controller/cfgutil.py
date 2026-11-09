"""Config helpers mirroring the contract in docs/SIMULATOR.md section 3-4 (first-match cohorts, per-cohort
allocation fallback to default, total-variation shift, changed dimensions)."""
from __future__ import annotations

import copy

AGENT = ["welcome_message", "voice", "caller_id"]
DIM_ORDER = ["cohorts", "welcome_message", "voice", "caller_id", "prompt_version", "schedule"]
TOL = 1e-3
CUSTOMER_FACING = {"cohorts", "welcome_message", "voice", "prompt_version"}
OPERATIONAL = {"schedule", "caller_id"}


def clone(cfg: dict) -> dict:
    return copy.deepcopy(cfg)


def dist(cfg: dict, cohort: str, dim: str) -> dict:
    return (cfg["allocations"].get(cohort) or {}).get(dim) or cfg["allocations"]["default"][dim]


def _tv(a: dict, b: dict) -> float:
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b))


def _sched_eq(a: dict, b: dict) -> bool:
    if set(a.get("hour_weights", {})) != set(b.get("hour_weights", {})):
        return False
    for k, t in a.get("hour_weights", {}).items():
        u = b["hour_weights"][k]
        if set(t) != set(u) or any(abs(t[h] - u[h]) > 5e-3 for h in t):
            return False
    return a.get("lead_preferred_hour", {}) == b.get("lead_preferred_hour", {})


def changed_dims(old: dict, new: dict) -> list[str]:
    ch = []
    if old["cohorts"] != new["cohorts"]:
        ch.append("cohorts")
    keys = set(old["allocations"]) | set(new["allocations"])
    for dim in AGENT:
        if any(_tv(dist(old, k, dim), dist(new, k, dim)) > TOL for k in keys):
            ch.append(dim)
    if old["prompt_version"] != new["prompt_version"]:
        ch.append("prompt_version")
    if not _sched_eq(old["schedule"], new["schedule"]):
        ch.append("schedule")
    return [d for d in DIM_ORDER if d in ch]


def max_shift(old: dict, new: dict) -> float:
    keys = {"default"} | {c["id"] for c in old["cohorts"]} | {c["id"] for c in new["cohorts"]}
    return max((_tv(dist(old, k, d), dist(new, k, d)) for k in keys for d in AGENT), default=0.0)


def cell_treatment(cfg: dict, cohort_masks: list, cell: int, hours: list[int]):
    """(cohort id, welcome, voice, caller, hour table) a lead with this feature cell receives under cfg."""
    cid = "default"
    for c, m in zip(cfg["cohorts"], cohort_masks):
        if m[cell]:
            cid = c["id"]
            break
    hw = cfg["schedule"].get("hour_weights", {})
    tab = hw.get(cid) if cid in hw else hw.get("default")
    return (cid, tuple(sorted(dist(cfg, cid, "welcome_message").items())),
            tuple(sorted(dist(cfg, cid, "voice").items())), tuple(sorted(dist(cfg, cid, "caller_id").items())),
            tuple(sorted((tab or {}).items())))
