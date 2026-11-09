"""Hallucination / prompt-regression incident response (C8).

detect (population threshold on QA flags) -> pick fix -> validate (credits) -> propose with evidence -> apply ->
verify. The LLM is only ever asked for *candidates*, and only when the cheapest safe fix (return to the last prompt
we have measured as good) is unavailable or fails validation: "the best compute is no compute". The decision between
candidates is made by validation pass-rate and our own measured history, never by the LLM's words.
"""
from __future__ import annotations

import json
from math import sqrt

from . import analytics as A
from . import cfgutil as U
from . import config as C
from .context import Ctx
from .simclient import SimError
from .stats import binom_sf, prop_z, two_prop_z

KNOWN_GOOD_MAX_FLAG = 0.025


def prompt_index(ctx: Ctx, wf: str, p: str) -> int:
    return ctx.meta[wf].opt_idx["prompt_version"][p]


def prompt_name(ctx: Ctx, wf: str, i: int) -> str:
    return ctx.meta[wf].options["prompt_version"][i]


def measured_prompts(ctx: Ctx, wf: str, now: int) -> dict:
    """Per prompt: connected calls, flag rate, engagement, last seen minute - from our own aggregates."""
    out = {}
    for p, conn, flags, eng, last in ctx.store.q(
            "SELECT prompt,sum(conn),sum(flags),sum(eng),max(tidx) FROM qa_stats WHERE wf=? GROUP BY prompt", (wf,)):
        if conn:
            out[prompt_name(ctx, wf, p)] = {"conn": conn, "flag_rate": flags / conn, "engagement": eng / conn,
                                            "last_minute": last * 15}
    return out


def baseline_flag_rate(ctx: Ctx, wf: str, now: int, exclude: str | None = None) -> float:
    m = measured_prompts(ctx, wf, now)
    good = [v["flag_rate"] for p, v in m.items() if p != exclude and v["conn"] >= 300 and v["flag_rate"] <= KNOWN_GOOD_MAX_FLAG]
    return max(min(good) if good else 0.015, 0.008)


def detect(ctx: Ctx, wf: str, now: int, state: dict, live_prompt: str) -> dict | None:
    """Population test on QA flags under the live prompt: exact binomial tail vs the known-good baseline rate, on a 2h
    and a 6h window (6h has the power to see mild regressions), two consecutive ticks. Returns evidence or None."""
    inc = state.setdefault("halluc", {"consec": 0})
    pi = prompt_index(ctx, wf, live_prompt)
    meas = measured_prompts(ctx, wf, now)
    exclude = live_prompt if live_prompt in meas and meas[live_prompt]["flag_rate"] > KNOWN_GOOD_MAX_FLAG else None
    p0 = baseline_flag_rate(ctx, wf, now, exclude=exclude)
    best = None
    for h in (C.HALLUC_WINDOW_TICKS * 15, 360):
        since = max(state.get("prompt_since", -10**9), now - h)
        w = A.qa_window(ctx, wf, since, now, prompt=pi)
        if w["conn"] < C.HALLUC_MIN_CONN:
            continue
        ph = w["flags"] / w["conn"]
        tail = binom_sf(w["flags"], w["conn"], p0)
        if tail <= C.HALLUC_P and ph >= C.HALLUC_MIN_RATIO * p0 and ph - p0 >= C.HALLUC_MIN_ABS:
            if best is None or tail < best["tail"]:
                best = {"prompt": live_prompt, "flag_rate": ph, "baseline": p0, "n_conn": w["conn"], "flags": w["flags"],
                        "window_minutes": now - since, "tail": tail, "z": prop_z(w["flags"], w["conn"], p0)}
    inc["consec"] = inc["consec"] + 1 if best else 0
    inc["last"] = {"minute": now, "baseline": round(p0, 4), "hit": bool(best)}
    if inc["consec"] >= C.HALLUC_CONSEC and best:
        best["false_positive_bound"] = (f"exact binomial tail {best['tail']:.1e} <= {C.HALLUC_P:.0e} on two "
                                        "consecutive checks (windows overlap, so treat as ~1e-5 per day-window)")
        return best
    return None


def detect_engagement_regression(ctx: Ctx, wf: str, now: int, state: dict, live_prompt: str, since: int) -> dict | None:
    """After a prompt change: engagement under the new prompt vs the 3 days before it (population, not anecdote)."""
    if since is None or now - since < 2 * 60:
        return None
    post = A.kpi_window(ctx, wf, since, now)
    pre = A.kpi_window(ctx, wf, since - 3 * 1440, since)
    if post["conn"] < 150 or pre["conn"] < 800:
        return None
    z = two_prop_z(post["eng"], post["conn"], pre["eng"], pre["conn"])
    rel = post["engagement"] / pre["engagement"] - 1
    if z <= -3.0 and rel <= -0.06:
        return {"prompt": live_prompt, "engagement_before": pre["engagement"], "engagement_after": post["engagement"],
                "relative": rel, "z": z, "n_post": post["conn"], "n_pre": pre["conn"]}
    return None


# ------------------------------------------------------------------ response
def credits_left(ctx: Ctx) -> float:
    return float(ctx.sim.get("/sim/state")["credits_remaining"])


def run_validation(ctx: Ctx, wf: str, prompt: str, inc: dict, n: int = C.VALIDATION_N) -> dict | None:
    cost = -(-n // 100)
    if inc["credits_spent"] + cost > C.INCIDENT_CREDIT_CAP or credits_left(ctx) < cost:
        return None
    r = ctx.sim.post("/validation/runs", {"workflow_id": wf, "prompt_version": prompt, "n": n})
    inc["credits_spent"] += cost
    inc["validations"].append({"prompt": prompt, "run": r["validation_run_id"], "pass_rate": r["pass_rate"], "n": n})
    ctx.store.audit("validation_run", wf, f"{prompt}: pass_rate {r['pass_rate']} on n={n} ({r['validation_run_id']})",
                    {"credits": cost, "incident": inc["id"]})
    return r


def ask_llm(ctx: Ctx, wf: str, inc: dict, problem: str) -> list[dict]:
    if inc["credits_spent"] + 3 > C.INCIDENT_CREDIT_CAP or credits_left(ctx) < 3:
        return []
    r = ctx.sim.post("/llm/suggest-fixes", {"workflow_id": wf, "problem": problem})
    inc["credits_spent"] += 3
    inc["llm"] = r["suggestions"]
    ctx.store.audit("llm_suggest", wf, "LLM suggestions (candidates only, not a decision): " +
                    ", ".join(f"{s['prompt_version']}" for s in r["suggestions"]),
                    {"suggestions": r["suggestions"], "incident": inc["id"], "credits": 3})
    return r["suggestions"]


def choose_fix(ctx: Ctx, wf: str, now: int, inc: dict, bad: str) -> str | None:
    """Pick the prompt to propose. Order: last measured-good prompt (cheapest, engagement known) -> LLM candidates."""
    thr = ctx.meta[wf].governance["min_validation_pass_rate"]
    meas = measured_prompts(ctx, wf, now)
    tried = {v["prompt"] for v in inc["validations"]}
    known = sorted([p for p, v in meas.items() if p != bad and v["conn"] >= 300 and v["flag_rate"] <= KNOWN_GOOD_MAX_FLAG
                    and p in ctx.meta[wf].options["prompt_version"]], key=lambda p: -meas[p]["last_minute"])
    for p in known:
        if p in tried:
            continue
        r = run_validation(ctx, wf, p, inc)
        if r and r["pass_rate"] >= thr:
            return p
    if not inc.get("llm"):
        sug = ask_llm(ctx, wf, inc, f"hallucination rate on {bad} is {inc['evidence'].get('flag_rate', 0):.3f}, "
                                   f"baseline {inc['evidence'].get('baseline', 0):.3f}; suggest safer prompt versions")
    else:
        sug = inc["llm"]
    best = None
    for s in sug:
        p = s["prompt_version"]
        if p == bad or p in tried or p not in ctx.meta[wf].options["prompt_version"]:
            continue
        r = run_validation(ctx, wf, p, inc)
        if r and r["pass_rate"] >= thr and (best is None or r["pass_rate"] > best[1]):
            best = (p, r["pass_rate"])
    return best[0] if best else None


def open_incident(ctx: Ctx, wf: str, now: int, kind: str, bad: str, evidence: dict) -> dict:
    iid = f"inc-{wf}-{now}"
    inc = {"id": iid, "kind": kind, "state": "detected", "bad": bad, "evidence": evidence, "validations": [],
           "credits_spent": 0, "opened": now, "history": [], "attempts": 0}
    ctx.store.db.execute("INSERT OR IGNORE INTO incidents VALUES(?,?,?,?,?,?,?)",
                         (iid, wf, kind, "detected", now, None, json.dumps(inc, default=str)))
    ctx.store.audit("incident_opened", wf, f"{kind}: prompt {bad} - {_fmt(evidence)}", {"incident": iid, **evidence},)
    ctx.store.alert(wf, "page", f"incident_{kind}", f"{wf}: {kind} on prompt {bad}; fix in progress ({iid})")
    return inc


def save(ctx: Ctx, inc: dict, wf: str):
    ctx.store.db.execute("UPDATE incidents SET state=?, closed_minute=?, body=? WHERE incident_id=?",
                         (inc["state"], inc.get("closed"), json.dumps(inc, default=str), inc["id"]))


def load_open(ctx: Ctx, wf: str) -> dict | None:
    r = ctx.store.q("SELECT body FROM incidents WHERE wf=? AND state NOT IN ('resolved','failed') "
                    "ORDER BY opened_minute DESC LIMIT 1", (wf,))
    return json.loads(r[0][0]) if r else None


def _fmt(e: dict) -> str:
    return ", ".join(f"{k}={round(v, 4) if isinstance(v, float) else v}" for k, v in e.items() if not isinstance(v, (dict, list)))


def verify(ctx: Ctx, wf: str, now: int, inc: dict, live_prompt: str) -> str | None:
    """After the fix is live: flag rate back to baseline and engagement not worse than before the incident."""
    t0 = inc["applied_minute"]
    if now - t0 < 90:
        return None
    pi = prompt_index(ctx, wf, live_prompt)
    w = A.qa_window(ctx, wf, t0, now, prompt=pi)
    if w["conn"] < C.HALLUC_MIN_CONN:
        return None
    base = baseline_flag_rate(ctx, wf, now, exclude=inc["bad"])
    z = prop_z(w["flags"], w["conn"], base)
    pre = A.kpi_window(ctx, wf, inc["opened"] - 3 * 1440, inc["opened"] - 120)
    post = A.kpi_window(ctx, wf, t0, now)
    eng_rel = (post["engagement"] / pre["engagement"] - 1) if pre["engagement"] and post["engagement"] else None
    res = {"flag_rate_after": round(w["flags"] / w["conn"], 4), "baseline": round(base, 4), "z": round(z, 2),
           "n": w["conn"], "engagement_vs_before": None if eng_rel is None else round(eng_rel, 4)}
    inc["verify"] = res
    if z < C.HALLUC_Z:
        return "resolved"
    return "failed"
