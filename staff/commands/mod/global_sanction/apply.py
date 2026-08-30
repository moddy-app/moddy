"""`/mod global apply` — sanction a user and/or servers as one infraction.

The command takes the *targets*; a Modals V2 form takes everything else (level,
reason, duration, grace period), so a full global sanction is one command and
one form. Every target lands in a single **case group**, and the user receives
**one** notice covering all of it.
"""

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.global_sanction._shared import (
    GROUP, GROUP_DESCRIPTION, parse_guild_ids, targets_recap,
)
from utils.global_sanction_views import GlobalSanctionModal, build_group_panel
from utils.i18n import t


@staff_command
class GlobalApplyCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = GROUP
    group_description = GROUP_DESCRIPTION
    name = "apply"
    permission = "global_sanction"
    description = "Apply a global sanction to a user and/or servers (one group)."
    # Answers with a Modal: Discord refuses one on a deferred interaction.
    opens_modal = True
    options = [
        SlashOption("user", "user", "The sanctioned user.", required=False),
        SlashOption("guilds", "string",
                    "Server ids to sanction with them (comma or space separated).",
                    required=False),
    ]

    def parse_message(self, raw: str) -> dict:
        """``mod.global apply <user_id> [guild_id ...]``."""
        parts = (raw or "").split()
        return {
            "user": parts[0] if parts else None,
            "guilds": " ".join(parts[1:]) if len(parts) > 1 else None,
        }

    async def execute(self, ctx):
        user = ctx.opt("user")
        guild_ids = parse_guild_ids(ctx.opt("guilds"))

        # The message transport gives a raw id; resolve it to a real user.
        if isinstance(user, str):
            from staff.framework import parse_user_id
            user_id = parse_user_id(user)
            user = None
            if user_id:
                try:
                    user = await ctx.bot.fetch_user(user_id)
                except discord.HTTPException:
                    user = None

        if user is None and not guild_ids:
            await ctx.send(view=design.invalid_usage(
                ctx.locale, "mod.global apply <user_id> [guild_id ...]"))
            return

        # Never sanction the team itself.
        if user is not None:
            user_data = await ctx.bot.db.get_user(user.id)
            if user_data["attributes"].get("TEAM") or ctx.bot.is_developer(user.id):
                await ctx.send(view=design.error(
                    t("staff.mod.case.staff_target_title", locale=ctx.locale),
                    t("staff.mod.case.staff_target", locale=ctx.locale),
                ))
                return

        locale = ctx.locale
        bot = ctx.bot

        async def _on_done(interaction: discord.Interaction, level, result):
            await interaction.followup.send(
                view=build_group_panel(
                    bot=bot, level=level, group_id=result["group_id"],
                    cases=result["cases"], enforcement=result["enforcement"],
                    notified=result["notified"], locale=locale,
                ),
                ephemeral=True,
            )

        recap = targets_recap(bot, user, guild_ids)

        await ctx.open_modal(
            lambda: GlobalSanctionModal(
                bot=bot, staff_id=ctx.author.id,
                user_id=user.id if user else None, guild_ids=guild_ids,
                targets_recap=recap, locale=locale, on_done=_on_done,
            ),
            label=t("global_sanctions.staff.apply_button", locale=locale),
            prompt_title=t("global_sanctions.staff.apply_title", locale=locale),
            prompt_description=t("global_sanctions.staff.apply_prompt",
                                 locale=locale, targets=recap),
        )
