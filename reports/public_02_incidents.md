# Grade report — public_02_incidents

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.374 | +58.6% | 0.255 | +40.5% | 0.502 | +7.3% | 41% | 78 (348) |
| nw_kyc | 0.504 | +34.9% | 0.312 | +33.2% | 0.606 | -0.4% | 0% | 27 (29) |
| nw_renewal | 0.472 | +9.0% | 0.294 | +12.1% | 0.536 | -3.4% | -15% | 61 (54) |
| ALL | 0.434 | +31.3% | 0.279 | +27.3% | 0.537 | +0.7% | 17% | 166 (431) |

## Changes applied by the controller

41 applied — beneficial: 16, neutral: 23, harmful: 2

- nw_renewal v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.035 → **beneficial**
- ks_cancel v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.018 → **neutral**
- ks_cancel v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.020 didΔ=+0.000 → **beneficial**
- nw_kyc v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.231 → **beneficial**
- nw_renewal v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.039 → **beneficial**
- ks_cancel v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.029 → **beneficial**
- ks_cancel v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.001 → **neutral**
- nw_renewal v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.009 → **neutral**
- nw_renewal v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.038 didΔ=+0.000 → **beneficial**
- ks_cancel v6 (controller_approved) dims=['cohorts', 'welcome_message', 'schedule'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- nw_kyc v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.096 → **harmful**
- ks_cancel v7 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.006 → **neutral**
- ks_cancel v8 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.155 → **beneficial**
- ks_cancel v9 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.098 → **beneficial**
- nw_renewal v6 (controller_approved) dims=['cohorts', 'welcome_message', 'schedule'] engageΔ=+0.025 didΔ=+0.000 → **beneficial**
- ks_cancel v10 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v7 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- ks_cancel v12 (controller_approved) dims=['prompt_version'] engageΔ=+0.102 didΔ=+0.000 → **beneficial**
- nw_kyc v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.153 → **beneficial**
- nw_renewal v8 (controller_approved) dims=['welcome_message'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- ks_cancel v13 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=-0.002 didΔ=+0.000 → **neutral**
- nw_renewal v9 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.001 → **neutral**
- ks_cancel v14 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v11 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.004 → **neutral**
- nw_renewal v12 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.002 → **neutral**
- ks_cancel v15 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=-0.001 didΔ=+0.000 → **neutral**
- nw_renewal v13 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v16 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.001 didΔ=+0.000 → **neutral**
- ks_cancel v17 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.001 → **neutral**
- nw_renewal v14 (controller_approved) dims=['voice'] engageΔ=-0.035 didΔ=+0.000 → **harmful**
- ks_cancel v18 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.006 → **neutral**
- ks_cancel v19 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.006 → **neutral**
- ks_cancel v20 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.002 → **neutral**
- ks_cancel v21 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v15 (controller_approved) dims=['voice'] engageΔ=-0.002 didΔ=+0.000 → **neutral**
- ks_cancel v22 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.033 → **beneficial**
- ks_cancel v23 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.024 → **beneficial**
- ks_cancel v24 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.034 → **beneficial**
- ks_cancel v25 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.033 → **beneficial**
- ks_cancel v26 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.031 → **beneficial**
- ks_cancel v27 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.008 → **neutral**

## Governance

```
{
  "approval_requests": 28,
  "decisions": {
    "rejected": 4,
    "approved": 22,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 4
  },
  "applied_via_approval": 19,
  "auto_applied": 22,
  "auto_apply_rejected": 0,
  "apply_conflicts": 3,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 3,
    "nw_kyc": 1,
    "ks_cancel": 2
  }
}
```

## Incidents

```
[
  {
    "type": "did_flagged",
    "workflow": "ks_cancel",
    "at": "D2 10:00",
    "flagged_did_share_by_day": {
      "2": 0.079,
      "3": 0.021,
      "4": 0.023,
      "5": 0.017,
      "6": 0.02,
      "7": 0.019,
      "8": 0.028,
      "9": 0.033
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "ks_cancel",
    "at": "D3 11:30",
    "mttr_minutes": 135,
    "fixed_by": "controller_approved"
  },
  {
    "type": "supervisor_edit",
    "workflow": "nw_renewal",
    "at": "D5 15:00"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "3": 0.463,
    "4": 0.477,
    "5": 0.472,
    "6": 0.648,
    "7": 0.756,
    "8": 0.773,
    "9": 0.768
  }
}
```

Credits used: 12 · validation runs: 2 · LLM fix calls: 0