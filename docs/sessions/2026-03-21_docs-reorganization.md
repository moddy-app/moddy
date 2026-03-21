# Session: Documentation Reorganization

**Date:** 2026-03-21
**Agent:** Claude Code (Opus 4.6)

## Summary

Complete reorganization and rewrite of the project documentation (`/docs/`) for better AI agent usability, plus creation of a detailed `CLAUDE.md` at the project root.

## Changes Made

### New Files
- `/CLAUDE.md` — Comprehensive AI agent guide with full project architecture, mandatory coding rules, documentation index, design patterns, and quick reference guides
- `/docs/INTERNAL_API.md` — Merged from 3 redundant API docs into one consolidated reference
- `/docs/sessions/README.md` — Template and conventions for session logs
- `/docs/sessions/2026-03-21_docs-reorganization.md` — This file

### Renamed Files (consistent UPPER_SNAKE_CASE naming)
- `docs/Components_V2.md` → `docs/COMPONENTS_V2.md`
- `docs/Module_System.md` → `docs/MODULE_SYSTEM.md`
- `docs/Error_Handling.md` → `docs/ERROR_HANDLING.md`
- `docs/emojis.md` → `docs/EMOJIS.md`
- `docs/RAILWAY_ENV_VARS.md` → `docs/RAILWAY.md`

### Deleted Files (replaced by merged or new docs)
- `docs/claude.md` — Replaced by root `/CLAUDE.md`
- `docs/INTERNAL_API_SETUP.md` — Merged into `docs/INTERNAL_API.md`
- `docs/INTERNAL_API_EXAMPLES.md` — Merged into `docs/INTERNAL_API.md`
- `docs/internal-api.md` — Merged into `docs/INTERNAL_API.md`

### Rewritten Files
- `docs/AGENTS.md` — Completely rewritten; removed outdated project structure, removed duplicated DB docs, kept incognito system docs and emoji quick reference, added cross-references to CLAUDE.md
- `docs/DESIGN.md` — Fixed broken cross-references to renamed docs
- `docs/RAILWAY.md` — Fixed broken cross-references
- `docs/MODULE_SYSTEM.md` — Fixed broken cross-reference to Components_V2
- `docs/endpoints/INTEGRATION_COMPLETE.md` — Fixed broken cross-references

## Decisions & Rationale

- **CLAUDE.md at root, not in docs/**: Following the convention that CLAUDE.md should be at the project root for automatic discovery by AI agents
- **Consistent UPPER_SNAKE_CASE naming**: All doc files now use the same naming convention for predictability
- **Merged 3 API docs into 1**: The old setup had significant duplication between `internal-api.md`, `INTERNAL_API_SETUP.md`, and `INTERNAL_API_EXAMPLES.md`. A single `INTERNAL_API.md` is cleaner and easier to maintain
- **Rewrote AGENTS.md instead of deleting**: Kept the incognito system documentation (still relevant and not covered elsewhere) but removed the heavily outdated project structure and duplicated DB docs
- **Sessions directory**: Created with README template to encourage consistent session logging

## Final Documentation Structure

```
/CLAUDE.md                              # Main AI agent entry point
/docs/
├── AGENTS.md                           # Supplementary AI agent docs (incognito, emoji quick ref)
├── BACKEND_INTEGRATION_STATUS.md       # Integration diagnostic (unchanged)
├── COMMANDS.md                         # Slash command guide (unchanged)
├── COMPONENTS_V2.md                    # Components V2 reference (renamed)
├── DATABASE.md                         # Database schema (unchanged)
├── DESIGN.md                           # UI/UX guidelines (refs fixed)
├── EMOJIS.md                           # Custom emoji list (renamed)
├── ERROR_HANDLING.md                   # Error handling guide (renamed)
├── INTERNAL_API.md                     # Bot-Backend API (merged, new)
├── MODULE_SYSTEM.md                    # Module system guide (renamed, refs fixed)
├── RAILWAY.md                          # Deployment & env vars (renamed, refs fixed)
├── STAFF_SYSTEM.md                     # Staff permission system (unchanged)
├── endpoints/                          # Individual API endpoint specs
│   └── ...
└── sessions/                           # AI session logs (new)
    ├── README.md
    └── 2026-03-21_docs-reorganization.md
```

## Known Issues / Follow-ups

- [ ] Some docs are still written in French (COMMANDS.md, DATABASE.md, MODULE_SYSTEM.md) — could be translated to English for consistency
- [ ] BACKEND_INTEGRATION_STATUS.md may be outdated (dated 2026-01-12) — should be verified
