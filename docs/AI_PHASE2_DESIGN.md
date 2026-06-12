# AI Advisor Phase 2 — Pre-Cycle Commentary

**Status:** decisions locked 2026-05-17; build in progress
**Drafted:** 2026-05-17

## Decisions (locked, but revisable after first fires)

1. **Cron from day one** — no button-only staging period. Dashboard page displays the latest cached run; no manual Generate button.
2. **Bundle includes open positions, clearly tagged** — open positions appear in a `## Context — Open Positions (not under review)` section, kept structurally separate from the `## Verdict Review (forward)` section so the AI does not conflate "position management" with "new entry decision."
3. **Regime window:** 1d (vs prior close) AND 5d (vs 5-trading-day average) — both anchors included.
4. **Daily-alert annotation ships in Phase 2** — when fresh commentary exists for today, append a ≤500-char summary to the 4:45 PM daily alert.
5. **Versioned prompt file:** `prompts/pre_cycle_commentary/v1.md`. The Python wrapper reads from this path so iterations are diffable.

User note: "once we see what all this looks like, we can make changes." → ship v1 quickly, iterate based on real fires.


**Builds on:** `project_ai_advisory_layer_plan.md` (Phase 2 of the 3-phase plan), `project_ai_advisor_built.md` (Phase 3 / Post-Mortem, shipped 2026-05-10), `lib/ai_macro_brief.py` (Phase 1 macro narrative, exists)
**Architecture rule:** AI is an ADVISOR. Mechanical qualifier remains the trade authority. Phase 2 annotates verdicts; it does not change them, generate new tickers, or predict prices.

---

## 1. What it produces

A daily (or on-demand) narrative that reads the qualifier verdict grid + regime state + macro brief through SOUL.md and emits:

1. **REGIME LANDSCAPE** — current stage (0–4) + 1–5d delta in spy_pct_to_ma200, IVR, term_spread, VRP, VIX. One sentence on whether macro is convergent or divergent with the bull-put signal / H1 / IF gate.
2. **VERDICT REVIEW** — per-GO and per-DOWNSIZE annotation: is the verdict macro-aligned (e.g. GO bull_put in stage 0 with contango+VRP+) or macro-counter (e.g. GO bull_put with curve flattening + Fed hawkish shift). Cite gate state and the macro element that creates the alignment/tension.
3. **CONCENTRATION & CORRELATION** — sector load count, structure mix, any "GO this cycle + already 2 in this sector from prior cycle" flags. Pulls from `spread_score_trades` open positions + `cycle_qualifier_runs` today's GOs.
4. **CYCLE NARRATIVE** — 1–2 short paragraphs synthesizing the above. No trade recommendations.
5. **Closes with one of:** HYPOTHESIS / NO HYPOTHESIS / NEXT QUESTION (per SOUL §Tone).

Every claim cited via SOUL §Quantitative Evidence Anchoring formats, extended with three new ones for Phase 2:

- `[QUALIFIER: opex · symbol · structure · verdict · reason-fragment]`
- `[REGIME: snapshot_date · field=value (Δ vs Nd avg)]`
- `[POSITION: id · symbol · structure · status]`

(Macro citations `[CURVE:]` / `[FEDWATCH:]` / `[FEDNEWS:]` already defined by `lib/ai_macro_brief.py`.)

---

## 2. When it runs

**On-demand:** dashboard button (Page 8). Always available, costs ~$0.10 per fresh run.

**Scheduled (gated):** cron at **9:30 ET weekdays** (5 min after qualifier 9:25). Gate: skip if today's qualifier verdict grid contains zero GO + zero DOWNSIZE rows (pure all-PENDING/SKIP days don't need commentary). When skipped, log "no decision-relevant verdicts; commentary skipped."

Optional second surface: append a short (≤500 char) one-paragraph summary to the 4:45 PM daily alert when fresh commentary exists for the day. Decide after first live runs.

---

## 3. Bundle composition (what the AI sees)

Composed by new file `dashboard/queries/pre_cycle_bundle.py` — mirror of `postmortem_bundle.py` pattern. Sections:

| # | Source | Query |
|---|---|---|
| 1 | **Today's verdict grid** | `cycle_qualifier_runs WHERE run_date = today()`. Group by structure × verdict; list every GO/DOWNSIZE row in full. |
| 2 | **Regime state** | `regime_state WHERE snapshot_date = today()` + the same fields 1d ago and 5d ago for Δ. |
| 3 | **Macro brief** | `lib.macro_brief.build_macro_brief()` + `render_text()`. Same 3 sections used in the daily alert. |
| 4 | **Open positions snapshot** | `spread_score_trades WHERE status='open' AND placed=1`. Just symbol/structure/sector/opex_date — enough for concentration math. |
| 5 | **Gate definitions (lite)** | A static 1-paragraph dump of which gates are wired and what they mean (H1, contango+VRP bull-put, IF term-inv, sector cap of 2). Hand-edited, lives in the prompt file rather than DB. |

Estimated bundle size: 2–4K tokens (comparable to macro brief; smaller than post-mortem's 2.8K + SOUL 4K).

---

## 4. Files to add

```
lib/ai_pre_cycle_commentary.py            # API wrapper (mirror ai_macro_brief.py)
dashboard/queries/pre_cycle_bundle.py     # Bundle composer (mirror postmortem_bundle.py)
dashboard/pages/8_Pre_Cycle.py            # UI: today's bundle preview + Generate button
scripts/monitor/pre_cycle_commentary.py   # Cron entrypoint (gated on GO/DOWNSIZE presence)
```

New DB table:

```sql
CREATE TABLE ai_pre_cycle_cache (
    run_date     TEXT NOT NULL,
    bundle_hash  TEXT NOT NULL,
    response_text TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    elapsed_seconds REAL,
    generated_at TEXT NOT NULL,
    model TEXT NOT NULL,
    PRIMARY KEY (run_date, bundle_hash)
);
```

---

## 5. Cost & cadence

Per fresh run: ~$0.22 (4K SOUL system, 3K user bundle, 3K output, Fable 5 @ $10/$50 per Mtok in/out → $0.07 input + $0.15 output).
Per cached run: $0.
Anthropic prompt cache (5-min TTL) reduces SOUL cost for back-to-back calls.

Expected fire frequency: cron fires weekdays but commentary only generates when verdicts include GO/DOWNSIZE. Realistic: 6–15 fires per OpEx cycle (one per ticker-day during the 12 trading-day entry window). Monthly cost ceiling: ~$3.30 (15 fires × $0.22).

---

## 6. What it MUST NOT do

(restated from `project_ai_advisory_layer_plan.md`)

- **Change verdicts.** GO/DOWNSIZE/PENDING/SKIP come from `cycle_qualifier.py`. Phase 2 reads and annotates; never overrides.
- **Generate new tickers.** Bundle excludes the universe list precisely so the AI cannot invent names outside cohort.
- **Predict prices or "call" the market.** SOUL already forbids this — Phase 2 prompt restates it.
- **Make backtest claims it can't cite.** All quantitative claims must cite via the formats above.

---

## 7. First-use sequencing

| Step | When | Test |
|---|---|---|
| Macro brief lives in cron alert | Mon 5/18 4:45 PM ET | first real fire of `lib/macro_brief` integration |
| Build Phase 2 (this doc) | After macro brief confirmed clean | — |
| Phase 2 dry-run | Day after merge, 9:30 ET | bundle preview only (no API call) |
| Phase 2 first real fire | First weekday with non-zero GO/DOWNSIZE in qualifier | typically 6/2 (45-DTE target for JUL OpEx) |

The current qualifier output (5/15) shows all PENDING/SKIP, so Phase 2's gate would skip — useful for confirming the gate works without burning API. First substantive fire likely 6/2 when the JUL bull_put_45dte / bear_call_45dte / inverted_fly_45dte windows open.

---

## 8. Open questions (please review before build)

1. **Cron-fire from day one, or button-only first?** Layer plan says cron. Recommend button-only for first 5 fires to evaluate output quality, then enable cron.
2. **Include open positions in the bundle?** Recommended yes (for concentration check). Alternative: keep Phase 2 strictly forward-looking (verdicts only) and let the Post-Mortem advisor handle position-level context. Trade-off: forward-only is cleaner mental model; bundled is more useful per call.
3. **Regime delta window:** 1d, 5d, or both? Recommend 1d (vs prior close) + 5d (vs 5-trading-day avg). Two anchors give the AI enough texture without bloating the bundle.
4. **Daily-alert annotation surface:** ship in Phase 2 or defer? Recommend defer — get the standalone page solid first, then decide if 4:45 PM email needs the bundle.
5. **Prompt iteration mechanism:** Phase 2 prompt is going to need revision after 5–10 real fires. Worth checking in a `prompts/pre_cycle_commentary.v1.md` file (versioned) rather than inlining in `ai_pre_cycle_commentary.py`? Recommend yes — easier to diff iterations.

---

## 9. Success criteria

Phase 2 ships when:

- [ ] Cron-gated entry skips on all-PENDING days without burning API
- [ ] First real fire produces commentary that cites every claim via the SOUL formats
- [ ] User reads it and reports: "this told me something the verdict grid alone didn't"
- [ ] No verdict-override or new-ticker hallucinations across first 10 fires
- [ ] Cached re-clicks return instantly with $0 spend

If after 10 fires the output is generic / non-additive vs reading the verdict grid alone → reconsider whether macro context belongs in Phase 2 or whether the gates already capture it. (Per layer plan: "AI must justify itself through cognitive augmentation, not prediction accuracy.")

---

## 10. Effort estimate

- `lib/ai_pre_cycle_commentary.py`: ~1.5h (mirror existing pattern)
- `dashboard/queries/pre_cycle_bundle.py`: ~2h (new SQL + Δ computation)
- `dashboard/pages/8_Pre_Cycle.py`: ~1.5h (mirror Post-Mortem page)
- `scripts/monitor/pre_cycle_commentary.py` + cron: ~1h
- Prompt + first-fire iteration: ~2h
- **Total:** ~8h, matching layer plan estimate.
