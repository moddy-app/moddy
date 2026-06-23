"""`/mod interserver_delete` — delete an inter-server message everywhere."""

import logging

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, ConfirmView
from utils import emojis
from utils.i18n import t
from utils.staff_logger import staff_logger

logger = logging.getLogger("moddy.staff.mod.interserver_delete")


@staff_command
class InterserverDeleteCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    name = "interserver_delete"
    permission = "interserver_delete"
    description = "Delete an inter-server message from all servers."
    options = [
        SlashOption("moddy_id", "string", "The Moddy message id.", required=True),
    ]

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale
        moddy_id = (ctx.opt("moddy_id") or "").strip().upper()
        if not moddy_id:
            await ctx.send(view=design.invalid_usage(locale, "mod.interserver_delete <moddy_id>"))
            return

        data = await bot.db.get_interserver_message(moddy_id)
        if not data:
            await ctx.send(view=design.error(
                t("staff.mod.interserver.notfound_title", locale=locale),
                t("staff.mod.interserver.notfound", locale=locale, id=f"`{moddy_id}`"),
            ))
            return
        if data["status"] == "deleted":
            await ctx.send(view=design.warning(
                t("staff.mod.interserver.already_deleted_title", locale=locale),
                t("staff.mod.interserver.already_deleted", locale=locale, id=f"`{moddy_id}`"),
            ))
            return

        async def _do_delete(interaction):
            deleted = await _purge(bot, data)
            await bot.db.delete_interserver_message(moddy_id)
            if staff_logger:
                await staff_logger.log_action(
                    "Inter-Server Message Deleted", ctx.author,
                    f"Deleted inter-server message {moddy_id}",
                    additional_info={"Deleted": f"{deleted} messages"},
                )
            logger.info("Inter-server message %s deleted by %s", moddy_id, ctx.author.id)
            return design.success(
                t("staff.mod.interserver.deleted_title", locale=locale),
                t("staff.mod.interserver.deleted", locale=locale, id=f"`{moddy_id}`", count=deleted),
            )

        await ctx.send(view=ConfirmView(
            bot=bot, author_id=ctx.author.id, locale=locale,
            title=t("staff.mod.interserver.confirm_title", locale=locale),
            description=t("staff.mod.interserver.confirm", locale=locale, id=f"`{moddy_id}`"),
            on_confirm=_do_delete, emoji=emojis.DELETE,
        ))


async def _purge(bot, data) -> int:
    """Delete all relayed copies + the original. Returns relayed delete count."""
    deleted = 0
    for relayed in data.get("relayed_messages", []):
        try:
            guild = bot.get_guild(relayed["guild_id"])
            channel = guild.get_channel(relayed["channel_id"]) if guild else None
            if not channel:
                continue
            msg = await channel.fetch_message(relayed["message_id"])
            await msg.delete()
            deleted += 1
        except discord.NotFound:
            pass
        except Exception as exc:
            logger.error("Error deleting relayed message %s: %s", relayed.get("message_id"), exc)
    try:
        guild = bot.get_guild(data["original_guild_id"])
        channel = guild.get_channel(data["original_channel_id"]) if guild else None
        if channel:
            original = await channel.fetch_message(data["original_message_id"])
            await original.delete()
    except Exception:
        pass
    return deleted
