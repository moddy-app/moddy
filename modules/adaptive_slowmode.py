"""
Module Adaptive Slowmode
Adjusts per-channel slowmode automatically based on message activity.
Each monitored channel has its own min/max delay and sensitivity settings.
"""

import asyncio
import copy
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import discord
import logging

from modules.module_manager import ModuleBase
from utils.emojis import TIME

logger = logging.getLogger('moddy.modules.adaptive_slowmode')

# Valid Discord slowmode values (seconds)
VALID_SLOWMODE: List[int] = [0, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 21600]

# Up-thresholds (ratio = activity / baseline) per sensitivity preset.
# Five thresholds → five levels above level 0 (= min_delay).
SENSITIVITY_THRESHOLDS: Dict[str, List[float]] = {
    "low":    [3.0, 6.0, 12.0, 24.0, 48.0],
    "medium": [2.0, 4.0,  8.0, 16.0, 32.0],
    "high":   [1.5, 3.0,  6.0, 12.0, 24.0],
}

# Ratio must drop below (up_threshold × factor) before a level decrease is applied.
HYSTERESIS_FACTOR = 0.65

# EWMA smoothing factor — small value = slow-moving baseline.
EWMA_ALPHA = 0.05

# Message rolling window (seconds).
ACTIVITY_WINDOW = 60

# Background check interval (seconds).
TASK_INTERVAL = 10

# Minimum baseline to prevent division-by-zero on quiet channels.
MIN_BASELINE = 0.5

# Cooldowns between consecutive edits.
INCREASE_COOLDOWN = 30    # seconds — fast rise
DECREASE_COOLDOWN = 300   # seconds — slow descent, one step at a time


@dataclass
class ChannelState:
    """Per-channel in-memory tracking state."""
    baseline: float = 1.0
    current_level: int = 0
    last_increase_time: float = 0.0
    last_decrease_time: float = 0.0
    # Each entry: (timestamp: float, author_id: int)
    message_log: deque = field(default_factory=deque)


class AdaptiveSlowmodeModule(ModuleBase):
    """
    Automatically adjusts per-channel slowmode based on real-time activity.
    Each monitored channel has independent min/max delay and sensitivity settings.

    Algorithm:
      1. Count messages in a 60-second rolling window, weighted by unique authors.
      2. Maintain a per-channel EWMA baseline representing "normal" activity.
      3. Compute ratio = current_activity / baseline.
      4. Map ratio to a slowmode level (0–5) using per-channel sensitivity thresholds,
         with hysteresis to prevent threshold oscillation.
      5. Apply level changes: fast rise (30 s cooldown), slow descent
         (one step every 5 min).

    Config structure (stored in guilds.data.modules.adaptive_slowmode):
      {
        "channels": {
          "<channel_id>": {
            "min_delay":  0,
            "max_delay":  120,
            "sensitivity": "medium"
          },
          ...
        }
      }
    """

    MODULE_ID = "adaptive_slowmode"
    MODULE_NAME = "Adaptive Slowmode"
    MODULE_DESCRIPTION = "Ajuste automatiquement le slowmode selon l'activité"
    MODULE_EMOJI = TIME

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)

        # channel_id (int) → {"min_delay", "max_delay", "sensitivity"}
        self.channels_config: Dict[int, Dict[str, Any]] = {}

        self._channel_states: Dict[int, ChannelState] = {}
        self._task: Optional[asyncio.Task] = None

    # -------------------------------------------------------------------------
    # ModuleBase interface
    # -------------------------------------------------------------------------

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            self.config = config_data

            raw_channels = config_data.get("channels", {})
            # JSON keys are always strings — normalise to int keys.
            self.channels_config = {int(k): v for k, v in raw_channels.items()}

            # Sync channel states: remove stale, add missing.
            for ch_id in list(self._channel_states):
                if ch_id not in self.channels_config:
                    del self._channel_states[ch_id]
            for ch_id in self.channels_config:
                self._channel_states.setdefault(ch_id, ChannelState())

            self.enabled = bool(self.channels_config)

            # Apply min_delay to each channel immediately on config (re)load.
            if self.channels_config:
                asyncio.create_task(self._apply_min_delays())

            return True
        except Exception as e:
            logger.error(f"Error loading adaptive_slowmode config: {e}")
            return False

    async def _apply_min_delays(self):
        """Set each channel's current slowmode to its configured min_delay on load."""
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return
        for ch_id, ch_cfg in self.channels_config.items():
            min_delay = ch_cfg.get("min_delay", 0)
            channel = guild.get_channel(ch_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                continue
            if channel.slowmode_delay == min_delay:
                continue
            try:
                await channel.edit(
                    slowmode_delay=min_delay,
                    reason="Adaptive Slowmode: application du délai minimal après mise à jour de la config",
                )
                state = self._channel_states.setdefault(ch_id, ChannelState())
                state.current_level = 0
                logger.info(
                    f"[Guild {self.guild_id}] #{channel.name}: "
                    f"min_delay applied → {min_delay}s"
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(
                    f"[Guild {self.guild_id}] Could not apply min_delay on #{channel.name}: {e}"
                )

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        channels = config_data.get("channels", {})
        if not channels:
            return False, "Au moins un salon est requis"

        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return False, "Serveur introuvable"

        for ch_id_str, ch_cfg in channels.items():
            try:
                ch_id = int(ch_id_str)
            except (ValueError, TypeError):
                return False, f"ID de salon invalide : `{ch_id_str}`"

            channel = guild.get_channel(ch_id)
            if not channel:
                return False, f"Salon `{ch_id}` introuvable"
            if not isinstance(channel, discord.TextChannel):
                return False, f"Le salon {channel.mention} doit être un salon textuel"
            if not channel.permissions_for(guild.me).manage_channels:
                return False, f"Permission **Gérer les salons** manquante sur {channel.mention}"

            min_delay = ch_cfg.get("min_delay", 0)
            max_delay = ch_cfg.get("max_delay", 120)

            if not isinstance(min_delay, int) or not (0 <= min_delay <= 21600):
                return False, f"Délai minimum invalide pour {channel.mention}"
            if not isinstance(max_delay, int) or not (1 <= max_delay <= 21600):
                return False, f"Délai maximum invalide pour {channel.mention}"
            if min_delay >= max_delay:
                return False, f"Le délai maximum doit être supérieur au minimum pour {channel.mention}"
            if ch_cfg.get("sensitivity", "medium") not in SENSITIVITY_THRESHOLDS:
                return False, f"Sensibilité invalide pour {channel.mention}"

        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        return {"channels": {}}

    # -------------------------------------------------------------------------
    # Lifecycle hooks
    # -------------------------------------------------------------------------

    async def on_enable(self):
        self._start_task()

    async def on_disable(self):
        self._stop_task()

    # -------------------------------------------------------------------------
    # Event handler — called by ModuleEvents cog
    # -------------------------------------------------------------------------

    async def on_message(self, message: discord.Message):
        """Record a message in the rolling window for its channel."""
        if message.channel.id not in self.channels_config:
            return
        state = self._channel_states.setdefault(message.channel.id, ChannelState())
        state.message_log.append((time.monotonic(), message.author.id))

    # -------------------------------------------------------------------------
    # Background task
    # -------------------------------------------------------------------------

    def _start_task(self):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._monitor_loop())

    def _stop_task(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _monitor_loop(self):
        try:
            while self.enabled:
                await asyncio.sleep(TASK_INTERVAL)
                if not self.enabled:
                    break
                try:
                    await self._check_all_channels()
                except Exception as e:
                    logger.error(
                        f"Error in adaptive_slowmode monitor (guild {self.guild_id}): {e}",
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            pass

    # -------------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------------

    async def _check_all_channels(self):
        now = time.monotonic()
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return

        for ch_id, ch_cfg in self.channels_config.items():
            min_delay  = ch_cfg.get("min_delay", 0)
            max_delay  = ch_cfg.get("max_delay", 120)
            sensitivity = ch_cfg.get("sensitivity", "medium")
            thresholds = SENSITIVITY_THRESHOLDS.get(sensitivity, SENSITIVITY_THRESHOLDS["medium"])

            state = self._channel_states.setdefault(ch_id, ChannelState())

            channel = guild.get_channel(ch_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                continue

            # Prune stale entries from the rolling window.
            cutoff = now - ACTIVITY_WINDOW
            while state.message_log and state.message_log[0][0] < cutoff:
                state.message_log.popleft()

            # Compute weighted activity metric.
            if state.message_log:
                count = len(state.message_log)
                unique_authors = len({entry[1] for entry in state.message_log})
                author_factor = min(1.5, unique_authors / 5.0)
                activity = count * author_factor
            else:
                activity = 0.0

            # Update EWMA baseline.
            state.baseline = EWMA_ALPHA * activity + (1.0 - EWMA_ALPHA) * state.baseline

            # Ratio vs. long-term average.
            ratio = activity / max(state.baseline, MIN_BASELINE)

            # Target level with hysteresis.
            target = self._compute_target_level(ratio, state.current_level, thresholds)

            if target == state.current_level:
                continue

            going_up = target > state.current_level

            if going_up:
                apply_level = target
                cooldown   = INCREASE_COOLDOWN
                last_edit  = state.last_increase_time
            else:
                apply_level = state.current_level - 1
                cooldown   = DECREASE_COOLDOWN
                last_edit  = state.last_decrease_time

            if now - last_edit >= cooldown:
                new_delay = self._level_to_delay(apply_level, min_delay, max_delay)
                await self._apply_slowmode(channel, new_delay, state, apply_level, going_up, now)

    @staticmethod
    def _compute_target_level(ratio: float, current_level: int, thresholds: List[float]) -> int:
        """Return the target level for a given ratio, applying hysteresis on the way down."""
        natural = sum(1 for thr in thresholds if ratio >= thr)

        if natural < current_level and current_level > 0:
            down_threshold = thresholds[current_level - 1] * HYSTERESIS_FACTOR
            if ratio >= down_threshold:
                return current_level

        return natural

    @staticmethod
    def _level_to_delay(level: int, min_delay: int, max_delay: int) -> int:
        """Map a level (0–5) to a valid Discord slowmode delay within [min_delay, max_delay]."""
        if level <= 0:
            return min_delay
        if level >= 5:
            return max_delay

        candidates = [v for v in VALID_SLOWMODE if min_delay < v < max_delay]
        if not candidates:
            return max_delay

        step = len(candidates) / 4.0
        idx = min(int((level - 1) * step), len(candidates) - 1)
        return candidates[idx]

    async def _apply_slowmode(
        self,
        channel: discord.TextChannel,
        delay: int,
        state: ChannelState,
        new_level: int,
        going_up: bool,
        now: float,
    ):
        """Apply the computed slowmode delay to the channel."""
        try:
            if channel.slowmode_delay == delay:
                state.current_level = new_level
                return

            direction = "hausse" if going_up else "baisse"
            await channel.edit(
                slowmode_delay=delay,
                reason=f"Adaptive Slowmode: activité en {direction} (niveau {new_level})",
            )

            state.current_level = new_level
            if going_up:
                state.last_increase_time = now
            else:
                state.last_decrease_time = now

            arrow = "↑" if going_up else "↓"
            logger.info(
                f"[Guild {self.guild_id}] #{channel.name}: "
                f"slowmode → {delay}s (level {new_level} {arrow})"
            )

        except discord.Forbidden:
            logger.warning(
                f"Missing Manage Channels permission on #{channel.name} "
                f"(guild {self.guild_id})"
            )
        except discord.HTTPException as e:
            logger.error(f"HTTP error applying slowmode on #{channel.name}: {e}")
