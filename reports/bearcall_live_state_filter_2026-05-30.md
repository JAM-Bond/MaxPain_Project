# Bear-call live-state filter — validation (2026-05-30)

**Verdict: INCONCLUSIVE** (gross/substrate P&L; Gate B slip-sensitive)

- all-OTM baseline $-0.241 | Gate-B-eligible $-0.165 | tagged n=1332

| cell | mean $/sh | n |
|---|---|---|
| all eligible | -0.165 | 1332 |
| LIVE-state | -0.235 | 330 |
| LIVE w/o IV-rank | -0.217 | 492 |
| RECOVERED | -0.128 | 478 |

| gate | result |
|---|---|
| A lift≥0.05 | False (-0.071) |
| B live>0 | False (-0.235) |
| C placebo | False (95th +0.080) |
| D walk-fwd | False (0/4) |
| E recovered<0 | True (-0.128) |
| F IV-rank carries | False (Δ -0.018) |
| G N-adequate | False (n=330) |
