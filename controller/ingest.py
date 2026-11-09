"""Event ingestion (C1, C3). Backlog-safe, idempotent, restartable.

 * Chunks are processed strictly in order; a chunk's aggregates and its row in `chunks` commit atomically.
 * Calls are aggregated with numpy into four small tables (see store.py); we never keep per-call rows in memory.
 * The same code path ingests `history` chunks (bootstrap before D0) and live `events` chunks.
"""
from __future__ import annotations

import datetime as dt
import json
import time

import numpy as np

from . import feat as F
from . import config as C
from .chaos import maybe_crash
from .context import Ctx

_BASE = dt.date(2027, 1, 4).toordinal()
_ts_cache: dict[str, int] = {}


def ts_minute(ts: str) -> int:
    m = _ts_cache.get(ts)
    if m is None:
        d = dt.date(int(ts[0:4]), int(ts[5:7]), int(ts[8:10])).toordinal() - _BASE
        m = d * 1440 + int(ts[11:13]) * 60 + int(ts[14:16])
        if len(_ts_cache) > 200_000:
            _ts_cache.clear()
        _ts_cache[ts] = m
    return m


def _gsum(keys: list, vals: list):
    K = np.stack(keys, axis=1)
    u, inv = np.unique(K, axis=0, return_inverse=True)
    inv = inv.ravel()
    sums = [np.bincount(inv, weights=v, minlength=len(u)).astype(np.int64) for v in vals]
    return u, sums


def ingest_chunk(ctx: Ctx, meta: dict) -> dict:
    """Returns {'events': n, 'calls': n, 'ms': float}. No-op if the chunk was already applied."""
    st = ctx.store
    n = meta["chunk"]
    if st.q("SELECT 1 FROM chunks WHERE chunk=?", (n,)):
        return {"events": 0, "calls": 0, "ms": 0.0, "skipped": True}
    t0 = time.perf_counter()
    lines = ctx.sim.fetch_lines(f"/events/chunks/{n}")
    history = meta["kind"] == "history"
    new_leads: dict[str, list] = {}
    calls: dict[str, list] = {}
    other: list[dict] = []
    loads = json.loads
    for ln in lines:
        if ln.startswith('{"type":"call"'):
            e = loads(ln)
            v = e["variant"]
            qa = e["qa"]
            calls.setdefault(e["workflow_id"], []).append((
                e["lead_id"], e["ts"], e["config_version"], e["cohort_id"], v["welcome_message"], v["voice"],
                v["caller_id"], v["prompt_version"], e["connected"], e["engaged"],
                (1 if qa["hallucination_flag"] else 0) if qa else 0, qa["flow_adherence"] if qa else 0.0,
                e["event_id"]))
        elif ln.startswith('{"type":"lead"'):
            e = loads(ln)
            new_leads.setdefault(e["workflow_id"], []).append(e)
        else:
            other.append(loads(ln))
    total_calls = 0
    last_event = meta["last_event_id"]
    with st.tx() as db:
        # ---- leads first (calls in the same chunk need their features)
        for wf, evs in new_leads.items():
            la = ctx.leads[wf]
            nums = np.fromiter((int(e["lead_id"].rsplit(":l", 1)[1]) for e in evs), dtype=np.int64, count=len(evs))
            feats = np.fromiter((F.pack(e["features"]) for e in evs), dtype=np.uint16, count=len(evs))
            days = np.fromiter((ts_minute(e["ts"]) // 1440 for e in evs), dtype=np.int16, count=len(evs))
            la.ensure(int(nums.max()) + 1)
            la.feat[nums], la.day[nums] = feats, days
            la.n = max(la.n, int(nums.max()) + 1)
            db.executemany("INSERT OR IGNORE INTO leads(wf,num,day,feat,concl) VALUES(?,?,?,?,0)",
                           [(wf, int(a), int(b), int(c)) for a, b, c in zip(nums, days, feats)])
        # ---- calls
        for wf, rows in calls.items():
            m = ctx.meta[wf]
            la = ctx.leads[wf]
            k = len(rows)
            total_calls += k
            num = np.fromiter((int(r[0].rsplit(":l", 1)[1]) for r in rows), dtype=np.int64, count=k)
            minute = np.fromiter((ts_minute(r[1]) for r in rows), dtype=np.int64, count=k)
            ver = np.fromiter((r[2] for r in rows), dtype=np.int64, count=k)
            coh = np.fromiter((ctx.cohort_code(wf, r[3]) for r in rows), dtype=np.int64, count=k)
            oi = m.opt_idx
            w = np.fromiter((oi["welcome_message"][r[4]] for r in rows), dtype=np.int64, count=k)
            v = np.fromiter((oi["voice"][r[5]] for r in rows), dtype=np.int64, count=k)
            d = np.fromiter((oi["caller_id"][r[6]] for r in rows), dtype=np.int64, count=k)
            p = np.fromiter((oi["prompt_version"][r[7]] for r in rows), dtype=np.int64, count=k)
            conn = np.fromiter((r[8] for r in rows), dtype=np.int64, count=k)
            eng = np.fromiter((r[9] for r in rows), dtype=np.int64, count=k)
            flag = np.fromiter((r[10] for r in rows), dtype=np.int64, count=k)
            adh = np.fromiter((int(r[11] * 1000) for r in rows), dtype=np.int64, count=k)
            last_event = max(last_event, max(r[12] for r in rows))
            ok = num < len(la.feat)
            cell = np.where(ok, la.feat[np.minimum(num, len(la.feat) - 1)], 0).astype(np.int64)
            one = np.ones(k, dtype=np.int64)
            # exp_stats
            u, (s_d, s_c, s_e) = _gsum([minute // C.SLOT_MIN, ver, coh, cell, w, v], [one, conn, eng])
            db.executemany(
                "INSERT INTO exp_stats VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(wf,slot,version,cohort,cell,w,v) DO "
                "UPDATE SET dials=dials+excluded.dials, conn=conn+excluded.conn, eng=eng+excluded.eng",
                [(wf, *map(int, u[i]), int(s_d[i]), int(s_c[i]), int(s_e[i])) for i in range(len(u))])
            # pick_stats (pickup by hour x (lang,tier,age))
            u, (s_d, s_c) = _gsum([minute // 60, F.pcell(cell)], [one, conn])
            db.executemany(
                "INSERT INTO pick_stats VALUES(?,?,?,?,?) ON CONFLICT(wf,hidx,pcell) DO UPDATE SET "
                "dials=dials+excluded.dials, conn=conn+excluded.conn",
                [(wf, *map(int, u[i]), int(s_d[i]), int(s_c[i])) for i in range(len(u))])
            # did_stats
            u, (s_d, s_c, s_e) = _gsum([minute // 15, d], [one, conn, eng])
            db.executemany(
                "INSERT INTO did_stats VALUES(?,?,?,?,?,?) ON CONFLICT(wf,tidx,caller) DO UPDATE SET "
                "dials=dials+excluded.dials, conn=conn+excluded.conn, eng=excluded.eng+eng",
                [(wf, *map(int, u[i]), int(s_d[i]), int(s_c[i]), int(s_e[i])) for i in range(len(u))])
            # qa_stats
            u, (s_d, s_c, s_f, s_e, s_a) = _gsum([minute // 15, p], [one, conn, flag, eng, adh])
            db.executemany(
                "INSERT INTO qa_stats VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(wf,tidx,prompt) DO UPDATE SET "
                "dials=dials+excluded.dials, conn=conn+excluded.conn, flags=flags+excluded.flags, "
                "eng=eng+excluded.eng, adh_milli=adh_milli+excluded.adh_milli",
                [(wf, *map(int, u[i]), int(s_d[i]), int(s_c[i]), int(s_f[i]), int(s_e[i]), int(s_a[i]))
                 for i in range(len(u))])
            if ctx.ledger and not history:
                evid = np.fromiter((r[12] for r in rows), dtype=np.int64, count=k)
                db.executemany("INSERT OR IGNORE INTO ledger VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                               [(int(evid[i]), wf, int(num[i]), int(minute[i]), int(ver[i]), int(coh[i]), int(w[i]),
                                 int(v[i]), int(d[i]), int(p[i]), int(conn[i]), int(eng[i])) for i in range(k)])
            # conclusions
            eidx = num[(eng == 1) & ok]
            if len(eidx):
                eidx = np.unique(eidx)
                la.concl[eidx] = 1
                db.executemany("UPDATE leads SET concl=1 WHERE wf=? AND num=?", [(wf, int(x)) for x in eidx])
        # ---- non-call events (kept for the audit trail; the main loop handles the ones it cares about)
        for e in other:
            last_event = max(last_event, e.get("event_id", 0))
            wf = e.get("workflow_id")
            db.execute("INSERT OR IGNORE INTO sim_events(event_id,type,wf,minute,body,handled) VALUES(?,?,?,?,?,?)",
                       (e["event_id"], e["type"], wf, ts_minute(e["ts"]), json.dumps(e), 1 if history else 0))
        maybe_crash(st, "ingest_mid")      # inside the transaction: must roll back, chunk re-read after restart
        ms = (time.perf_counter() - t0) * 1000
        db.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?,?)",
                   (n, meta["kind"], meta["events"], last_event, meta["until_minute"], ms, time.time()))
    return {"events": meta["events"], "calls": total_calls, "ms": ms}
