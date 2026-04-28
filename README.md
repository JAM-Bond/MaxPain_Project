# MaxPain_Project

New home for the max pain trading system. Being gradually populated as the Metal_Project + spread_score line is audited and merged.

**Status (2026-04-17):** scaffold only. Nothing here reads the live DB or touches production paths yet. All live trading, crons, and the 8502 dashboard remain in `~/Metal_Project/`.

## Why the rename

"Metal" was accurate when the universe was GLD/SLV only. The system is now 50 symbols across sectors. The edge is max pain pinning via dealer gamma hedging — the name should say so.

## Phased migration

| Phase | When | What moves |
|---|---|---|
| **1. UI experiment** | now → May 15 OpEx | New 8503 head-to-head dashboard lives in `dashboard/`; reads `~/Metal_Project/data/shared/metal_project.db` during the bake-off |
| **2. Merge** | post-May post-mortem | Surviving AUDIT.md items migrate here. DB renamed `maxpain.db`. Crons repointed. Agent `com.maxpain.dashboard` replaces `com.metalproject.dashboard` |
| **3. Archive** | post-merge | `~/Metal_Project/` archived as reproducible research artifact |

## Directory layout

```
~/MaxPain_Project/
├── README.md                 # this file
├── CLAUDE.md                 # Claude Code context
├── dashboard/                # 8503 head-to-head viewer (new, in dev)
│   ├── app.py
│   ├── views/
│   ├── queries/
│   └── launchd/
├── notebooks/                # audit analyses, tercile tests, regime studies
└── docs/
    └── MERGE_PLAN.md         # summer-2026 migration checklist
```

## Authoritative docs (still in Metal_Project)

Until merge, these live at `~/Metal_Project/`:
- [SYSTEM_GUIDE.md](../Metal_Project/SYSTEM_GUIDE.md) — full system reference
- [CLAUDE.md](../Metal_Project/CLAUDE.md) — project-wide Claude Code context
- [AUDIT.md](../Metal_Project/AUDIT.md) — feature-by-feature earning-its-keep scorecard
