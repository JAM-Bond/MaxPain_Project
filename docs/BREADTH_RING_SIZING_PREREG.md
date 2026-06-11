# Breadth-Ring Sizing Gate — Pre-Registration

**Status: SEALED 2026-06-11 by user. RESULT 2026-06-11: bull_put gate FALSIFIED (Gate A+C fail) → not built; step B descriptive stays. Zebra shows a strong directional signal → separate pre-reg warranted. See §14.**

**Purpose:** Define, before any promotion, the exact rule by which the breadth ring would *bias the recommended size* of new long-delta credit-vertical entries — specifically, downsizing them on 🔴 (narrowing-while-extended) days — and the sealed evidence gates that rule must clear before it influences any recommendation.

**Origin:** The RSP/SPY breadth ring (`lib/breadth_ring.py`, shipped 2026-06-10 as a descriptive alert section) was walk-forward validated and then characterized on a 13-year backfill (`breadth_ring_daily`, 3,580 rows 2012→present; study `scripts/backtest/breadth_signal_study.py`; memory `project_rsp_spy_breadth_signal`). On the full record the state ladder is monotone in forward 42-day SPY outcome: 🟢 +3.19% / 🟡 +2.56% / **🔴 −0.02%**, with the 🔴 state showing the lowest hit-rate (62%) and the highest drawdown odds (P(>10%) = 10%). Within SPY uptrends, narrowing vs broadening **halves forward return and ~triples the probability of a >10% drawdown (2%→7%)**. The signal is a risk/quality (drawdown-fragility) read, not a point-direction predictor — which is why the only rule contemplated here is a *sizing* bias on the single worst state, not an entry/exit timing trigger.

---

## 1. Why this exists

Step B (already shipped) co-locates a descriptive breadth note on long-delta construction cards on 🟡/🔴 days. It informs the human but changes nothing. This pre-reg covers the *next* step: letting the ring actually change the **recommended size** of a new long-delta entry. Under the absolute advisory-only rule the system never places an order, so "gate" here means the alert *recommends* a smaller size; the user still decides and executes by hand. This document seals the rule and its evidence bar so promotion is a discipline decision, not an in-the-moment one.

## 2. Conceptual basis

A bull put (or zebra) is long delta: it is hurt by a broad-market drawdown. The 🔴 state — equal-weight (RSP) lagging cap-weight (SPY) *while* breadth is already extended — is the narrow-megacap-top signature, the one state where forward drift historically vanishes and left-tail risk is highest. Halving exposure to *new* long-delta entries opened into that state is a risk-reduction bet that the elevated drawdown odds outweigh the forgone (near-zero) forward drift. The bet is on the **distribution** (fatter left tail), not on a directional forecast.

## 3. Sealed rule under test

On a cycle qualifier day, for a NEW entry whose structure is in
`LONG_DELTA_STRUCTURES = {bull_put, bull_put_mp, zebra, zebra_protected}`:

- **Ring 🔴 (narrowing + extended) at entry → bias the recommended size to DOWNSIZE (half).**
- **Ring 🟡 or 🟢 → no sizing change** (🟡 keeps the step-B descriptive card note).

Scope and guardrails (all sealed):
- **New entries only.** Does not touch open-position management, exits, or rolls.
- **Long-delta only.** No effect on bear_call (short delta), inverted_fly (long vol), or covered_call.
- **Downsize, never skip.** The rule reduces size; it never zeroes an otherwise-qualified entry (a skip would also censor the outcome data needed to keep validating).
- **Advisory only.** Changes the *recommended* size surfaced in the alert/qualifier; the user places every order manually. Stacks beneath existing sizing logic (qualifier GO/DOWNSIZE, loss-cap, concentration caps) — it can only push size *down*, never up.
- **Only 🔴 gates.** 🟡 is deliberately left descriptive; we are not gating on the broad narrowing state, only the extended-narrowing extreme.

## 4. Validation methodology

**Critical anti-censoring design:** during the paper-track validation window we do **not** apply the downsize. We keep recommending/placing long-delta entries at normal size and simply **tag each by its entry-day ring state** (join `breadth_ring_daily.asof = entry_date` — no schema change needed). The downsize is *evaluated counterfactually*, then applied only after promotion. Downsizing or skipping during validation would shrink or censor the very outcomes we must observe.

- **Step 0 — Entry-state tag.** For every closed long-delta paper cycle, attach the entry-day ring `status` via the date join. (Backfill makes this work retroactively for the whole paper history.)
- **Step 1 — Backtest corroboration (independent of the thin paper sample).** Re-use the existing bull-put / zebra historical backtests; tag each cycle by its entry-day ring state from `breadth_ring_daily`; compare 🔴-entry vs non-🔴-entry per-cycle P&L, max-loss-hit rate, and realized max adverse excursion across the 13-year record and within walk-forward splits.
- **Step 2 — Counterfactual sizing.** Apply the half-size rule to 🔴-entry cycles and measure the change in cohort total P&L and in drawdown/loss-cap-hit rate vs the full-size baseline.

## 5. Sealed decision rule (promotion requires ALL)

- **Gate A — 🔴 entries underperform.** In the backtest corroboration, 🔴-entry long-delta cycles show materially worse outcomes than non-🔴 entries: lower mean per-cycle P&L **and** a higher loss-cap/stop-hit (or large-adverse-excursion) rate, by a margin set at seal time (proposed: mean P&L lower by ≥ $0.10/share AND tail-hit rate higher by ≥ 5pts).
- **Gate B — Downsizing helps risk-adjusted outcome.** The counterfactual half-size on 🔴 days reduces the cohort's drawdown / loss-cap-hit rate without erasing total edge beyond a sealed tolerance (proposed: tail-hit rate down ≥ 5pts; total cohort P&L not reduced by more than the proportional size reduction would mechanically imply).
- **Gate C — Walk-forward stability.** Gate A holds in BOTH train (≤2019) and test (≥2020) splits, same sign — not a single-regime artifact.
- **Gate D — Paper non-contradiction + adequacy.** Across the paper window, tagged 🔴-entry long-delta cycles do not *contradict* the backtest (no positive surprise). Adequacy: ≥ 8 closed 🔴-entry long-delta paper cycles. **If fewer than 8** (likely — 🔴 is ~15% of days and long-delta entries are monthly), the rule stays **backtest-corroborated only**; it is NOT promoted to a live sizing gate on thin live evidence, and step B (descriptive) remains the shipped state.

## 6. What promotion looks like

If all gates pass: wire the 🔴 → half-size bias into the qualifier's sizing layer for `LONG_DELTA_STRUCTURES`, beneath the existing caps (can only reduce size). Surface it explicitly in the alert ("size halved: breadth 🔴"). Record the activation in the plan revision log and a memory entry. It remains advisory — a smaller recommended size, never an order.

## 7. What we are NOT testing

- Not gating on 🟡 (broad narrowing) — only the 🔴 extreme.
- Not an entry-skip or market-timing rule — size bias only.
- Not touching open-position management, exits, rolls, or hedge sizing.
- Not affecting bear_call / inverted_fly / covered_call.
- Not a direction forecast — the validated content is drawdown-risk, not up/down.

## 8. Negative-result plan

If Gate A fails (🔴 entries do not underperform), the sizing gate is **falsified and not built**. Step B (the descriptive card annotation) remains as the shipped, no-harm state — the human still sees the breadth context, the system just doesn't act on it. Record the negative result in `project_rsp_spy_breadth_signal`.

## 9. Falsification triggers (post-promotion, if applicable)

- Two consecutive quarterly reviews where 🔴-entry long-delta cycles no longer underperform → demote the gate back to descriptive (step B).
- Evidence the effect was a single-regime artifact (e.g., concentrated entirely in 2022) → re-open the walk-forward gate.

## 10. Build artifacts (post-seal)

- Backtest tagger: join `breadth_ring_daily` entry-state onto bull-put / zebra cycle results; corroboration + counterfactual notebook/script under `scripts/backtest/`.
- Paper tag: date-join only (no schema change); a small query in the post-mortem to report 🔴-entry vs non-🔴 long-delta outcomes.
- On promotion: sizing hook in the qualifier + alert annotation + plan revision-log entry.

## 11. Effort estimate

Backtest corroboration + counterfactual: ~2–3 hours (data already on disk). Promotion wiring (only if gates pass): ~1–2 hours.

## 12. Sign-off

**Drafted by:** Claude Opus 4.8 (1M context)
**Drafted on:** 2026-06-11
**Sealed-by:** user
**Sealed-on:** 2026-06-11

## 14. Result — corroboration run 2026-06-11

Script `scripts/backtest/breadth_ring_sizing_corroboration.py`; tagged cycles at
`data/profile/breadth_ring_sizing_corroboration.parquet`. Every cycle tagged by its
entry-day ring state (merge_asof on `breadth_ring_daily.asof`).

**BULL_PUT (primary, N=14,091 full-universe, managed exit) — gate FALSIFIED:**
- **Gate A FAIL.** 🔴-entry mean P&L −0.062 vs non-🔴 −0.052 (Δ −0.010; sealed need ≤ −0.10);
  loss-cap-hit 9.7% vs 9.5% (Δ +0.3pts; need ≥ +5). 🔴 bull_puts do not materially underperform.
- **Gate C FAIL.** Train split even shows 🔴 marginally *better* on loss-cap-hit (−0.6pts) — signs
  don't align across splits.
- Gate B PASS mechanically (half-sizing 🔴 trims dollar-downside 6.4%, total P&L +7.7%) but moot once A fails.
- **Reading:** a defined-risk, managed bull_put absorbs the 🔴 drawdown risk via its structure +
  50%/21-DTE exits; the index-level drawdown signal does not translate into materially worse per-cycle
  credit-spread P&L. This is a clean, sensible negative.

**DECISION (per §5 all-gates + §8 negative-result plan):** the bull_put sizing gate is **NOT built.**
Step B (descriptive card annotation) remains the shipped, no-harm state.

**ZEBRA (secondary directional check, N=2,405 tier1+tier2 overlay combined-hold) — strong signal:**
🔴-entry mean P&L **−0.82 vs +10.67 non-🔴 (Δ −11.5/cycle)**; worst-decile −35.8 vs −24.7. 🔴-entry
zebras were dramatically worse — consistent with the economics (zebra = synthetic long stock, real
directional exposure that a narrow-megacap-top regime punishes, unlike a defined-risk spread).
**NOT promoted off this** — secondary check (🔴 N=376, debit structure, not the sealed bull_put
metrics). It motivates a **separate zebra-specific pre-reg** (zebra-appropriate tail metric, walk-forward,
adequacy) — the next thing to specify if pursued.

**Net:** the signal's downside-risk content is real but shows up in the structure that carries the
directional exposure (zebra), not the defined-risk bull_put the gate was anchored on. The ring stays
descriptive everywhere; a zebra gate is the live follow-up question.

## 13. Cross-references

- Signal + history: memory `project_rsp_spy_breadth_signal`; study `scripts/backtest/breadth_signal_study.py`.
- Ring code: `lib/breadth_ring.py`; refresh cron `scripts/pipeline/refresh_breadth_ring.py`; backfill `scripts/pipeline/backfill_breadth_ring.py`.
- Step B (shipped descriptive annotation): `lib/breadth_ring.py:card_annotation` + `scripts/monitor/daily_alert.py` construction loop.
- Discipline: advisory-only (`feedback_never_execute_trades`); loss-cap (`feedback_loss_cap_discipline`); descriptive-before-gating (`project_go_live_plan`).
