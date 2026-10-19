"""
Lookup utility tools: ``/status``, ``/company`` and ``/username``.
See docs/features/TOOLS_LOOKUP.md and the shared contract in docs/features/TOOLS.md.

* ``/status`` — are Discord, GitHub, Cloudflare... up? (Atlassian Statuspage
  pages, 60 s cache). Without argument: an overview with a service select.
* ``/company`` — a French company by name, SIREN or SIRET (open
  recherche-entreprises API).
* ``/username`` — where a username exists, with the community WhatsMyName
  dataset. Heavy by nature (hundreds of outgoing requests), so guarded by a
  per-user cooldown and a bot-wide cap of two simultaneous searches.

Every outgoing request goes through ``bot.public_api``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from services.public_api import PublicAPIError
from tools import company as co
from tools import status_registry as sr
from tools import whatsmyname as wmn
from utils import tool_card as tc
from utils import tools_lookup_views as views
from utils.i18n import i18n
from utils.incognito import add_incognito_option, get_incognito_setting

logger = logging.getLogger("moddy.tools_lookup")

#: Seconds between two edits of the live progress bar (Discord tolerates ~5 edits / 2 s).
PROGRESS_INTERVAL = 2.0


class ToolsLookup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cooldowns = wmn.Cooldowns()
        self.slots = wmn.SearchSlots()

    # ------------------------------------------------------------------ #
    # /status
    # ------------------------------------------------------------------ #

    @app_commands.command(name="status",
                          description="Check whether Discord, GitHub and other services are up")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        service="A service to look at (leave empty for an overview of all of them)",
        incognito="Make response visible only to you")
    @add_incognito_option()
    async def status_command(self, interaction: discord.Interaction,
                             service: Optional[app_commands.Range[str, 1, 40]] = None,
                             incognito: Optional[bool] = None):
        locale = i18n.get_user_locale(interaction)
        ephemeral = get_incognito_setting(interaction)
        requester = None if ephemeral else interaction.user.id

        chosen = None
        if service:
            chosen = sr.resolve_service(service)
            if chosen is None:
                await tc.respond(interaction, ephemeral=True, view=tc.tool_error_card(
                    tc.ToolError("not_found", tool="status", query=service), locale))
                return

        await interaction.response.defer(ephemeral=ephemeral)
        api = self.bot.public_api
        if chosen is not None:
            summary = await sr.fetch_summary(api, chosen)
            view = views.build_status_detail(chosen, summary, locale, requester_id=requester)
        else:
            summaries = await sr.fetch_all(api)
            view = views.build_status_overview(summaries, locale, requester_id=requester)
        await interaction.edit_original_response(view=view, allowed_mentions=tc.NO_MENTIONS)

    @status_command.autocomplete("service")
    async def status_autocomplete(self, interaction: discord.Interaction,
                                  current: str) -> List[app_commands.Choice[str]]:
        return [app_commands.Choice(name=s.name, value=s.key)
                for s in sr.find_services(current, limit=25)]

    # ------------------------------------------------------------------ #
    # /company
    # ------------------------------------------------------------------ #

    @app_commands.command(name="company",
                          description="Look up a French company by name, SIREN or SIRET")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        search="A company name, a SIREN (9 digits) or a SIRET (14 digits)",
        incognito="Make response visible only to you")
    @add_incognito_option()
    async def company_command(self, interaction: discord.Interaction,
                              search: app_commands.Range[str, 1, co.MAX_QUERY_LENGTH],
                              incognito: Optional[bool] = None):
        locale = i18n.get_user_locale(interaction)
        ephemeral = get_incognito_setting(interaction)
        requester = None if ephemeral else interaction.user.id

        query = co.classify_query(search)
        try:
            if query.kind == co.KIND_TEXT and len(query.value) < co.MIN_TEXT_LENGTH:
                raise tc.ToolError("too_short", tool="company", min=co.MIN_TEXT_LENGTH)
            if not query.valid:
                raise tc.ToolError("bad_number", tool="company", number=query.value)
        except tc.ToolError as exc:
            await tc.respond(interaction, view=tc.tool_error_card(exc, locale), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=ephemeral)
        try:
            result = await co.search_companies(self.bot.public_api, query)
            if not result.companies:
                raise tc.ToolError("not_found", tool="company", query=query.value)
        except PublicAPIError as exc:
            await interaction.edit_original_response(
                view=tc.tool_error_card(views.api_error(exc, "company"), locale))
            return
        except tc.ToolError as exc:
            await interaction.edit_original_response(view=tc.tool_error_card(exc, locale))
            return

        if query.kind != co.KIND_TEXT or len(result.companies) == 1:
            company = next((c for c in result.companies if query.kind == co.KIND_TEXT
                            or c.siren == query.value[:9]), result.companies[0])
            view = views.build_company_card(
                company, locale, searched_siret=query.value if query.kind == co.KIND_SIRET else None,
                requester_id=requester)
        else:
            view = views.build_company_results(result, query.value, locale, requester_id=requester)
        await interaction.edit_original_response(view=view, allowed_mentions=tc.NO_MENTIONS)

    # ------------------------------------------------------------------ #
    # /username
    # ------------------------------------------------------------------ #

    @app_commands.command(name="username",
                          description="Find where a username is used across hundreds of sites")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        username="The username to look for",
        incognito="Make response visible only to you")
    @add_incognito_option()
    async def username_command(self, interaction: discord.Interaction,
                               username: app_commands.Range[str, 1, 64],
                               incognito: Optional[bool] = None):
        locale = i18n.get_user_locale(interaction)
        ephemeral = get_incognito_setting(interaction)
        requester = None if ephemeral else interaction.user.id
        user_id = interaction.user.id

        try:
            name = wmn.validate_username(username)
        except wmn.UsernameError as exc:
            await tc.respond(interaction, ephemeral=True, view=tc.tool_error_card(
                tc.ToolError(exc.code, tool="username", max=wmn.MAX_USERNAME), locale))
            return
        wait = self.cooldowns.remaining(user_id)
        if wait > 0:
            await tc.respond(interaction, ephemeral=True, view=tc.tool_error_card(
                tc.ToolError("cooldown", retry=wmn.format_wait(wait)), locale))
            return
        if not self.slots.try_enter():
            await tc.respond(interaction, ephemeral=True, view=tc.tool_error_card(
                tc.ToolError("busy", tool="username"), locale))
            return

        try:
            self.cooldowns.hit(user_id)
            await interaction.response.send_message(
                view=views.username_loading_card(locale, name), ephemeral=ephemeral)
            try:
                outcome = await self._search(interaction, name, locale)
            except tc.ToolError as exc:
                self.cooldowns.clear(user_id)    # nothing was searched: do not punish
                await interaction.edit_original_response(view=tc.tool_error_card(exc, locale))
                return
            payload = views.result_payload(outcome)
            token = await tc.save_state(self.bot, views.STATE_KIND_USERNAME, payload,
                                        ttl=wmn.RESULT_TTL)
            await interaction.edit_original_response(
                view=views.build_username_result(payload, token, 0, locale, requester_id=requester),
                allowed_mentions=tc.NO_MENTIONS)
        finally:
            self.slots.leave()

    async def _search(self, interaction: discord.Interaction, name: str,
                      locale: str) -> wmn.SearchOutcome:
        """Load the dataset, run the search and keep the progress bar moving."""
        api = self.bot.public_api
        try:
            dataset = await wmn.load_dataset(api)
        except (PublicAPIError, ValueError) as exc:
            logger.warning("WhatsMyName dataset unavailable: %s", exc)
            raise tc.ToolError("dataset", tool="username") from exc

        progress = wmn.Progress()
        task = asyncio.ensure_future(wmn.run_search(api, dataset, name, progress=progress))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=PROGRESS_INTERVAL)
                if done:
                    return task.result()
                shown = (progress.done, progress.total) if progress.total else None
                try:
                    await interaction.edit_original_response(
                        view=views.username_loading_card(locale, name, progress=shown))
                except discord.HTTPException:
                    pass                      # a missed frame of the bar is harmless
        finally:
            if not task.done():
                task.cancel()


async def setup(bot):
    await bot.add_cog(ToolsLookup(bot))
