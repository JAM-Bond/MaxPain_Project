# CLAUDE.md — MaxPain_Project

Context for Claude Code working in this directory.

## What this is

The future home of the max pain trading system. **Scaffold only** as of 2026-04-17. Being gradually populated as the Metal_Project audit (see `~/Metal_Project/AUDIT.md`) progresses through the May–June 2026 bake-off.

## What lives here now

- `dashboard/` — new 8503 head-to-head Streamlit viewer (in development). Reads the live DB at `~/Metal_Project/data/shared/metal_project.db`. Does not write.
- `notebooks/` — empty. Scratch space for audit analyses.
- `docs/` — planning docs only.

## Authoritative references

**This project does NOT yet have its own SYSTEM_GUIDE or CLAUDE-style full context.** For anything touching trading logic, data pipelines, symbols, or audit decisions, consult:

- `~/Metal_Project/SYSTEM_GUIDE.md` — v7.6, full system reference (~1500 lines)
- `~/Metal_Project/CLAUDE.md` — project-wide instructions, scripts map, never-do list
- `~/Metal_Project/AUDIT.md` — tiered scorecard of features; guides what migrates here

## Rules while this is a scaffold

1. **Do not copy Metal_Project code here yet.** Migration happens after May post-mortem, informed by AUDIT.md verdicts.
2. **Do not duplicate the DB.** The dashboard reads from `~/Metal_Project/data/shared/metal_project.db` during the bake-off. No local DB file.
3. **Do not register the 8503 launchd agent** until the dashboard has something to show. Plist lives in `dashboard/launchd/` as a template only.
4. **Do not move or touch any cron jobs.** 4:15 / 4:20 / 4:30 / 4:45 PM ET crons all point at `~/Metal_Project/` — they stay until merge.
5. **Do not rename the DB.** `metal_project.db` → `maxpain.db` happens at merge, not before.

## First work expected here

The 8503 head-to-head viewer comparing spread_evaluator (original Metal book) vs spread_score_tracker (independent line). See `docs/MERGE_PLAN.md` and AUDIT.md's UI section.
