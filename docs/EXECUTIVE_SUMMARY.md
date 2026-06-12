# MaxPain Project — Executive Summary

## Abstract

MaxPain Project is a quantitative research system that studies price behavior in the 21-trading-day window surrounding monthly options expiration (OpEx) across a universe of 50 liquid equities, sector ETFs, and broad-market indices. The central hypothesis is that dealer delta-hedging obligations produce a structural, tradeable price pattern around each monthly expiration — a mechanical pull toward the *max pain* strike that maximizes aggregate option-holder losses. The mechanic is argued to be contractual rather than informational, on three grounds: (a) market makers hedge whether or not the pattern is known to other participants; (b) the mechanism has survived in the options literature for 15+ years without visible decay; (c) held-out chronological validation across a 50/50 split (`grid_search_validation.py`) shows 0 of 31 confirmed symbols lose significance out of sample. The project operationalizes this hypothesis through a layered statistical framework, a two-layer symbol-ranking system, an AI analytical layer (Anthropic Claude Fable 5, referred to throughout as the **SOUL**) that consumes the per-symbol payload and produces ranked trading decisions under an explicit rule hierarchy, and two parallel trading approaches currently running a head-to-head empirical bake-off. The first *live-money* cycle (April 2026 OpEx) closed 11 stock positions with realized P&L of **+$1,761** (9 winners, 2 losers; hit rate 82%). Both losers were energy-sector regime casualties of the April 8 2026 US-Iran oil truce (XOM −6.88%, XLE −5.07%) — failures of environment, not of the mechanic. Concurrently, 23 *paper-evaluated* credit-spread recommendations from the Original system closed for the same OpEx, and 40 *paper-evaluated* spread trades from the Score system remain open for the May 15 OpEx. The May and June 2026 cycles will provide the first comparable evidence between the two parallel approaches; the resulting post-mortem cross-tabs will drive the decision about which components of each system — or which combination — survive into a merged production book.

## Operator profile and objective function

This system is designed for a **solo retail trader** with a ~$100,000 capital base, not an institutional desk. The objective function is explicitly **capital preservation first, income generation second**. Structural decisions throughout — mandatory 3.5% stop-limit on stock-only positions, defined-risk credit spreads only, a MAX_POSITIONS hard cap to prevent over-deployment, a sector-exposure soft cap, a watching-status embargo on symbols under adverse regime — all prioritize downside avoidance over upside maximization. The system also assumes the operator cannot run intraday algorithmic execution, cannot absorb indefinite drawdowns from correlated positions, and must make all trading decisions by close-of-day with manual MOC-order submission. These constraints shape the filter set, the selection algorithm, and the risk policy. A reader accustomed to institutional infrastructure should calibrate expectations accordingly: this is a *small, disciplined, survivable* system, not an aggressive or capacity-scaling one.

## Data infrastructure and universe construction

### Data sources

The system is DB-centric. All per-day data lives in a local SQLite database (`~/MaxPain_Project/data/shared/maxpain.db`); scripts read and write to it, and no hardcoded symbol lists exist anywhere in the codebase. The data stack:

- **Daily OHLCV** — Schwab Market Data API (primary), yfinance (fallback on Schwab auth failure).
- **Live option chains (OI, IV, greeks, strike structure)** — Schwab `/marketdata/v1/chains` endpoint.
- **Max pain / pin-zone computation** — computed live from the Schwab chain on every snapshot.
- **Dividend calendar** — yfinance (drives the skip-dividend logic).
- **Historical options (GLD / SLV long history)** — Alpha Vantage archive (subscription cancelled May 2026; local archive retained).
- **Scan candidates** — ThinkorSwim sizzle-index CSV exports dropped into `data/scans/`.
- **AI analytical output** — Anthropic Claude Fable 5 API (`claude-fable-5`), called once per Final Analysis run.

### Cron schedule (America/New_York)

| Time | Job | Purpose |
|---|---|---|
| 8:45 AM | DB backup | Nightly SQLite restore point |
| 9:15 AM | Snapshot cron | OI, IV, gamma profile, MP for all confirmed symbols (post-OI-update window) |
| 4:15 PM | Close-price update | Updates `current_price` to actual close; triggers spread mark-to-market |
| 4:20 PM | Spread score mark | Daily MTM on `spread_score_trades`; refreshes the 7-metric composite; scans for new entries |
| 4:30 PM | OpEx monitor | Writes per-symbol verdicts (GO / NO_MOM / SKIP) to `analysis_signals` |
| 4:45 PM | Daily email alert | RED / YELLOW / INFO tier alerts with action recommendations |

### Universe construction

Candidate symbols enter the universe through a seven-step automated pipeline (`scan_pipeline.py`):

1. **Ingest** — a TOS sizzle-index CSV scan is dropped into `data/scans/`, parsed, and candidates are loaded into `scan_candidates` with appearance-count tracking.
2. **Pre-filter** — candidates outside $30–$275, with cumulative move > 5%, earnings within 10 days, or stale bid-ask data are filtered.
3. **Build events** — `build_events.py` extracts the 21-day OpEx event windows from the Schwab price history for each new symbol.
4. **Event study** — `opex_event_study.py --save` runs the full statistical test battery on the D-6 → D-3 fixed (Config B) window.
5. **Rank scores** — `compute_rank_scores.py` computes the Layer 1 composite and writes it to `symbol_stats`.
6. **Liquidity check** — `check_option_liquidity.py` pulls the next monthly chain, computes MP live, and stamps `liquid_puts` / `liquid_calls` on the symbol row.
7. **Candidate report** — `generate_candidate_report.py` produces a CONFIRM / MONITOR / REJECT recommendation.

A symbol is admitted to the `active` universe when it simultaneously (a) passes the BH-FDR gate at the current universe-size-adjusted threshold, (b) has N ≥ 30 Config B observations, (c) has `boot_ci_low` strictly positive, and (d) passes the per-symbol Config B Sharpe ≥ 0.60 and hit rate ≥ 70% thresholds. Symbols failing any of the four are flagged MONITOR (promising but marginal) or REJECT. The current universe is **50 symbols — 49 active, 1 watching** (XOM, sidelined 2026-04-08 for the US-Iran oil-truce regime event).

## Strategy — two systems in parallel

The project runs two independent trading systems simultaneously and compares them empirically before committing to a merged production book. The first system ("Original," implemented in `spread_evaluator.py`) enters credit spreads eight trading days before OpEx and exits three days before, capturing the structural D-6 → D-3 window where dealer hedging pressure concentrates most sharply; spreads are anchored at the max pain strike and evaluated via 10,000-path Monte Carlo using empirical return distributions rather than log-normal assumptions. The second system ("Score," implemented in `spread_score_tracker.py`) enters multi-week credit spreads filtered by a 7-metric volatility-regime composite — IV rank, IV percentile, volatility risk premium, GEX z-score, short-strike delta, IV skew, charm at max pain, and VIX term structure — and tracks three parallel exit strategies: Hold-to-D3, Sosnoff 80%-of-credit early exit, and a shadow-rolled variant simulating tastytrade-style rolling at |Δ|≥0.50. Both systems exploit the same structural mechanic at different temporal slices: the Original captures the sharpest part of the pinning arc, the Score rides the full multi-week pin formation including the IV and gamma buildup that precedes it. The rationale for running them in parallel rather than picking one is epistemic: it is not yet known whether these represent one edge expressed at two horizons or two distinct edges, nor whether a union (trade when either filter approves) or intersection (trade only when both approve) outperforms either alone. The discriminating evidence arrives in May and June 2026; merge decisions are made via the cycle post-mortem's cross-tabs, not via prior conviction.

### Regime sensitivity — the watching mechanism

When a symbol's structural mechanic is temporarily overwhelmed by a macro event — an oil-price collapse, a tariff announcement, a geopolitical truce — the symbol is moved to `watching` status rather than being removed from the universe. The edge is not presumed gone; the conditions are presumed unfavorable. When the regime event resolves, the symbol is reactivated with a timestamped audit entry in `symbol_status_log`. This distinction between *regime sensitivity* and *edge decay* is a deliberate design choice: over-reactive universe management would remove legitimately-edged symbols after one bad cycle, while no regime management would expose the book to correlated regime losses (as observed in cycle #1 with XOM and XLE, both energy-sector casualties of the April 8 oil shock). The `watching` flag is a two-way signal: it embargoes entry and, when a position is already open in a symbol that flips to `watching` mid-hold, it becomes a hard priority-exit instruction to the SOUL.

## Methods — filters, statistics, and symbol selection

Candidate symbols are admitted through a sequential filter cascade, evaluated by a progressive statistical battery that scales with sample size, and ranked by a two-layer composite formula. The momentum filter is the primary quality gate — empirically validated in the 2020 COVID stress test, where it blocked 30 of 31 symbols in March 2020 (producing an effectively all-cash book during the worst single-month equity crash in a decade) and correctly re-opened participation in the April recovery.

### Admission filters (apply to both systems)

- **Momentum filter** — cumulative return ≥ +0.5% over the trailing window. The dead-zone ±0.5% has empirical Sharpe 0.024 on N = 2,269 observations (`momentum_magnitude_study.py`), i.e. a coin flip; ≥ +0.5% is the structural threshold.
- **Skip months** — per-symbol seasonal blackout dates discovered by the event study. Hard stops, not advisories.
- **Option-chain liquidity gate** — live Schwab-chain check; requires at least two strikes in MP±2 with OI ≥ 500, bid > 0, and bid-ask < 15% of mid. Annotation only, does not exclude from analysis.
- **Symbol universe gate** — must clear Bonferroni + BH-FDR on the D-6 → D-3 main-window event study before admission to the `active` universe.

### Statistical tests — named, with rationale for each

The battery expands with sample size; each test is used where its assumptions hold.

- **Sign test (binomial)** — N ≥ 5. Tests whether win rate > 50%. Chosen because it assumes only independence, not distributional form — the only test usable at very small N.
- **Wilcoxon signed-rank test** — N ≥ 10. Tests whether median return > 0. Chosen because OpEx returns are known to be fat-tailed and skewed, violating the t-test's normality assumption; the non-parametric median test is robust to these violations.
- **Shapiro-Wilk test** — N ≥ 20. Tests the normality assumption. Chosen as the gate on whether parametric t-test results can be trusted; if Shapiro-Wilk rejects normality, bootstrap CI and Wilcoxon are authoritative and the t-test is recorded for reference only.
- **Student's t-test** — N ≥ 20, only if Shapiro-Wilk passes. Tests whether mean return > 0. Chosen because it is the most powerful test when its assumptions hold and because it provides direct confirmation of mean-return positivity alongside non-parametric results.
- **Bootstrap 95% confidence interval (BCa)** — N ≥ 15. Returns a conservative lower bound on the true mean under resampling. **The single most important ranking input** (20% weight in `rank_score`): chosen because it is the closest available analogue to a pessimistic floor on symbol quality with no distributional assumption required.
- **Block bootstrap (3–6-month blocks)** — audit tool run once against the standard bootstrap to test whether serial correlation was inflating the CIs. Result (`block_bootstrap_audit.py`, across all 31 confirmed symbols): 0 of 31 symbols lost significance, median CIs 6.2% *narrower* than iid. Chosen because ruling out this threat validated the simpler iid bootstrap for routine ranking use.
- **Lag-1 autocorrelation (`return_consistency`)** — ranking input, 20% weight. Measures whether a symbol's edge persists month-to-month (AC > 0) or alternates (AC < 0). Chosen because it is orthogonal to Sharpe and CI low; it replaced hit rate in the ranking formula (which was redundant with Sharpe) and immediately repriced three symbols (SLV, JPM, XLV) whose streakiness had been hidden under hit rate.
- **Bonferroni correction** — informational column on the display. Controls family-wise error rate at α/N. Chosen for reference because it is the strictest and most familiar multiple-comparison correction; retained after BH-FDR became the gate because readers expect to see it.
- **Benjamini-Hochberg FDR correction** — the ranking gate (10% weight in `rank_score`). Controls the expected proportion of false discoveries rather than family-wise error. Chosen over Bonferroni because (a) the hypothesis structure is correlated-test discovery, not single-hypothesis confirmation; (b) held-out validation (`grid_search_validation.py`, 50/50 chronological split) confirmed 0 of 31 symbols fail out-of-sample so the real false-discovery rate is near zero; (c) BH-FDR scales to 100+ symbols without pathological threshold tightening.
- **Monte Carlo simulation (10,000 paths, empirical D-6 → D-3 returns)** — used by the Original system for per-spread P&L forecasting. Chosen over log-normal / Black-Scholes because it samples directly from each symbol's actual return distribution, preserving fat tails, skew, and seasonality that closed-form assumptions erase. Outputs P(profit), E[PnL], VaR, MC Sharpe per candidate spread.
- **Grid search held-out validation (50/50 chronological split)** — window-selection audit. Chosen to answer "is the D-6 → D-3 window a real structural window or a backtest artifact?" Result (`grid_search_validation.py`): grid-searched "best windows" match across splits only 48% of the time, but the fixed D-6 → D-3 window is robust in 27 / 31 symbols. This is *why* the ranking formula uses `config_b_sharpe`, not `best_sharpe`.

**Decision tree when tests disagree.** The typical disagreement is Shapiro-Wilk rejecting normality at N ≥ 20 while the t-test still reports positive mean. In this case the bootstrap CI and Wilcoxon are authoritative; the t-test result is retained as a reference only and does not enter `rank_score`. This ordering is deliberately conservative — it prefers non-parametric evidence over potentially-invalid parametric evidence. The `rank_score` formula codifies this by using `boot_ci_low` (non-parametric) as the primary risk-adjusted input rather than any parametric mean.

**Normalization.** All `rank_score` inputs are **min-max normalized to [0, 1]** across the active universe at each re-rank. Min-max was chosen over z-score because it preserves ordinal position exactly and because the ranking gate is already handled by the binary BH-FDR term; z-scoring would re-introduce implicit Gaussianity assumptions where none are warranted for a fat-tailed return distribution.

### The two systems compared

Both systems exploit the same dealer-hedging mechanic. They differ in *which temporal slice of the pinning arc they trade* and *how risk is defined*.

| Dimension | Original (Metal_Project) | Score (spread_score) |
|---|---|---|
| **Hold duration** | **~5 trading days** (enter D-8, exit D-3) | **Multi-week** (enter 2–3 weeks before OpEx, exit D-3 or early) |
| **Temporal slice** | Sharpest part of the hedging arc | Full pin-formation arc including IV & gamma buildup |
| **Primary structure** | Single credit spreads (bull put or bear call) anchored at max pain | Iron butterflies / iron condors anchored at max pain |
| **Entry filters** | momentum + rank-tier + credit/width ≥ 0.20 + OI ≥ 500 + BA ≤ 15% + directional agreement | 7-metric composite ≥ 0.45 + IV rank ≥ 0.40 + credit/width ≥ 0.35 + OI ≥ 100 |
| **Entry P&L model** | 10K-path Monte Carlo on empirical returns | 7-metric volatility-regime composite (IVR, IVP, VRP, GEX z, delta, skew, charm, VIX term) |
| **Risk management** | **3.5% stop-limit on stock-only positions**; spreads defined-risk by long leg | Defined-risk by spread structure; **no stock-only trades**; shadow roll at \|Δ\|≥0.50 → MP, \|Δ\|>0.65 → ATM |
| **Exit strategies tracked** | D-3 MOC only | Three in parallel — Hold-to-D3, Sosnoff 80%-of-credit, Shadow Roll |
| **DB tables** | `spread_recommendations`, `spread_cycle_summary` | `spread_score_trades`, `spread_score_daily`, `spread_score_roll_shadows` |

The duration difference is the central scientific question: the Original system isolates the 5-day window where dealer hedging pressure concentrates most sharply, while the Score system holds through the full arc and relies on a richer volatility-regime filter to decide whether entry is warranted in the first place. If the 5-day P&L captured by Original is already the dominant share of what the Score system collects over its multi-week hold, the two represent one edge extracted at two efficiencies and Original wins on capital turnover. If the two capture disjoint P&L, they are two distinct edges and a merged book benefits from running both.

### Portfolio-level risk management

Filter and ranking operate per-symbol. Risk control operates per-book:

- **MAX_POSITIONS = 25** hard cap on funded positions from a $100,000 account. Surplus GO signals are placed in a NOT SELECTED section with a one-line exclusion reason and no capital allocated.
- **Score-weighted sizing with floor and cap** — each selected symbol receives `suggested_dollars = $100K × (score_weight clamped to [3%, 12%])`. The 3% floor prevents negligible positions; the 12% cap prevents over-concentration. High-rank symbols receive 2–3× the allocation of marginal ones.
- **Sector exposure advisory** — soft cap of 4 positions and 35% weight per sector. The SOUL renders an explicit SECTOR EXPOSURE warning when any sector breaches the cap; tie-breaks within 0.02 `rank_score` prefer the under-represented sector. Advisory only — quantitative rank still wins the slot.
- **Watching embargo** — `watching`-status symbols are never recommended for entry. If a held position's symbol flips to `watching` mid-hold, the SOUL issues a hard priority-exit instruction with the original watch note quoted verbatim.
- **Mandatory 3.5% stop-limit on stock-only positions** with ~0.5% buffer. Crash-validated against 2020 COVID (momentum filter blocked 30 / 31 symbols; hybrid-model stop triggered 6× over the year) and cycle #1 (XOM / XLE regime losses would have been larger absent stops).
- **Defined-risk spread structures** — the long leg in every credit spread bounds maximum loss. Iron-butterfly and iron-condor structures common in the Score book cap per-contract max loss to narrow amounts (typically $30–$150).

Above the portfolio, at the *universe* level: the momentum filter itself is the primary defense against correlated drawdowns. When the broad market is in a crash regime, momentum is negative across most symbols and the book shrinks to near-cash by construction. The March 2020 stress test is the canonical validation — momentum blocked 30 of 31 confirmed symbols, leaving a one-position book during the worst single-month equity crash in a decade.

### Symbol selection algorithm

**Layer 1 — static `rank_score`** (recomputed only when the universe changes):

```
rank_score = 0.20·norm(boot_ci_low)
           + 0.20·norm(config_b_sharpe)
           + 0.20·norm(return_consistency)
           + 0.15·norm(config_b_n)
           + 0.15·norm(−worst_return)
           + 0.10·bh_fdr_pass
```

Bucketed into tiers: **Core** (≥ 0.55) · **Strong** (≥ 0.40) · **Selective** (≥ 0.25) · **Monitor** (< 0.25).

**Layer 2 — per-cycle `cycle_score`** (provisional, under empirical audit):

```
cycle_score = rank_score × momentum_magnitude_adjustment
                         × gamma_multiplier
                         × OI_concentration_multiplier
```

Three momentum-adjustment variants (stepped / linear / log) are logged in parallel; the empirical winner survives the audit, the other two are cut. This is the clearest scheduled cut in the project: one of three must die after sufficient cycles.

**Capital allocation:** See *Portfolio-level risk management* above.

The entire selection pipeline — from candidate CSV ingestion to ranked recommendation — is DB-driven and automated. Hardcoded symbol lists exist nowhere in the codebase.

## The AI analytical layer — SOUL

The quantitative pipeline produces a structured per-symbol payload. An Anthropic Claude Fable 5 API call — known internally as the **SOUL** — consumes that payload and produces the final human-readable trading recommendation. The SOUL is not a black-box ranker; it is a constrained reasoning layer operating under an explicit rule hierarchy with audit-logged outputs.

### Input payload (per symbol)

- **Ranking context** — `rank_score`, tier, `boot_ci_low`, Sharpe, `return_consistency`, N, worst return.
- **Current cycle signal** — verdict (GO / NO_MOM / SKIP), `momentum_pct`, `mp_strike`, `mp_dist_pct`, `mp_above_price`, `pcr`, `expected_move`, D-3 → D-2 flag.
- **Market microstructure** — `net_gamma`, `gamma_flip_strike`, `oi_concentration_at_mp`, `pin_confidence`, `stop_loss_flag`.
- **Liquidity flags** — `liquid_puts`, `liquid_calls`, `strike_spacing`, `liquidity_flag`.
- **Open-position state** — P&L to date on any held position, plus a `watched_during_hold` sub-dict if the symbol flipped to watching after entry.
- **Live track record** — symbol-level hit rate and mean return across the forward-test cycles to date, with an N-adequacy flag (PRELIMINARY / SUGGESTIVE / DEVELOPING / ADEQUATE).

### Input payload (run-level)

- **Sector exposure** — current sector distribution + `over_cap_sectors` list.
- **Capital allocation** — `MAX_POSITIONS`, `cutoff_rank_score`, `n_selected`, `n_not_selected`.
- **Market mechanic context** — TOS scan-filter rationale + structural-knowledge block on sizzle regimes, Thursday front-run, Monday slingshot, counter-positioning caution.
- **Regime risks** — output of a web-search regime-risk scan (HIGH severity flags produce inline per-symbol warnings).
- **Run-over-run diff** — `build_run_diff()` tags changes since the previous run (verdict flips, momentum-sign changes, gamma-flip shifts, stop-breach escalations), capped at 5 bullets.

### Rule hierarchy (excerpt)

The SOUL operates under ~20 numbered rules enforced in the system prompt. The core ones:

1. **Rank first.** Order ranked setups by `rank_score` descending; never alphabetize.
2. **`boot_ci_low` is the single most important number** — the pessimistic floor on true mean.
3. **Momentum is binary** — failed momentum = no signal, no exceptions.
4. **MP and PCR are secondary** — adjust conviction, not primary signal.
5. **Skip months are hard stops.**
6. **`watching` symbols are never recommended for entry.**
7. **Hard position cap** — `MAX_POSITIONS = 25`. Only `selected = true` symbols appear in RANKED SETUPS. Surplus GO signals go to NOT SELECTED with a one-line exclusion reason.
8. **Selection rationale is mandatory** — each selected symbol must cite specific metrics and compare itself to the top excluded symbol if near the cutoff.
9. **Live stats at low N are directional only** — PRELIMINARY (N < 10) or SUGGESTIVE (N < 20) forward-test results never override `rank_score` or `boot_ci_low`.
10. **`currently_watching = true` is a hard priority-exit** — not advisory — with the original watch note quoted verbatim.

### Output structure

Every SOUL run produces the same sectioned output, in this order:

| Section | Contents |
|---|---|
| **⚠ ATTENTION** | Run-over-run diff (first by design; easy to miss at the bottom). |
| **CYCLE SUMMARY** | Verdict distribution, universe-level stats. |
| **RANKED SETUPS** | Selected positions with per-symbol rationale and suggested shares. |
| **NOT SELECTED** | Excluded GO signals with one-line reasons. |
| **WATCHING** | Sidelined symbols with the original watch note. |
| **OPEN POSITIONS** | Current P&L and exit guidance on held positions. |
| **SECTOR EXPOSURE** | Rendered only when the soft cap is breached. |
| **REGIME RISKS** | Output of the regime-risk web-search scan; HIGH severity = inline warnings. |

### Audit trail and anti-hallucination discipline

Every SOUL run is persisted: the full JSON payload goes to `monitor_log.payload`, the rendered output to `analysis_runs.analysis`, and per-symbol verdicts to `analysis_signals`. A second, backward-looking SOUL (`exec_summary_soul.py`) generates the post-mortem executive summary under a **five-layer anti-hallucination discipline**: (1) prompt-level rules forbidding novel numbers, (2) payload sanitization stripping interpretive fields, (3) a regex-based output validator cross-checking numbers against the payload, (4) section isolation so the LLM fills only the interpretation and suggested-changes slots, (5) an `exec_summary_runs` audit log. Both SOUL systems are constitutionally hedged: at PRELIMINARY or SUGGESTIVE N, the correct output is explicitly "we don't know yet."

## Timeline — current cycle and iteration cadence

### Current cycle (May 2026 OpEx, 2026-05-15)

- **Score book (MaxPain_Project):** 40 trades already in place, entered 2026-04-16 and 2026-04-17 after the v7.6 filter tightening — 10 iron butterflies, 3 asymmetric iron condors, and 11 single-sided credit spreads across 21 symbols. Daily mark-to-market and 7-metric refresh run on the 4:20 PM ET cron. Shadow-roll simulation also active.
- **Original book (Metal_Project):** entry scheduled for **~2026-05-05 (D-8)**. Monitor + Final Analysis at D-8 close produces GO / NO_MOM / SKIP verdicts for each confirmed symbol; capital allocated across up to 25 selected symbols from the 50-symbol universe. Exit at **~2026-05-12 (D-3)** via MOC orders. Stock-only positions carry the mandatory 3.5% stop-limit.
- **Joint post-mortem:** evening of 2026-05-15, post-OpEx close. `cycle_postmortem.py --live --save` runs once and produces the pin accuracy, signal accuracy, spread calibration, and `BY QUALITY FILTERS` / `BY LIQUIDITY FLAG` / `BY STRIKE SPACING` cross-tabs that constitute the empirical evidence.

### Why the durations differ — the greeks

The two systems target different phases of the dealer-hedging arc, and therefore different greek regimes.

- **Original (~5 trading days, D-8 → D-3) — a gamma trade.** Entering at D-8 positions the book at the leading edge of the gamma ramp. As expiration approaches, option gamma concentrates sharply around at-the-money and max-pain strikes, forcing dealers into increasingly aggressive delta-hedging — that hedging flow is the mechanical engine of the pin. Exiting at D-3 is not discretion, it is risk management: gamma rises non-linearly into the final 72 hours, and any short-spread position held into that window faces explosive delta risk on an adverse move. Delta hedging by dealers creates the edge; gamma blow-up on the trader eats it. The Original system takes the gamma phase cleanly and exits before the singularity.
- **Score (~2–3 weeks) — a vega, charm, and VRP trade.** Entering two to three weeks before OpEx positions the book in the vega-rich pre-pinning window, when implied volatility is elevated and the gamma arc has not yet begun to dominate. Profitability is driven by (a) **vega** — volatility-risk-premium compression as realized volatility resolves to or below implied; (b) **charm at the max-pain strike** — the rate at which the short leg's delta decays passively over time, bleeding the spread toward worthless if the pin is forming; (c) **vanna and IV skew** — cross-sensitivities that shift mid-cycle as IV evolves; (d) **VIX term structure (VIX / VIX3M)** — regime cue for whether the pin is likely to form cleanly. The 7-metric composite exists precisely because at this horizon the *volatility regime*, not the gamma pulse, determines whether the trade works.
- **The greek risk consequence.** A Score position still open at D-3 inherits the gamma risk that the Original system specifically avoids. This is why the Score system runs a shadow-roll simulation at |Δ| ≥ 0.50 (roll to MP) and |Δ| > 0.65 (roll to ATM, tastytrade-style "roll to the money"): the roll exists to keep the short leg out of gamma-explosion territory in the final days. Whether rolling actually adds P&L versus Hold-to-D3 is one of the questions the May post-mortem will answer.

### Iteration cadence — refine until a consistent pattern emerges

Each monthly OpEx produces one cycle of empirical evidence. The decision rule for refinement is the same as the audit's selection rule: the post-mortem cross-tab is the mechanism.

- **After each cycle** the cross-tabs reveal which filter thresholds produced tercile separation in realized P&L. Thresholds that did are kept. Thresholds that did not are tightened, replaced, or cut.
- **Each system is refined independently** cycle-over-cycle. The Original and Score filter sets evolve in parallel; no merge is performed until the evidence supports it.
- **"Consistent pattern" is operationally defined** as three consecutive cycles in which no filter flips its tercile-separation direction — i.e. the same filter rankings emerge cycle after cycle, the same structural differences between Original and Score hold up across regimes, and the symbol-pair winners become predictable rather than coin-flip. *Three cycles is a pragmatic stopping rule rather than a statistically derived threshold; the definition may tighten (to five cycles, or to a Wilcoxon-signed-rank p-value threshold on the paired per-cycle P&L differences) if early data suggests three-cycle windows are noise-dominated.*
- **At that point**, the merge question (union / intersection / winner-take-all) is answerable empirically rather than speculatively, and MaxPain_Project moves from scaffold to production per the AUDIT.md tier plan.

## Final results — evaluation, merits and deficiencies, merge path

*This section is pre-registered. The evaluation protocol, hypothesis space, and candidate merge paths are specified now; empirical findings from the May, June, and subsequent cycles fill in below as data accumulates.*

However elegant a filter set looks in backtest, the final judgment of both systems lives in two numbers: **slippage** (the gap between quoted mid-prices and actual fills, plus per-leg commissions) and **net P&L** after that friction is deducted. A filter that prefers illiquid strikes can look superior on paper and lose the edge at the broker; a system that holds over more days accumulates execution cost the backtest never counted. Every metric below is computed net of costs, not on mid-quote P&L.

**Forward-test vs live disclaimer.** All current Original spread P&L (cycle #1, 23 closed legs) and all current Score spread P&L (40 open trades) are *paper* — forward-test recommendations marked to market through the broker's paperMoney or via computed spread mids. The only *live-money* trading evidence is the cycle #1 stock P&L of +$1,761. This section's framework applies equally to both streams, but the reader should weight paper evidence less than live evidence until paperMoney-vs-live fidelity is separately validated.

### What gets measured

- **Net P&L per contract** — realized, net of commissions and slippage.
- **Slippage audit** — dollar gap between recorded `entry_credit` and actual fill, and between final mark and actual exit fill. Logged per trade, aggregated per system.
- **Sharpe (annualized)** — risk-adjusted net return. Each cycle is one observation; meaningful after ~12 cycles.
- **Max drawdown** — worst cumulative peak-to-trough net P&L per system.
- **Hit rate** — % of trades net-profitable after costs.
- **Capital efficiency** — net P&L ÷ (capital at risk × days held). Directly penalizes longer-duration holds for idle-capital turnover.

### Hypothesis space — why one system might outperform the other

- **Original wins on capital turnover.** If the 5-day D-6 → D-3 window captures most of the P&L that the Score system accumulates over 2–3 weeks, Original's ~4× higher turnover compounds into a materially higher annualized return.
- **Score wins on selection quality.** If the 7-metric composite is genuinely predictive, Score avoids cycles where the pin fails to form — at the cost of idle capital between trades.
- **Original wins on friction.** Two fill events per trade; no rolls; no early exits. Score incurs friction at entry, at each shadow roll, at Sosnoff 80% hits, and at final exit.
- **Score wins on risk-adjusted return.** Iron butterflies and condors carry narrow, defined max loss. If hit rate holds, lower variance per trade produces higher Sharpe despite lower gross P&L.
- **Rolling pays — or it doesn't.** Shadow-roll simulation at |Δ|≥0.50 is the specific question. If rolled trades outperform Hold-to-D3 by more than the roll friction (approximately four leg fills per roll), rolling stays in the merged book. If not, shadow-roll is audit fodder and gets cut.
- **Stop-loss protection matters when regime breaks.** Original's 3.5% stop on stock-only positions bounded XOM / XLE losses in cycle #1. Score has no stock exposure — so a repeat of that regime event costs Score nothing directly, but also excludes it from any stock-only upside Original captures.

### Merits and deficiencies (structural, pre-data)

**Original — Metal_Project, 5-day gamma-window system**

- *Merits:* clean D-8 → D-3 exposure capturing the sharpest part of the gamma arc; stop-limited stock positions with a crash-tested 3.5% threshold (validated March 2020 and cycle #1 XOM / XLE); Monte Carlo uses empirical return distribution (not log-normal), preserving fat tails and per-symbol skew; simple ops model with two fill events per trade; lowest per-cycle friction; no gamma-explosion risk by construction.
- *Deficiencies:* capital idle roughly 75% of the calendar between cycles; no volatility-regime filter — can enter in poor IV conditions; Monte Carlo doesn't model mid-hold IV shifts; stock-only positions still carry gap-through-stop tail risk; structure is the same every cycle, so can't exploit regime-specific opportunities.

**Score — MaxPain_Project, multi-week composite system**

- *Merits:* 7-metric composite explicitly captures the volatility regime before entering; multi-week holds maintain continuous capital deployment; shadow-roll framework mechanizes the "manage losers" decision rather than leaving it to discretion; iron-butterfly structures cap max loss per contract to narrow defined amounts; Sosnoff 80% early-exit bounds held-trade decay risk.
- *Deficiencies:* holds through the full gamma ramp (shadow roll is the intended mitigant but itself unvalidated); roll friction compounds across four legs per roll; the 7-metric composite is an unvalidated filter stack where some metrics may be noise; no stock equivalent, so the system is all-options and cannot capture stock-only momentum plays; more moving parts means more ways to inadvertently overfit.

### Merge candidates

Three non-mutually-exclusive paths, evaluated empirically once the consistent-pattern criterion is met:

- **Union (OR-gate).** Trade any symbol approved by either system. Maximizes coverage; accepts the lower-quality trades from whichever filter is looser in context. Best if the two systems catch disjoint P&L.
- **Intersection (AND-gate).** Trade only when both systems approve. Smallest book, highest conviction, maximum capital per position. Best if the systems agree on the best trades and disagree on the marginal ones.
- **Synthesis (Score filter, Original timing).** *Hypothesized candidate, not an empirical recommendation; carries the lowest projected friction of the three but has zero supporting data yet.* Use Score's 7-metric composite and IV-regime gates to select which symbols to trade; use Original's D-8 → D-3 window to harvest the gamma phase cleanly. Retain shadow-roll only if its audit shows positive net-of-friction P&L. Retain stop-limited stock positions from Original for names where momentum is strong and a liquid spread is unavailable.

### Success criterion for the merged book

The merged book is declared superior to either standalone when it beats both on *net P&L and Sharpe* over a matched cycle window, validated by a paired test across cycles (t-test if Shapiro-Wilk passes on the per-cycle P&L differences, Wilcoxon signed-rank otherwise). **The paired test is meaningless at N ≥ 2 cycles, informative at N ≥ 6, and adequately powered at N ≥ 12.** Until the threshold is met, both systems continue running in parallel and the monthly post-mortem refines each independently. MaxPain_Project transitions from scaffold to production only when this empirical trigger fires — mechanical, not aesthetic, per the AUDIT.md principle that the cross-tab is the mechanism.

## Limitations and threats to validity

The thesis is non-trivial to falsify and the system is non-trivial to defend. The following are the known threats, ranked roughly by severity:

- **Modern-era focus (2019-present).** All symbols except GLD / SLV are studied on a 2019+ window. This era coincides with the rise of zero-DTE options, the retail options boom, and materially different hedging flow patterns than pre-2019. If the structural mechanic partially reflects these era-specific dynamics, future regime shifts (retail participation collapse, regulatory change, market-maker consolidation, widespread 0DTE dominance) could alter the edge. *Mitigation:* continued live forward-testing with post-mortem cross-tabs; monitoring for tercile-separation decay cycle-over-cycle.
- **Single stress test observation (2020 COVID).** The momentum filter's crash-defense behavior is validated against exactly one crash. No guarantee it saves the book in the next crash, which will look different. *Mitigation:* 3.5% stop-limit as a second-line defense; defined-risk spread structures by construction; sector-exposure soft cap.
- **Uneven backtest N across universe.** Early confirmed symbols (SPY, QQQ, GLD, etc.) have N ≈ 50–58 Config B observations; April 2026 additions (VZ, AXP, PFE, etc.) have N ≈ 29–43. Bootstrap CIs are wider on the newer entries. The ranking formula gives lower weight to small-N symbols (`config_b_n` at 15%) rather than excluding them, but a skeptical reader should discount the rank of the newest 10–14 symbols until they accumulate live evidence.
- **paperMoney MOC execution fidelity.** All forward-test fills are paperMoney quotes. MOC behavior in paperMoney may systematically differ from live — particularly on illiquid strikes. The liquidity gate (`check_option_liquidity.py`) partially addresses this, but the discrepancy is untested. *Mitigation:* planned side-by-side live-vs-paper comparison on a subset of symbols before scaling real-money commitment.
- **Gap-through-stop risk.** The 3.5% stop-limit protects against intraday drawdowns but not overnight gaps past the stop. A single earnings surprise, tariff announcement, or overnight geopolitical event can fill the stop at materially worse than −3.5%. *Mitigation:* earnings-calendar exclusion already in place; tariff and geopolitical risk captured (imperfectly) via the regime-risks web-search scan.
- **Operational dependencies.** Daily crons depend on Schwab OAuth token freshness; the April 13 2026 credential rotation silently broke the 4:15 PM cron for three days, losing a D-3 spread mark. *Mitigation:* `Schwab/auth.py` hardened to fail loudly on non-interactive reauth; ongoing work on cron failure alerting.
- **Statistical framework is correlated-test discovery.** BH-FDR is the correct control for this structure, but symbol correlations (especially within sector) mean some validated-per-symbol signals may be partially redundant at the portfolio level. *Mitigation:* sector exposure soft-cap; ranking uses `return_consistency` to favor symbols whose edge is orthogonal across cycles.
- **Selection from a scan-filtered candidate pool.** The universe is constructed by running the event study on symbols that first passed a TOS sizzle-index filter (volume-driven unusual-activity scan). This introduces a selection bias — the system is testing a universe pre-filtered to have elevated options activity. The pattern may not generalize to the broader liquid-option universe. *Mitigation:* the April 2026 OEX (S&P 100) sweep was a partial check — 74 of 79 OEX candidates passed Bonferroni, suggesting the pattern extends beyond the sizzle-scan pool but is not necessarily universal.

**A falsification criterion.** The thesis is considered empirically falsified when *all three* of the following hold simultaneously: (a) three consecutive cycles of *live* trading produce negative net-of-cost P&L across the active book; (b) the losses are distributed across symbols rather than concentrated in a single regime-casualty sector; (c) the 12-cycle rolling Sharpe drops below 0.5 annualized. Condition (a) alone is a bad quarter, not a falsification. Condition (b) rules out regime events masking a preserved edge. Condition (c) sets the statistical significance bar. If all three are met simultaneously, the project retires MaxPain as a production strategy and reconstitutes around whatever structural edge (if any) remains in the evidence.

## Glossary

### Options and greeks

- **MP (max pain)** — strike at which aggregate option-holder P&L is minimized; thought to act as a "pull strike" for dealer-hedged markets.
- **OpEx** — options expiration; the monthly Friday on which listed options expire (standard monthly OpEx is the third Friday).
- **DTE** — days to expiration.
- **D−N / D+N** — trading-day offset from OpEx Friday. D−8 = 8 trading days before OpEx; D−3 = 3 before; D+1 = the Monday after.
- **OI** — open interest; number of outstanding option contracts at a given strike.
- **PCR** — put/call ratio; ratio of put OI to call OI at a given strike or for the whole chain.
- **Gamma** — second derivative of option price w.r.t. underlying price; rises non-linearly near expiration.
- **Gamma flip strike** — the strike at which dealer net gamma crosses zero; dealers are stabilizing (long gamma) above it, destabilizing (short gamma) below it.
- **Delta** — first derivative of option price w.r.t. underlying price; also the hedge ratio used by dealers.
- **Vega** — sensitivity of option price to implied volatility.
- **Charm** — rate of delta decay over time (d²Price / dS dt); matters most for near-term options near the money.
- **Vanna** — cross-sensitivity (d²Price / dS dVol); relevant when IV moves mid-cycle.
- **Theta** — rate of price decay over time; credit-spread sellers are net-theta-positive.

### Volatility metrics

- **IV** — implied volatility.
- **IVR / IV rank** — current IV's position within its trailing 52-week range, scaled 0–1.
- **IVP / IV percentile** — percent of prior trading days on which IV was below its current level.
- **VRP** — volatility risk premium; ATM IV minus trailing 30-day realized volatility.
- **GEX** — gamma exposure; aggregate dealer gamma positioning.
- **VIX term structure** — VIX / VIX3M ratio; contango (> 1) or backwardation (< 1) as a regime cue.
- **Skew** — 25-delta put IV minus 25-delta call IV; proxy for crash-fear pricing.

### Spreads and orders

- **Credit spread** — two-leg position selling one option and buying a further-OTM protective leg; receives net credit at entry, defined max loss.
- **Bull put spread** — short put + longer-OTM long put; profits when the underlying stays above the short strike.
- **Bear call spread** — short call + longer-OTM long call; profits when the underlying stays below the short strike.
- **Iron butterfly** — simultaneous bull put and bear call sharing the same body strike (usually ATM or MP); profits when the underlying pins to that strike.
- **Iron condor** — like an iron butterfly but with the two body strikes separated; broader profit zone, smaller max credit.
- **MOC** — market-on-close; order type that guarantees execution at the official closing print.
- **Sosnoff 80% rule** — convention of closing a short-premium trade early once 80% of the maximum credit has been captured.

### System terms

- **Config B** — canonical D-6 → D-3 event window used across the event study, ranking, and live signals.
- **Layer 1** — static symbol ranking (`rank_score` on `symbol_stats`), recomputed only when the universe changes.
- **Layer 2** — per-cycle ranking adjustment (`cycle_score` on `cycle_signals`), recomputed at each monitor run.
- **SOUL** — the Anthropic Claude Fable 5 analytical prompt producing the Final Analysis output.
- **BH-FDR** — Benjamini-Hochberg false discovery rate correction.
- **Bonferroni** — family-wise error rate correction (α / N).
- **Bootstrap 95% CI (BCa)** — bias-corrected-and-accelerated non-parametric confidence interval on mean return.
- **Return consistency** — lag-1 autocorrelation of Config B returns; positive = edge persists month-to-month.
- **Pin confidence** — 0–1 composite score measuring pin strength from OI concentration, gamma alignment, MP proximity, and PCR balance.
- **Watching** — symbol status meaning "edge is real, conditions temporarily unfavorable"; embargoes entry, triggers priority exit on open holds.
- **AUDIT.md** — feature-by-feature earning-its-keep scorecard tiering the project into Core / Validated / Provisional / Likely Cut / Deferred. Selection criterion: the post-mortem cross-tab.
