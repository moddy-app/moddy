"""
StaffContext — unifies message-based and slash-based staff command execution.

A single :class:`StaffContext` is built for every invocation (message or slash)
so command bodies never branch on the transport. It exposes the author, guild,
locale and an ``incognito`` flag, plus transport-agnostic helpers:

- :meth:`send` — reply (message) or respond/followup (slash, ephemeral when
  ``incognito`` is set). Returns the sent :class:`discord.Message` so callers
  can ``await msg.edit(...)`` uniformly.
- :meth:`open_modal` — slash opens the modal directly; message (or an
  already-responded slash) sends a button that opens it, satisfying the rule
  "message commands surface a button that opens the modal".
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import discord
from discord import ui

from utils.i18n import t
from utils import emojis
from cogs.error_handler import BaseView


def resolve_message_locale(bot, user_id: int, guild: Optional[discord.Guild]) -> str:
    """Best-effort locale for a message command (no ``interaction.locale``).

    Falls back to the guild's preferred locale, then English. Slash commands
    use the real ``interaction.locale`` instead.
    """
    if guild is not None and getattr(guild, "preferred_locale", None):
        return str(guild.preferred_locale)
    return "en-US"


class StaffContext:
    """Transport-agnostic execution context for a staff command."""

    def __init__(
        self,
        *,
        bot,
        command,
        author: discord.abc.User,
        guild: Optional[discord.Guild],
        channel: Optional[discord.abc.Messageable],
        locale: str,
        interaction: Optional[discord.Interaction] = None,
        message: Optional[discord.Message] = None,
        options: Optional[Dict[str, Any]] = None,
        raw_args: str = "",
        incognito: bool = True,
        cog=None,
    ):
        self.bot = bot
        self.command = command
        self.author = author
        self.guild = guild
        self.channel = channel
        self.locale = locale
        self.interaction = interaction
        self.message = message
        self.options = options or {}
        self.raw_args = raw_args
        self.incognito = incognito
        self.cog = cog

    # --- factories ---------------------------------------------------------

    @classmethod
    def from_interaction(cls, bot, command, interaction: discord.Interaction,
                         options: Dict[str, Any], incognito: bool, cog=None) -> "StaffContext":
        from utils.i18n import get_locale
        return cls(
            bot=bot,
            command=command,
            author=interaction.user,
            guild=interaction.guild,
            channel=interaction.channel,
            locale=get_locale(interaction),
            interaction=interaction,
            options=options,
            incognito=incognito,
            cog=cog,
        )

    @classmethod
    def from_message(cls, bot, command, message: discord.Message,
                     options: Dict[str, Any], raw_args: str, cog=None) -> "StaffContext":
        return cls(
            bot=bot,
            command=command,
            author=message.author,
            guild=message.guild,
            channel=message.channel,
            locale=resolve_message_locale(bot, message.author.id, message.guild),
            message=message,
            options=options,
            raw_args=raw_args,
            incognito=False,  # message replies are public in-channel
            cog=cog,
        )

    # --- option access -----------------------------------------------------

    @property
    def is_slash(self) -> bool:
        return self.interaction is not None

    def opt(self, name: str, default: Any = None) -> Any:
        value = self.options.get(name, default)
        return default if value is None else value

    # --- responding --------------------------------------------------------

    async def send(self, view: Optional[ui.LayoutView] = None,
                   content: Optional[str] = None) -> Optional[discord.Message]:
        """Send the first/follow-up response and return the message."""
        if self.is_slash:
            if self.interaction.response.is_done():
                return await self.interaction.followup.send(
                    content=content, view=view, ephemeral=self.incognito, wait=True
                )
            await self.interaction.response.send_message(
                content=content, view=view, ephemeral=self.incognito
            )
            try:
                return await self.interaction.original_response()
            except discord.HTTPException:
                return None
        # message transport
        if self.cog is not None and hasattr(self.cog, "reply_with_tracking"):
            return await self.cog.reply_with_tracking(self.message, view=view, content=content)
        return await self.message.reply(content=content, view=view, mention_author=False)

    async def defer(self, thinking: bool = True):
        if self.is_slash and not self.interaction.response.is_done():
            await self.interaction.response.defer(ephemeral=self.incognito, thinking=thinking)

    async def open_modal(self, modal_factory: Callable[[], discord.ui.Modal], *,
                         label: str, emoji: Optional[str] = None,
                         prompt_title: Optional[str] = None,
                         prompt_description: Optional[str] = None) -> Optional[discord.Message]:
        """Open a modal (slash) or send a button that opens it (message).

        ``modal_factory`` is called fresh each time the modal is opened so the
        same prompt can be reopened after a failed submit.
        """
        if self.is_slash and not self.interaction.response.is_done():
            await self.interaction.response.send_modal(modal_factory())
            return None

        view = _ModalButtonView(
            bot=self.bot,
            author_id=self.author.id,
            modal_factory=modal_factory,
            label=label,
            emoji=emoji,
            locale=self.locale,
            prompt_title=prompt_title,
            prompt_description=prompt_description,
        )
        return await self.send(view=view)


class _ModalButtonView(BaseView):
    """A short-lived prompt with a single button that opens a modal.

    Used for message commands (and already-responded slash) where a modal
    cannot be sent inline. Author-checked; intentionally not persistent — it
    wraps an in-memory modal factory that cannot survive a restart.
    """

    def __init__(self, *, bot, author_id: int, modal_factory, label: str,
                 emoji: Optional[str] = None, locale: str = "en-US",
                 prompt_title: Optional[str] = None, prompt_description: Optional[str] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.author_id = author_id
        self.modal_factory = modal_factory
        self.locale = locale

        from staff.framework import design
        container = design.make_container("developer")
        container.add_item(ui.TextDisplay(design.title_line(
            emoji or emojis.EDIT,
            prompt_title or t("staff.common.modal_prompt.title", locale=locale),
        )))
        container.add_item(ui.TextDisplay(
            prompt_description or t("staff.common.modal_prompt.description", locale=locale)
        ))
        self.add_item(container)

        row = ui.ActionRow()
        button = ui.Button(
            label=label,
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(emoji) if emoji else None,
        )
        button.callback = self._open
        row.add_item(button)
        self.add_item(row)

    async def _open(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                t("staff.common.not_your_menu", locale=self.locale), ephemeral=True
            )
            return
        await interaction.response.send_modal(self.modal_factory())
