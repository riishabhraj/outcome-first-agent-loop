# Grade report — public_03_load

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| arav_cancel_2 | 0.395 | +17.1% | 0.264 | +8.5% | 0.527 | +6.2% | 43% | 424 (409) |
| arav_kyc_1 | 0.438 | +22.0% | 0.255 | +18.7% | 0.597 | -0.6% | 0% | 407 (352) |
| arav_renewal_0 | 0.458 | +13.0% | 0.274 | +3.7% | 0.592 | +8.1% | 45% | 471 (408) |
| kest_cancel_2 | 0.389 | +15.4% | 0.263 | +8.3% | 0.522 | +4.6% | 31% | 424 (418) |
| kest_kyc_1 | 0.426 | +19.8% | 0.250 | +16.8% | 0.595 | -0.4% | 0% | 425 (370) |
| kest_renewal_0 | 0.468 | +16.5% | 0.284 | +7.3% | 0.588 | +8.0% | 43% | 431 (403) |
| nort_cancel_2 | 0.391 | +15.2% | 0.262 | +7.3% | 0.529 | +6.1% | 44% | 407 (368) |
| nort_kyc_1 | 0.450 | +26.4% | 0.253 | +17.7% | 0.616 | +3.4% | 22% | 380 (318) |
| nort_renewal_0 | 0.457 | +12.7% | 0.276 | +4.5% | 0.587 | +7.1% | 41% | 429 (419) |
| tide_cancel_2 | 0.397 | +15.5% | 0.263 | +7.8% | 0.531 | +6.0% | 43% | 387 (401) |
| tide_kyc_1 | 0.464 | +30.2% | 0.270 | +25.8% | 0.593 | -0.9% | 0% | 452 (376) |
| tide_renewal_0 | 0.452 | +11.7% | 0.276 | +4.4% | 0.579 | +6.1% | 36% | 435 (425) |
| ALL | 0.432 | +17.8% | 0.266 | +10.4% | 0.571 | +4.7% | 28% | 5072 (4667) |

## Changes applied by the controller

81 applied — neutral: 39, beneficial: 36, harmful: 6

- nort_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.020 → **neutral**
- nort_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- kest_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.020 → **neutral**
- kest_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- arav_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.020 → **neutral**
- arav_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- tide_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.020 → **neutral**
- tide_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- kest_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.067 → **beneficial**
- kest_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- tide_cancel_2 v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- nort_cancel_2 v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- tide_renewal_0 v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- nort_renewal_0 v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- nort_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.022 → **beneficial**
- nort_kyc_1 v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.288 → **beneficial**
- tide_kyc_1 v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.305 → **beneficial**
- nort_cancel_2 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.045 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.049 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.042 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.028 → **beneficial**
- tide_cancel_2 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.028 → **beneficial**
- arav_cancel_2 v6 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.004 → **neutral**
- tide_cancel_2 v6 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.005 → **neutral**
- nort_cancel_2 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.027 → **beneficial**
- nort_cancel_2 v6 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.003 → **neutral**
- nort_cancel_2 v7 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.007 → **neutral**
- kest_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.021 → **beneficial**
- arav_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- tide_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.015 → **neutral**
- kest_cancel_2 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.002 → **neutral**
- kest_kyc_1 v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.247 → **beneficial**
- tide_cancel_2 v7 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.006 didΔ=+0.000 → **neutral**
- nort_renewal_0 v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.029 didΔ=+0.000 → **beneficial**
- nort_cancel_2 v8 (controller_approved) dims=['voice'] engageΔ=+0.011 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v7 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.015 didΔ=+0.000 → **beneficial**
- tide_renewal_0 v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- kest_cancel_2 v6 (controller_approved) dims=['cohorts', 'welcome_message', 'schedule'] engageΔ=+0.038 didΔ=+0.000 → **beneficial**
- tide_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.123 → **harmful**
- arav_renewal_0 v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.050 didΔ=+0.000 → **beneficial**
- arav_kyc_1 v2 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.300 → **beneficial**
- kest_renewal_0 v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.058 didΔ=+0.000 → **beneficial**
- nort_cancel_2 v9 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.033 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v8 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.026 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v8 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.021 didΔ=+0.000 → **beneficial**
- nort_kyc_1 v3 (controller_approved) dims=['welcome_message', 'voice', 'caller_id'] engageΔ=+0.090 didΔ=-0.138 → **harmful**
- kest_renewal_0 v6 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_renewal_0 v6 (controller_approved) dims=['cohorts', 'voice', 'schedule'] engageΔ=+0.006 didΔ=+0.000 → **neutral**
- nort_renewal_0 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.041 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v6 (controller_approved) dims=['cohorts', 'voice', 'schedule'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v9 (controller_approved) dims=['voice'] engageΔ=+0.009 didΔ=+0.000 → **neutral**
- kest_renewal_0 v7 (controller_approved) dims=['voice'] engageΔ=+0.008 didΔ=+0.000 → **neutral**
- kest_cancel_2 v7 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.025 didΔ=+0.000 → **beneficial**
- nort_cancel_2 v10 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_kyc_1 v4 (controller_approved) dims=['cohorts', 'caller_id', 'schedule'] engageΔ=+0.000 didΔ=+0.273 → **beneficial**
- kest_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.132 → **harmful**
- arav_cancel_2 v10 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nort_cancel_2 v11 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_cancel_2 v9 (controller_approved) dims=['welcome_message'] engageΔ=+0.007 didΔ=+0.000 → **neutral**
- tide_renewal_0 v7 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.045 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v11 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_cancel_2 v10 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nort_renewal_0 v7 (controller_approved) dims=['voice'] engageΔ=-0.015 didΔ=+0.000 → **harmful**
- kest_renewal_0 v8 (controller_approved) dims=['voice'] engageΔ=-0.011 didΔ=+0.000 → **harmful**
- arav_renewal_0 v7 (controller_approved) dims=['cohorts', 'voice', 'schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- kest_cancel_2 v8 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.025 didΔ=+0.000 → **beneficial**
- kest_kyc_1 v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.275 → **beneficial**
- arav_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.125 → **beneficial**
- arav_cancel_2 v12 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- kest_renewal_0 v9 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.030 → **harmful**
- nort_cancel_2 v12 (controller_approved) dims=['cohorts', 'voice', 'schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- arav_cancel_2 v13 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_renewal_0 v8 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- kest_renewal_0 v10 (controller_approved) dims=['cohorts', 'voice', 'schedule'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- nort_kyc_1 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=-0.007 didΔ=+0.209 → **beneficial**
- arav_renewal_0 v8 (controller_approved) dims=['cohorts', 'voice', 'schedule'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- arav_renewal_0 v9 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.022 didΔ=+0.000 → **beneficial**
- tide_renewal_0 v9 (controller_approved) dims=['welcome_message'] engageΔ=-0.001 didΔ=+0.000 → **neutral**

## Governance

```
{
  "approval_requests": 75,
  "decisions": {
    "approved": 64,
    "rejected": 8,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 8
  },
  "applied_via_approval": 58,
  "auto_applied": 23,
  "auto_apply_rejected": 0,
  "apply_conflicts": 6,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "kest_renewal_0": 2,
    "nort_renewal_0": 3,
    "nort_kyc_1": 1,
    "nort_cancel_2": 4,
    "arav_renewal_0": 3,
    "arav_kyc_1": 1,
    "arav_cancel_2": 2,
    "tide_renewal_0": 3,
    "tide_kyc_1": 1,
    "tide_cancel_2": 2,
    "kest_kyc_1": 1,
    "kest_cancel_2": 1
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
  "nort_renewal_0:welcome_message=W3": {
    "3": 0.702,
    "4": 0.701
  },
  "kest_renewal_0:welcome_message=W3": {
    "3": 0.7,
    "4": 0.7
  },
  "arav_renewal_0:welcome_message=W3": {
    "3": 0.703,
    "4": 0.509
  },
  "tide_renewal_0:welcome_message=W3": {
    "3": 0.405,
    "4": 0.295
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0