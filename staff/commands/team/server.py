"""`/team server` — detailed information about a server Moddy is in.

Merges the former ``t.serverinfo`` and ``t.server`` into one command: Discord
information plus Moddy database attributes.
"""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_guild_id
from utils import emojis
from utils.i18n import t


@staff_command
class ServerCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "server"
    aliases = ("serverinfo",)
    description = "Detailed info about a server (Discord + Moddy data)."
    options = [
        SlashOption("guild_id", "string", "Target guild id.", required=True),
    ]

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale
        gid = parse_guild_id(ctx.opt("guild_id") or "")
        if not gid:
            await ctx.send(view=design.invalid_usage(locale, "t.server <guild_id>"))
            return

        guild = bot.get_guild(gid)
        if not guild:
            await ctx.send(view=design.error(
                t("staff.team.server_notfound_title", locale=locale),
                t("staff.team.server_notfound", locale=locale, id=f"`{gid}`"),
            ))
            return

        humans = sum(1 for m in guild.members if not m.bot)
        bots = guild.member_count - humans if guild.member_count else 0
        owner = f"<@{guild.owner_id}> (`{guild.owner_id}`)"

        fields = [{
            "name": f"{emojis.INFO} {t('staff.team.server.basic', locale=locale)}",
            "value": (
                f"**{t('staff.team.server.name', locale=locale)}:** {guild.name}\n"
                f"**ID:** `{guild.id}`\n"
                f"**{t('staff.team.server.owner', locale=locale)}:** {owner}\n"
                f"**{t('staff.team.server.created', locale=locale)}:** <t:{int(guild.created_at.timestamp())}:R>"
            ),
        }, {
            "name": f"{emojis.USER} {t('staff.team.server.members', locale=locale)}",
            "value": (
                f"**{t('staff.team.server.total', locale=locale)}:** `{guild.member_count:,}`\n"
                f"**{t('staff.team.server.humans', locale=locale)}:** `{humans:,}`\n"
                f"**{t('staff.team.server.bots', locale=locale)}:** `{bots:,}`"
            ),
        }, {
            "name": f"{emojis.COMMANDS} {t('staff.team.server.channels', locale=locale)}",
            "value": (
                f"**Text:** `{len(guild.text_channels)}` • **Voice:** `{len(guild.voice_channels)}`\n"
                f"**{t('staff.team.server.categories', locale=locale)}:** `{len(guild.categories)}` • "
                f"**{t('staff.team.server.roles', locale=locale)}:** `{len(guild.roles)}`"
            ),
        }, {
            "name": f"{emojis.STAR} {t('staff.team.server.boost', locale=locale)}",
            "value": f"**{t('staff.team.server.level', locale=locale)}:** `{guild.premium_tier}` • **{t('staff.team.server.boosts', locale=locale)}:** `{guild.premium_subscription_count}`",
        }]

        if bot.db:
            try:
                guild_data = await bot.db.get_guild(gid)
                attributes = guild_data.get("attributes", {}) or {}
                if attributes:
                    attr_lines = [f"`{k}`" + (f": `{v}`" if v is not True else "") for k, v in attributes.items()]
                    fields.append({
                        "name": f"{emojis.SETTINGS} {t('staff.team.attributes', locale=locale)}",
                        "value": " • ".join(attr_lines),
                    })
            except Exception:
                pass

        if guild.features:
            features = ", ".join(f.replace("_", " ").title() for f in guild.features[:10])
            fields.append({"name": f"{emojis.WEB} {t('staff.team.server.features', locale=locale)}", "value": features})

        await ctx.send(view=design.panel(
            "info",
            t("staff.team.server.title", locale=locale, name=guild.name),
            "",
            fields=fields,
            emoji=emojis.WEB,
            accent="primary",
        ))
