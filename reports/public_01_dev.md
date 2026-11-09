# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.389 | +13.8% | 0.260 | +6.4% | 0.522 | +6.0% | 34% | 61 (57) |
| nw_kyc | 0.460 | +25.4% | 0.307 | +26.7% | 0.583 | -2.4% | 0% | 25 (21) |
| nw_renewal | 0.451 | +8.4% | 0.273 | +3.4% | 0.565 | +4.4% | 18% | 53 (54) |
| ALL | 0.425 | +14.2% | 0.274 | +9.1% | 0.550 | +3.8% | 19% | 139 (132) |

## Changes applied by the controller

26 applied — neutral: 15, beneficial: 9, harmful: 2

- nw_renewal v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.018 → **neutral**
- ks_cancel v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.018 → **neutral**
- nw_renewal v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- ks_cancel v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.254 → **beneficial**
- nw_renewal v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.028 → **beneficial**
- ks_cancel v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.029 → **beneficial**
- ks_cancel v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.003 → **neutral**
- ks_cancel v6 (controller_approved) dims=['cohorts', 'voice', 'schedule'] engageΔ=+0.018 didΔ=+0.000 → **beneficial**
- nw_renewal v5 (controller_approved) dims=['cohorts', 'welcome_message', 'schedule'] engageΔ=+0.010 didΔ=+0.000 → **neutral**
- nw_kyc v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.103 → **harmful**
- ks_cancel v7 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.007 → **neutral**
- ks_cancel v8 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.029 didΔ=+0.000 → **beneficial**
- nw_kyc v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.182 → **beneficial**
- nw_renewal v6 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.006 didΔ=+0.000 → **neutral**
- ks_cancel v9 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.023 didΔ=+0.000 → **beneficial**
- nw_renewal v7 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- ks_cancel v10 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v8 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- nw_renewal v9 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.015 → **neutral**
- nw_kyc v5 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- ks_cancel v11 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.018 didΔ=+0.000 → **beneficial**
- nw_renewal v10 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=-0.036 didΔ=+0.000 → **harmful**
- nw_kyc v6 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.006 → **neutral**
- ks_cancel v12 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=-0.002 didΔ=+0.000 → **neutral**
- nw_renewal v11 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.004 didΔ=+0.000 → **neutral**

## Governance

```
{
  "approval_requests": 23,
  "decisions": {
    "approved": 20,
    "rejected": 3,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 3
  },
  "applied_via_approval": 19,
  "auto_applied": 7,
  "auto_apply_rejected": 0,
  "apply_conflicts": 1,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 2,
    "nw_kyc": 2,
    "ks_cancel": 1
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "3": 0.316,
    "4": 0.362,
    "5": 0.619,
    "6": 0.729,
    "7": 0.707
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0