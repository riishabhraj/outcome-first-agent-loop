"""SQLite store: all controller state lives here, so kill -9 + restart resumes (C12/C13).

Design rules
 * one writer process; WAL; every ingest chunk is applied in ONE transaction together with its 'chunks' row, so a
   crash re-reads at most the chunk in flight and never double-counts (idempotent ingestion).
 * every outbound mutation (approval request, apply, auto-apply) is written as an intent row BEFORE the HTTP call,
   with a deterministic proposal_id; restart reconciles intents against the simulator.
 * the audit table is append-only; `controller why` answers provenance questions from it alone.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS chunks(chunk INTEGER PRIMARY KEY, kind TEXT, events INTEGER, last_event_id INTEGER,
  until_minute INTEGER, ingest_ms REAL, wall REAL);
CREATE TABLE IF NOT EXISTS leads(wf TEXT, num INTEGER, day INTEGER, feat INTEGER, concl INTEGER DEFAULT 0,
  PRIMARY KEY(wf, num)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS cohort_codes(wf TEXT, name TEXT, code INTEGER, PRIMARY KEY(wf, name));
-- per (slot=6h) x config version x cohort x feature cell x welcome x voice
CREATE TABLE IF NOT EXISTS exp_stats(wf TEXT, slot INTEGER, version INTEGER, cohort INTEGER, cell INTEGER,
  w INTEGER, v INTEGER, dials INTEGER, conn INTEGER, eng INTEGER,
  PRIMARY KEY(wf, slot, version, cohort, cell, w, v)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS pick_stats(wf TEXT, hidx INTEGER, pcell INTEGER, dials INTEGER, conn INTEGER,
  PRIMARY KEY(wf, hidx, pcell)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS did_stats(wf TEXT, tidx INTEGER, caller INTEGER, dials INTEGER, conn INTEGER, eng INTEGER,
  PRIMARY KEY(wf, tidx, caller)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS qa_stats(wf TEXT, tidx INTEGER, prompt INTEGER, dials INTEGER, conn INTEGER, flags INTEGER,
  eng INTEGER, adh_milli INTEGER, PRIMARY KEY(wf, tidx, prompt)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ledger(event_id INTEGER PRIMARY KEY, wf TEXT, num INTEGER, minute INTEGER, version INTEGER,
  cohort INTEGER, w INTEGER, v INTEGER, d INTEGER, p INTEGER, conn INTEGER, eng INTEGER);
CREATE INDEX IF NOT EXISTS ledger_lead ON ledger(wf, num);
CREATE TABLE IF NOT EXISTS sim_events(event_id INTEGER PRIMARY KEY, type TEXT, wf TEXT, minute INTEGER, body TEXT,
  handled INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS config_versions(wf TEXT, version INTEGER, actor TEXT, changed_dims TEXT,
  applied_minute INTEGER, proposal_id TEXT, approval_id TEXT, config TEXT, PRIMARY KEY(wf, version));
CREATE TABLE IF NOT EXISTS proposals(proposal_id TEXT PRIMARY KEY, wf TEXT, route TEXT, kind TEXT, state TEXT,
  created_minute INTEGER, base_version INTEGER, dims TEXT, change_summary TEXT, impact_score REAL,
  affected_leads INTEGER, request TEXT, config TEXT, approval_id TEXT, decided_minute INTEGER, apply_by TEXT,
  applied_version INTEGER, reasons TEXT, parent_id TEXT, updated_minute INTEGER, note TEXT);
CREATE INDEX IF NOT EXISTS proposals_wf ON proposals(wf, state);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, sim_minute INTEGER, wall REAL, wf TEXT,
  kind TEXT, actor TEXT, summary TEXT, details TEXT, proposal_id TEXT, approval_id TEXT, version INTEGER);
CREATE INDEX IF NOT EXISTS audit_wf ON audit(wf, sim_minute);
CREATE INDEX IF NOT EXISTS audit_prop ON audit(proposal_id);
CREATE TABLE IF NOT EXISTS cohorts(wf TEXT, cohort_id TEXT, where_json TEXT, first_minute INTEGER,
  PRIMARY KEY(wf, cohort_id));
CREATE TABLE IF NOT EXISTS wf_state(wf TEXT PRIMARY KEY, body TEXT);
CREATE TABLE IF NOT EXISTS incidents(incident_id TEXT PRIMARY KEY, wf TEXT, kind TEXT, state TEXT, opened_minute INTEGER,
  closed_minute INTEGER, body TEXT);
CREATE TABLE IF NOT EXISTS loop_metrics(sim_minute INTEGER PRIMARY KEY, wall REAL, events INTEGER, ingest_ms REAL,
  decide_ms REAL, tick_ms REAL, credits REAL, decisions INTEGER, backlog INTEGER, rss_mb REAL);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, sim_minute INTEGER, wf TEXT, severity TEXT,
  code TEXT, message TEXT, resolved_minute INTEGER);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA temp_store=MEMORY")
        self.db.execute("PRAGMA cache_size=-200000")
        self.db.executescript(SCHEMA)
        self.now = int(self.kv_get("now_minute", -10**9))
        self._depth = 0

    # ---- transactions
    @contextmanager
    def tx(self):
        if self._depth:
            self._depth += 1
            try:
                yield self.db
            finally:
                self._depth -= 1
            return
        self.db.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        finally:
            self._depth = 0

    # ---- kv
    def kv_get(self, key, default=None):
        r = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def kv_set(self, key, value):
        self.db.execute("INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value)))

    # ---- per-workflow decision state (JSON blob)
    def wf_get(self, wf: str, default=None):
        r = self.db.execute("SELECT body FROM wf_state WHERE wf=?", (wf,)).fetchone()
        return json.loads(r[0]) if r else (default if default is not None else {})

    def wf_set(self, wf: str, body: dict):
        self.db.execute("INSERT INTO wf_state VALUES(?,?) ON CONFLICT(wf) DO UPDATE SET body=excluded.body",
                        (wf, json.dumps(body, separators=(",", ":"))))

    # ---- audit (append-only)
    def audit(self, kind: str, wf: str | None, summary: str, details: dict | None = None, actor: str = "controller",
              proposal_id=None, approval_id=None, version=None):
        self.db.execute(
            "INSERT INTO audit(sim_minute,wall,wf,kind,actor,summary,details,proposal_id,approval_id,version) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (self.now, time.time(), wf, kind, actor, summary, json.dumps(details or {}, default=_j), proposal_id,
             approval_id, version))

    def alert(self, wf, severity, code, message):
        """severity: page | ticket | info. De-duplicated while an identical alert is open."""
        r = self.db.execute("SELECT id FROM alerts WHERE wf IS ? AND code=? AND resolved_minute IS NULL",
                            (wf, code)).fetchone()
        if r:
            return False
        self.db.execute("INSERT INTO alerts(sim_minute,wf,severity,code,message) VALUES(?,?,?,?,?)",
                        (self.now, wf, severity, code, message))
        self.audit("alert", wf, f"[{severity}] {code}: {message}", {"severity": severity, "code": code})
        return True

    def resolve_alert(self, wf, code):
        self.db.execute("UPDATE alerts SET resolved_minute=? WHERE wf IS ? AND code=? AND resolved_minute IS NULL",
                        (self.now, wf, code))

    def set_now(self, minute: int):
        self.now = minute
        self.kv_set("now_minute", minute)

    def q(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def close(self):
        self.db.close()


def _j(o):
    try:
        import numpy as np
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except Exception:
        pass
    return str(o)
