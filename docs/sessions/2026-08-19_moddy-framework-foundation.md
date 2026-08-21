# Session: Moddy Framework Foundation

**Date:** 2026-08-19
**Agent:** Codex

## Summary

Created the initial `moddy` framework package as a compatibility layer over
discord.py.

## Changes Made

- `moddy/` — Public bot, cog, command, UI, interaction, testing, and ext APIs.
- `tests/test_moddy_framework.py` — Contract tests for inheritance, command
  installation contexts, and Components V2 construction.
- `docs/MODDY_FRAMEWORK.md` — API, migration, and testing guide.

## Decisions & Rationale

- Kept the framework thin and did not fork or wrap Discord transport classes.
- Kept `BaseView` and `BaseModal` in their existing location for now because
  they own the production error-reporting path.
- Encoded the global/guild command policy in explicit decorators.

## Known Issues / Follow-ups

- [ ] Migrate UI error handling only after extracting it with dedicated tests.
- [ ] Add framework-level persistent component helpers after agreeing on the
  state-encoding contract.
