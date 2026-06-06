# Pre-Registration — ETF Money-Flow → GICS Sector Rotation

_Sealed 2026-06-05. Thresholds, signals, signs, gates, and the OOS span are all
committed BEFORE looking at any out-of-sample result. This is the flow study the
SSGA navhist data was found for; the macro→sector **price** version was a powered
NULL (the efficiency wall — see `project_macro_positioning_overlay`). Flows are a
**structurally different, less-arbitraged positioning signal** that can lead price
and reveal price–flow divergence._

## Question
Does **where money is actually flowing** (creation/redemption-derived net ETF flow)
predict **next-month GICS-sector-ETF excess return vs SPY**? Three pre-committed
hypotheses, one shared walk-forward, one shared metric battery, one shared gate set.

## Data
- **Flow:** `data/flows/sector_flows_monthly.parquet`, reconstructed from SSGA
  navhist as **net flow = Δ(shares outstanding) × NAV**
  (`reference_ssga_sector_flow_data`). Shares-outstanding (hence flow) begins
  **2006-05** for the 9 classic sectors; XLRE from 2015-10; XLC from 2018-06.
- **Return:** total-return-adjusted monthly close (yfinance `auto_adjust=True`,
  daily → month-end resample). Total-return adjustment is deliberate: raw NAV is
  cut by dividend distributions, which would systematically penalize high-yield
  sectors (XLU, XLP) in a cross-sectional ranking. Benchmark = SPY (total return).

## Universe (11 SSGA GICS sector SPDRs)
XLB XLC XLE XLF XLI XLK XLP XLRE XLU XLV XLY. No GLD/SLV (not SSGA — no flow data).
A sector enters a given month's cross-section only when it has BOTH a valid
3-month flow signal AND a valid t+1 return. XLRE effectively from ~2016-01, XLC
from ~2018-09; earlier months run a 9-sector cross-section.

## Frequency / horizon
Monthly, month-end. Signal uses flow **through** month t; target is excess return
realized **over** month t+1. Strictly causal — no look-ahead.

## The flow signal (the lever)
- **Organic flow rate:** `flow%_t = flow_t / AUM_{t-1}` (lagged-AUM denominator so
  within-month price appreciation does NOT contaminate the denominator).
- **Primary window — `FLOW3`** = `flow%_t + flow%_{t-1} + flow%_{t-2}` (trailing
  3-month cumulative organic flow). Smooths the month-to-month noise in
  creation/redemption. **This is the single sealed primary signal.**
- **`FLOW1`** (1-month flow%) = exploratory secondary — reported for color, **never
  gated.** (Pre-committing one primary window avoids a multiple-window fish.)

## Three sealed hypotheses
Each produces, every OOS month, a **predicted-excess vector** over the available
sectors → a predicted ranking. Signs for H1/H2 are **theory-pre-committed** (not
fit), so full causal scoring already IS out-of-sample for them.

- **H1 — Flow momentum.** predicted excess ∝ **+FLOW3**. Hypothesis: sectors taking
  in money keep outperforming next month (positioning momentum). Sign committed +.
- **H2 — Price–flow divergence.** cross-sectional divergence
  `d = z_xs(PRICE3) − z_xs(FLOW3)`, where `PRICE3` = trailing 3-month total return.
  predicted excess ∝ **−d**. Hypothesis: **price up on weak/negative flow = a
  hollow rally to fade** (high d → underperform); **price flat/down on strong flow =
  quiet accumulation** (low d → outperform). Sign committed.
- **H3 — Flow → forward excess (model).** expanding-window **pooled OLS**:
  `Excess_{s+1} ~ a + b·FLOW3_s` fit on all pairs `s ≤ t−1` (known outcomes only);
  predicted `Excess_{t+1} = â + b̂·FLOW3_t`. Lets the data set the slope OOS rather
  than pre-committing it — a sign-agnostic cross-check on H1.

## Expanding-window walk-forward (the real 20-year test)
- **Warmup:** first **60 months** from the first flow month (2006-06).
- **OOS evaluation span:** **2011-06 → 2026-05** (~180 months; trailing incomplete
  month dropped). H3 **refits each month** on the expanding window `≤ t−1`. H1/H2
  carry pre-committed signs (their warmup is nominal); all three are scored on the
  **identical** OOS span for an apples-to-apples comparison.
- The holdout span contains 2011 EU crisis, 2013 taper, 2015-16, 2018 Q4, COVID,
  the 2022 rate shock, and the AI boom — deep regime variety, non-stationary by
  design. Breaking OOS is informative, not a failure.

## Shared OOS metric battery (per hypothesis)
1. **Rank-corr** — monthly Spearman(predicted excess, realized t+1 excess), averaged.
2. **Top-pick hit-rate** — top-predicted sector beats SPY (realized t+1 excess > 0).
3. **Tercile spread** — realized t+1 excess of (top − bottom predicted tercile),
   mean %/mo + fraction of months positive.
4. **Long-short t-stat** — `mean(spread) / se(spread) · √n_months`.

## Sealed gates (applied per hypothesis)
- **Gate A — ranking skill:** mean OOS Spearman **≥ +0.08**.
- **Gate B — spread:** mean OOS tercile spread **≥ +0.20%/mo** AND positive in
  **≥ 53%** of OOS months.
- **Gate C — significance:** long-short spread **t-stat ≥ +2.0**.
- **PASS(hypothesis) = A AND B AND C.** (All three required — flows are the more
  promising lever, so demand conviction, not a single lucky metric.)

## Verdict logic
- **PASS:** at least one of H1 / H2 / H3 clears A AND B AND C.
- **NULL (terminal):** otherwise. ~180 OOS months × up-to-11-name cross-section is
  **adequate power** → a null sends monthly flow-rotation to the **graveyard**, not
  "re-run later." Honest prior: a real but modest H2 (divergence) edge is the most
  plausible survivor; H1 momentum is the most likely to be already arbitraged.

## If PASS — use
Feeds a **sector positioning overlay** (sibling of the macro overlay): "in the
current flow picture, lean credit verticals toward favored sectors, away from
disfavored." Sector-level, evidence-based, **soft** — NOT a standalone rotation
strategy unless the OOS edge is large and stable.

## v2 — deferred unless v1 shows life
Weekly horizon; **flow acceleration** (Δflow%, not level); **flow × macro-regime**
interaction (does momentum work risk-on, divergence risk-off?); flow-**surprise**
vs each sector's own trailing baseline. None of these are run in v1.

## Honesty notes
- Selection-edge hunt → full skepticism (the ~12-deep graveyard discipline).
- H1/H2 signs are theory-pre-committed, not fit → causal scoring IS OOS for them;
  H3's slope is the only fitted quantity, and it refits walk-forward.
- One sealed primary window (3m). FLOW1 is exploratory and never decides a gate.
- Total-return-adjusted returns remove the high-dividend-sector cross-sectional bias
  that raw NAV would impose.

---

## RESULT — NULL (terminal). Ran 2026-06-05.
`scripts/backtest/sector_flow_rotation_study.py` → `data/profile/sector_flow_rotation_study.parquet`.
180 OOS months (2011-06→2026-05), 8–11-sector cross-section.

| Hypothesis (primary FLOW3) | rank-corr (A≥+0.08) | spread %/mo (B≥+0.20, pos≥53%) | t-stat (C≥+2.0) | top-pick | PASS |
|---|---|---|---|---|---|
| H1 flow momentum            | **−0.028** | +0.047, 47% | +0.19 | 45.0% | ✗ |
| H2 price–flow divergence    | **−0.004** | −0.132, 43% | −0.64 | 45.6% | ✗ |
| H3 flow→fwd-excess (OLS)    | **+0.028** | −0.047, 53% | −0.19 | 53.3% | ✗ |

Secondary **FLOW1 (1-month)** equally dead: H1 rank-corr −0.020 (t −0.25); H2 +0.005 (t −0.06).
Every metric sits within ±0.03 rank-corr of zero, |t|<0.65, top-pick at coin-flip.
H2 (divergence — the pre-registered most-plausible survivor) came in slightly **negative**.

**Verdict: terminal NULL.** 180 OOS months × up-to-11 cross-section is adequately
powered → **monthly ETF flow-rotation → graveyard.** No re-run. This is consistent
with, not contradicted by, the macro→price efficiency wall: creation/redemption flow
does not lead next-month sector excess return in any tradeable way over 2011–2026.
v2 ideas (weekly horizon, flow acceleration, flow×regime) remain **deferred** — v1
showed no life to justify them.

### Data-integrity finding (the real byproduct)
The original `flow = Δshares × NAV` reconstruction was **split-contaminated**: on a
2:1 ETF split shares double while NAV halves, manufacturing a phantom inflow of
~100% of AUM. The prior session's "verified XLE Dec-2025 +$13.5B inflow" was in fact
the 2025-12-05 XLE 2:1 split, **not** a real flow. Fixed in
`lib/ssga_flows.reconstruct_flows` (split-adjust shares to today's units before
differencing); flow store rebuilt. Hygiene layer added in the study: 12-month
inception seasoning + a beyond-physical |flow%|>30%/mo guard (20 sector-months
capped). The clean signal gives the **same** null as the contaminated one — the
verdict is robust to the fix, but the fixed store is the one to keep for any future
use of these flows.
