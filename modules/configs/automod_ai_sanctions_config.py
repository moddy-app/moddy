"""
``/config`` -> Automod AI -> **Sanctions**.

*What does Moddy do when it catches something?* — answered one type of
infraction at a time:

* the **list** groups every type (messages, images, profiles, and the ones the
  server defined itself) with its current sanction in plain words, and offers
  "+ Add an infraction" (a Moddy template in one click, or from scratch);
* the **type screen** shows one type: its description, the on/off switch,
  "Moddy default" vs "Customise" (a Modal V2 with one picker per severity), the
  profile action, and — for the server's own infractions — Edit and Delete.

Everything is saved instantly (see ``automod_ai_common.save_cfg``).

Persistence
-----------
The list has static ``moddy:automod:cfg:sanctions:<action>`` custom_ids and is
registered as a shell. The type screen carries the infraction id in its
custom_ids (``moddy:automod:cfg:tbtn:<action>:<type>`` /
``moddy:automod:cfg:tsel:<type>``), so its controls are ``DynamicItem`` s
registered through ``AutomodConfigPersistence``; the wrapper view is not (it
cannot be built without a type). Auth is Manage Server, re-checked on every
click.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from automod import custom_infractions, infractions
from automod.rules_check import validate_custom_infraction
from cogs.error_handler import BaseModal, BaseView
from modules.configs import automod_ai_common as common
from modules.configs._common import load_before_modal
from modules.configs.automod_ai_common import (
    TYPE_ID_RE, back_row, cid, go, row, select, title, tp,
)
from utils.emojis import (
    ADD, DELETE, EDIT, LEGAL, SYNC, TOGGLE_OFF, TOGGLE_ON, WARNING,
)
from utils.i18n import i18n

logger = logging.getLogger("moddy.modules.automod_ai_sanctions")

#: Above this many characters the list falls back to its compact wording so a
#: message with 24 types stays under Discord's 4000-character text budget.
_TEXT_BUDGET = 3300

_BUTTON_ACTIONS = "toggle|custom|reset|edit|delete|delyes|view|back"


def _guarded(callback):
    """Route a dynamic-item callback error to the central handler."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


# =========================================================================== #
# Rendering entry points
# =========================================================================== #
async def render_sanctions(interaction: discord.Interaction) -> None:
    await common.ack(interaction)
    cfg = await common.load_cfg(interaction.client, interaction.guild_id)
    await common.respond(interaction, AutomodSanctionsView(
        interaction.client, interaction.guild_id, i18n.get_user_locale(interaction), cfg))


async def render_type(interaction: discord.Interaction, cat: str, *,
                      confirm_delete: bool = False) -> None:
    """Open the screen of one infraction type (back to the list if it is gone)."""
    await common.ack(interaction)
    cfg = await common.load_cfg(interaction.client, interaction.guild_id)
    if not common.type_exists(cfg, cat):
        await common.respond(interaction, AutomodSanctionsView(
            interaction.client, interaction.guild_id, i18n.get_user_locale(interaction), cfg))
        return
    await common.respond(interaction, TypeView(
        interaction.client, interaction.guild_id, i18n.get_user_locale(interaction),
        cfg, cat, confirm_delete=confirm_delete))


# =========================================================================== #
# The list
# =========================================================================== #
def _list_text(cfg: Dict[str, Any], locale: str, compact: bool) -> List[str]:
    """One text block per family, each a heading and one line per type."""
    groups = common.type_ids(cfg)
    blocks = []
    for family in common.FAMILY_ORDER:
        ids = groups[family]
        heading = f"**{tp(f'family.{family}', locale)}**"
        if not ids:
            blocks.append(f"{heading}\n-# {tp('list.no_custom', locale)}")
            continue
        lines = []
        for cat in ids:
            line = common.sanction_line(cat, cfg, locale)
            if compact and len(line) > 90:
                name = common.type_name(cat, cfg, locale)
                line = f"{common.type_emoji(cat)} **{name}** · {tp('sanction.custom', locale)}"
            lines.append(line)
        blocks.append(heading + "\n" + "\n".join(lines))
    return blocks


class AutomodSanctionsView(BaseView):
    """The sanctions list. Persistent: yes. Auth: Manage Server."""

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None, locale: str = "en-US",
                 cfg: Optional[Dict[str, Any]] = None):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.cfg = common.normalize_cfg(cfg)
        self._build_view()

    def _build_view(self) -> None:
        self.clear_items()
        locale, cfg = self.locale, self.cfg
        is_shell = self.bot is None

        container = ui.Container()
        container.add_item(title(locale, "list.title", LEGAL))
        container.add_item(ui.TextDisplay(tp("list.intro", locale)))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        blocks = _list_text(cfg, locale, compact=False)
        if sum(len(b) for b in blocks) > _TEXT_BUDGET:
            blocks = _list_text(cfg, locale, compact=True)
        for block in blocks:
            container.add_item(ui.TextDisplay(block))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Pick a type to open it. Built-ins (<= 14) + custom (<= 10) always fit
        # the 25 options of one select, so there is nothing to paginate.
        groups = common.type_ids(cfg)
        options = []
        for family in common.FAMILY_ORDER:
            for cat in groups[family]:
                _icon, text = common.sanction_summary(cat, common.entry_of(cfg, cat), locale)
                options.append(discord.SelectOption(
                    label=common.type_name(cat, cfg, locale)[:100], value=cat,
                    description=text[:100], emoji=common.emoji_of(common.type_emoji(cat))))
        container.add_item(row(select(
            self.on_pick, custom_id=cid("sanctions", "pick"), options=options[:25],
            placeholder=tp("list.pick_placeholder", locale))))

        # "+ Add an infraction": templates not added yet, plus "from scratch".
        count = len(cfg["infractions_perso"])
        if count < custom_infractions.MAX_INFRACTIONS or is_shell:
            container.add_item(row(select(
                self.on_add, custom_id=cid("sanctions", "add"),
                options=add_options(cfg, locale, all_templates=is_shell),
                placeholder=tp("list.add_placeholder", locale))))
        if count >= custom_infractions.MAX_INFRACTIONS:
            container.add_item(ui.TextDisplay(
                f"-# {WARNING} {tp('list.limit', locale, max=custom_infractions.MAX_INFRACTIONS)}"))
        self.add_item(container)
        self.add_item(back_row(self.on_back, "sanctions", locale))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await common.gate(interaction)

    async def on_pick(self, interaction: discord.Interaction):
        values = interaction.data.get("values") or []
        if not values:
            await common.ack(interaction)
            return
        await render_type(interaction, values[0])

    async def on_add(self, interaction: discord.Interaction):
        values = interaction.data.get("values") or []
        choice = values[0] if values else ""
        locale = i18n.get_user_locale(interaction)
        load = common.load_cfg(interaction.client, interaction.guild_id)
        if choice == "scratch":
            # The modal must be the first response, so the limit check reads
            # the config under a time bound instead of after an ack.
            ok, cfg = await load_before_modal(interaction, load)
            if not ok:
                return
        elif choice in custom_infractions.TEMPLATES:
            await common.ack(interaction)
            cfg = await load
        else:
            load.close()
            await common.ack(interaction)
            return
        if len(cfg["infractions_perso"]) >= custom_infractions.MAX_INFRACTIONS:
            await common.send_error(interaction, tp(
                "list.limit", locale, max=custom_infractions.MAX_INFRACTIONS))
            return
        if choice == "scratch":
            modal = InfractionModal(locale)
            modal.bot = interaction.client
            await interaction.response.send_modal(modal)
            return
        await add_from_template(interaction, cfg, choice)

    async def on_back(self, interaction: discord.Interaction):
        await go(interaction, "home")

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())


def add_options(cfg: Dict[str, Any], locale: str, *, all_templates: bool = False
                ) -> List[discord.SelectOption]:
    """Moddy's ready-made infractions not added yet, then "from scratch"."""
    used = {e.get("modele") for e in cfg["infractions_perso"]}
    options = []
    for tid in custom_infractions.template_ids():
        if tid in used and not all_templates:
            continue
        tpl = custom_infractions.TEMPLATES[tid]
        options.append(discord.SelectOption(
            label=tp_t(tpl.name_key, locale)[:100], value=tid,
            description=tp_t(tpl.desc_key, locale)[:100], emoji=common.emoji_of(ADD)))
    options.append(discord.SelectOption(
        label=tp("list.add_scratch", locale), value="scratch",
        description=tp("list.add_scratch_desc", locale)[:100], emoji=common.emoji_of(EDIT)))
    return options


def tp_t(key: str, locale: str) -> str:
    from utils.i18n import t
    return t(key, locale=locale)


async def add_from_template(interaction: discord.Interaction, cfg: Dict[str, Any],
                            template_id: str) -> None:
    """Add a Moddy template as a custom infraction (no AI check: Moddy wrote it)."""
    from utils.guild_language import guild_locale
    server_locale = await guild_locale(interaction.client, interaction.guild_id)
    entry = custom_infractions.from_template(
        template_id, locale=server_locale,
        existing_ids=custom_infractions.ids(cfg["infractions_perso"]))
    cfg["infractions_perso"].append(entry)
    if not await common.save_cfg(interaction, cfg):
        await render_sanctions(interaction)
        return
    await render_type(interaction, custom_infractions.categorie_of(entry))


# =========================================================================== #
# One type
# =========================================================================== #
class TypeButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:automod:cfg:tbtn:(?P<action>{_BUTTON_ACTIONS}):(?P<t>{TYPE_ID_RE})",
):
    """An action button of the type screen. Auth: Manage Server."""

    def __init__(self, action: str, cat: str, *, label: str = "—",
                 style: discord.ButtonStyle = discord.ButtonStyle.secondary,
                 emoji: Optional[str] = None, disabled: bool = False):
        super().__init__(ui.Button(
            label=label[:80], style=style, disabled=disabled,
            emoji=common.emoji_of(emoji),
            custom_id=f"moddy:automod:cfg:tbtn:{action}:{cat}"))
        self.action = action
        self.cat = cat

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["action"], match["t"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await common.gate(interaction):
            return
        await getattr(self, f"_on_{self.action}")(interaction)

    # -- actions ----------------------------------------------------------- #
    async def _on_back(self, interaction: discord.Interaction):
        await render_sanctions(interaction)

    async def _on_view(self, interaction: discord.Interaction):
        await render_type(interaction, self.cat)

    async def _on_toggle(self, interaction: discord.Interaction):
        await common.ack(interaction)
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        if not common.type_exists(cfg, self.cat):
            await render_sanctions(interaction)
            return
        entry = dict(common.entry_of(cfg, self.cat))
        entry["enabled"] = entry.get("enabled", True) is False
        common.store_entry(cfg, self.cat, entry)
        await common.ack(interaction)
        await common.save_cfg(interaction, cfg)
        await render_type(interaction, self.cat)

    async def _on_reset(self, interaction: discord.Interaction):
        await common.ack(interaction)
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        cfg["sanctions"].pop(self.cat, None)
        await common.ack(interaction)
        await common.save_cfg(interaction, cfg)
        await render_type(interaction, self.cat)

    async def _on_custom(self, interaction: discord.Interaction):
        # Modal pre-fill needs the stored sanction: bounded read (see load_before_modal).
        ok, cfg = await load_before_modal(
            interaction, common.load_cfg(interaction.client, interaction.guild_id))
        if not ok:
            return
        if not common.type_exists(cfg, self.cat):
            await render_sanctions(interaction)
            return
        locale = i18n.get_user_locale(interaction)
        entry = common.entry_of(cfg, self.cat)
        modal = SanctionModal(
            locale, self.cat, common.type_name(self.cat, cfg, locale),
            common.effective_crans(self.cat, entry), entry.get("recidive", True) is not False)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def _on_edit(self, interaction: discord.Interaction):
        ok, cfg = await load_before_modal(
            interaction, common.load_cfg(interaction.client, interaction.guild_id))
        if not ok:
            return
        found = custom_infractions.find(cfg["infractions_perso"], self.cat)
        if found is None:
            await render_sanctions(interaction)
            return
        modal = InfractionModal(i18n.get_user_locale(interaction), found)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def _on_delete(self, interaction: discord.Interaction):
        """First click asks; the type screen re-renders in confirmation mode."""
        await render_type(interaction, self.cat, confirm_delete=True)

    async def _on_delyes(self, interaction: discord.Interaction):
        await common.ack(interaction)
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        cfg["infractions_perso"] = [
            e for e in cfg["infractions_perso"]
            if custom_infractions.categorie_of(e) != self.cat]
        # The sanction of a deleted infraction must go with it: the module
        # refuses a config holding the sanction of an unknown infraction.
        cfg["sanctions"].pop(self.cat, None)
        await common.ack(interaction)
        await common.save_cfg(interaction, cfg)
        await render_sanctions(interaction)


class TypeActionSelect(
    ui.DynamicItem[ui.Select],
    template=rf"moddy:automod:cfg:tsel:(?P<t>{TYPE_ID_RE})",
):
    """What Moddy does about a profile problem (timeout / reset / alert / scale)."""

    def __init__(self, cat: str, *, item: Optional[ui.Select] = None):
        super().__init__(item or ui.Select(
            options=[discord.SelectOption(label="—", value="none")],
            custom_id=f"moddy:automod:cfg:tsel:{cat}"))
        self.cat = cat

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["t"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await common.gate(interaction):
            return
        await common.ack(interaction)
        values = interaction.data.get("values") or []
        cfg = await common.load_cfg(interaction.client, interaction.guild_id)
        typ = common.resolve_type(cfg, self.cat)
        if typ is None or typ.famille != "profil":
            await render_sanctions(interaction)
            return
        if not values or values[0] not in profile_actions(self.cat):
            await common.ack(interaction)
            return
        entry = dict(common.entry_of(cfg, self.cat))
        entry["action_profil"] = values[0]
        common.store_entry(cfg, self.cat, entry)
        await common.ack(interaction)
        await common.save_cfg(interaction, cfg)
        await render_type(interaction, self.cat)


def profile_actions(cat: str) -> List[str]:
    """Actions offered for a profile type ("reset the name" only fits a name)."""
    actions = list(infractions.ACTIONS_PROFIL)
    if cat != "profil_pseudo":
        actions.remove("rename_warn")
    elif cat not in infractions.HOLD_TYPES:
        actions.remove("hold")
    return actions


def _can_customise(cat: str, entry: Dict[str, Any]) -> bool:
    """The per-severity pickers only matter when the scale is in play."""
    action = common.profile_action(cat, entry)
    return action is None or action == "bareme"


class TypeView(BaseView):
    """One infraction type. Not registered: its controls are dynamic items."""

    def __init__(self, bot, guild_id: int, locale: str, cfg: Dict[str, Any], cat: str,
                 *, confirm_delete: bool = False):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.cfg = cfg
        self.cat = cat
        self.confirm_delete = confirm_delete
        self._build_view()

    def _build_view(self) -> None:
        self.clear_items()
        locale, cfg, cat = self.locale, self.cfg, self.cat
        is_perso = infractions.is_perso(cat)
        entry = common.entry_of(cfg, cat)
        name = common.type_name(cat, cfg, locale)

        container = ui.Container()
        container.add_item(ui.TextDisplay(f"### {common.type_emoji(cat)} {name}"))

        if self.confirm_delete:
            container.add_item(ui.TextDisplay(
                f"{WARNING} **{tp('type.delete_title', locale)}**\n"
                f"{tp('type.delete_body', locale, name=name)}"))
            container.add_item(row(
                TypeButton("view", cat, label=tp("type.cancel", locale),
                           emoji=None, style=discord.ButtonStyle.secondary),
                TypeButton("delyes", cat, label=tp("type.delete_confirm", locale),
                           emoji=DELETE, style=discord.ButtonStyle.danger)))
            self.add_item(container)
            return

        container.add_item(ui.TextDisplay(common.type_desc(cat, cfg, locale)))
        if not common.monitored(cfg, cat):
            container.add_item(ui.TextDisplay(f"-# {WARNING} {tp('type.not_monitored', locale)}"))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # The current sanction: shown as text because "Customise" opens a Modal
        # and nothing else on the screen would tell what is stored.
        enabled = entry.get("enabled", True) is not False
        icon, text = common.sanction_summary(cat, entry, locale)
        current = f"{icon} {text}" if icon else text
        block = f"**{tp('type.sanction', locale)}**\n{current}"
        if not enabled:
            block += f"\n-# {tp('type.off_profile' if infractions.get(cat).famille == 'profil' else 'type.off', locale)}"
        elif not entry or common.entry_is_default(cat, entry):
            if common.profile_action(cat, entry) in (None, "bareme"):
                block += f"\n-# {common.default_summary(cat, locale)}\n-# {tp('type.default_hint', locale)}"
        container.add_item(ui.TextDisplay(block))

        buttons = [
            TypeButton("toggle", cat,
                       label=tp("type.enabled" if enabled else "type.disabled", locale),
                       emoji=TOGGLE_ON if enabled else TOGGLE_OFF,
                       style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary),
        ]
        if _can_customise(cat, entry):
            buttons.append(TypeButton("custom", cat, label=tp("type.customise", locale),
                                      emoji=EDIT, style=discord.ButtonStyle.primary,
                                      disabled=not enabled))
        buttons.append(TypeButton("reset", cat, label=tp("type.reset", locale), emoji=SYNC,
                                  disabled=cat not in cfg["sanctions"]))
        container.add_item(row(*buttons))

        if infractions.get(cat).famille == "profil":
            current_action = common.profile_action(cat, entry)
            container.add_item(ui.TextDisplay(
                f"**{tp('type.profile_action', locale)}**\n-# {tp('type.profile_action_hint', locale)}"))
            options = [
                discord.SelectOption(
                    label=tp(f"profile_action.{a}", locale),
                    description=tp(f"profile_action.{a}_desc", locale)[:100],
                    value=a, default=(a == current_action))
                for a in profile_actions(cat)
            ]
            container.add_item(row(TypeActionSelect(cat, item=ui.Select(
                options=options, min_values=1, max_values=1,
                custom_id=f"moddy:automod:cfg:tsel:{cat}",
                placeholder=tp("type.profile_action", locale)))))

        if is_perso:
            container.add_item(row(
                TypeButton("edit", cat, label=tp("type.edit", locale), emoji=EDIT),
                TypeButton("delete", cat, label=tp("type.delete", locale), emoji=DELETE,
                           style=discord.ButtonStyle.danger)))
        self.add_item(container)

        from utils.i18n import t
        self.add_item(row(TypeButton(
            "back", cat, label=t("modules.config.buttons.back", locale=locale),
            emoji=common.emojis.BACK)))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await common.gate(interaction)


# =========================================================================== #
# Modals
# =========================================================================== #
class SanctionModal(BaseModal):
    """"Customise": one picker per severity + the escalation switch (Modal V2).

    Four ``Label``/``Select`` pairs and one ``Label``/``Checkbox`` = the five
    top-level components a modal allows.
    """

    def __init__(self, locale: str, cat: str = "insulte", name: str = "",
                 crans: Optional[Dict[str, int]] = None, recidive: bool = True):
        super().__init__(title=tp("customise.modal_title", locale, name=name)[:45])
        self.locale = locale
        self.cat = cat
        crans = crans or common.effective_crans(cat, {})

        self.severity_selects: Dict[str, ui.Select] = {}
        for gravity in infractions.GRAVITES:
            picker = ui.Select(
                min_values=1, max_values=1, required=True,
                options=[
                    discord.SelectOption(
                        label=common.cran_label(n, locale), value=str(n),
                        emoji=common.emoji_of(common.cran_icon(n)),
                        default=(n == crans.get(gravity)))
                    for n in common.CRAN_CHOICES
                ])
            self.severity_selects[gravity] = picker
            self.add_item(ui.Label(
                text=tp(f"gravity_label.{gravity}", locale)[:45],
                description=tp(f"gravity_desc.{gravity}", locale)[:100],
                component=picker))

        self.escalate = ui.Checkbox(default=recidive)
        self.add_item(ui.Label(
            text=tp("customise.escalate", locale)[:45],
            description=tp("customise.escalate_desc", locale)[:100],
            component=self.escalate))

    async def on_submit(self, interaction: discord.Interaction):
        crans = {}
        for gravity, picker in self.severity_selects.items():
            # Select -> a list of values (a RadioGroup would be `.value`).
            crans[gravity] = int(picker.values[0])
        await apply_sanctions(interaction, self.cat, crans, bool(self.escalate.value))


async def apply_sanctions(interaction: discord.Interaction, cat: str,
                          crans: Dict[str, int], recidive: bool) -> None:
    """Store the per-severity sanctions picked in :class:`SanctionModal`."""
    await common.ack(interaction)
    cfg = await common.load_cfg(interaction.client, interaction.guild_id)
    if not common.type_exists(cfg, cat):
        await render_sanctions(interaction)
        return
    entry = dict(common.entry_of(cfg, cat))
    entry.setdefault("enabled", True)
    if crans == common.effective_crans(cat, {}) and recidive:
        # Exactly Moddy's defaults: say so rather than store a copy of them.
        entry["mode"] = "auto"
        entry.pop("crans", None)
        entry.pop("recidive", None)
    else:
        entry.update(mode="perso", crans=dict(crans), recidive=bool(recidive))
    common.store_entry(cfg, cat, entry)
    await common.save_cfg(interaction, cfg)
    await render_type(interaction, cat)


def split_lines(text: str) -> List[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def split_keywords(text: str) -> List[str]:
    return [part.strip() for part in re.split(r"[,;\n]", text or "") if part.strip()]


class InfractionModal(BaseModal):
    """Create or edit a server-defined infraction (Modal V2, four fields)."""

    def __init__(self, locale: str, existing: Optional[Dict[str, Any]] = None):
        super().__init__(title=tp("infraction.modal_title_edit" if existing
                                  else "infraction.modal_title_new", locale)[:45])
        self.locale = locale
        self.existing = existing
        existing = existing or {}

        self.name_input = ui.TextInput(
            style=discord.TextStyle.short, max_length=custom_infractions.MAX_NOM,
            default=existing.get("nom"),
            placeholder=tp("infraction.name_placeholder", locale)[:100])
        self.add_item(ui.Label(text=tp("infraction.name_label", locale)[:45],
                               description=tp("infraction.name_desc", locale)[:100],
                               component=self.name_input))

        self.description_input = ui.TextInput(
            style=discord.TextStyle.paragraph,
            max_length=custom_infractions.MAX_DESCRIPTION, default=existing.get("description"),
            placeholder=tp("infraction.description_placeholder", locale)[:100])
        self.add_item(ui.Label(text=tp("infraction.description_label", locale)[:45],
                               description=tp("infraction.description_desc", locale)[:100],
                               component=self.description_input))

        self.examples_input = ui.TextInput(
            style=discord.TextStyle.paragraph, required=False,
            max_length=custom_infractions.MAX_EXEMPLES * (custom_infractions.MAX_EXEMPLE + 1),
            default="\n".join(existing.get("exemples") or []) or None,
            placeholder=tp("infraction.examples_placeholder", locale)[:100])
        self.add_item(ui.Label(text=tp("infraction.examples_label", locale)[:45],
                               description=tp("infraction.examples_desc", locale)[:100],
                               component=self.examples_input))

        self.keywords_input = ui.TextInput(
            style=discord.TextStyle.paragraph, required=False,
            max_length=custom_infractions.MAX_MOTS_CLES * (custom_infractions.MAX_MOT_CLE + 2),
            default=", ".join(existing.get("mots_cles") or []) or None,
            placeholder=tp("infraction.keywords_placeholder", locale)[:100])
        self.add_item(ui.Label(text=tp("infraction.keywords_label", locale)[:45],
                               description=tp("infraction.keywords_desc", locale)[:100],
                               component=self.keywords_input))

    async def on_submit(self, interaction: discord.Interaction):
        # Acknowledge first (config read + AI safety check follow).
        await common.ack(interaction)
        locale = self.locale
        bot = interaction.client
        existing = self.existing
        cfg = await common.load_cfg(bot, interaction.guild_id)
        pid = existing["id"] if existing else custom_infractions.new_id(
            custom_infractions.ids(cfg["infractions_perso"]))
        candidate = {
            "id": pid,
            "nom": self.name_input.value,
            "description": self.description_input.value,
            "exemples": split_lines(self.examples_input.value),
            "mots_cles": split_keywords(self.keywords_input.value),
            "modele": (existing or {}).get("modele"),
        }
        cleaned = custom_infractions.normalize([candidate])
        if not cleaned:
            await common.send_error(interaction, tp("infraction.error_invalid", locale))
            return
        entry = cleaned[0]

        safe, reason = await validate_custom_infraction(bot, interaction.guild_id, entry)
        if not safe:
            if reason == "too_long":
                body = tp("infraction.error_too_long", locale)
            elif reason == "unavailable":
                body = tp("infraction.error_unavailable", locale)
            elif reason == "invalid":
                body = tp("infraction.error_invalid", locale)
            else:
                body = tp("infraction.error_unsafe", locale, reason=reason or "—")
            await common.send_error(interaction, body)
            return

        # Re-read: the config may have changed while the AI was thinking.
        cfg = await common.load_cfg(bot, interaction.guild_id)
        current = cfg["infractions_perso"]
        if existing:
            if custom_infractions.find(current, existing["id"]) is None:
                await render_sanctions(interaction)
                return
            cfg["infractions_perso"] = [entry if e["id"] == pid else e for e in current]
        else:
            if len(current) >= custom_infractions.MAX_INFRACTIONS:
                await common.send_error(interaction, tp(
                    "list.limit", locale, max=custom_infractions.MAX_INFRACTIONS))
                return
            current.append(entry)
        if not await common.save_cfg(interaction, cfg):
            await render_sanctions(interaction)
            return
        await render_type(interaction, custom_infractions.categorie_of(entry))
