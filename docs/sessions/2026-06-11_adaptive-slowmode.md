# Session — 2026-06-11 · Adaptive Slowmode module

## What was done

Implemented the **Adaptive Slowmode** server module from scratch.

The module monitors configured text channels and automatically adjusts their Discord
slowmode based on real-time message activity, using a contextualised algorithm that
adapts to each server's baseline rather than a fixed universal threshold.

### Algorithm summary

| Step | Detail |
|---|---|
| Activity metric | Messages in a 60-second rolling window × author factor `min(1.5, unique_authors / 5)` |
| Baseline | Per-channel EWMA (`α = 0.05`) — slow-moving average representing "normal" activity |
| Ratio | `current_activity / max(baseline, 0.5)` |
| Level mapping | 6 levels (0–5): level 0 = `min_delay`, level 5 = `max_delay`; levels 1–4 picked from valid Discord values in the range |
| Hysteresis | Down-threshold = up-threshold × 0.65 — prevents oscillation at boundary |
| Rise | Jump directly to target level; 30 s cooldown |
| Descent | One level at a time; 5-minute cooldown (slow descent as requested) |
| Rate limit | Maximum 2 edits/channel per 10 min in practice (respected by cooldowns) |
| Bots / webhooks | Filtered upstream in `ModuleEvents.on_message` |

### Sensitivity presets (ratio thresholds for levels 1–5)

| Preset | L1 | L2 | L3 | L4 | L5 |
|---|---|---|---|---|---|
| `low` | 3× | 6× | 12× | 24× | 48× |
| `medium` | 2× | 4× | 8× | 16× | 32× |
| `high` | 1.5× | 3× | 6× | 12× | 24× |

## Files created

- `modules/adaptive_slowmode.py` — Core module logic
- `modules/configs/adaptive_slowmode_config.py` — `/config` UI panel

## Files modified

- `cogs/config.py` — Added `adaptive_slowmode` branch in `on_module_select`
- `cogs/module_events.py` — Added `on_message` routing to adaptive_slowmode
- `locales/fr.json` — French translations for the module
- `locales/en-US.json` — English translations for the module
- `CLAUDE.md` — Updated project structure

## Configuration stored in DB

Path: `guilds.data.modules.adaptive_slowmode`

```json
{
  "channel_ids": [123456789, 987654321],
  "min_delay": 0,
  "max_delay": 120,
  "sensitivity": "medium"
}
```

## Known limits / follow-ups

- Baseline state is in-memory only — resets to 1.0 on bot restart (by design for v1)
- No per-channel granularity for min/max/sensitivity (global settings apply to all channels)
- No audit log UI in dashboard yet — see backend integration notes below

## Backend / dashboard integration notes

See main session notes for full integration spec.
