# MaxPain_Project

Home for the max pain trading system. Active development project.

**Status (2026-05-17):** primary project. Live DB, all crons, dashboard, daily alert, qualifier, and post-mortem stack all run from here. `~/Metal_Project/` is empty of live infra (DB and Schwab/auth ported 2026-05-15→05-17) and is awaiting acceptance-test wait before deletion.

## Why the rename

"Metal" was accurate when the universe was GLD/SLV only. The system is now 50 symbols across sectors. The edge is max pain pinning via dealer gamma hedging — the name should say so.

## Phased migration

| Phase | When | What moves |
|---|---|---|
| **1. UI experiment** | now → May 15 OpEx | New 8503 head-to-head dashboard lives in `dashboard/`; reads `~/MaxPain_Project/data/shared/maxpain.db` |
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

## Authoritative docs

- [CLAUDE.md](CLAUDE.md) — project-wide Claude Code context
- [docs/TRADING_PLAN.rtf](docs/TRADING_PLAN.rtf) — canonical trading runbook (mechanics + gates)
- [config/SOUL.md](config/SOUL.md) — AI advisor system prompt anchor
- [docs/EXECUTIVE_SUMMARY.md](docs/EXECUTIVE_SUMMARY.md) — historical narrative (pre-2026-05 framing; partly superseded)
