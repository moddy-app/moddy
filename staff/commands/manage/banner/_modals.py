"""Modals V2 + type-selection view for site/dashboard banners."""

import re

import discord
from discord import ui

from staff.framework import design
from utils import emojis
from utils.i18n import t
from cogs.error_handler import BaseView, BaseModal

VALID_TYPES = ("announcement", "incident", "maintenance", "information", "warning", "resolved")


def _visibility_label(locale: str):
    return t("staff.manage.banner.visibility", locale=locale)[:45]


def _visibility_group(locale: str, dash: bool, web: bool) -> ui.CheckboxGroup:
    return ui.CheckboxGroup(
        options=[
            discord.CheckboxGroupOption(label=t("staff.manage.banner.dashboard", locale=locale), value="dashboard", default=dash),
            discord.CheckboxGroupOption(label=t("staff.manage.banner.website", locale=locale), value="website", default=web),
        ],
        min_values=0, max_values=2, required=False,
    )


async def _save(bot, banner_id, *, msg, btype, icon_svg, color, dash, web):
    if banner_id is not None:
        return await bot.db.update_banner(banner_id, msg, btype, icon_svg, color, dash, web)
    return await bot.db.create_banner(msg, btype, icon_svg, color, dash, web)


class TypedBannerModal(BaseModal):
    """Create/edit a typed banner (predefined style)."""

    def __init__(self, bot, locale: str, banner_id=None, prefill=None):
        super().__init__(title=t("staff.manage.banner.typed", locale=locale)[:45])
        self.bot = bot
        self.locale = locale
        self.banner_id = banner_id

        cur_type = (prefill or {}).get("type") or "announcement"
        self.type_select = ui.Select(
            min_values=1, max_values=1,
            options=[discord.SelectOption(label=tp.title(), value=tp, default=(tp == cur_type)) for tp in VALID_TYPES],
        )
        self.message_input = ui.TextInput(style=discord.TextStyle.paragraph, max_length=1000,
                                          default=(prefill or {}).get("message", ""))
        self.visibility = _visibility_group(locale, (prefill or {}).get("show_dashboard", True), (prefill or {}).get("show_website", True))

        self.add_item(ui.Label(text=t("staff.manage.banner.type", locale=locale)[:45], component=self.type_select))
        self.add_item(ui.Label(text=t("staff.manage.banner.message", locale=locale)[:45], component=self.message_input))
        self.add_item(ui.Label(text=_visibility_label(locale), component=self.visibility))

    async def on_submit(self, interaction: discord.Interaction):
        vis = self.visibility.values
        row = await _save(self.bot, self.banner_id, msg=self.message_input.value.strip(),
                          btype=self.type_select.values[0], icon_svg=None, color=None,
                          dash="dashboard" in vis, web="website" in vis)
        await interaction.response.send_message(view=design.success(
            t("staff.manage.banner.saved", locale=self.locale),
            f"**ID:** `{row['id']}` • **{row['type']}**\n{row['message'][:300]}",
        ), ephemeral=True)


class CustomBannerModal(BaseModal):
    """Create/edit a custom banner (icon SVG + colour)."""

    def __init__(self, bot, locale: str, banner_id=None, prefill=None):
        super().__init__(title=t("staff.manage.banner.custom", locale=locale)[:45])
        self.bot = bot
        self.locale = locale
        self.banner_id = banner_id

        self.message_input = ui.TextInput(style=discord.TextStyle.paragraph, max_length=1000,
                                          default=(prefill or {}).get("message", ""))
        self.icon_input = ui.TextInput(style=discord.TextStyle.paragraph, max_length=2000,
                                       default=(prefill or {}).get("icon_svg", "") or "")
        self.color_input = ui.TextInput(max_length=7, default=(prefill or {}).get("color", "#5865F2") or "#5865F2")
        self.visibility = _visibility_group(locale, (prefill or {}).get("show_dashboard", True), (prefill or {}).get("show_website", True))

        self.add_item(ui.Label(text=t("staff.manage.banner.message", locale=locale)[:45], component=self.message_input))
        self.add_item(ui.Label(text=t("staff.manage.banner.icon", locale=locale)[:45], component=self.icon_input))
        self.add_item(ui.Label(text=t("staff.manage.banner.color", locale=locale)[:45], component=self.color_input))
        self.add_item(ui.Label(text=_visibility_label(locale), component=self.visibility))

    async def on_submit(self, interaction: discord.Interaction):
        color = self.color_input.value.strip()
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            await interaction.response.send_message(view=design.error(
                t("staff.common.invalid_usage.title", locale=self.locale),
                t("staff.manage.banner.bad_color", locale=self.locale, color=f"`{color}`"),
            ), ephemeral=True)
            return
        vis = self.visibility.values
        row = await _save(self.bot, self.banner_id, msg=self.message_input.value.strip(),
                          btype=None, icon_svg=self.icon_input.value.strip(), color=color,
                          dash="dashboard" in vis, web="website" in vis)
        await interaction.response.send_message(view=design.success(
            t("staff.manage.banner.saved", locale=self.locale),
            f"**ID:** `{row['id']}` • custom • `{color}`\n{row['message'][:300]}",
        ), ephemeral=True)


class BannerTypeSelectView(BaseView):
    """Pick typed vs custom, then open the matching modal."""

    def __init__(self, bot, author_id: int, locale: str, banner_id=None, prefill=None):
        super().__init__(timeout=180)
        self.bot = bot
        self.author_id = author_id
        self.locale = locale
        self.banner_id = banner_id
        self.prefill = prefill

        container = design.make_container("primary")
        container.add_item(ui.TextDisplay(design.title_line(emojis.BANNER, t("staff.manage.banner.choose_title", locale=locale))))
        container.add_item(ui.TextDisplay(t("staff.manage.banner.choose", locale=locale)))
        self.add_item(container)

        row = ui.ActionRow()
        typed = ui.Button(label=t("staff.manage.banner.typed", locale=locale), style=discord.ButtonStyle.primary)
        typed.callback = self._typed
        row.add_item(typed)
        custom = ui.Button(label=t("staff.manage.banner.custom", locale=locale), style=discord.ButtonStyle.secondary)
        custom.callback = self._custom
        row.add_item(custom)
        self.add_item(row)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(t("staff.common.not_your_menu", locale=self.locale), ephemeral=True)
            return False
        return True

    async def _typed(self, interaction: discord.Interaction):
        if await self._guard(interaction):
            await interaction.response.send_modal(TypedBannerModal(self.bot, self.locale, self.banner_id, self.prefill))

    async def _custom(self, interaction: discord.Interaction):
        if await self._guard(interaction):
            await interaction.response.send_modal(CustomBannerModal(self.bot, self.locale, self.banner_id, self.prefill))
