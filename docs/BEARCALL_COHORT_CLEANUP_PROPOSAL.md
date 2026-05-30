# Proposal — Bear-Call Cohort Cleanup & Membership Governance

**Status: PROPOSAL (not applied).** Drafted 2026-05-30 (Opus 4.8 session). Requires user sign-off before any `gate_config.py` edit. Evidence: `project_bearcall_live_state_filter_rejected`, `reports/bearcall_live_state_filter_2026-05-30.md`, `scripts/research/bearcall_cohort_spotlight.py`.

---

## 1. The problem (measured, not asserted)

`COHORT_BEAR_CALL` is populated by the auto-promotion pipeline's Gate B: a name is promoted when its bear-call walk-forward shows ≥3/4 splits positive + most-recent-split mean ≥ $5/contract. **That criterion does not validate out-of-sample:**
- Reconstructed Gate-B-eligible cycles (no look-ahead) average **−$0.165/sh forward** (gross, 1,332 cycles).
- The members' "positive" recent backtest means are a **declining-period artifact** — every positive single-name member is a name that *fell* in 2024-26 (ADBE/BA/DOW/TGT/MRK), and the profit is the decline it already caught. UNH's +$1.59 recent mean is the 2025 crash; the stock has since recovered +62%. ZTS's +$0.72 is a single 2025 crash; it's now at the lows.
- Six independent studies (weakness, theta-timing, sector RS, sector stage-2, below-MA, live-state/IV filter) found **no persistent forward edge in single-name bear-call selection**. The only validated bearish condition is the broad-market H1 regime gate.

**Net:** the pipeline is feeding the live book bear-call names on a selection that captures past declines, not future ones.

## 2. Proposed changes

### 2a. Drop chronic-negative single names (immediate)
Negative in BOTH the full history and the recent period — no basis for membership:

| name | mean_all | mean_recent | note |
|---|---|---|---|
| **WMT** | −0.105 | −0.380 | actively *up*-trending — wrong direction for a bear call |
| **IBM** | −0.294 | −0.912 | recent sharply negative |
| **MMM** | −0.051 | −0.041 | chronic small-negative |
| **DVN** | −0.125 | −0.074 | chronic small-negative |

Also flag **ARRY** — in the cohort but no cycle data found in any substrate; remove or re-extract+re-validate before keeping.

### 2b. Label index/ETF members as regime-gated-only (documentation)
`SPX, SPY, QQQ, DIA, IWM, XLP, IEF, TMF` show deeply negative *un-gated* backtests (SPX −3.8/sh) — **expected and fine**, because their bear-calls only fire under H1 (broad market below its 200-DMA + IVR > 0.5). Document them as a separate, regime-gated tier so their un-gated stats are never read as a defect, and so they are never deployed outside H1.

### 2c. Tighten single-name membership: require *persistent*, not period-lucky, expectancy
Replace "most-recent split positive" with a **held-out persistence** test: a single name qualifies only if its bear-call expectancy is positive on a validation period that comes *after* the qualifying period (the reconstruction in the rejected pre-reg), AND the edge is **not concentrated in a single crash window** (e.g., drop the single best-performing month and require the remainder still positive — the ABBV-style concentration check from `sector_etf_stage2`). On current evidence, **few or no single names pass this** — which is the point.

### 2d. Strategic recommendation (the honest conclusion)
Given no validated single-name bear-call edge, the defensible posture is to **shrink single-name bear-call to near-zero and route bearish exposure to the two validated paths**: (i) H1-gated index/ETF bear-calls, and (ii) *buying* cheap convexity for crash protection (the user's actual mandate) when fragility signals fire. Single-name bear-calls, if kept at all, should be H1-gated too and explicitly tagged discretionary + ⅓ size.

## 3. What this means for the three live positions
- **UNH** — backtest positive is the over crash; live state adverse (recovered, above rising 200-DMA, IV 2nd pctile). Manage to close.
- **ZTS** — recent +0.72 is one crash, now exhausted (97% realized vol, on the strike). Book it.
- **STZ** — the only one with a *live* reason: genuinely still below a falling 200-DMA, recent +0.27 reflects an ongoing downtrend. Hold/manage normally, but recognize it's regime-dependent (needs continued weakness), not a validated edge.

## 4. Decision points for the user
1. Apply the 2a drops now (WMT/IBM/MMM/DVN, + ARRY pending re-extract)? **(low-risk, recommended)**
2. Adopt the 2b regime-gated tier labeling?
3. Commission the 2c persistence-gate as a sealed pre-reg (build the held-out + concentration check into the pipeline's Gate B)?
4. Adopt the 2d strategic shrink, or keep single-name bear-calls as discretionary/H1-gated?

## 5. Cross-references
- `project_bearcall_live_state_filter_rejected` · `project_theta_timing_null` · `project_bear_call_h1_h3_findings` (the validated H1 gate) · `scripts/maintenance/auto_promotion_gate_check.py` (the Gate B to amend).
