"""`/mod case create` — open the case-creation flow for a user or guild."""

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.case._shared import resolve_entity
from utils.i18n import t
from utils.moderation_cases import EntityType
from utils.case_management_views import CaseSelectionView


class _SelectionView(CaseSelectionView):
    """Case selection view safe for ephemeral responses (no message.delete)."""

    def __init__(self, *args, locale: str = "en-US", **kwargs):
        self._locale = locale
        super().__init__(*args, **kwargs)

    async def on_cancel_button(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=design.info(
            t("staff.common.cancelled", locale=self._locale),
            t("staff.common.cancelled_desc", locale=self._locale),
        ))


@staff_command
class CaseCreateCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = "case"
    group_description = "Moderation case management"
    name = "create"
    permission = "case_create"
    description = "Create a moderation case for a user or guild."
    options = [
        SlashOption("user", "user", "Target user.", required=False),
        SlashOption("guild_id", "string", "Target guild id (instead of a user).", required=False),
    ]

    async def execute(self, ctx):
        entity_type, entity_id, entity_name, error = await resolve_entity(ctx)
        if error:
            await ctx.send(view=error)
            return

        if entity_type == EntityType.USER:
            user_data = await ctx.bot.db.get_user(entity_id)
            if user_data["attributes"].get("TEAM") or ctx.bot.is_developer(entity_id):
                await ctx.send(view=design.error(
                    t("staff.mod.case.staff_target_title", locale=ctx.locale),
                    t("staff.mod.case.staff_target", locale=ctx.locale),
                ))
                return

        view = _SelectionView(
            bot=ctx.bot, staff_id=ctx.author.id, entity_type=entity_type,
            entity_id=entity_id, entity_name=entity_name, locale=ctx.locale,
        )
        await ctx.send(view=view)
