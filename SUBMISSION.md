# Outcome-First Agent Loop — Submission

**Name:** _[fill in]_ · **Date:** _[fill in]_ · **Hours actually spent (be honest):** _[fill in — see the note at the end of §14]_ · **Language / storage:** Python 3.11+ (numpy, httpx) / one SQLite file (WAL)

> Run it: `./run.sh <SIM_URL>` (restarts itself, resumes from the store). Operate it: `docs/RUNBOOK.md`. Everything quoted
> below is reproducible: `scripts/run_scenario.sh <scenario>`, `python scripts/crash_test.py`, `python -m pytest -q tests`.
> Reports: `reports/public_01_dev.md`, `reports/public_02_incidents.md`, `reports/public_03_load.md`, `reports/load_test.md`, `reports/crash_recovery.md`.

---

## 0. TL;DR

* An always-on reconciler: ingest events -> detect -> hypothesise/experiment -> compose **one** desired config per workflow -> route it through governance (auto-apply only where policy and my own rule allow, otherwise one batched approval) -> verify -> explain from its own SQLite store.
* **public_01: +14.2%** leads-concluded rate vs baseline (pickup +9.1%, engagement +3.8%); **public_02: +31.3%** (pickup +27.3%, engagement +0.7%, hallucinated calls 431 -> 166, supervisor's bad prompt fixed in 75–150 min, spam-flagged caller ID drained to ~2% of dials within a day); **public_03 (12 workflows, 650k dials/day): +17.8%** at ~41 s per simulated day and 135k events/s ingest. (Numbers vary a few points run to run: the simulated approver's random delays and rejections differ with every proposal id.)
* Crash-tested: 4 injected hard-kills at the dangerous instants + 2 external SIGKILLs in one public_02 run, 6 restarts, same outcome (conclusion rate 0.435 vs 0.434 uninterrupted), no double-apply, no lost approval.
* Not clean: 2–6 changes per scenario are judged **harmful** by hidden truth (§11); in public_02 `nw_renewal`'s *primary* KPI ends 3.4% **below** baseline in the final run (−4.4%…+0.2% across six runs) and `nw_kyc`'s engagement guardrail is breached in some runs (−3.3%…−0.4%; −2.4% in public_01's final run); decay rollback is slow on the 5-day load scenario (§13).
* Cut on purpose: per-person call time, welcome x voice interactions, cross-tenant fair-share scheduling, any LLM role beyond proposing prompt candidates.

## 1. Assumptions

1. **Policies are the contract, memos are opinions.** Approver hours/latency/TTL/apply window/request limits/auto-apply dims and `max_shift` are re-read from `GET /workflows` every tick and never hard-coded; nothing about the public scenarios' *truth* (effect sizes, DID health, decay timing, archetypes) is encoded. Everything is learned from events. (Where this document quotes a hidden value — P4's 2.5% hallucination rate, P5's engagement penalty, DID base health — it comes from the public scenario files / grader report and is used only to *explain* results afterwards; the controller never reads it and measures these things itself.)
2. **Headline outcome = leads concluded / leads uploaded** (the README's business outcome). Pickup and engagement are always reported separately, per workflow, never blended.
3. **"Customer-facing"** = `welcome_message`, `voice`, `prompt_version`, `cohorts`; **"operational"** = `schedule`, `caller_id` (Compliance's own split). I never auto-apply a customer-facing change for an ordinary improvement, even where the policy allows it (only Kestrel's `ks_cancel` does). Exception: an *emergency rollback* of a harmful state, if the policy permits.
4. **Client preferences stricter than platform policy** are honoured and live in `config.py::TENANT_PREFS` keyed by tenant id (Kestrel: <= 2 requests/day, never before 11:00; Northwind: named approver for anything customers hear). Hidden scenarios with other tenant ids get conservative defaults (<= 2/day, one slot reserved for urgent work).
5. **For a pickup-primary workflow** (`nw_kyc`) I do not run welcome/voice experiments (they cannot move pickup); they start only if the engagement *guardrail* is eroding ("defence mode").
6. **Welcome and voice are independently randomised per call**, so each is analysed on its own marginal, stratified by (config version, cohort). Interactions are not tested.
7. **Engagement is measured per connected call** and repeated calls to one lead are treated as independent (mildly anti-conservative; the lead pool also depletes of easy converts, which depresses engagement *rates* as pickup rises — see §13).
8. **Caller-ID health is load-dependent and slow-moving** (inferred from the data: a DID used for 100% of dials degrades, spreading helps, the effect decays over days). Never read from a scenario.
9. **`hallucination_flag` is imperfect** (README), so the flag rate is compared with a baseline measured from our own history per workflow, with an exact binomial tail, not a fixed threshold.
10. **Credits are scarce** (200–400): incident spend is capped at 40 per incident, ordinary operation spends none.
11. A supervisor edit means "I own this dimension for now": locked for 48 h; the experiment on that dimension restarts with the supervisor's value as the new control.
12. One controller process; workflows handled sequentially inside a 15-simulated-minute tick. A tick is the unit of "decision lag".
13. The simulator's clock is ours to advance; I use 15-minute ticks (the minimum) so incidents are seen within a tick.

## 2. Scope decisions

| # | Capability | Built / Partial / Cut | Why | What it would take to finish |
|---|---|---|---|---|
| C1 | Ingestion at load scale | **Built** | Foundation. ~135k events/s on one core; one transaction per chunk -> idempotent; backlog-safe | Partition by tenant across processes for 10x (§8) |
| C2 | Joins + **one shared cohort object** | **Built** | `Slice` (canonical id) is used by experiments, schedule, config, audit and the supervisor view; lead features joined by lead number | — |
| C3 | Bootstrap from history | **Built** | History seeds per-hour pickup shapes (cohort discovery, hour weights), flag baselines per prompt, the dry-run replay model | Use history engagement by cell as a prior for arms (currently flat prior) |
| C4 | Call-time optimisation | **Partial** | Per-**cohort** hour weights built. **Per-person cut**: a person gets <= 8 attempts ever, so a personal pickup profile is mostly noise, and `lead_preferred_hour` x6/x0.25 is a blunt instrument | Per-person only for returning persons with >= 3 past connects, shrunk to the cohort profile |
| C5 | Caller-ID health | **Built** | Biggest single lever (KYC pickup +25–31%). Peer test, rotation, probation, capped exploration of unmeasured DIDs | Fit the load-response curve instead of reweighting by recent pickup (§13) |
| C6 | Welcome/voice experimentation | **Built** | The primary lever for engagement-primary workflows. Min sample, multiple comparisons, two-stage confirmation | Interaction terms; bandit allocation instead of stage rules |
| C7 | Promotion, decay, rollback | **Built** (partial on timing) | Needed for the decaying W3. Own-rate-vs-control test | Faster on short horizons (§13) |
| C8 | Hallucination incident response | **Built** | Detect -> (validate | LLM) -> propose -> verify; 6–12 credits/incident | Also verify engagement *before* applying a non-measured prompt |
| C9 | Governance client | **Built** | The graded boundary. Lifecycle, batching, timing, expiry/lapse/stale, auto-apply within policy, urgent lane | — |
| C10 | Impact, affected leads, dry run | **Built (honest, model-based)** | Product ranks the queue by impact; it has to mean something | A real hold-out replay when more history exists |
| C11 | Supervisor view | **Built** (JSON + one HTML page) | Anita's "one screen" | Auth, per-client branding |
| C12 | Audit store | **Built** | Constraint 6; `why`, `lead`, `audit` CLI | — |
| C13 | Crash recovery, demonstrated | **Built** | SRE non-negotiable; `scripts/crash_test.py` | — |
| C14 | Observability of the loop | **Partial** | `status` + alerts table + page/ticket rules; no Prometheus/Grafana | Export `loop_metrics` as a scrape endpoint |
| C15 | Fair allocation of credits/approver attention | **Partial/Cut** | Per-incident credit cap, an urgent request slot per workflow, request spacing. No cross-tenant scheduler | A global budget arbiter once there are > ~50 workflows (§8) |

**Principle used to choose:** weight by *expected outcome per unit of governance risk*. Caller-ID and call-time first (large, mostly
operational, cheap to verify), then welcome/voice (the primary KPI where it applies), then the incident path (it is both a
graded event and the place where a mistake hurts customers). What I cut is either noisy at this data scale (per-person) or only
matters once the platform is bigger than the simulator (fair share). I preferred five things that survive a kill -9 over fifteen that
do not.

## 3. Stakeholders

Claims were checked against the policies the simulator enforces (public scenarios). Where a claim is wrong I say so in the reply.

### 01 Sales (Nikhil Rao)
* **Accepted:** a headline number; speed on things that are safe to be fast about — caller-ID and call-time changes auto-apply where policy permits (`nw_renewal`, `ks_cancel`); winners are promoted after a two-stage confirmation, not on a lunchtime p-value.
* **Pushed back on:** one *blended* score (the product, the README and Compliance all forbid blending pickup and engagement; I show the business outcome *leads concluded / uploaded* plus the two KPIs side by side); "live for everyone by evening" (a variant that is "winning by lunch" fails to replicate on fresh data about 1 time in 3 — 23 of 59 candidates in the load run); rolling out the "persuasive" prompt (P4: hallucination 2.5% vs 0.6%).
* **Wrong against the policy:** "Kestrel has turned on auto-apply for **everything**" — `ks_cancel` permits only schedule/caller_id/welcome/voice, **not** prompt or cohorts, and nothing that moves an allocation by more than 0.2 TV (the simulator answers 403). "Northwind is fine with auto-apply on the KYC flow" — `nw_kyc` has auto-apply **disabled**; `nw_renewal` allows schedule + caller-ID only.
* **Reply:** *"Nikhil — for the summit I can show, per client, 'leads concluded: +14–31% vs the old setup' with pickup and engagement beside it, and 'caller-ID health fixed in a day' / 'bad script caught and replaced in ~2 h'. I can't give one blended score (the clients' product definitions forbid it) and I won't push winners live on a morning read: ~1 in 3 early winners vanish on fresh data. Two corrections before you promise anything: Kestrel's auto-apply covers only call timing, caller ID, welcome and voice, capped at a 20% shift; Northwind KYC has auto-apply off. I'm happy to demo the supervisor screen instead."*

### 02 Client compliance (Meera Iyer, Northwind)
* **Accepted:** every welcome/voice/cohort/prompt change goes through a named approval (also on Kestrel where auto would be allowed); prompt changes carry a validation run id; every proposal carries summary/diff/affected leads/dry-run/rationale; requests are only sent inside approver hours (renewal: 10:00–15:00 so a 4 h latency still lands before 19:00; KYC: 10:00–11:00 given its 8 h latency, 10–18); "slow and right": exploration shifts are small, a candidate must replicate on fresh data, harmful arms are eliminated early; the auditor question (#3) is answered by `controller lead` (the variant, cohort, config version and the proposal/approval that put each dimension there) and `controller why`.
* **Pushed back on:** nothing outright; but exploration is a customer-facing change and *does* briefly send some customers a variant that may be worse (the controlled-test cost). That is stated in every exploration request ("expected KPI change ~0 by design; value is information").
* **Wrong/incomplete:** "operational settings such as call timing and caller-ID rotation are covered by the policy we signed." True for `nw_renewal` (schedule + caller_id, `max_shift` 0.5). **False for `nw_kyc`: auto-apply is disabled**, so even caller-ID rotation there needs an approval. Also, per-cohort hour weights need *cohorts*, and `cohorts` is not an auto-apply dimension for Northwind, so the first schedule change is an approval on both workflows. The per-customer variant ledger is stored per call at dev scale and switched off above 120k dials/day (a cost decision; the aggregate by cohort/variant/version is always kept).
* **Reply:** *"Meera — agreed on every point, with two specifics. Anything a customer hears (opening, voice, script, cohorts) is sent to your named approver; scripts carry the validation run. You said timing and caller-ID rotation are covered by your signed policy: that's right for renewals, but the KYC workflow's policy has auto-apply switched off, so there those changes also wait for your approver. For any customer and day we can show, within minutes, which variant they heard, which proposal put it live and who approved it. One thing you should know: a controlled test means some customers hear a variant we haven't proven yet; we keep it small and stop an arm as soon as it looks worse."*

### 03 Platform/SRE (Kabir Menon)
* **Accepted:** kill/restart test (§9, `reports/crash_recovery.md`); idempotency everywhere (deterministic proposal ids, intent rows, reconcile against `GET /approvals` and config history); actionable alerts only (§10); no new stateful infra (one SQLite file); `controller status` for ingest lag / decisions per hour / credit burn without logs; the 10-minute-per-day target (measured ~41 s per simulated day, single-threaded, on a laptop).
* **Pushed back on:** nothing, except one honesty note: SQLite is single-writer, which is the right trade at this size and the first thing I'd shard at 10x (by tenant), not replace.
* **Claim to check:** "we already run Postgres and Redis" — true, and I did not use them; adopting Postgres is a drop-in for `store.py` if he prefers one operational surface, but it adds a network hop to the hottest path (~4 M aggregate upserts per 5 days).
* **Reply:** *"Kabir — no new infrastructure: one SQLite file, one process, `run.sh` restarts it. Kill -9 test: 6 kills (4 injected at the nasty instants — after the intent row, after the simulator accepted but before we recorded, mid-transaction — plus 2 external), run finished with the same result, nothing applied twice, nothing lost. Load: one simulated day of the 650k-dials scenario takes ~41 s end to end (simulator included), ingest 135k events/s, peak 676 MB, tick p95 1.1 s. At 10x the first thing to break is the single SQLite writer; plan is one process per tenant group. Pages: stuck-approval, incident without a fix, credits gone during an incident. Everything else is a ticket."*

### 04 Client ops supervisor (Anita Desai, Kestrel)
* **Accepted:** her edits are never overwritten — the dimension is locked for 48 h, the experiment on it restarts from her value, her edit is recorded as a first-class event; **max 2 requests/day and none before 11:00** (hard cap 2 incl. urgent; routine <= 1 so an incident fix always has a slot; submission window 11:00–17:00); one batched request per change set ("five things in one request"); one screen (`/` and `controller view`): being tested, winning, what we want from her ranked by impact, changed today; customer-facing changes go through her approval, not around it.
* **Pushed back on:** "tell me *before* anything changes on a running campaign" — for **operational** changes (a caller-ID rotation when a number is spam-flagged) waiting for a human would cost hours of dead calls, so those are applied within policy and appear under "changed today" with the reason. She is told; she is not asked.
* **Claims vs policy:** her cap (2/day) is stricter than `ks_cancel`'s policy (4/24h); I follow hers.
* **Her last point** (a "fix" that read the compliance script verbatim: nobody hallucinated, nobody stayed): choosing a fix by safety score alone picks exactly that prompt. The fix pipeline reverts to the last prompt we have *measured* as safe **with its engagement** and only asks the LLM when none exists; after every prompt change an engagement-regression check runs (>= 6% drop, z <= -3) and reverts. I saw this happen once during development (a false-positive incident picked P5, engagement -14%) — that is why the detector now uses an exact tail and the candidate order exists.
* **Reply:** *"Anita — two requests a day at most, none before 11:00, each one batched; you'll see a single screen with what's being tested, what's winning, and what we need from you. Your own edits are never overwritten: if you change a prompt or a voice, we leave that setting alone for 48 hours and re-test from your choice. Voice, welcome message and script changes only happen with your approval. The exception is operational: if a caller ID gets spam-flagged we rotate it straight away (within your policy) and list it on your screen — waiting would mean hours of unanswered calls. We won't pick a 'safe' script blindly; we go back to the last one we've measured keeping customers on the line."*

### 05 Product (Tara Kulkarni)
* **Accepted:** separate KPIs everywhere; **one cohort object** (`feat.Slice`, canonical id) used by detection, experiments, config and the view; proposals carry impact/diff/affected leads/dry run, the queue is ranked by impact; **an expired proposal is a rejection** — it is re-queued from fresh data, never resubmitted (`replan` audit events); only what happened counts as evidence (stats are keyed by the variant and config version *recorded on the call*, so an arm that was never approved or never ran contributes nothing; verified by `mid_flight: next_campaign_only` handling); a winner updates only its workflow.
* **Open questions she asked me to settle:** minimum cohort size = **>= 8% of connected calls and >= 60 connected calls per arm** to be tested at all, **>= 80 per arm on fresh data** to confirm. Evaluation window = cumulative for stage 1, fresh-since-live for stage 2, last 18 h for decay. Cost: ~1.5 days to first flag on a 1,100-lead/day workflow, ~3 days to a confirmed winner, ~1 in 3 flags rejected (the price of not shipping false winners: §5).
* **Wishlist triage:** consolidated view **built**, caller-ID health **built**, per-person call-time **cut** (not P0: the evidence in §2).
* **Claim to check:** "impact score … must mean something." It does now (expected relative gain in leads concluded per dial, 10% -> 1.0, model-based and labelled as such); an early version often saturated at 1.0, which would have made the ranking meaningless.
* **Reply:** *"Tara — pickup and engagement stay separate everywhere; cohorts are one object with one id (e.g. `age_band=60+|lang=hi`) used by detection, tests and your supervisor view. Minimum for a test: 8% of calls and 60 connected calls per arm; a winner must replicate on 80+ fresh calls per arm. That costs ~3 days before a variant goes wide, and about a third of early 'winners' are dropped at that gate — that's the price of near-zero false winners. Expired requests are re-queued with fresh data, never re-sent. Per-person call time is cut: ≤8 attempts per person is too thin; cohort-level timing delivers most of the gain. Impact is expected relative lift in leads concluded per dial."*

### 06 CTO (Aryamann)
* **Accepted:** explainability over raw lift; "the best compute is no compute" (ordinary operation spends **0** credits; an incident spends 6–12, only on validation and — if no measured-good prompt exists — one LLM call); scale is the problem (§8); choose and say no (§2); docs for the person on call (`docs/RUNBOOK.md`); "trust the system's contract over anyone's recollection" — I did, and found the wrong claims listed above.
* **Pushed back on:** nothing; I'd add that the LLM has no decision role at all in this build (it only ever supplies candidate prompts when no measured-good prompt exists), which is stricter than constraint 4 requires.
* **Reply:** *"Aryamann — chosen: caller-ID health, cohort call-timing, welcome/voice tests with two-stage confirmation, the incident pipeline, governance, audit, crash safety. Said no to: per-person timing, interaction effects, cross-tenant fair share. Credits: zero in normal operation, 6–12 per incident. At 10x the first break is the single SQLite writer and then approver attention, not CPU; the plan is sharding by tenant and a global request budget. Known weak spots are in §13, including three changes per run that hidden truth calls harmful."*

## 4. Architecture

```
 simulator (HTTP)                                                       ┌─────────────────────────────────────────┐
 ──────────────                                                         │ controller (one process, SQLite WAL)    │
 /events/chunks ───► ingest.py ──(1 tx / chunk, idempotent)──► exp_stats, pick_stats, did_stats, qa_stats, leads, ledger
 /sim/advance ◄──┐          │                                            │            │ (4 small aggregate tables, numpy group-by)
                 │          ▼                                            │            ▼
                 │   handle_events (config_changed by supervisor → lock + epoch reset)
                 │          │
                 │   per workflow, every 15 sim-min:
                 │     incidents.py   QA-flag tail test · validate-on-edit · fix pipeline ──┐
                 │     did.py         peer test · pickup-proportional shares · probation ───┤
                 │     experiments.py stage-1 flag → stage-2 confirm · eliminate · decay ───┤  targets in wf_state (JSON)
                 │     schedule.py    cohort discovery · per-cohort hour weights ───────────┘
                 │                         │
                 │                    planner.compose()  ← the ONLY renderer of targets into a config
                 │                         │ desired config (≤20 cohorts) vs live config
                 │                    route(): ops part → auto-apply (if policy) │ rest → ONE batched approval
 /workflows/*/config  ◄───────────────────┘                                      │
 /approvals, /apply  ◄──────────── governance.py (intent row → HTTP → reconcile) ──┘
 /validation, /llm   ◄──────────── incidents.py (credits, capped)
                                       audit (append-only) ──► view.py: why · lead · view · status · /api/*
```
**State** is entirely in `state/*.db`: aggregates (`exp_stats` by 3 h slot × config version × cohort × feature cell × welcome × voice; `pick_stats` hour × (lang,tier,age); `did_stats` 15 min × caller; `qa_stats` 15 min × prompt), `leads` (feature code + concluded), `proposals` (lifecycle + intent), `audit`, `config_versions`, `wf_state` (decision targets/stages/locks), `incidents`, `alerts`, `loop_metrics`.
**On restart:** reload lead arrays and cohort codes from SQLite; re-read the world (`/sim/state`, configs, approvals); ingest any chunk not in `chunks`; reconcile `submitting` proposals against the simulator; continue the same tick. Nothing durable is lost; only the current tick's decision is recomputed (idempotent: in-flight proposals suppress duplicates; deterministic ids).

## 5. The learning loop

* **Cohorts.** Two kinds of splits, both data-driven. (a) *Schedule:* for `age_band`, `city_tier`, `lang`, agglomerate feature values whose hour-of-day pickup **shape** is statistically indistinguishable (sum of squared log-ratio differences / variance vs a chi-square-like threshold, 3 sigma); a feature becomes a cohort split only if some pair differs. (b) *Treatment:* single-feature slices with a stage (candidate/winner/eliminated) create splits. Config cohorts = product of per-feature groups, capped at 20 (least important feature dropped first); the biggest block is left as `default`. Minimum: a slice must hold >= 8% of connected calls. This is how fragmentation is avoided; before the *hierarchy rule* (below) cohorts fragmented into many tiny cells (e.g. `city_tier=1|lang=en|value_band=high`).
* **Experiment design.** Welcome and voice are analysed independently (per-call independent draws). Exploration starts with control 40% / unknown arms sharing 60%, proposed once per workflow (approval). **Hypotheses** = (slice, option) vs control, slices = ALL + each single feature value. **Stage 1:** >= 60 connected calls per arm, stratified lift (Mantel-Haenszel over (config version, cohort) strata — without stratification the lift is confounded with where a variant was sent: Simpson, covered by a test), one-sided z >= max(2.5, z_{alpha=0.2/m}) over the m tests of that checkpoint, relative lift >= 4%, not fading, pickup guardrail not significantly harmed. **Stage 2:** the candidate gets 40% (control 30%), and is confirmed only on **fresh data after that allocation went live**: z >= 1.28, lift >= 2%, >= 80 per arm. Very strong stage 1 (z >= 3.8, lift >= 6%) promotes directly. **Hierarchy rule:** a slice gets its own decision only if it differs from what already holds for the whole population (a global winner is not re-discovered in each sub-slice). **Elimination:** z <= -2 and lift <= -3% stops sending traffic to that arm in that slice (limits exploration harm). Winners keep a 20% control holdout so lift keeps being measured.
* **Winner, multiple comparisons, false-winner rate.** Deterministic z-tests; no LLM. Measured by simulation under a true null (A/A, n=150 per arm, 24,000 tests): stage 1 flags 0.23% of hypotheses, **promotes 0.025%**; for a family of 85 tests that is P(any flag) ~18% and **P(any false winner) ~2% per checkpoint** (a bound over ~8 checkpoints: <= ~15%, tests are positively correlated so reality is lower). Observed in the real runs: public_01 12 flagged -> 3 rejected on fresh data -> 5 promoted; public_02 12 -> 6 rejected -> 3 promoted; load 59 -> 23 rejected -> 29 promoted (3 via the strong path). Rejections at stage 2 are the false alarms being caught.
* **Decay and rollback.** For every winner: lift is compared in the last 18 h vs fresh data since promotion. **Decay = the arm's own engagement rate fell (z <= -2.5, large n so well powered) while the control's did not (z >= -1.5) and the arm-vs-control lift dropped below half of its earlier value**, two consecutive checks 3 h apart. A decayed option is banned workflow-wide for the epoch (otherwise it is re-flagged slice by slice from stale pre-decay data — that bug showed up as repeated spurious candidates and a re-promotion in an early run) and the rollback is an urgent proposal (auto only where policy permits). Promotion also refuses anything already fading (last 36 h and early-vs-late halves). Measured: public_02 W3 decays after ~2 days of exposure; flagged D3 18:00, effective by ~D5 (approvals + stale re-proposals add ~1 day); public_03 (5-day horizon): by day 4 the decaying W3 still held 70% of its population in 2 of 4 renewal workflows and had fallen to 51% / 30% in the other two (§13).
* **Call time and caller ID.** History seeds hour-of-day pickup shapes per cohort; weights = (pickup(h) / mean)^2, floor 0.15 of max, shrunk to the pooled hour shape with 30 pseudo-dials, recomputed daily, **committed only if the table moved >= 25%** (hysteresis: noise never creates a proposal). Caller ID: a DID is *flagged* only on a **step** — pickup <= 70% of its peers at z <= -3.5 over 3 h **and** >= 80% of peers over the preceding 6–30 h (a merely overloaded DID declines gradually and is handled by reweighting; flagging it caused violent rotate-and-return cycles, the main source of "harmful" verdicts early on). Shares are proportional to (shrunk relative pickup)^2, floor 4%; unmeasured DIDs capped at 10% (25% when one DID carries >= 80% of traffic, which is the load-concentration case, e.g. KYC at 100% D1); flagged DIDs 2%; after 12 h they go on **probation** (4% cap) so a recovery can be seen. A rebalance must be predicted (fresh 8 h pickups) to raise current health by >= 3% (6% when it costs an approver's attention). Exploration is automatic: every DID is probed at a floor share.
* **Guardrail KPI.** Per arm: pickup lift is tested alongside engagement; an arm is blocked if pickup is significantly harmed (z <= -2.5, lift <= -guardrail). Per applied change, +24 h verification compares the guardrail KPI before/after and raises a ticket at z <= -3 and a relative drop > the allowed drop. For pickup-primary workflows, once engagement is half-way to its allowed drop vs pre-D0 (z <= -1.65) "defence mode" starts welcome/voice tests. **Honest result:** in some public_02 runs `nw_kyc` ended at -2.7…-3.3% engagement vs the 2% allowed (§11); defence mode triggered only on D10 in the worst one.

## 6. Governance

**Proposal state machine** (`governance.py`):
```
 submit ─► submitting ──(sim accepts)──► pending ─┬─► approved ──apply 200──► applied
              │ 409 stale_base / 422               ├─► rejected  (reasons visible only now) ─► cool-down, re-plan from FRESH data
              ▼                                    ├─► expired   (no decision before TTL)   ─► re-plan from fresh data (never re-sent)
        stale | invalid                            └─► stale     (live version != base_version: supervisor edit or our own auto-apply)
                       approved ─► lapsed (apply window missed; poll every 15 min makes this ~impossible, alert at 90 min)
 auto route: submitting ─► applied | refused (403, ticket) | stale
```
* **Batching & timing.** All pending changes of a workflow become **one** request (diff lists every dimension). Routine requests only inside `[max(approver_start, tenant quiet hour), approver_end - ceil(max_latency/60))` (renewal 10–15, KYC 10–11, Kestrel 11–17) so the decision lands inside approver hours; never at night; at most `min(policy limit - 1 reserved, tenant pref)` per 24 h; >= 5 h apart; one in flight; a routine change must move an allocation >= 12% (TV) or change an experiment stage or gain >= 1% pickup in replay, else "below materiality" and it waits. Rejections cool down 6 h (24 h for "fatigue"). **Urgent lane** (incident fixes, decay rollbacks): ignores the window, the spacing and the one-in-flight rule, and uses the reserved slot. A routine request that goes stale because of an urgent one is simply re-proposed.
* **When I auto-apply and when I voluntarily don't.** Auto-apply: operational dims (schedule when cohorts are unchanged, caller_id) where `auto_apply.enabled`, the dims are allowed and the shift fits `max_shift` (I step toward the target at 0.9 x `max_shift` per apply; there is no rate limit on auto-apply). I never call it speculatively: the simulator logs a 403 and it looks like a bug. **Voluntarily not**: customer-facing dims even where the policy allows it (Compliance + Anita) — approval path. **Emergency exception**: a prompt revert or a decay rollback may auto-apply if the policy permits (none of the public policies do for prompts).
* **Impact / affected leads / dry run** (`explain.py`). *Impact* = expected relative gain in leads concluded per dial = (1+Δpickup)(1+Δengagement)-1, 10% -> 1.0, from replay and measured arm lifts (unmeasured arms count 0, candidates count half); exploration ≈ 0 and says so. *Affected leads* = leads inside their cadence window, not concluded, whose effective treatment (cohort, W/V/D distribution, hour table) differs old vs new, from our own lead table (+ `future_leads_per_day` in the diff). *Dry run* = model replay on pre-D0 history: expected pickup per dial under old vs new weights, DID health from fresh pickups, engagement from measured lifts; labelled "model-based replay, not a counterfactual measurement". After +24 h the realised before/after is logged next to the prediction.
* **Supervisor edit mid-experiment** (public_02, `nw_renewal` voice at D5 15:00): the edit is a first-class audit event with a diff; the dimension is locked 48 h (compose copies the live values for it); the experiment epoch restarts with the supervisor's value as control and only data from the new version counts; any in-flight request is marked stale and re-proposed; after the lock the new proposal says why ("dimensions [...] recently edited by the client supervisor are left untouched" / the new control).

## 7. Incidents

* **Hallucination spike.** Population test on connected calls under the live prompt: exact one-sided binomial tail <= 1e-5 vs the known-good baseline flag rate (the lowest measured rate among prompts with >= 300 connected calls and a rate <= 2.5%), on a 2 h **and** a 6 h window (6 h catches mild regressions), flag rate >= 1.6x baseline and +1 pp, on 2 consecutive ticks. **Expected false positives:** ~1e-5 per effective window x ~12 effective windows/day -> ~1e-4 per workflow-day (windows overlap, so I treat the two-tick rule as one test; the first implementation used a normal approximation and raised a false incident on 7 flags out of 105 calls, p ~ 5e-4 — hence the exact tail).
* **Fast path: validate on edit.** A supervisor prompt change triggers one validation run of the new prompt (6 credits, n=600) immediately, because waiting for ~200 bad calls costs hours; `pass_rate < policy minimum` opens the incident at once. (Limit: a mildly bad prompt such as P4, ~2.5% hallucination, passes the 0.97 threshold ~60% of the time and is only caught by the live test after ~1000–1500 connected calls, roughly a day.)
* **Picking the fix.** (1) The last prompt we have *measured* as safe with its engagement (history/live) -> 1 validation run (6 credits), propose it. (2) Only if none, ask the LLM (3 credits) for candidates, validate each (6), take the highest pass rate >= the policy minimum. The LLM never decides; unmeasured candidates can be engagement-destroying (P5, "near-verbatim script": -14% engagement), so every applied fix is followed by an engagement-regression check that reverts.
* **Credits.** public_02: 12 total (two validation runs: the supervisor's P2 and the revert target P1), LLM 0. Cap 40 per incident; alert if < 10% left while one is open.
* **Verification.** After apply and >= 90 min / 100 connected calls: flag-rate tail vs baseline *and* engagement vs the 3 days before; `resolved` or retry the next candidate (max 3), else page.
* **Timeline, public_02 `ks_cancel` (one run):** D3 11:30 supervisor ships P2 (hallucination 5.5%) -> 11:45 tick processes the edit, validates P2: pass_rate 0.965 < 0.97 -> incident opened, page alert -> 11:45 validates P1 (0.99), urgent proposal sent (a routine request already in flight is left to go stale) -> ~13:40 approver decides -> next tick applied (v13) -> ~16:45 verified (flag rate 0.77% vs 1.5% baseline). Grader MTTR over my last runs: **75, 90, 105, 120, 135, 150 min** (floor = the approver's 20–120 min latency). Before the validate-on-edit path it was 315 min, and one run took 225 min because the urgent fix queued behind a routine request already in flight (urgent now bypasses that rule). Hallucinated calls 431 -> 166 (baseline 431).

## 8. Scale (public_03_load, 12 workflows, 650k dials/day, 5 days)

Measured by the controller's own `loop_metrics` (`reports/load_test.md`), one process on Apple silicon (no Linux/4-vCPU box was available to me; the process is single-threaded so 4 vCPU changes little, memory ~676 MB is well under 8 GB):

| | |
|---|---|
| ingest | **135k events/s live**, 144k/s bootstrap (5.5 M history events in 38 s) |
| decision lag | tick (ingest + 12 workflows) avg 237 ms, p95 1.1 s, p99 1.8 s, max 2.3 s (the 00:00 tick: daily partition/schedule refresh) |
| wall per simulated day | **~41 s** end to end including the simulator (~35 s of it); SRE target 600 s |
| peak RSS / store | 676 MB / 81 MB |
| max chunk backlog | 1 |

**What breaks first at 10x** (120 workflows, 8.5 M events/day): (1) the **single SQLite writer** — ingest becomes ~60 s per simulated day on one core: fine for the simulator's 15-min tick but not for a real-time deadline with headroom; shard by tenant group (one process, one DB each; no cross-workflow state exists except credits). (2) **Peak memory** — a chunk is read as a list of lines (~110 MB per 250k events) and lead arrays are per workflow; stream-parse instead of list, and bound arrays by the cadence window. (3) The **midnight tick** (2.3 s at 12 workflows -> ~25 s): stagger per workflow. (4) **Approver attention and credits are the real limit**: ~60–70 requests in 5 days for 12 workflows -> at 100s of workflows it needs a global request budget per tenant and a credit arbiter (C15, cut). CPU is not the constraint. Partitioning: by tenant (approvers and policies are per tenant), workflows within a tenant stay in one process so the shared tenant preferences/budget are local.

## 9. Reliability

* **Method:** `scripts/crash_test.py` runs public_02 with `./run.sh` as the supervisor, injects **4 hard kills** (`os._exit(137)` = SIGKILL semantics, no cleanup) at: `intent` (intent row committed, HTTP not yet sent), `sent` (simulator accepted, answer not recorded), `apply` (simulator applied, not recorded), `ingest_mid` (inside the chunk transaction), and **2 external SIGKILLs** (D2 11:00, D6 12:00). 6 restarts.
* **Result (`reports/crash_recovery.md`):** run finished with exit 0; every one of 8 invariants PASS (no request sent twice, no change applied twice, every applied approval recorded, nothing left `submitting`, nothing lapsed because of a restart, no refused auto-apply, no open proposal that disagrees with the simulator); outcome 0.435 conclusion rate vs 0.434 uninterrupted. 2 intents were reconciled from the simulator, 1 was found never to have reached it and was abandoned (state `abandoned`, nothing sent blindly).
* **What is lost on a kill:** nothing durable. The HTTP response in flight, and the current tick's computed-but-uncommitted decisions, are recomputed (decisions are idempotent because an in-flight proposal suppresses a second one and ids are deterministic).
* **Idempotency keys:** `proposal_id = <store nonce>-<workflow>-<a|u><seq>`, seq allocated inside the same transaction as the intent row; `409 duplicate_proposal_id` is read as "it did arrive" and resolved by lookup. Chunks: `chunks(chunk)` primary key committed with the aggregates. A state file from another world is archived, never reused.
* **Simulator errors:** reads retry 8x with exponential backoff (connect/5xx); `advance` is never retried blindly (re-read state instead); `409 stale_base_version` -> mark stale, re-propose later; `410 lapsed` -> re-propose; `403 auto_apply_not_permitted` -> a ticket (I check policy first so this should never happen; it never did); `402 out_of_credits` -> alert; `422 no_change` is swallowed, other 422s open a ticket (it means our config was invalid).
* **Honest gap:** the test kills the controller, not the simulator, and not the disk (WAL + `synchronous=NORMAL` survives process death, not power loss).

## 10. Audit & observability

**"Why did workflow X change at time T, who approved it, what happened next?"** — real output from the public_02 run:
```
$ python -m controller why --db state/public_02_incidents.db --wf ks_cancel --at "D3 17:00"
WHY  ks_cancel at D3 17:00
  config in effect: v13  set D3 14:00 by controller_approved  dims=['prompt_version']
  proposal e9c4-ks_cancel-a0022  route=approval kind=incident_fix  approval=apr_00013
    summary   : Replace prompt P2 with P1 (hallucination incident)
    impact    : 0.35   affected leads: 0
    rationale : unsafe_prompt_edit on P2: ... validation_pass_rate=0.965, threshold=0.97 ... Proposed P1 (validation pass_rate 0.99 on n=600).
    evidence  : {"validation_run_id": "val_00002"}
  lifecycle (who did what, when):
    D3 11:45  proposal_intent      [controller] submitting: Replace prompt P2 with P1 (hallucination incident)
    D3 11:45  proposal_submitted   [controller] approval apr_00013 pending (expires 2027-01-07T23:45:00+05:30)
    D3 14:00  proposal_approved    [approver] apr_00013 approved at 2027-01-07T13:41:00+05:30; apply by 2027-01-07T15:41:00+05:30
    D3 14:00  approval_applied     [controller_approved] v13 applied dims=['prompt_version']
    D4 14:00  verification         [controller] v13 +24h: pickup 0.0358, engagement -0.0315
  what the controller had seen before proposing:   (candidates flagged, caller-ID flag/rotation, supervisor_edit at v12, prompt_edit_validated ...)
```
**"Why did this customer hear this variant?"** (Compliance #3): `python -m controller lead --wf nw_renewal --lead nw_renewal:l00014303 --day 1` returns, per call: time, config version, cohort, welcome/voice/caller-ID/prompt actually used, and for each dimension **the config version, actor, proposal id and approval id that last set it** (e.g. voice V3 <- v3, `controller_approved`, `apr_00001`, D0 13:00). Then `why --at` expands that proposal.
**Loop health without logs:** `python -m controller status` -> ingest events/s, tick avg/p95/max, decisions/h, backlog, peak RSS, credits spent/left, proposals by state, recent alerts. `controller view` / `http://127.0.0.1:8701/` -> the supervisor screen.

**Page** (a human must act before morning): `incident_*` (a prompt is hurting customers), `incident_no_fix`, `credits_exhausted_during_incident`, `approved_not_applied` (>90 min: it lapses at the apply window — the loop is stuck), and an external page if `run.sh` gives up after 50 restarts. **Ticket**: `did_flagged_*` (auto-rotated), `guardrail_*` / `guardrail_erosion`, `invalid_proposal`, `auto_apply_refused`, `ingest_backlog`. **Never page**: approver rejections/expiries, stale re-proposals, candidates failing to replicate, pickup flaps.

## 11. Results

**public_01_dev** (`reports/public_01_dev.md`)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured |
|---|---|---|---|---|---|---|---|
| ks_cancel | 0.389 | +13.8% | 0.260 | +6.4% | 0.522 | +6.0% | 34% |
| nw_kyc | 0.460 | +25.4% | 0.307 | +26.7% | 0.583 | **-2.4%** | 0% (pickup-primary) |
| nw_renewal | 0.451 | +8.4% | 0.273 | +3.4% | 0.565 | +4.4% | 18% |
| **ALL** | **0.425** | **+14.2%** | 0.274 | +9.1% | 0.550 | +3.8% | 19% |

26 changes applied: 9 beneficial, 15 neutral, **2 harmful** (`nw_kyc` v3 caller-ID via approval, DID health -10.3%; `nw_renewal` v10 welcome/voice exploration, engagement -3.6% at apply). 23 approval requests (20 approved, 3 "judgement call" rejections), 7 auto-applies, 1 stale-base conflict, 0 lapsed, 0 credits. **`nw_kyc` engagement is -2.4% vs a 2% guardrail: a small breach** (same mechanism as §11 below).

**public_02_incidents** (`reports/public_02_incidents.md`)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured |
|---|---|---|---|---|---|---|---|
| ks_cancel | 0.374 | +58.6% | 0.255 | +40.5% | 0.502 | +7.3% | 41% |
| nw_kyc | 0.504 | +34.9% | 0.312 | +33.2% | 0.606 | -0.4% | 0% |
| nw_renewal | 0.472 | +9.0% | 0.294 | +12.1% | 0.536 | **-3.4%** | -15% |
| **ALL** | **0.434** | **+31.3%** | 0.279 | +27.3% | 0.537 | +0.7% | 17% |

41 applied: 16 beneficial, 23 neutral, **2 harmful** (`nw_kyc` v3 caller-ID via approval, DID health -9.6%; `nw_renewal` v14 voice exploration, engagement -3.5%); hallucinated calls 431 -> 166; incident MTTR 135 min; credits 12 (2 validation runs, 0 LLM); flagged `ks_cancel` D2 share 7.9% on the flag day -> ~2% afterwards.
**Honest reading of this table:** `nw_renewal`'s *primary* KPI ends **3.4% below baseline**. Its supervisor sets the default voice to V2 on D5 (mid-experiment); our re-learning after that edit explores arms that are worse than that incumbent and the 4.5 remaining days are too few to recover. Across my last six runs of this scenario `nw_renewal` engagement ranged from -4.4% to +0.2% and `nw_kyc` engagement from -3.3% to -0.4%.

**public_03_load** (`reports/public_03_load.md`): ALL conclusion rate **+17.8%** (pickup +10.4%, engagement +4.7%), 81 changes (36 beneficial, 39 neutral, **6 harmful**), 0 credits. KYC workflows +20–30% conclusion with engagement -0.9…+3.4% (guardrail respected in all four); renewal/cancel engagement +4.6…+8.1%.

**Where I helped:** caller-ID (the big one: KYC runs every dial through one DID at 100% load; spreading it is +17–33% pickup), call-time per cohort, welcome/voice for engagement-primary workflows, the hallucination incident (431 -> 166 hallucinated calls), the spam-flagged DID.
**Where I was harmful, and why:**
 1. `nw_kyc` **caller_id via approval** (public_01 v3 -10.3%, public_02 v3 -9.6% instantaneous DID health at apply). The approval path decides now but applies hours later; KYC is `next_campaign_only` so new DIDs have little data when the move is decided, and the load stock moved in between. The gate (predicted gain >= 6%, shrinkage 100 pseudo-calls) removed most of it; this one remains.
 2. Welcome/voice **exploration** steps (public_01 `nw_renewal` v10 -3.6%, public_02 `nw_renewal` v14 -3.5% engagement at apply): sending 60% of a workflow's traffic to untested arms is negative whenever the untested arms are worse than the incumbent (here the supervisor had just set voice V2 as the default, V4/V1 were worse). That cost is inherent to exploration; reducing the share (more control) trades harm against speed.
 3. Load run: 6 harmful of 81, same two kinds.
**`nw_kyc` engagement guardrail:** in the final public_02 run it holds (-0.4%) but in earlier runs it was breached (-2.7%…-3.3% vs 2% allowed): boosting pickup brings repeat callers who already declined, and engagement is a rate per connected call. Defence-mode experiments only start once the erosion is visible, which was D10 in the worst run. I would start them from D0 with a small exploration share.
**Engagement gap captured** is 15–45% on engagement-primary workflows (−15% for the public_02 `nw_renewal` case above) — a lot is left on the table because most slice effects only become significant after ~4–6 days of traffic at these volumes.

## 12. Trade-offs & rejected alternatives

| Option | Why considered | Why rejected / what instead |
|---|---|---|
| Thompson sampling / bandit allocation | Optimal exploration, no stage rules | Needs allocation changes at tick speed; every config change is an approval (latency hours, 2/day) — so allocation is **piecewise stages**, which is also explainable to a compliance head |
| Pooled arm comparison across cohorts | Simpler, more data | Simpson's paradox once cohort allocations differ — stratified MH instead (test included) |
| Per-slice "winner" for every feature value | Finds heterogeneity | Fragmented cohorts (30+) and re-discovered the same variant in each slice; replaced by the hierarchy rule |
| LLM picks the fix / scores variants | The brief's "AI" story | Constraint 4; also unsafe (P5). LLM only supplies candidates, and only when no measured-good prompt exists |
| Normal-approx z for flag spikes | Simple | False incident on 7/105 flags; exact binomial tail |
| One flag on a DID → rotate hard | Fast incident response | Overload looks like a flag; cycles of rotate/return were the main harm source; now step-detect + probation |
| Per-person preferred hour | The headline for the summit | <= 8 attempts per person; noise; cut |
| Postgres/Redis for state | SRE already runs them | SQLite is zero-ops, single file, crash-safe, ~135k events/s; Postgres adds a hop on the hottest path |
| Auto-apply customer-facing changes where policy allows | Speed (Sales) | Compliance/Kestrel asked for named approval; the cost is ~2 h latency per stage |
| Validate every supervisor prompt edit | Immediate evidence | Cost 6 credits per edit; chosen because MTTR was dominated by detection (315 -> ~100 min) |
| Fixed 1 request/day | Simple, safe for approvers | Added ~24 h to every stage; now 2/day with spacing and a reserved urgent slot |

## 13. Known weaknesses

1. **Exploration is not free**: 2–6 changes per scenario are judged harmful (§11), and in public_02 `nw_renewal` ends below baseline on its primary KPI; I trade speed against that and have not found a setting that removes it.
2. **Approval-path caller-ID moves** (KYC) are decided on data that is hours old and applied later; the remaining -9.7% cases are this.
3. **`nw_kyc` engagement guardrail breached in some runs of public_02** (-2.7%…-3.3%; fine in the final run): pickup gains dilute engagement; defence mode starts too late.
4. **Decay rollback is slow on short horizons**: detection ~1–1.5 days after the decay becomes visible, plus approval and stale re-proposals; on public_03 two of four decaying renewals still sent ~70% of their traffic to the decaying variant at the end of day 4. The detector needs >= 100 arm calls in both windows.
5. **Slice power**: small slices (e.g. 60+ with 4 voices) need 4–6 days; effects that exist only there are found late or not at all on 8-day scenarios. A Bayesian hierarchical model would borrow strength; I did not build it.
6. **Mild hallucination (P4-like, ~2.5%)** passes validation ~60% of the time and is only flagged by the live test after ~1 day.
7. **No welcome x voice interaction**, no prompt experimentation for engagement (the engagement effect of a prompt cannot be measured within a week at these volumes, and validation measures safety only).
8. **Caller-ID reweighting is pickup-proportional**, a lagging heuristic; a fitted load-response model (dial stock with decay) would be the principled version and would avoid more harmful verdicts.
9. **The ledger is off at load scale** (cost), so the per-customer auditor answer exists only for small deployments; aggregates by cohort/variant/version are always kept.
10. **Tests of the statistics are simulation-level**; the full loop is tested end-to-end on a 3-day world plus the crash test, not property-tested.
11. **Single process, single writer**; not tested on a real 4-vCPU Linux box.
12. **Tenant preferences are matched by tenant id** (`TENANT_PREFS`); a hidden scenario with other ids gets the defaults.

## 14. If this went to production at Formi next month

1. **Days 1–3:** shadow mode — run against the live event stream with governance calls stubbed to "log what we would do", compare with what supervisors do. Stand up `controller status` and the page/ticket alerts on the real pager.
2. **Days 3–5:** replace the 15-simulated-minute tick with a stream consumer (Kafka/SQS offsets in place of chunk numbers — the idempotency design is the same) and move the store to Postgres if one operational surface is preferred.
3. **Week 1:** enable the operational lane (caller-ID, call-time) for two friendly workflows on auto-apply within policy; keep customer-facing on approvals.
4. **Week 1–2:** fix the known harm sources first (§13: caller-ID load model; defence mode from D0; smaller exploration share), measured against the "harmful" count not just lift.
5. **Week 2:** enable welcome/voice tests on one tenant with a named approver; turn on the per-customer ledger with retention (IRDAI).
6. **Week 2:** shard by tenant, add the credit/approver-attention arbiter (C15), and write the weekly Northwind summary from the audit store (`why`/`audit` queries already produce it).

**Note on process and authorship (required by §10 of the brief):** this controller was built with an AI coding assistant (Claude Code) in a single long session, iterating against the three public scenarios; I (the human submitter) am responsible for being able to defend every decision above in the review. The commit history, the hours figure, and the name/date fields at the top are for you to complete honestly; I cannot supply them.
