"""`/team help` — list the staff commands you can use, across all groups.

Framework-aware: introspects the router's registry and shows only the commands
the caller is allowed to run, grouped by department, with both the slash and the
message form.
"""

from staff.framework import StaffCommand, staff_command, design, CommandType
from staff.framework.registry import SLASH_GROUPS
from utils import emojis
from utils.i18n import t

# Display order + a representative emoji per command type.
TYPE_ORDER = [
    (CommandType.DEV, emojis.DEV),
    (CommandType.TEAM, emojis.STAFF),
    (CommandType.MANAGEMENT, emojis.SETTINGS),
    (CommandType.MODERATOR, emojis.BLACKLIST),
    (CommandType.SUPPORT, emojis.SUPPORT),
    (CommandType.COMMUNICATION, emojis.MESSAGE),
]


@staff_command
class HelpCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "help"
    description = "List the staff commands you can use."

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale
        router = bot.get_cog("StaffCommandsRouter")
        if not router:
            await ctx.send(view=design.error(
                t("staff.common.error.title", locale=locale),
                t("staff.common.error.description", locale=locale),
            ))
            return

        # type_value -> list of (label, description)
        commands_by_type: dict = {}
        seen = set()

        async def _allowed(cmd) -> bool:
            ok, _ = await router._has_permission(cmd, ctx.author.id)
            return ok

        for (tv, name), cmd in router.message_index.items():
            if name != cmd.name or id(cmd) in seen:
                continue
            seen.add(id(cmd))
            if await _allowed(cmd):
                commands_by_type.setdefault(tv, []).append((cmd.name, cmd.description))

        for (tv, group), bucket in router.subgroup_index.items():
            local_seen = set()
            for sub, cmd in bucket.items():
                if sub != cmd.name or id(cmd) in local_seen:
                    continue
                local_seen.add(id(cmd))
                if await _allowed(cmd):
                    commands_by_type.setdefault(tv, []).append((f"{group} {cmd.name}", cmd.description))

        fields = []
        for ctype, emoji in TYPE_ORDER:
            entries = commands_by_type.get(ctype.value)
            if not entries:
                continue
            slash_name = SLASH_GROUPS[ctype][0]
            entries.sort(key=lambda e: e[0])
            lines = "\n".join(f"`{label}` — {desc}" for label, desc in entries)
            fields.append({
                "name": f"{emoji} {t(f'staff.team.help.groups.{ctype.value}', locale=locale)} — `/{slash_name}` · `{ctype.value}.`",
                "value": lines,
            })

        if not fields:
            await ctx.send(view=design.info(
                t("staff.team.help.title", locale=locale),
                t("staff.team.help.empty", locale=locale),
            ))
            return

        await ctx.send(view=design.panel(
            "info",
            t("staff.team.help.title", locale=locale),
            t("staff.team.help.subtitle", locale=locale),
            fields=fields,
            emoji=emojis.COMMANDS,
            accent="primary",
        ))
