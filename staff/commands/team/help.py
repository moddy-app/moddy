"""`/team help` — interactive staff command menu.

Shows a department picker (Select) using the staff role badges; choosing a
department lists the commands you can run there, with both the slash and message
forms. Framework-aware: introspects the router registry and filters by the
caller's permissions.
"""

import discord
from discord import ui

from staff.framework import StaffCommand, staff_command, design, CommandType
from staff.framework.registry import SLASH_GROUPS
from utils import emojis
from utils.i18n import t
from cogs.error_handler import BaseView

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


class HelpView(BaseView):
    """Department picker + command list. Author-checked, short-lived."""

    def __init__(self, *, bot, author_id: int, locale: str, data: dict):
        super().__init__(timeout=300)
        self.bot = bot
        self.author_id = author_id
        self.locale = locale
        self.data = data  # type_value -> [(label, description), ...]
        # Departments available to this user, in display order.
        self.available = [ct for ct in TYPE_ORDER if data.get(ct.value)]
        self.selected = self.available[0].value if self.available else None
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
        options = []
        for ct in self.available:
            entries = self.data.get(ct.value, [])
            options.append(discord.SelectOption(
                label=t(f"staff.team.help.groups.{ct.value}", locale=loc),
                value=ct.value,
                emoji=discord.PartialEmoji.from_str(DEPT_BADGE.get(ct.value)) if DEPT_BADGE.get(ct.value) else None,
                description=t("staff.team.help.count", locale=loc, count=len(entries)),
                default=ct.value == self.selected,
            ))
        row = ui.ActionRow()
        select = ui.Select(placeholder=t("staff.team.help.placeholder", locale=loc),
                           min_values=1, max_values=1, options=options, custom_id="help_dept")
        select.callback = self._on_select
        row.add_item(select)
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

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(t("staff.common.not_your_menu", locale=self.locale), ephemeral=True)
            return
        values = interaction.data.get("values", [])
        if values:
            self.selected = values[0]
            self._build()
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer()


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

        data: dict = {}
        seen = set()

        async def _allowed(cmd) -> bool:
            ok, _ = await router._has_permission(cmd, ctx.author.id)
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

        if not data:
            await ctx.send(view=design.info(
                t("staff.team.help.title", locale=ctx.locale),
                t("staff.team.help.empty", locale=ctx.locale),
            ))
            return

        await ctx.send(view=HelpView(bot=ctx.bot, author_id=ctx.author.id, locale=ctx.locale, data=data))
