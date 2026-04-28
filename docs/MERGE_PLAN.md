# Merge Plan — Metal_Project → MaxPain_Project

Companion to `~/Metal_Project/AUDIT.md`. AUDIT decides *what* merges; this doc tracks *how* and *when*.

**Target:** Summer 2026, after May + June 2026 OpEx post-mortems.

---

## Phase 1 — UI experiment (now → May 15 2026)

Goal: stand up the 8503 head-to-head viewer without disturbing live Metal_Project.

- [ ] `dashboard/app.py` — real Streamlit entry (scaffold in place)
- [ ] `dashboard/queries/` — SQL VIEW spec joining `spread_cycle_summary` + `spread_score_trades` on (symbol, opex_date)
- [ ] `dashboard/views/comparison_grid.py` — streamlit-aggrid, row per (symbol, opex)
- [ ] `dashboard/views/per_trade.py` — radar (7-metric) + P&L path + diagnostics timeline
- [ ] `dashboard/views/scorecard.py` — per-cycle 3-strategy + head-to-head totals
- [ ] `dashboard/launchd/com.maxpain.dashboard.plist` — activate when dashboard is non-empty
- [ ] Smoke test against live DB (read-only)

**Rule:** no writes to `metal_project.db` from this dashboard. Read-only while Metal_Project owns the DB.

### Pause point — 2026-04-17

What you have works. The paper bake-off viewer does its job, column defs are documented, selection drill-down works, Scorecard tab covers the cycle-level summary. Adding features now means speculating about what the May data will show — the audit principle (cross-tab is the mechanism) argues against building more UI before there's data to drive its design.

If you pick it up later, the three natural next moves in rough priority:

1. **Per-trade drill-down** — radar chart (7-metric shape per Score trade, entry-vs-current) + P&L path with shadow-roll overlay. Small scope, genuinely informative.
2. **Register the launchd agent** — once the dashboard has earned its persistence. One-line `launchctl load`.
3. **Real book lane** — your stock `trade_log` next to the two paper books in Final results. Bigger design question (stocks aren't spreads), worth deferring until post-May merge.

---

## Phase 2 — Merge (post-May post-mortem, ~May 18–31 2026)

Pre-flight:
- [ ] May post-mortem complete (`cycle_postmortem.py --live --save`)
- [ ] Tier 2 items in AUDIT.md have verdicts written in
- [ ] Metal_Project committed to clean state, tagged `pre-maxpain-merge`

Migration steps:
- [ ] Copy DB: `cp ~/Metal_Project/data/shared/metal_project.db ~/MaxPain_Project/data/shared/maxpain.db`
- [ ] Copy/rewrite surviving scripts per AUDIT Tier 1 + validated Tier 2
- [ ] Port `config/paths.py` — new `ROOT`, new DB name
- [ ] Port `Schwab/auth.py` — use `Path(__file__).resolve().parents[1]` pattern (already hardened)
- [ ] Port cron jobs — update paths to `~/MaxPain_Project/`
- [ ] Disable Metal_Project cron jobs
- [ ] Stop `com.metalproject.dashboard`, load `com.maxpain.dashboard` on 8502 (or keep 8503 and retire 8502 cleanly)
- [ ] Move `AUDIT.md` + `SYSTEM_GUIDE.md` here, update refs
- [ ] First live cycle under MaxPain_Project: June OpEx entry (~D-8 = ~June 10 2026)

Cut list (delete, do not port):
- All Tier 3 items from AUDIT.md
- Two losing momentum-adjustment variants
- Any Tier 2 item whose May post-mortem showed no tercile separation

---

## Phase 3 — Archive (post-merge)

- [ ] `~/Metal_Project/` → final commit, tag `archived`, push, leave in place as read-only
- [ ] Reference link from MaxPain_Project README

---

## Open questions (to resolve before Phase 2)

1. **DB rename strategy** — rename file or keep `metal_project.db` under new home? Prefer rename for symmetry with project name.
2. **Git history** — fresh repo for MaxPain_Project, or `git filter-repo` from Metal_Project? Fresh is simpler and Metal_Project is archived anyway.
3. **Dashboard port** — does 8503 become the permanent home, or reclaim 8502 at merge?
4. **Cron job cutover** — blue/green (run both for a day) or hard swap over a weekend?
