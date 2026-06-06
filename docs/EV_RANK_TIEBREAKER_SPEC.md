# EV-Rank Tiebreaker — Spec

_Status: SPEC (queued) · target ready-to-go ≤ 2026-08-15 · author handoff 2026-06-04_

## Purpose

When two or more candidates have **already cleared every gate** (regime, earnings,
window, budget, MA bucket, sector cap, macro cap) and are competing for the same
slot — specifically, when a concentration cap must choose which names to keep at
full size and which to downsize — pick by **live reward/risk geometry** instead of
the current alphabetical tiebreaker.

This is a **tiebreaker among already-qualified names**, NOT a new selection
signal. It does not relitigate any macro/backtest finding
(`project_live_book_selection_signature_test`: entry filters don't lift the
baseline). The claim is only ordinal: among equally-qualified candidates, prefer
the one that pays more when it wins and loses less when it loses.

## What it answers

> "Among equally attractive candidates, which trade makes the most if it wins and
> loses the least if it loses?"

Assumes realtime Schwab chains (available via `lib/schwab_options.fetch_chain_with_greeks`).

## The metric (structure-aware)

Every input is already produced by the live construction path
(`scripts/monitor/trade_construction.py` → `scripts/backtest/structures.open_*`):
`pos.entry_credit`, `pos.notes["wing_width" | "debit" | "capital_efficiency" |
"extrinsic_cushion"]`, and per-leg `delta` (Schwab returns delta on the chain).

**Credit verticals — `bull_put`, `bear_call`, `bull_put_mp`** (the clean, primary case):
- `credit  = pos.entry_credit`
- `wing    = notes["wing_width"]`
- `max_loss = wing − credit`
- `POP ≈ 1 − |short-leg delta|`  (short put/call delta, trader convention; risk-neutral proxy)
- `EV = POP·credit − (1−POP)·max_loss`   (per share; ×100 for per-contract)
- **rank key = `EV / max_loss`**  (expected return per dollar at risk; higher = better)

**ZEBRA — `zebra_tier1`, `zebra_tier2`, `anti_zebra`** (delta-1 debit; reward uncapped):
- defined risk = `debit`; no clean single-delta POP.
- rank key = `notes["capital_efficiency"]` (stock-equivalent delta per dollar at risk),
  with `extrinsic_cushion ≥ 0` as a hard pre-gate (already computed/displayed).

**Inverted fly — `inverted_fly_*`** (long-vol debit):
- `debit = −pos.entry_credit`; `max_profit_per_side = wing − debit`
- rank key = `max_profit_per_side / debit` (reward-to-risk on a move).

> Note: today's over-concentration almost always lands on **ZEBRA tier-2** (see the
> 2026-06-04 qualifier dry-run), so the ZEBRA branch is the one that fires first in
> practice — build/validate it alongside the vertical branch, don't defer it.

## Hard gates run BEFORE the EV sort (EV never overrides risk discipline)

Bake in the loss-cap discipline and the TOS lessons:
1. **`credit > 0`** — reject zero/negative-credit structures outright
   (`project_tos_spread_hacker_experiment`: vendor "P(profit) 98%" debit traps).
2. **`credit/width ≥ G.MIN_CREDIT_WIDTH`** (0.50) — the loss-cap floor
   (`feedback_loss_cap_discipline`; with C/W ≥ 0.5, `max_loss ≤ credit`).
3. Defined-risk / budget gates already applied upstream by the qualifier.

Only candidates passing these get an EV score; the rest keep current behavior.

## Where it plugs in

Replace the **alphabetical** tiebreaker inside the two concentration caps in
`scripts/qualifier/cycle_qualifier.py`:
- `apply_sector_concentration_cap` ranking is `(verdict_rank, symbol)`
- `apply_macro_concentration_cap` ranking is `(verdict_rank, symbol)`

New ranking: `(verdict_rank, −ev_per_risk, symbol)` — GO still beats DOWNSIZE
(qualifier confidence first), then **best reward/risk kept**, alphabetical only as
a final deterministic tiebreak. The top-N by this order keep full size; the rest
downsize (macro cap) / skip (sector cap), exactly as today.

(Optional phase 2: expose a standalone `rank_candidates_by_ev(rows)` for capital
allocation across all GO names, not just within an over-concentrated bucket.)

## Module layout

- **New `lib/trade_ev.py`** — `score_candidate(symbol, structure, expiry) -> EVScore`
  (a dataclass: `credit, wing, max_loss, short_delta, pop, ev, ev_per_risk,
  bidask_width, passes_hard_gates, structure_kind, error`). Reuses the existing
  opener + chain path; factor the raw-number computation out of
  `trade_construction._vertical_metrics` / `_zebra_metrics` / `_inverted_fly_metrics`
  so the construction block and the ranker share ONE source of truth (no formula drift).
- **`cycle_qualifier.py`** — call `score_candidate` lazily inside the caps.

## Performance / Schwab budget

The qualifier currently makes ONE bulk-quote call. Per-candidate chain fetches are
heavier, so:
- **Fetch lazily** — only score candidates inside a bucket that is *over* the cap
  (i.e. only when a tiebreak actually decides something). Buckets at/under the cap
  never trigger a fetch.
- **Cache** `fetch_chain_with_greeks` results per `(symbol, expiry)` for the run.
- This bounds extra calls to the rare over-concentration case; well within limits.

## Persistence (audit, mirrors the regime_primary cols)

Add idempotent columns to `cycle_qualifier_runs` + the parquet:
`ev_per_risk, pop, credit, max_loss, ev_rank_position` (e.g. "kept 2/6 by EV").
Lets the post-mortem ask: did EV-ranked keeps outperform the downsized tail?

## Fail-open

If a chain fetch / construction fails for a candidate, fall back to the current
alphabetical order for that name (treat EV as unknown, sort last among ties).
The qualifier must never fail closed on a Schwab outage — same contract as the
existing `fetch_schwab_spots` budget gate.

## Caveats (state them; don't overclaim)

- **POP-from-delta is a risk-neutral proxy** — ignores skew and drift, so it's
  biased. But it's *consistently* biased across candidates, which is all an
  ordinal tiebreaker needs. We are not claiming the absolute EV is tradeable alpha.
- **Mid-priced selection** (matches the validated cohort selections); the bid-ask
  width is carried as a secondary sort because slippage erodes thin spreads
  (`feedback_patient_limit_rule`).

## Validation & timeline

- Not an alpha claim → no formal backtest gate. Validation = log keep/downsize
  decisions through the paper-test window and let the standing exit-timing
  post-mortem compare EV-kept vs EV-downsized outcomes.
- **Build in July** so it gets real paper-cycle exposure before the live switch
  (~2026-08-19) and clears the ≤ 2026-08-15 ready-to-go target.

## Build checklist

1. [ ] `lib/trade_ev.py` with `score_candidate` + `EVScore`; refactor raw metrics
       out of `trade_construction.py` into a shared helper.
2. [ ] Hard-gate filter (credit>0, C/W≥0.50) before scoring.
3. [ ] Per-run chain cache; lazy-fetch only for over-cap buckets.
4. [ ] Swap alphabetical → `−ev_per_risk` in both concentration caps.
5. [ ] Persist EV audit columns (DB ALTER + parquet).
6. [ ] Fail-open fallback to alphabetical.
7. [ ] Unit test (synthetic over-cap bucket) + live dry-run on a real window.
8. [ ] One paper cycle of keep-decision logging before live.
