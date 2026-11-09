# Crash-recovery test: public_02_incidents

Injected hard-kills (plan `intent:2,sent:2,apply:2,ingest_mid:120`): **4** of 4 fired; external SIGKILLs: **2**; supervisor restarts by run.sh: **6**.

| check | result |
|---|---|
| run finished with exit 0 and the scenario reached its end | PASS |
| no approval request sent twice (unique proposal ids == requests) | PASS |
| no change applied twice (applied approvals unique) | PASS |
| every applied approval is recorded as applied in our store | PASS |
| no proposal left in 'submitting' (intent never resolved) | PASS |
| no approval lapsed because of a restart | PASS |
| no auto-apply refused (no duplicate/blind re-send) | PASS |
| open proposals at the end (0) match the simulator's view exactly (none forgotten) | PASS |

Simulator ground truth: 23 approval requests, 17 applied via approval, 11 auto-applied, 3 stale-base conflicts, 0 lapsed.
Controller store: proposal states {'abandoned': 1, 'applied': 28, 'stale': 3, 'rejected': 3}; 2 intents reconciled after restart.

Outcome despite 6 restarts: conclusion rate 0.435, pickup 0.277, engagement 0.541; incidents: [{"type": "supervisor_edit", "workflow": "ks_cancel", "at": "D3 11:30", "mttr_minutes": 120, "fixed_by": "controller_approved"}].

State lost on a crash: nothing durable. In-flight HTTP responses are lost and recovered by reconciling intent rows against the simulator (GET /approvals, config history). The only thing recomputed is the current tick's decision, which is idempotent.

**Overall: PASS**
