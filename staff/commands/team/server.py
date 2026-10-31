"""`/team server` — detailed information about a server Moddy is in.

Merges the former ``t.serverinfo`` and ``t.server`` into one command: Discord
information plus Moddy database attributes.
"""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_guild_id
from utils import emojis
from utils.i18n import t
from utils.members import fetch_all_members


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
            # Not on Moddy: maybe one of the mini-bots is there.
            view = await minibot_server_view(bot, gid, locale) \
                if not getattr(bot, "app_id", None) else None
            await ctx.send(view=view or design.error(
                t("staff.team.server_notfound_title", locale=locale),
                t("staff.team.server_notfound", locale=locale, id=f"`{gid}`"),
            ))
            return

        # Guilds are not chunked at startup (config.CHUNK_GUILDS_AT_STARTUP), so
        # the split is pulled on demand here rather than counted off a partial
        # cache, which would silently under-report. `None` when the pull fails.
        members = await fetch_all_members(guild, cache=False)
        if members is None:
            humans_str = bots_str = "?"
        else:
            humans = sum(1 for m in members if not m.bot)
            bots = (guild.member_count - humans) if guild.member_count else (len(members) - humans)
            humans_str, bots_str = f"{humans:,}", f"{bots:,}"
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
                f"**{t('staff.team.server.humans', locale=locale)}:** `{humans_str}`\n"
                f"**{t('staff.team.server.bots', locale=locale)}:** `{bots_str}`"
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

            # Mini-bots: which of our apps are here, and which modules they run
            # instead of Moddy (docs/features/MINIBOTS.md).
            try:
                from minibots.ownership import MODDY_APP, config_app, is_foreign
                from minibots.spec import app_display_name
                apps = sorted(a for a in await bot.db.apps_in_guild(gid) if a != MODDY_APP)
                stored = (guild_data.get("data", {}) or {}).get("modules", {}) or {}
                owned = sorted(
                    f"`{module_id}` → `{config_app(config)}`"
                    for module_id, config in stored.items() if is_foreign(config, MODDY_APP)
                )
                if apps or owned:
                    lines = [f"**{t('staff.team.server.minibots_present', locale=locale)}:** "
                             + (", ".join(f"`{app_display_name(a)}`" for a in apps) or "`-`")]
                    if owned:
                        lines.append(f"**{t('staff.team.server.minibots_modules', locale=locale)}:** "
                                     + " • ".join(owned))
                    fields.append({
                        "name": f"{emojis.MODDY} {t('staff.team.server.minibots', locale=locale)}",
                        "value": "\n".join(lines),
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


async def minibot_server_view(bot, guild_id: int, locale: str):
    """The server as a mini-bot sees it (Moddy is not there), or None."""
    from minibots.queries import ask
    from minibots.spec import app_display_name
    results = await ask(bot, "guild_info", {"guild_id": str(guild_id)}) or {}
    found = [(app, info) for app, info in sorted(results.items())
             if isinstance(info, dict) and info.get("id")]
    if not found:
        return None
    app, info = found[0]
    apps = ", ".join(f"`{app_display_name(a)}`" for a, _ in found)
    fields = [{
        "name": f"{emojis.INFO} {t('staff.team.server.basic', locale=locale)}",
        "value": (
            f"**{t('staff.team.server.name', locale=locale)}:** {info['name']}\n"
            f"**ID:** `{info['id']}`\n"
            f"**{t('staff.team.server.owner', locale=locale)}:** <@{info['owner_id']}> (`{info['owner_id']}`)\n"
            f"**{t('staff.team.server.created', locale=locale)}:** <t:{info['created']}:R>"
        ),
    }, {
        "name": f"{emojis.USER} {t('staff.team.server.members', locale=locale)}",
        "value": f"**{t('staff.team.server.total', locale=locale)}:** `{info['members']:,}`",
    }, {
        "name": f"{emojis.COMMANDS} {t('staff.team.server.channels', locale=locale)}",
        "value": (
            f"**Text:** `{info['text']}` • **Voice:** `{info['voice']}`\n"
            f"**{t('staff.team.server.categories', locale=locale)}:** `{info['categories']}` • "
            f"**{t('staff.team.server.roles', locale=locale)}:** `{info['roles']}`"
        ),
    }, {
        "name": f"{emojis.STAR} {t('staff.team.server.boost', locale=locale)}",
        "value": f"**{t('staff.team.server.level', locale=locale)}:** `{info['premium_tier']}` • "
                 f"**{t('staff.team.server.boosts', locale=locale)}:** `{info['boosts']}`",
    }]
    return design.panel(
        "info", t("staff.team.server.title", locale=locale, name=info["name"]),
        t("staff.team.server.via_minibot", locale=locale, apps=apps),
        fields=fields, emoji=emojis.WEB, accent="primary",
    )
