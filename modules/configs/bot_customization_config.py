"""
Configuration UI for the Bot Customization module.

Two sections, two tiers:

- **Identity** (premium only): nickname, avatar and bio, all edited from a
  single Modal V2 — the avatar goes through a ``FileUpload`` component, so the
  server uploads an image instead of pasting a URL. Non-premium servers see the
  section locked with a link to the dashboard.
- **Name style** (free for everyone): font + effect + colours.

There is no draft/save cycle here: a modal submit patches Discord immediately
(the change *is* visible in the member list right away), so a pending "Save"
button would be lying. Everything funnels through
``BotCustomizationModule.apply_customization`` which persists, patches and
technical-logs in one place.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseModal, BaseView
from modules.bot_customization import (
    EFFECT_KEYS,
    EFFECTS,
    FONTS,
    MAX_AVATAR_BYTES,
    MAX_BIO_LENGTH,
    MAX_NICKNAME_LENGTH,
    MODULE_ID,
    BotCustomizationModule,
    CustomizationError,
    color_to_hex,
    parse_hex_color,
    style_is_empty,
)
from modules.configs._common import check_guild_perms
from utils.components_v2 import create_error_message, create_success_message
from utils.emojis import (
    AT, BACK, DELETE, EDIT, IMAGE, MODDY_SQUARE, NOTE, PREMIUM, STAR, UNDONE,
)
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.modules.bot_customization_config")

_CID_EDIT_IDENTITY = "moddy:bot_customization:config:edit_identity"
_CID_RESET_IDENTITY = "moddy:bot_customization:config:reset_identity"
_CID_EDIT_STYLE = "moddy:bot_customization:config:edit_style"
_CID_RESET_STYLE = "moddy:bot_customization:config:reset_style"
_CID_BACK = "moddy:bot_customization:config:back"
_CID_RESET_ALL = "moddy:bot_customization:config:reset_all"

DASHBOARD_URL = "https://dashboard.moddy.app/select-premium-servers"

# Sentinel select value for "no font" / "no effect".
NONE_VALUE = "none"


def _guild_avatar_url(bot, guild_id: int, avatar_hash: str) -> Optional[str]:
    """CDN URL of the bot's per-guild avatar (not exposed by discord.py)."""
    if not bot or not bot.user or not avatar_hash:
        return None
    ext = "gif" if avatar_hash.startswith("a_") else "png"
    return (f"https://cdn.discordapp.com/guilds/{guild_id}/users/"
            f"{bot.user.id}/avatars/{avatar_hash}.{ext}?size=256")


# =========================================================================== #
# Modals (V2)
# =========================================================================== #

class BotIdentityModal(BaseModal):
    """Nickname + bio + avatar upload, premium only."""

    def __init__(self, bot, locale: str, config: Dict[str, Any]):
        super().__init__(
            title=t('modules.bot_customization.identity.modal_title', locale=locale),
            timeout=None,
        )
        self.bot = bot
        self.locale = locale

        self.nickname_input = ui.TextInput(
            style=discord.TextStyle.short,
            default=config.get("nickname") or "",
            max_length=MAX_NICKNAME_LENGTH,
            required=False,
        )
        self.add_item(ui.Label(
            text=t('modules.bot_customization.identity.nickname_label', locale=locale),
            description=t('modules.bot_customization.identity.nickname_description', locale=locale),
            component=self.nickname_input,
        ))

        self.bio_input = ui.TextInput(
            style=discord.TextStyle.paragraph,
            default=config.get("bio") or "",
            max_length=MAX_BIO_LENGTH,
            required=False,
        )
        self.add_item(ui.Label(
            text=t('modules.bot_customization.identity.bio_label', locale=locale),
            description=t('modules.bot_customization.identity.bio_description',
                          locale=locale, max=MAX_BIO_LENGTH),
            component=self.bio_input,
        ))

        self.avatar_input = ui.FileUpload(min_values=0, max_values=1, required=False)
        self.add_item(ui.Label(
            text=t('modules.bot_customization.identity.avatar_label', locale=locale),
            description=t('modules.bot_customization.identity.avatar_description', locale=locale),
            component=self.avatar_input,
        ))

        # The attribution line is not negotiable — say so before submit rather
        # than surprising the server afterwards.
        self.add_item(ui.TextDisplay(
            t('modules.bot_customization.identity.attribution_note', locale=locale)
        ))

    async def on_submit(self, interaction: discord.Interaction):
        await _submit_identity(
            interaction,
            nickname=self.nickname_input.value,
            bio=self.bio_input.value,
            attachments=list(self.avatar_input.values or []),
        )


class NameStyleModal(BaseModal):
    """Font, effect and colours of the bot's display name (free tier)."""

    def __init__(self, bot, locale: str, style: Dict[str, Any]):
        super().__init__(
            title=t('modules.bot_customization.style.modal_title', locale=locale),
            timeout=None,
        )
        self.bot = bot
        self.locale = locale

        font_id = style.get("font_id")
        self.font_select = ui.Select(
            placeholder=t('modules.bot_customization.style.font_placeholder', locale=locale),
            options=[
                discord.SelectOption(
                    label=t('modules.bot_customization.style.none', locale=locale),
                    value=NONE_VALUE,
                    default=font_id is None,
                )
            ] + [
                discord.SelectOption(
                    label=name, value=str(fid), default=(font_id == fid),
                )
                for fid, name in FONTS.items()
            ],
            min_values=1,
            max_values=1,
        )
        self.add_item(ui.Label(
            text=t('modules.bot_customization.style.font_label', locale=locale),
            component=self.font_select,
        ))

        effect_id = style.get("effect_id")
        self.effect_select = ui.Select(
            placeholder=t('modules.bot_customization.style.effect_placeholder', locale=locale),
            options=[
                discord.SelectOption(
                    label=t('modules.bot_customization.style.none', locale=locale),
                    value=NONE_VALUE,
                    default=effect_id is None,
                )
            ] + [
                discord.SelectOption(
                    label=t(f'modules.bot_customization.style.effects.{key}.label', locale=locale),
                    description=t(f'modules.bot_customization.style.effects.{key}.description',
                                  locale=locale)[:100],
                    value=str(eid),
                    default=(effect_id == eid),
                )
                for eid, key in EFFECT_KEYS.items()
            ],
            min_values=1,
            max_values=1,
        )
        self.add_item(ui.Label(
            text=t('modules.bot_customization.style.effect_label', locale=locale),
            component=self.effect_select,
        ))

        colors: List[int] = list(style.get("colors") or [])
        self.color1_input = ui.TextInput(
            style=discord.TextStyle.short,
            default=color_to_hex(colors[0]) if len(colors) > 0 else "",
            max_length=7,
            required=False,
        )
        self.add_item(ui.Label(
            text=t('modules.bot_customization.style.color1_label', locale=locale),
            description=t('modules.bot_customization.style.color1_description', locale=locale),
            component=self.color1_input,
        ))

        self.color2_input = ui.TextInput(
            style=discord.TextStyle.short,
            default=color_to_hex(colors[1]) if len(colors) > 1 else "",
            max_length=7,
            required=False,
        )
        self.add_item(ui.Label(
            text=t('modules.bot_customization.style.color2_label', locale=locale),
            description=t('modules.bot_customization.style.color2_description', locale=locale),
            component=self.color2_input,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        values = self.font_select.values or [NONE_VALUE]
        font_raw = values[0]
        effect_values = self.effect_select.values or [NONE_VALUE]
        effect_raw = effect_values[0]

        await _submit_style(
            interaction,
            font_id=None if font_raw == NONE_VALUE else int(font_raw),
            effect_id=None if effect_raw == NONE_VALUE else int(effect_raw),
            color1=self.color1_input.value,
            color2=self.color2_input.value,
        )


# =========================================================================== #
# Submit handlers (shared by the modals — never hold view state)
# =========================================================================== #

async def _get_module(bot, guild_id: int) -> BotCustomizationModule:
    """Live module instance, or a fresh one when nothing is configured yet."""
    module = await bot.module_manager.get_module_instance(guild_id, MODULE_ID)
    if module is None:
        module = BotCustomizationModule(bot, guild_id)
        config = await bot.module_manager.get_module_config(guild_id, MODULE_ID)
        await module.load_config(config or {})
    return module


async def _read_attachment(attachment: discord.Attachment) -> tuple[bytes, str]:
    if attachment.size > MAX_AVATAR_BYTES:
        raise CustomizationError("image_too_large", str(attachment.size))
    content_type = (attachment.content_type or "").split(";")[0].strip().lower()
    data = await attachment.read()
    return data, content_type


async def _submit_identity(interaction: discord.Interaction, *, nickname: str,
                           bio: str, attachments: List[discord.Attachment]):
    if not await check_guild_perms(interaction):
        return

    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    await interaction.response.defer()

    from utils.subscription import is_guild_premium
    if not await is_guild_premium(bot, interaction.guild_id):
        await interaction.followup.send(
            view=create_error_message(
                t('modules.bot_customization.premium.title', locale=locale),
                t('modules.bot_customization.premium.description', locale=locale),
            ),
            ephemeral=True,
        )
        return

    module = await _get_module(bot, interaction.guild_id)

    kwargs: Dict[str, Any] = {"nickname": nickname, "bio": bio}
    try:
        if attachments:
            kwargs["avatar"] = await _read_attachment(attachments[0])
        await module.apply_customization(
            actor_id=interaction.user.id, source="config", **kwargs
        )
    except CustomizationError as exc:
        await _send_error(interaction, locale, exc)
        return

    await interaction.followup.send(
        view=create_success_message(
            t('modules.bot_customization.applied.title', locale=locale),
            t('modules.bot_customization.applied.description', locale=locale),
        ),
        ephemeral=True,
    )
    await _refresh_panel(interaction, locale)


async def _submit_style(interaction: discord.Interaction, *, font_id: Optional[int],
                        effect_id: Optional[int], color1: str, color2: str):
    if not await check_guild_perms(interaction):
        return

    bot = interaction.client
    locale = i18n.get_user_locale(interaction)
    await interaction.response.defer()

    try:
        colors = [c for c in (parse_hex_color(color1), parse_hex_color(color2))
                  if c is not None]
        module = await _get_module(bot, interaction.guild_id)
        await module.apply_customization(
            style={"font_id": font_id, "effect_id": effect_id, "colors": colors},
            actor_id=interaction.user.id,
            source="config",
        )
    except CustomizationError as exc:
        await _send_error(interaction, locale, exc)
        return

    await interaction.followup.send(
        view=create_success_message(
            t('modules.bot_customization.applied.title', locale=locale),
            t('modules.bot_customization.applied.description', locale=locale),
        ),
        ephemeral=True,
    )
    await _refresh_panel(interaction, locale)


async def _send_error(interaction: discord.Interaction, locale: str,
                      exc: CustomizationError):
    description = t(f'modules.bot_customization.errors.{exc.code}', locale=locale)
    if description == f'modules.bot_customization.errors.{exc.code}':
        description = t('modules.bot_customization.errors.discord_error', locale=locale)
    await interaction.followup.send(
        view=create_error_message(
            t('modules.bot_customization.errors.title', locale=locale), description,
        ),
        ephemeral=True,
    )


async def _refresh_panel(interaction: discord.Interaction, locale: str):
    """Re-render the panel the modal was opened from."""
    try:
        view = await BotCustomizationConfigView.create(
            interaction.client, interaction.guild_id, interaction.user.id, locale,
        )
        await interaction.edit_original_response(view=view)
    except discord.HTTPException as exc:
        logger.debug(f"Could not refresh bot customization panel: {exc}")


# =========================================================================== #
# Main panel
# =========================================================================== #

class BotCustomizationConfigView(BaseView):
    """Bot Customization configuration panel.

    Persistent: yes. Auth: Manage Server in the guild (re-checked on every
    click via check_guild_perms — never a stored user_id, which cannot
    survive a restarted shell).
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None,
                 user_id: Optional[int] = None, locale: str = "en-US",
                 current_config: Optional[Dict[str, Any]] = None,
                 is_premium: bool = False):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale
        self.is_premium = is_premium

        config = BotCustomizationModule(bot, guild_id).get_default_config()
        if current_config:
            config.update(current_config)
        self.config = config
        self.style = config.get("style") or {"font_id": None, "effect_id": None, "colors": []}

        self._build_view()

    @classmethod
    async def create(cls, bot, guild_id: int, user_id: int, locale: str
                     ) -> "BotCustomizationConfigView":
        """Async factory — premium state and config must be read before render."""
        from utils.subscription import is_guild_premium

        config = await bot.module_manager.get_module_config(guild_id, MODULE_ID)
        premium = await is_guild_premium(bot, guild_id)
        return cls(bot, guild_id, user_id, locale, config, premium)

    # ----------------------------------------------------------------- #
    # Rendering
    # ----------------------------------------------------------------- #

    def _build_view(self):
        self.clear_items()

        container = ui.Container()
        container.add_item(ui.TextDisplay(
            f"### {MODDY_SQUARE} {t('modules.bot_customization.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.bot_customization.config.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        self._build_identity_section(container)
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        self._build_style_section(container)

        self.add_item(container)
        self._add_action_buttons()

    def _build_identity_section(self, container: ui.Container):
        nickname = self.config.get("nickname")
        bio = self.config.get("bio")
        avatar_hash = self.config.get("avatar_hash")
        none_label = t('modules.bot_customization.config.not_set', locale=self.locale)

        body = (
            f"**{PREMIUM} {t('modules.bot_customization.identity.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.bot_customization.identity.section_description', locale=self.locale)}\n"
            f"{AT} **{t('modules.bot_customization.identity.nickname_label', locale=self.locale)}** — "
            f"`{nickname or none_label}`\n"
            f"{IMAGE} **{t('modules.bot_customization.identity.avatar_label', locale=self.locale)}** — "
            f"`{t('modules.bot_customization.config.custom', locale=self.locale) if avatar_hash else none_label}`\n"
            f"{NOTE} **{t('modules.bot_customization.identity.bio_label', locale=self.locale)}** — "
            f"`{bio or none_label}`"
        )

        avatar_url = _guild_avatar_url(self.bot, self.guild_id, avatar_hash) if avatar_hash else None
        if avatar_url:
            container.add_item(ui.Section(
                ui.TextDisplay(body),
                accessory=ui.Thumbnail(media=avatar_url),
            ))
        else:
            container.add_item(ui.TextDisplay(body))

        # Registration shell (bot is None): render both branches' buttons so
        # discord.py learns every custom_id — see docs/PERSISTENT_VIEWS.md.
        is_shell = self.bot is None

        if self.is_premium or is_shell:
            row = ui.ActionRow()
            edit_btn = ui.Button(
                label=t('modules.bot_customization.identity.edit', locale=self.locale),
                style=discord.ButtonStyle.primary,
                emoji=discord.PartialEmoji.from_str(EDIT),
                custom_id=_CID_EDIT_IDENTITY,
            )
            edit_btn.callback = self.on_edit_identity
            row.add_item(edit_btn)

            reset_btn = ui.Button(
                label=t('modules.bot_customization.identity.reset', locale=self.locale),
                style=discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(UNDONE),
                custom_id=_CID_RESET_IDENTITY,
                disabled=not (nickname or bio or avatar_hash) and not is_shell,
            )
            reset_btn.callback = self.on_reset_identity
            row.add_item(reset_btn)
            container.add_item(row)

        if not self.is_premium:
            container.add_item(ui.TextDisplay(
                f"-# {PREMIUM} {t('modules.bot_customization.premium.locked', locale=self.locale)}"
            ))
            link_row = ui.ActionRow()
            link_row.add_item(ui.Button(
                label=t('modules.bot_customization.premium.button', locale=self.locale),
                style=discord.ButtonStyle.link,
                url=DASHBOARD_URL,
            ))
            container.add_item(link_row)

    def _build_style_section(self, container: ui.Container):
        none_label = t('modules.bot_customization.config.not_set', locale=self.locale)
        font_id = self.style.get("font_id")
        effect_id = self.style.get("effect_id")
        colors = self.style.get("colors") or []

        effect_label = none_label
        if effect_id in EFFECT_KEYS:
            effect_label = t(
                f'modules.bot_customization.style.effects.{EFFECT_KEYS[effect_id]}.label',
                locale=self.locale,
            )

        colors_label = ", ".join(color_to_hex(c) for c in colors) or none_label

        container.add_item(ui.TextDisplay(
            f"**{STAR} {t('modules.bot_customization.style.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.bot_customization.style.section_description', locale=self.locale)}\n"
            f"-# {t('modules.bot_customization.style.free_note', locale=self.locale)}\n"
            f"**{t('modules.bot_customization.style.font_label', locale=self.locale)}** — "
            f"`{FONTS.get(font_id, none_label)}`\n"
            f"**{t('modules.bot_customization.style.effect_label', locale=self.locale)}** — "
            f"`{effect_label}`\n"
            f"**{t('modules.bot_customization.style.colors_label', locale=self.locale)}** — "
            f"`{colors_label}`"
        ))

        row = ui.ActionRow()
        edit_btn = ui.Button(
            label=t('modules.bot_customization.style.edit', locale=self.locale),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(EDIT),
            custom_id=_CID_EDIT_STYLE,
        )
        edit_btn.callback = self.on_edit_style
        row.add_item(edit_btn)

        reset_btn = ui.Button(
            label=t('modules.bot_customization.style.reset', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(UNDONE),
            custom_id=_CID_RESET_STYLE,
            disabled=style_is_empty(self.style) and self.bot is not None,
        )
        reset_btn.callback = self.on_reset_style
        row.add_item(reset_btn)
        container.add_item(row)

    def _add_action_buttons(self):
        row = ui.ActionRow()

        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            custom_id=_CID_BACK,
        )
        back_btn.callback = self.on_back
        row.add_item(back_btn)

        is_shell = self.bot is None
        has_config = bool(
            self.config.get("nickname") or self.config.get("bio")
            or self.config.get("avatar_hash") or not style_is_empty(self.style)
        )
        if has_config or is_shell:
            delete_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(DELETE),
                label=t('modules.config.buttons.delete', locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_RESET_ALL,
            )
            delete_btn.callback = self.on_reset_all
            row.add_item(delete_btn)

        self.add_item(row)

    # ----------------------------------------------------------------- #
    # Callbacks — state is always re-derived from the interaction
    # ----------------------------------------------------------------- #

    async def on_edit_identity(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)

        from utils.subscription import is_guild_premium
        if not await is_guild_premium(bot, interaction.guild_id):
            await interaction.response.send_message(
                view=create_error_message(
                    t('modules.bot_customization.premium.title', locale=locale),
                    t('modules.bot_customization.premium.description', locale=locale),
                ),
                ephemeral=True,
            )
            return

        config = await bot.module_manager.get_module_config(interaction.guild_id, MODULE_ID) or {}
        await interaction.response.send_modal(BotIdentityModal(bot, locale, config))

    async def on_edit_style(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        config = await bot.module_manager.get_module_config(interaction.guild_id, MODULE_ID) or {}
        style = config.get("style") or {}
        await interaction.response.send_modal(NameStyleModal(bot, locale, style))

    async def on_reset_identity(self, interaction: discord.Interaction):
        await self._reset(interaction, identity=True, style=False)

    async def on_reset_style(self, interaction: discord.Interaction):
        await self._reset(interaction, identity=False, style=True)

    async def on_reset_all(self, interaction: discord.Interaction):
        await self._reset(interaction, identity=True, style=True)

    async def _reset(self, interaction: discord.Interaction, *,
                     identity: bool, style: bool):
        if not await check_guild_perms(interaction):
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        await interaction.response.defer()

        kwargs: Dict[str, Any] = {}
        if identity:
            kwargs.update({"nickname": None, "bio": None, "avatar": None})
        if style:
            kwargs["style"] = None

        try:
            module = await _get_module(bot, interaction.guild_id)
            await module.apply_customization(
                actor_id=interaction.user.id, source="config", **kwargs
            )
        except CustomizationError as exc:
            await _send_error(interaction, locale, exc)
            return

        await interaction.followup.send(
            view=create_success_message(
                t('modules.bot_customization.reset.title', locale=locale),
                t('modules.bot_customization.reset.description', locale=locale),
            ),
            ephemeral=True,
        )
        await _refresh_panel(interaction, locale)

    async def on_back(self, interaction: discord.Interaction):
        if not await check_guild_perms(interaction):
            return

        from cogs.config import ConfigMainView
        locale = i18n.get_user_locale(interaction)
        main_view = ConfigMainView(
            interaction.client, interaction.guild_id, interaction.user.id, locale,
        )
        await interaction.response.edit_message(view=main_view)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())
