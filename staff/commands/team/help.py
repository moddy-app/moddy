"""`/team help` — interactive staff command menu.

Shows a department picker (Select) using the staff role badges; choosing a
department lists the commands you can run there, with both the slash and message
forms. Framework-aware: introspects the router registry and filters by the
caller's permissions.
"""

import re

import discord
from discord import ui

from staff.framework import StaffCommand, staff_command, design, CommandType
from staff.framework.registry import SLASH_GROUPS
from utils import emojis
from utils.i18n import i18n, t
from utils.components_v2 import create_error_message
from cogs.error_handler import BaseView

_CID_DEPT_TEMPLATE = r"moddy:staffhelp:dept:(?P<owner>\d{1,20})"


def _guarded(callback):
    """Route DynamicItem callback errors to the central error handler.

    A DynamicItem dispatched via ``bot.add_dynamic_items`` has no live
    ``BaseView``, so ``BaseView.on_error`` never fires. Copied from
    ``utils/appeal_views.py``.
    """
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001 — funnel everything to the handler
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


async def _build_help_data(bot, author_id: int) -> dict:
    """Re-derive the permission-filtered command listing for one staff member.

    Shared by the command entry point and the persistent DynamicItem
    callback, so a restarted shell rebuilds exactly the same listing the
    original command would have.
    """
    router = bot.get_cog("StaffCommandsRouter")
    if not router:
        return {}

    data: dict = {}
    seen = set()

    async def _allowed(cmd) -> bool:
        ok, _ = await router._has_permission(cmd, author_id)
        return ok

    for (tv, name), cmd in router.message_index.items():
        if name != cmd.name or id(cmd) in seen:
            continue
        seen.add(id(cmd))
        if await _allowed(cmd):
            data.setdefault(tv, []).append((cmd.name, cmd.description))

    for (tv, group), bucket in router.subgroup_index.items():
        local_seen = set()
        for sub, cmd in bucket.items():
            if sub != cmd.name or id(cmd) in local_seen:
                continue
            local_seen.add(id(cmd))
            if await _allowed(cmd):
                data.setdefault(tv, []).append((f"{group} {cmd.name}", cmd.description))

    return data

# Department display order + the staff badge used to represent it (same icons as
# the /manage staff role selector).
TYPE_ORDER = [
    CommandType.DEV, CommandType.TEAM, CommandType.MANAGEMENT,
    CommandType.MODERATOR, CommandType.SUPPORT, CommandType.COMMUNICATION,
]
DEPT_BADGE = {
    "d": emojis.DEV_BADGE,
    "t": emojis.MODDYTEAM_BADGE,
    "m": emojis.MANAGER_BADGE,
    "mod": emojis.MODERATOR_BADGE,
    "sup": emojis.SUPPORTAGENT_BADGE,
    "com": emojis.COMMUNICATION_BADGE,
}


class HelpDeptSelect(ui.DynamicItem[ui.Select], template=_CID_DEPT_TEMPLATE):
    """Department picker on the /team help card. Auth: owner only."""

    def __init__(self, owner_id: int, *, locale: str = "en-US",
                 available=None, selected: str = None, data: dict = None):
        available = available or []
        data = data or {}
        options = [
            discord.SelectOption(
                label=t(f"staff.team.help.groups.{ct.value}", locale=locale),
                value=ct.value,
                emoji=discord.PartialEmoji.from_str(DEPT_BADGE.get(ct.value)) if DEPT_BADGE.get(ct.value) else None,
                description=t("staff.team.help.count", locale=locale, count=len(data.get(ct.value, []))),
                default=ct.value == selected,
            )
            for ct in available
        ] or [discord.SelectOption(label="—", value="none")]
        super().__init__(
            ui.Select(
                placeholder=t("staff.team.help.placeholder", locale=locale),
                min_values=1, max_values=1, options=options,
                custom_id=f"moddy:staffhelp:dept:{owner_id}",
                disabled=not available,
            )
        )
        self.owner_id = owner_id
        self.locale = locale

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Select, match: re.Match):
        return cls(int(match["owner"]), locale=i18n.get_user_locale(interaction))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            locale = i18n.get_user_locale(interaction)
            await interaction.response.send_message(
                view=create_error_message(
                    t("errors.not_your_message.title", locale=locale),
                    t("errors.not_your_message.description", locale=locale),
                ),
                ephemeral=True,
            )
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        values = self.item.values
        selected = values[0] if values else None
        data = await _build_help_data(bot, interaction.user.id)
        view = HelpView(bot=bot, author_id=interaction.user.id, locale=locale, data=data, selected=selected)
        await interaction.response.edit_message(view=view)


class HelpView(BaseView):
    """Department picker + command list. Persistent: yes (via HelpDeptSelect).

    Auth: owner only — enforced by the encoded owner_id on the select, NOT
    by interaction_check (which cannot work on a restarted shell).
    """

    __persistent__ = True

    def __init__(self, *, bot=None, author_id: int = None, locale: str = "en-US",
                 data: dict = None, selected: str = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.author_id = author_id
        self.locale = locale
        self.data = data or {}  # type_value -> [(label, description), ...]
        # Departments available to this user, in display order.
        self.available = [ct for ct in TYPE_ORDER if self.data.get(ct.value)]
        self.selected = selected or (self.available[0].value if self.available else None)
        self._build()

    def _build(self):
        self.clear_items()
        loc = self.locale
        container = design.make_container("primary")
        container.add_item(ui.TextDisplay(
            f"{design.title_line(emojis.COMMANDS, t('staff.team.help.title', locale=loc))}\n"
            f"-# {t('staff.team.help.subtitle', locale=loc)}"
        ))

        # Department select.
        row = ui.ActionRow()
        row.add_item(HelpDeptSelect(
            self.author_id or 0, locale=loc, available=self.available,
            selected=self.selected, data=self.data,
        ))
        self.add_item(row)

        # Selected department's commands.
        if self.selected:
            ct = CommandType(self.selected)
            slash = SLASH_GROUPS[ct][0]
            badge = DEPT_BADGE.get(self.selected, "")
            dept_name = t(f"staff.team.help.groups.{self.selected}", locale=loc)
            entries = sorted(self.data.get(self.selected, []), key=lambda e: e[0])
            lines = "\n".join(f"`/{slash} {label}` · `{self.selected}.{label}`\n-# {desc}"
                              for label, desc in entries)
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(f"{badge} **{dept_name}**\n{lines}"))

        self.add_item(container)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: owner only — owner_id is encoded in the select's
        custom_id and compared to interaction.user.id on click."""
        bot.add_dynamic_items(HelpDeptSelect)


@staff_command
class HelpCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "help"
    description = "Browse the staff commands you can use."

    async def execute(self, ctx):
        router = ctx.bot.get_cog("StaffCommandsRouter")
        if not router:
            await ctx.send(view=design.error(
                t("staff.common.error.title", locale=ctx.locale),
                t("staff.common.error.description", locale=ctx.locale),
            ))
            return

        data = await _build_help_data(ctx.bot, ctx.author.id)

        if not data:
            await ctx.send(view=design.info(
                t("staff.team.help.title", locale=ctx.locale),
                t("staff.team.help.empty", locale=ctx.locale),
            ))
            return

        await ctx.send(view=HelpView(bot=ctx.bot, author_id=ctx.author.id, locale=ctx.locale, data=data))
