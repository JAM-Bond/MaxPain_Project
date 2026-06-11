# Breadth-Ring ZEBRA Sizing Gate — Pre-Registration

**Status: SEALED 2026-06-11 by user.** (Gate B tightened at seal: CVaR-10 reduction ≥8%, total-P&L within ±1%.)

**Purpose:** Seal the rule by which the breadth ring would *bias the recommended size* of new ZEBRA entries — downsizing them by half on 🔴 (narrowing-while-extended) days — together with the evidence already established, the forward (paper) non-contradiction check, the promotion mechanics, and the negative-result plan. This is the zebra counterpart to the bull_put pre-reg ([[BREADTH_RING_SIZING_PREREG]]), which was **falsified** for bull_put; the signal lives in the long-delta debit structure instead.

**Origin:** The breadth ring is a validated drawdown-risk read (memory `project_rsp_spy_breadth_signal`). The bull_put sizing gate failed (a defined-risk, managed spread absorbs the risk). The zebra finding ([[BREADTH_RING_ZEBRA_FINDING]]) and its §5 robustness battery showed the signal expresses cleanly in zebra (synthetic long stock = real directional exposure): 🔴-entry zebras earn ~0 vs ~+17/cycle with a fatter left tail, robust across overlay variants and to dropping the biggest years.

---

## 1. Why this exists

A zebra is long synthetic stock plus a long-put overlay, held to expiry — it carries genuine directional exposure and a real left tail, unlike a defined-risk credit spread. The ring's 🔴 state (the narrow-megacap-top signature) is exactly the regime that should punish that exposure, and the data agrees. This document seals (a) the sizing rule, (b) the gates — already largely met in backtest — required to promote it to a live recommendation, and (c) the forward guardrail, so promotion is a discipline decision, not an in-the-moment one. Under the absolute advisory-only rule the system never places an order; "gate" means the qualifier *recommends* a smaller zebra size and the user still places by hand.

## 2. Conceptual basis — this is a TAIL gate, not a return gate

The defining fact, and the reason the gates differ from the bull_put pre-reg: **🔴 zebras are ~break-even on mean (≈ 0/cycle), not money-losers.** The gate does not improve expected return; it sheds left-tail risk at ~zero cost. Half-sizing 🔴 entries cut cohort CVaR-10 by +10.7% for a +0.07% change in total P&L (§5 Test 4). So every gate is framed around tail/drawdown reduction, and the success criterion is "less tail for ~no give-up," not "higher mean."

## 3. Sealed rule under test

On a cycle qualifier day, for a NEW entry whose structure is in
`ZEBRA_STRUCTURES = {zebra, zebra_protected}` (cohorts COHORT_ZEBRA_TIER1 / TIER2):

- **Ring 🔴 (narrowing + extended) at entry → bias the recommended size to DOWNSIZE (half).**
- **Ring 🟡 or 🟢 → no sizing change** (🟡 keeps the step-B descriptive card note).

Guardrails (all sealed):
- **New entries only.** Does not touch open zebras, the overlay, exits, or rolls.
- **Zebra only.** No effect on bull_put / bear_call / inverted_fly / covered_call.
- **Downsize (half), never skip.** §5 Test 4 showed half dominates skip on tail reduction (CVaR −10.7% vs −7.1%) while keeping the position (no upside censoring, no data censoring). Skip was considered and rejected.
- **Advisory only.** Changes the *recommended* zebra size in the alert/qualifier; the user places every order. Stacks beneath existing sizing (qualifier verdict, the ZEBRA capital-outlay allocation, concentration caps) — can only push size *down*.
- **Only 🔴 gates.** 🟡 stays descriptive.

## 4. Evidence already established (backtest corroboration — DONE)

The §5 robustness battery (`scripts/backtest/breadth_ring_zebra_robustness.py`, 2026-06-11; combined parent+overlay hold P&L, tier-1+tier-2, N=2,405) PASSED all four checks:
- **Overlay-variant:** 🔴 underperforms in ALL of ATM/OTM-5/OTM-10 and BOTH walk-forward splits (full Δ −13.6 / −15.5 / −17.4). The traded with-overlay structure is sign-consistent (the earlier parent-only flip was a no-overlay artifact).
- **Drop-year:** baseline Δ −17.4; leave-one-out range [−21.8, −15.0]; **drop 2021 AND 2023 still −12.5** — not a 2-year artifact.
- **Tail counterfactual:** half-🔴 cuts cohort CVaR-10 +10.7% (−82.8→−73.9) for +0.07% total P&L.
- **Adequacy:** 🔴 N train 208 / test 168 (pooled adequate; per-year thin — pooled inference only).

This is the backtest leg. **Limitations sealed into the record:** it is historical/in-sample (the walk-forward split is the only out-of-sample element); the penalty is far larger in the drawdown-heavy test period (Δ −28.8) than the calm train period (−5.9), so the gate's realized value concentrates in stress and is ~nil in calm markets; the cohort is the backtest cohort.

## 5. Forward validation methodology (the remaining leg)

Because 🔴 is rare (~16% of days) and zebra entries are infrequent (75-DTE, small cohorts), the paper window will yield very few forward 🔴 zebra entries — likely too few for a powered test. So the forward leg is a **non-contradiction guardrail**, not a confirmatory test, and (per pre-reg D) uses the **anti-censoring tag-don't-downsize** design:
- Through the paper window, keep recommending/placing zebra entries at normal size; **tag** each by its entry-day ring state (date-join `breadth_ring_daily.asof = entry_date`, no schema change).
- At the gate review, check that forward 🔴 zebra outcomes do **not contradict** the in-sample tail penalty (no 🔴 outperformance surprise; tail not tighter than non-🔴).

## 6. Sealed decision rule (promotion to a live half-size recommendation requires ALL)

- **Gate A — Backtest tail robustness (MET, §4).** §5 all-pass: 🔴 fatter tail + ~break-even mean, overlay-robust, drop-year-stable (incl. drop 2021+2023), walk-forward sign-stable, pooled-adequate. Re-affirmed at seal, not re-run.
- **Gate B — Tail-reduction is material and ~free (MET, §4 Test 4).** Half-🔴 reduces cohort CVaR-10 by **≥ 8%** (achieved +10.7%) with total cohort P&L change within **±1%** (achieved +0.07%). _(Thresholds tightened from the draft's ≥5% / ±3% at seal, 2026-06-11, to demand a materially meaningful tail cut at a genuinely near-free cost; the current evidence still clears the tighter bar, and any future re-run on a changed cohort/overlay must too.)_
- **Gate C — Forward non-contradiction.** Through the paper window, tagged forward 🔴 zebra entries do not contradict the penalty. **Adequacy realism:** if forward 🔴 zebra N < 3 (likely), Gate C is treated as "not contradicted" by default and promotion rests on Gates A+B, with the forward tag continuing as a post-promotion monitor (§9). This is stated explicitly so thin forward N is not silently treated as confirmation.
- **Gate D — Advisory integrity.** The rule only reduces size, stacks under existing caps, never blocks an otherwise-qualified entry, and touches no non-zebra structure. (Design property, verified at wiring.)

## 7. What promotion looks like

Wire 🔴 → half-size into the qualifier's sizing layer for `ZEBRA_STRUCTURES`, beneath the existing ZEBRA capital-outlay allocation and caps (can only reduce). Surface explicitly in the alert ("zebra size halved: breadth 🔴"). Record in the plan revision log + a memory entry. Live only after the paper window per the standing paper-test discipline. Remains advisory — a smaller recommended size, never an order.

## 8. What we are NOT testing

- Not gating bull_put (falsified), bear_call, inverted_fly, or covered_call.
- Not a return-improvement claim — 🔴 zebras are ~break-even; this is tail reduction only.
- Not gating on 🟡 — only the 🔴 extreme.
- Not touching the overlay, open-position management, exits, or rolls.
- Not an entry-skip or market-timing rule — half-size only.

## 9. Negative-result plan & falsification triggers

- If the forward tag CONTRADICTS (🔴 zebras outperform / tighter tail) during paper → do NOT promote; the gate stays descriptive (step B), record the reversal in `project_rsp_spy_breadth_signal`.
- Post-promotion: two consecutive quarterly reviews where 🔴-entry zebras no longer show a fatter tail → demote the gate to descriptive.
- If a future overlay/cohort change makes the §5 robustness no longer hold on re-run → re-open the gate.

## 10. Build artifacts (post-seal)

- Forward tag: date-join only (no schema change); a post-mortem query reporting 🔴-entry vs non-🔴 zebra tail by paper cycle.
- On promotion: sizing hook in the qualifier for `ZEBRA_STRUCTURES` + alert annotation + revision-log entry.
- Re-run harness for Gate A/B on cohort/overlay changes: `scripts/backtest/breadth_ring_zebra_robustness.py`.

## 11. Effort estimate

Promotion wiring (qualifier sizing hook + alert + revision log), only on promotion: ~1–2 hours. Forward tag query: ~30 min. No new backtest needed (Gates A/B already met).

## 12. Sign-off

**Drafted by:** Claude Opus 4.8 (1M context)
**Drafted on:** 2026-06-11
**Sealed-by:** user
**Sealed-on:** 2026-06-11

## 13. Cross-references

- Finding + robustness: [[BREADTH_RING_ZEBRA_FINDING]]; `scripts/backtest/breadth_ring_zebra_robustness.py`.
- Bull_put pre-reg (falsified counterpart): [[BREADTH_RING_SIZING_PREREG]].
- Signal + history: memory `project_rsp_spy_breadth_signal`. ZEBRA structure: memory `project_zebra_findings`.
- Discipline: advisory-only (`feedback_never_execute_trades`); defined-risk/sizing (`feedback_loss_cap_discipline`); descriptive-before-gating (`project_go_live_plan`); paper-test window (`project_paper_test_window`).
