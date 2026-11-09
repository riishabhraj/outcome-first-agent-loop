# Runbook: the outcome-first controller

For the engineer who gets paged while the author is on leave. Everything below is runnable.

## 1. Run it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt          # numpy, httpx (+ fastapi/pyyaml for the simulator)
python -m sim serve --scenario scenarios/public_02_incidents.yaml --run-dir runs/mine --force   # the world, :8700
./run.sh http://127.0.0.1:8700                                              # the controller; returns when the scenario ends
# or everything incl. baseline + grade:   scripts/run_scenario.sh public_02_incidents
python -m pytest -q tests                                                   # 25 tests, ~15 s
python scripts/crash_test.py public_02_incidents 8702                       # kill -9 the controller 6 times mid-run
```

`run.sh` restarts the process if it dies (max 50 times) and passes `CTRL_DB` (default `state/controller.db`). State from a
*different* simulator world (clock behind the store, other scenario) is detected at start and archived as
`<db>.stale-<ts>`, never reused. `CTRL_FRESH=1` forces that.

Env knobs: `CTRL_DB`, `CTRL_VIEW_PORT` (default 8701, 0 = off), `CTRL_LEDGER=on|off` (per-call treatment ledger; auto-off above
120k dials/day), `CTRL_CRASH_PLAN` (test only). All thresholds are in `controller/config.py` with a comment each.

## 2. Map of the code

| file | job |
|---|---|
| `main.py` | the loop: ingest -> events -> per workflow {incidents, caller-ID, experiments, schedule, compose, route} -> advance 15 min |
| `ingest.py` | chunk -> numpy -> 4 aggregate tables + lead features; one transaction per chunk (idempotent) |
| `store.py` | SQLite schema; `audit` is the append-only decision log |
| `feat.py` | feature codec and **`Slice`**, the one shared cohort object (canonical id, e.g. `age_band=60+\|lang=hi,ta`) |
| `stats.py`, `analytics.py` | stratified lift (Mantel-Haenszel), exact binomial tail, read-side queries |
| `experiments.py` | welcome/voice hypotheses: stage 1 flag, stage 2 confirm on fresh data, eliminate, decay, allocation weights |
| `schedule.py` | which features split hour-of-day pickup shape (cohorts), per-cohort hour weights, historical replay |
| `did.py` | caller-ID peer test (step change), pickup-proportional shares, probation |
| `planner.py` | **compose**: targets in `wf_state` -> one desired config (blocks = product of per-feature groups, <= 20 cohorts) |
| `governance.py` | the only module that mutates config; proposal state machine; intent rows; reconcile |
| `incidents.py` | hallucination / prompt-regression detection and fix pipeline |
| `explain.py` | diff, affected leads, dry run, impact score for a proposal |
| `view.py` | `why`, `lead`, `view`, `status`, `audit` CLI and the HTTP view |
| `chaos.py` | crash injection for the recovery test (inert unless `CTRL_CRASH_PLAN`) |

## 3. Questions you will be asked, and the command

```bash
python -m controller why    --db state/X.db --wf nw_renewal --at "D3 14:00"   # why did it change, who approved, what happened next
python -m controller lead   --db state/X.db --wf nw_renewal --lead nw_renewal:l00014303 --day 1   # what did THIS customer hear, and why
python -m controller view   --db state/X.db [--tenant kestrel_finance]         # the supervisor's one screen (also http://127.0.0.1:8701/)
python -m controller status --db state/X.db                                    # loop health: ingest rate, tick time, backlog, credits, alerts
python -m controller audit  --db state/X.db --wf ks_cancel --kind incident     # raw decision log
```

All of these read only the controller's SQLite file, never the simulator.

## 4. Alerts

**Page** (a human must act before morning):

| code | meaning | what to do |
|---|---|---|
| `incident_unsafe_prompt_edit`, `incident_hallucination_spike`, `incident_engagement_regression` | a prompt is hurting customers; the pipeline is already fixing it | `audit --kind incident`; if state is `failed` a human must pick a prompt. Fix proposals are `urgent` and skip the routine queue |
| `incident_no_fix` | no candidate prompt passed validation | choose a prompt by hand and send it through the client's approval |
| `credits_exhausted_during_incident` | < 10% credits left while an incident is open: validation/LLM calls will 402 | top up credits |
| `approved_not_applied` | an approved change sat > 90 min; it **lapses** at the apply window | the loop is probably stuck: check `status` (tick_ms, backlog) and the process log |

**Ticket** (can wait): `did_flagged_<D>` (rotation is automatic), `guardrail_*` / `guardrail_erosion`, `invalid_proposal`
(a bug: the simulator refused our config), `auto_apply_refused`, `ingest_backlog`.
**Never page**: approver rejections, expiries, stale re-proposals, candidates that failed to replicate, pickup flaps. They are
normal and are recomputed from fresh data.

## 5. Failure modes

* **Controller dies**: nothing to do; `run.sh` restarts it and it resumes. Look for `[run.sh] controller exited rc=` in its log.
  Three restarts in an hour = find the traceback (it is printed to stderr).
* **Simulator 5xx / unreachable**: reads retry with backoff (8 tries). `advance` is never retried blindly: after an ambiguous
  failure we re-read `/sim/state` and carry on. A 402 (out of credits) propagates as an alert, never a retry loop.
* **A proposal is `submitting` forever**: impossible after a restart (`poll` reconciles it against `GET /approvals`); if you see it,
  the intent row is in `proposals` and the id is deterministic, so look it up in the simulator by `proposal_id`.
* **`stale` proposals**: the config moved (supervisor edit or our own auto-apply). Expected; the next batch is recomputed.
* **Cohort explosion / too many requests**: cohorts are capped at 20; requests at `min(policy, tenant pref)` per 24 h with one slot
  reserved for urgent work; no two routine requests within 5 h; a routine change needs a >= 12% allocation shift or a stage change.
* **Everything looks stuck at night**: by design. Routine requests are only sent inside `[approver_start, approver_end - max_latency)`.

## 6. Extending

* New tenant preference: `TENANT_PREFS` in `config.py` (keyed by tenant id).
* New decision module: write targets into `wf_state`, render them in `planner.compose`, never call the simulator's mutating
  endpoints directly: go through `Governance`.
* New alert: `store.alert(wf, severity, code, message)`; de-duplicated while open; add it to the table above.
