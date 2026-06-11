"""
Module Adaptive Slowmode
Adjusts channel slowmode automatically based on message activity using
EWMA baselines and hysteresis to prevent oscillation.
"""

import asyncio
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

    Algorithm:
      1. Count messages in a 60-second rolling window, weighted by unique authors.
      2. Maintain a per-channel EWMA baseline representing "normal" activity.
      3. Compute ratio = current_activity / baseline.
      4. Map ratio to a slowmode level (0–5) using configurable sensitivity thresholds,
         with hysteresis to prevent threshold oscillation.
      5. Apply level changes: fast rise (30 s cooldown), slow descent
         (one step every 5 min).
    """

    MODULE_ID = "adaptive_slowmode"
    MODULE_NAME = "Adaptive Slowmode"
    MODULE_DESCRIPTION = "Ajuste automatiquement le slowmode selon l'activité"
    MODULE_EMOJI = TIME

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)

        self.channel_ids: List[int] = []
        self.min_delay: int = 0
        self.max_delay: int = 120
        self.sensitivity: str = "medium"

        self._channel_states: Dict[int, ChannelState] = {}
        self._task: Optional[asyncio.Task] = None

    # -------------------------------------------------------------------------
    # ModuleBase interface
    # -------------------------------------------------------------------------

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            self.config = config_data
            self.channel_ids = list(config_data.get("channel_ids", []))
            self.min_delay = int(config_data.get("min_delay", 0))
            self.max_delay = int(config_data.get("max_delay", 120))
            self.sensitivity = config_data.get("sensitivity", "medium")

            # Create state entries for new channels, remove stale ones.
            for ch_id in self.channel_ids:
                self._channel_states.setdefault(ch_id, ChannelState())
            for ch_id in list(self._channel_states):
                if ch_id not in self.channel_ids:
                    del self._channel_states[ch_id]

            self.enabled = bool(self.channel_ids)
            return True
        except Exception as e:
            logger.error(f"Error loading adaptive_slowmode config: {e}")
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        channel_ids = config_data.get("channel_ids", [])
        if not channel_ids:
            return False, "Au moins un salon est requis"

        min_delay = config_data.get("min_delay", 0)
        max_delay = config_data.get("max_delay", 120)

        if not isinstance(min_delay, int) or not (0 <= min_delay <= 21600):
            return False, "Le délai minimum doit être entre 0 et 21600 secondes"
        if not isinstance(max_delay, int) or not (1 <= max_delay <= 21600):
            return False, "Le délai maximum doit être entre 1 et 21600 secondes"
        if min_delay >= max_delay:
            return False, "Le délai maximum doit être supérieur au délai minimum"

        if config_data.get("sensitivity", "medium") not in SENSITIVITY_THRESHOLDS:
            return False, "Sensibilité invalide"

        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return False, "Serveur introuvable"

        for ch_id in channel_ids:
            channel = guild.get_channel(ch_id)
            if not channel:
                return False, f"Salon `{ch_id}` introuvable"
            if not isinstance(channel, discord.TextChannel):
                return False, f"Le salon {channel.mention} doit être un salon textuel"
            if not channel.permissions_for(guild.me).manage_channels:
                return False, f"Permission **Gérer les salons** manquante sur {channel.mention}"

        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        return {
            "channel_ids": [],
            "min_delay": 0,
            "max_delay": 120,
            "sensitivity": "medium",
        }

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
        if message.channel.id not in self.channel_ids:
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

        thresholds = SENSITIVITY_THRESHOLDS.get(self.sensitivity, SENSITIVITY_THRESHOLDS["medium"])

        for ch_id in self.channel_ids:
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
                # Jump directly to the target level on the way up.
                apply_level = target
                cooldown = INCREASE_COOLDOWN
                last_edit = state.last_increase_time
            else:
                # Step down one level at a time on the way down.
                apply_level = state.current_level - 1
                cooldown = DECREASE_COOLDOWN
                last_edit = state.last_decrease_time

            if now - last_edit >= cooldown:
                new_delay = self._level_to_delay(apply_level)
                await self._apply_slowmode(channel, new_delay, state, apply_level, going_up, now)

    @staticmethod
    def _compute_target_level(ratio: float, current_level: int, thresholds: List[float]) -> int:
        """Return the target level for a given ratio, applying hysteresis on the way down."""
        # Natural level: the highest threshold the ratio satisfies.
        natural = sum(1 for thr in thresholds if ratio >= thr)

        # Hysteresis guard: only step down if ratio is well below the current up-threshold.
        if natural < current_level and current_level > 0:
            down_threshold = thresholds[current_level - 1] * HYSTERESIS_FACTOR
            if ratio >= down_threshold:
                return current_level  # Not low enough yet — hold position.

        return natural

    def _level_to_delay(self, level: int) -> int:
        """Map a level (0–5) to a valid Discord slowmode delay within [min_delay, max_delay]."""
        if level <= 0:
            return self.min_delay
        if level >= 5:
            return self.max_delay

        # Build a sorted list of valid Discord values strictly between min and max.
        candidates = [v for v in VALID_SLOWMODE if self.min_delay < v < self.max_delay]

        if not candidates:
            return self.max_delay

        # Distribute levels 1–4 across the candidate list.
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
                f"[Guild {self.guild_id}] #{channel.name}: slowmode → {delay}s "
                f"(level {new_level} {arrow})"
            )

        except discord.Forbidden:
            logger.warning(
                f"Missing Manage Channels permission on #{channel.name} "
                f"(guild {self.guild_id})"
            )
        except discord.HTTPException as e:
            logger.error(f"HTTP error applying slowmode on #{channel.name}: {e}")
