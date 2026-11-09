"""All tunables in one place. Nothing here is derived from scenario truth; every number is a policy choice
documented in SUBMISSION.md §5."""
from __future__ import annotations

TICK_MIN = 15

# ---- statistics
MIN_CONN_PER_ARM = 60         # connected calls per arm (in a slice) before an arm is even tested
Z_FIRST = 2.5                 # stage-1 one-sided z to flag a candidate winner
MIN_LIFT = 0.04               # ...and a practical-significance floor (relative engagement lift)
Z_CONFIRM = 1.28              # stage-2 one-sided z on FRESH data only (post-flag)
MIN_CONFIRM_LIFT = 0.02
CONFIRM_MIN_CONN = 80
Z_ELIMINATE = 2.0             # arm clearly worse than control in a slice -> stop sending traffic
MAX_ELIM_LIFT = -0.03
MIN_SLICE_SHARE = 0.08        # a slice needs >= 8% of connected calls to be a hypothesis
EXPLORE_CONTROL_SHARE = 0.40  # control share per dim while exploring
CAND_SHARES = (0.40, 0.30)    # (candidate, control) while confirming; rest shared by unknown arms
WINNER_SHARES = (0.70, 0.20)  # (winner, control holdout); rest shared by unknown arms
# decay: winner's recent lift vs control must not fall below this fraction of its promotion lift
DECAY_FRACTION = 0.5          # recent lift must also be below this fraction of the post-promotion lift
DECAY_Z = 2.5                 # z of the winner arm's OWN rate falling (large n), control must not be falling too
DECAY_MIN_ARM = 150
DECAY_MIN_CTRL = 40
DECAY_WINDOW_H = 18
DECAY_MIN_N = 100             # connected calls per arm, in both the recent and the reference window
DECAY_CONFIRM_CHECKS = 2
SLOT_MIN = 180                # time resolution of exp_stats

# ---- schedule
HOUR_GAMMA = 2.0
HOUR_FLOOR = 0.15
SCHED_MIN_CHANGE_L1 = 0.25    # re-propose hour tables only if they moved this much (normalised L1)
PROFILE_DAYS = 21

# ---- caller id
DID_WINDOW_H = 24
DID_GAMMA = 2.0
DID_FLOOR = 0.04
DID_MIN_SHARE_CHANGE = 0.06   # TV below this: don't bother (auto-apply route)
DID_MIN_SHARE_CHANGE_APPROVAL = 0.15  # ...and when it costs an approver's attention
DID_EXPLORE_CAP = 0.10        # max share for a DID we have not measured yet...
DID_EXPLORE_CAP_CONCENTRATED = 0.25  # ...unless one DID carries >= 80% of traffic (load concentration is the risk)
DID_MEASURED_N = 100
DID_MIN_GAIN = 0.03            # a rebalance must be predicted to raise current pickup health by this much
DID_MIN_GAIN_APPROVAL = 0.06
DID_GATE_WINDOW_H = 8
MIN_ALLOC_SHIFT = 0.12        # non-urgent agent-facing proposals must move some allocation by this much (TV)
MIN_PICKUP_GAIN = 0.01
DID_DAMPING = 0.7
DID_REBALANCE_EVERY_H = 6
DID_FLAG_TTL_H = 12
DID_PROBATION_CAP = 0.03
DID_RECOVERY_WINDOW_H = 24     # a probation DID has too few dials for a 3 h window; judge recovery on a day
DID_FLAG_Z = 3.5
DID_FLAG_RATIO = 0.70
DID_FLAG_MIN_N = 60
DID_PEER_PRIOR_N = 100

# ---- hallucination incidents
HALLUC_WINDOW_TICKS = 8       # 2h
HALLUC_MIN_CONN = 100
HALLUC_P = 1e-5                # exact one-sided binomial tail needed to open an incident
HALLUC_Z = 3.5                 # (kept for reporting)
HALLUC_MIN_RATIO = 1.6
HALLUC_MIN_ABS = 0.01
HALLUC_CONSEC = 2
VALIDATION_N = 600
INCIDENT_CREDIT_CAP = 40
CREDIT_RESERVE_FRAC = 0.35    # never spend the last 35% on anything but incidents

# ---- governance behaviour
SUBMIT_AT_APPROVER_START = True
DEFAULT_MAX_REQ_PER_DAY = 2   # self-imposed ceiling of batched requests per workflow per day
MIN_SPACING_H = 5             # ...and never two non-urgent requests closer than this
REJECT_COOLDOWN_H = 6
FATIGUE_COOLDOWN_H = 24
AUTO_APPLY_CUSTOMER_FACING = False   # welcome/voice/prompt/cohorts: approval even where policy would permit auto
EMERGENCY_AUTO_ROLLBACK = True       # ...except rolling back a harmful state, if policy permits
URGENT_RESERVE = 1            # request slots per 24h that routine proposals may not use (incident fixes, rollbacks)
GUARDRAIL_DEFENSE_FRACTION = 0.5   # pickup-primary: start protecting engagement once it is this far into its allowed drop
SUPERVISOR_LOCK_H = 48        # after a supervisor edit of a dim, we do not touch that dim for this long

# ---- tenant preferences (client asks that are stricter than platform policy). Keyed by tenant_id.
TENANT_PREFS = {
    "kestrel_finance": {"max_req_per_day": 2, "hard_max_req_per_day": 2, "no_requests_before_hour": 11,
                        "notify_before_change": True},
    "northwind_life": {"max_req_per_day": 2, "no_requests_before_hour": 10, "weekly_summary": True},
}
DEFAULT_TENANT_PREFS = {"max_req_per_day": 2, "no_requests_before_hour": 0}

# ---- impact score: expected relative lift on the primary KPI, scaled so 10% -> 1.0
IMPACT_FULL_LIFT = 0.10

# ---- cohorts
MAX_COHORTS = 20

# ---- per-call ledger (who heard what, why): on below this many dials/day across the platform
LEDGER_MAX_DIALS_PER_DAY = 120_000

RECENCY_H = 36                # candidate/winner must also look good in the most recent window (not already decaying)
