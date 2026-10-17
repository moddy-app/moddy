"""
``/config`` -> Automod -> **Sanctions**.

*What does Moddy do when it catches something?* -- answered one type of
infraction at a time:

* the **list** (a main sub-view: Save / Cancel over a draft) groups every type
  under a big heading (messages, images, profiles, the server's own) and shows,
  as a plain bullet list, what each one really does -- never "Moddy default",
  always the effective actions -- and offers "+ Add an infraction" (a Moddy
  template in one click, or from scratch);
* the **type screen** (a deeper screen: **no Save of its own**) edits one type:
  its actions (a multi-select validated by ``automod/actions.py``), the exact
  timeout duration (a Modal V2), the moderator-approval option, escalation, the
  on/off switch, the profile action, and -- for the server's own infractions --
  Edit and Delete. Every edit goes into the ``sanctions`` draft of the list
  (``automod_ai_common.load_working`` / ``stash``) and is only written when the
  list is saved.

Persistence
-----------
The list has static ``moddy:automod:cfg:sanctions:<action>`` custom_ids and is
registered as a shell. The type screen carries the infraction id in its
custom_ids (``moddy:automod:cfg:tbtn:<action>:<type>`` /
``moddy:automod:cfg:tsel:<type>`` / ``moddy:automod:cfg:tact:<type>``), so its
controls are ``DynamicItem`` s registered through ``AutomodConfigPersistence``;
the wrapper view is not (it cannot be built without a type). Auth is Manage
Server, re-checked on every click; the draft is read back on every click.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from automod import actions as actions_mod
from automod import custom_infractions, infractions
from automod.rules_check import validate_custom_infraction
from cogs.error_handler import BaseModal, BaseView
from config import COLORS
from modules.configs import automod_ai_common as common
from modules.configs._common import load_before_modal
from modules.configs.automod_ai_common import (
    TYPE_ID_RE, SectionView, cid, row, select, title, tp, trail,
)
from utils.emojis import (
    ADD, BACK, DELETE, EDIT, LEGAL, PENDING, SYNC, TIME, TOGGLE_OFF, TOGGLE_ON, WARNING,
)
from utils.errors import InvalidInput
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.modules.automod_sanctions")

#: Above this many characters the list falls back to its compact wording so a
#: message with 24 types stays under Discord's 4000-character text budget.
_TEXT_BUDGET = 3300

_BUTTON_ACTIONS = "toggle|approval|recidive|timeout|reset|edit|delete|delyes|view|back"
_ACCENT = discord.Colour(COLORS["primary"])


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
    _stored, working, pending = await common.load_working(interaction, "sanctions")
    await common.respond(interaction, AutomodSanctionsView(
        interaction.client, interaction.guild_id, i18n.get_user_locale(interaction),
        working, pending))


async def render_type(interaction: discord.Interaction, cat: str, *,
                      confirm_delete: bool = False, notice: str = "") -> None:
    """Open the screen of one infraction type (back to the list if it is gone)."""
    await common.ack(interaction)
    _stored, working, pending = await common.load_working(interaction, "sanctions")
    locale = i18n.get_user_locale(interaction)
    if not common.type_exists(working, cat):
        await common.respond(interaction, AutomodSanctionsView(
            interaction.client, interaction.guild_id, locale, working, pending))
        return
    await common.respond(interaction, TypeView(
        interaction.client, interaction.guild_id, locale, working, cat,
        confirm_delete=confirm_delete, pending=pending, notice=notice))


async def _edit_entry(interaction: discord.Interaction, cat: str, mutate) -> None:
    """Change one entry of the ``sanctions`` draft, then re-show the type.

    ``mutate(entry, working, locale)`` edits the entry dict in place and may
    return a notice (shown once under the actions picker).
    """
    await common.ack(interaction)
    stored, working, _pending = await common.load_working(interaction, "sanctions")
    if not common.type_exists(working, cat):
        await render_sanctions(interaction)
        return
    entry = dict(common.entry_of(working, cat))
    notice = mutate(entry, working, i18n.get_user_locale(interaction)) or ""
    common.store_entry(working, cat, entry)
    await common.stash(interaction, "sanctions", stored, working)
    await render_type(interaction, cat, notice=notice)


# =========================================================================== #
# The list
# =========================================================================== #
def _list_text(cfg: Dict[str, Any], locale: str, compact: bool) -> List[str]:
    """One text block per family: an icon heading and one bullet per type."""
    groups = common.type_ids(cfg)
    blocks = []
    for family in common.FAMILY_ORDER:
        ids = groups[family]
        heading = f"{common.FAMILY_ICONS[family]} **{tp(f'family.{family}', locale)}**"
        if not ids:
            blocks.append(f"{heading}\n-# {tp('list.no_custom', locale)}")
            continue
        lines = []
        for cat in ids:
            line = common.sanction_line(cat, cfg, locale)
            if compact and len(line) > 90:
                line = line[:87].rstrip() + "…"
            lines.append(line)
        blocks.append(heading + "\n" + "\n".join(lines))
    return blocks


class AutomodSanctionsView(SectionView):
    """The sanctions list. Persistent: yes. Auth: Manage Server."""

    SECTION = "sanctions"
    SCREEN = "sanctions"

    def _build_view(self) -> None:
        self.clear_items()
        locale, cfg = self.locale, self.cfg
        is_shell = self.bot is None

        container = ui.Container(accent_colour=_ACCENT)
        container.add_item(trail(locale, tp("nav.sanctions", locale)))
        container.add_item(title(locale, "list.title", LEGAL))
        container.add_item(ui.TextDisplay(tp("list.intro", locale)))
        for item in self.pending_notice():
            container.add_item(item)
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
                options.append(discord.SelectOption(
                    label=common.type_name(cat, cfg, locale)[:100], value=cat,
                    description=common.policy_text(cat, common.entry_of(cfg, cat), locale)[:100]))
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
        self.add_item(self.footer())

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
        load = common.load_working(interaction, "sanctions")
        if choice == "scratch":
            # The modal must be the first response, so the limit check reads
            # the config under a time bound instead of after an ack.
            ok, loaded = await load_before_modal(interaction, load)
            if not ok:
                return
        elif choice in custom_infractions.TEMPLATES:
            await common.ack(interaction)
            loaded = await load
        else:
            load.close()
            await common.ack(interaction)
            return
        stored, working, _pending = loaded
        if len(working["infractions_perso"]) >= custom_infractions.MAX_INFRACTIONS:
            await common.send_error(interaction, tp(
                "list.limit", locale, max=custom_infractions.MAX_INFRACTIONS))
            return
        if choice == "scratch":
            modal = InfractionModal(locale)
            modal.bot = interaction.client
            await interaction.response.send_modal(modal)
            return
        await add_from_template(interaction, stored, working, choice)


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
            label=t(tpl.name_key, locale=locale)[:100], value=tid,
            description=t(tpl.desc_key, locale=locale)[:100], emoji=common.emoji_of(ADD)))
    options.append(discord.SelectOption(
        label=tp("list.add_scratch", locale), value="scratch",
        description=tp("list.add_scratch_desc", locale)[:100], emoji=common.emoji_of(EDIT)))
    return options


async def add_from_template(interaction: discord.Interaction, stored: Dict[str, Any],
                            working: Dict[str, Any], template_id: str) -> None:
    """Add a Moddy template as a custom infraction (no AI check: Moddy wrote it)."""
    from utils.guild_language import guild_locale
    server_locale = await guild_locale(interaction.client, interaction.guild_id)
    entry = custom_infractions.from_template(
        template_id, locale=server_locale,
        existing_ids=custom_infractions.ids(working["infractions_perso"]))
    working["infractions_perso"].append(entry)
    await common.stash(interaction, "sanctions", stored, working)
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
        def mutate(entry, _working, _locale):
            entry["enabled"] = entry.get("enabled", True) is False
        await _edit_entry(interaction, self.cat, mutate)

    async def _on_approval(self, interaction: discord.Interaction):
        def mutate(entry, _working, _locale):
            entry["require_approval"] = entry.get("require_approval", False) is not True
        await _edit_entry(interaction, self.cat, mutate)

    async def _on_recidive(self, interaction: discord.Interaction):
        def mutate(entry, _working, _locale):
            entry["recidive"] = entry.get("recidive", True) is False
        await _edit_entry(interaction, self.cat, mutate)

    async def _on_reset(self, interaction: discord.Interaction):
        await common.ack(interaction)
        stored, working, _pending = await common.load_working(interaction, "sanctions")
        working["sanctions"].pop(self.cat, None)
        await common.stash(interaction, "sanctions", stored, working)
        await render_type(interaction, self.cat)

    async def _on_timeout(self, interaction: discord.Interaction):
        # Modal pre-fill needs the working sanction: bounded read (see load_before_modal).
        ok, loaded = await load_before_modal(
            interaction, common.load_working(interaction, "sanctions"))
        if not ok:
            return
        _stored, working, _pending = loaded
        if not common.type_exists(working, self.cat):
            await render_sanctions(interaction)
            return
        policy = common.policy_of(self.cat, common.entry_of(working, self.cat))
        modal = TimeoutModal(i18n.get_user_locale(interaction), self.cat,
                             policy.timeout_seconds if policy else None)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def _on_edit(self, interaction: discord.Interaction):
        ok, loaded = await load_before_modal(
            interaction, common.load_working(interaction, "sanctions"))
        if not ok:
            return
        _stored, working, _pending = loaded
        found = custom_infractions.find(working["infractions_perso"], self.cat)
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
        stored, working, _pending = await common.load_working(interaction, "sanctions")
        working["infractions_perso"] = [
            e for e in working["infractions_perso"]
            if custom_infractions.categorie_of(e) != self.cat]
        # The sanction of a deleted infraction must go with it: the module
        # refuses a config holding the sanction of an unknown infraction.
        working["sanctions"].pop(self.cat, None)
        await common.stash(interaction, "sanctions", stored, working)
        await render_sanctions(interaction)


class TypeActionsSelect(
    ui.DynamicItem[ui.Select],
    template=rf"moddy:automod:cfg:tact:(?P<t>{TYPE_ID_RE})",
):
    """The actions Moddy applies for a type (several at once)."""

    def __init__(self, cat: str, *, item: Optional[ui.Select] = None):
        super().__init__(item or ui.Select(
            options=[discord.SelectOption(label="—", value="delete")],
            custom_id=f"moddy:automod:cfg:tact:{cat}"))
        self.cat = cat

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["t"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await common.gate(interaction):
            return
        picked = [v for v in (interaction.data.get("values") or [])
                  if v in actions_mod.STORED_ACTIONS]
        if not picked:
            await common.ack(interaction)
            return
        clean, dropped = actions_mod.resolve_conflicts(picked)

        def mutate(entry, _working, locale):
            policy = common.policy_of(self.cat, entry)
            if entry.get("mode", "auto") == "auto" and policy and tuple(clean) == policy.actions:
                return None  # same as Moddy's own plan: keep the ladder
            timeout = policy.timeout_seconds if policy and "timeout" in policy.actions else None
            entry.update(infractions.set_entry_actions(entry, clean, timeout))
            entry.pop("by_gravity", None)
            if not dropped:
                return None
            winner = "ban" if "ban" in clean else "kick"
            return tp("type.dropped", locale, winner=common.act_label(winner, locale),
                      dropped=", ".join(common.act_label(a, locale) for a in dropped))
        await _edit_entry(interaction, self.cat, mutate)


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
        values = interaction.data.get("values") or []
        typ = infractions.get(self.cat)
        if typ is None or typ.famille != "profil" or not values \
                or values[0] not in profile_actions(self.cat):
            await common.ack(interaction)
            return

        def mutate(entry, _working, _locale):
            entry["action_profil"] = values[0]
        await _edit_entry(interaction, self.cat, mutate)


def profile_actions(cat: str) -> List[str]:
    """Actions offered for a profile type ("reset the name" only fits a name)."""
    actions = list(infractions.ACTIONS_PROFIL)
    if cat != "profil_pseudo":
        actions.remove("rename_warn")
    elif cat not in infractions.HOLD_TYPES:
        actions.remove("hold")
    return actions


class TypeView(BaseView):
    """One infraction type. Not registered: its controls are dynamic items.

    No Save here: everything goes to the ``sanctions`` draft of the list.
    """

    def __init__(self, bot, guild_id: int, locale: str, cfg: Dict[str, Any], cat: str,
                 *, confirm_delete: bool = False, pending: bool = False, notice: str = ""):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.cfg = cfg
        self.cat = cat
        self.confirm_delete = confirm_delete
        self.pending = pending
        self.notice = notice
        self._build_view()

    def _build_view(self) -> None:
        self.clear_items()
        locale, cfg, cat = self.locale, self.cfg, self.cat
        entry = common.entry_of(cfg, cat)
        typ = infractions.get(cat)
        is_perso = infractions.is_perso(cat)
        is_profile = typ.famille == "profil"
        name = common.type_name(cat, cfg, locale)
        crumb_name = common.safe_text(name)

        if self.confirm_delete:
            container = ui.Container(accent_colour=discord.Colour(COLORS["error"]))
            container.add_item(trail(locale, tp("nav.sanctions", locale), crumb_name))
            container.add_item(ui.TextDisplay(f"### {WARNING} {tp('type.delete_title', locale)}"))
            container.add_item(ui.TextDisplay(tp("type.delete_body", locale, name=crumb_name)))
            self.add_item(container)
            self.add_item(row(
                TypeButton("view", cat, label=tp("type.cancel", locale)),
                TypeButton("delyes", cat, label=tp("type.delete_confirm", locale),
                           emoji=DELETE, style=discord.ButtonStyle.danger)))
            return

        policy = common.policy_of(cat, entry)
        enabled = policy.enabled
        container = ui.Container(accent_colour=_ACCENT)
        container.add_item(trail(locale, tp("nav.sanctions", locale), crumb_name))
        container.add_item(ui.TextDisplay(f"### {common.type_emoji(cat)} {name}"))
        container.add_item(ui.TextDisplay(common.type_desc(cat, cfg, locale)))
        if not common.monitored(cfg, cat):
            container.add_item(ui.TextDisplay(f"-# {WARNING} {tp('type.not_monitored', locale)}"))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        current_profile_action = common.profile_action(cat, entry)
        if is_profile:
            container.add_item(ui.TextDisplay(
                f"**{tp('type.profile_action', locale)}**\n-# {tp('type.profile_action_hint', locale)}"))
            options = [
                discord.SelectOption(
                    label=tp(f"profile_action.{a}", locale),
                    description=tp(f"profile_action.{a}_desc", locale)[:100],
                    value=a, default=(a == current_profile_action))
                for a in profile_actions(cat)
            ]
            container.add_item(row(TypeActionSelect(cat, item=ui.Select(
                options=options, min_values=1, max_values=1,
                custom_id=f"moddy:automod:cfg:tsel:{cat}",
                placeholder=tp("type.profile_action", locale)))))

        if not enabled:
            container.add_item(ui.TextDisplay(
                f"-# {tp('type.off_profile' if is_profile else 'type.off', locale)}"))
        elif not is_profile or current_profile_action == "bareme":
            self._actions_block(container, entry, policy)

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        buttons = [TypeButton(
            "toggle", cat, label=tp("type.enabled" if enabled else "type.disabled", locale),
            emoji=TOGGLE_ON if enabled else TOGGLE_OFF,
            style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary),
            TypeButton("reset", cat, label=tp("type.reset", locale), emoji=SYNC,
                       disabled=cat not in cfg["sanctions"])]
        if is_perso:
            buttons.append(TypeButton("edit", cat, label=tp("type.edit", locale), emoji=EDIT))
            buttons.append(TypeButton("delete", cat, label=tp("type.delete", locale), emoji=DELETE,
                                      style=discord.ButtonStyle.danger))
        container.add_item(row(*buttons))
        if self.pending:
            container.add_item(ui.TextDisplay(f"-# {WARNING} {tp('type.pending_hint', locale)}"))
        self.add_item(container)
        self.add_item(row(TypeButton("back", cat, label=t("modules.config.buttons.back", locale=locale),
                                     emoji=BACK)))

    def _actions_block(self, container: ui.Container, entry: Dict[str, Any], policy) -> None:
        """Actions picker, timeout duration, approval and escalation."""
        locale, cat = self.locale, self.cat
        is_profile = infractions.get(cat).famille == "profil"
        container.add_item(ui.TextDisplay(
            f"**{tp('type.actions', locale)}**\n-# {tp('type.actions_hint', locale)}"))
        container.add_item(row(TypeActionsSelect(cat, item=ui.Select(
            options=[discord.SelectOption(
                label=common.act_label(a, locale), value=a,
                description=tp(f"act.{a}_desc", locale)[:100],
                emoji=common.emoji_of(common.action_icon(a)),
                default=a in policy.actions)
                for a in actions_mod.STORED_ACTIONS],
            min_values=1, max_values=len(actions_mod.STORED_ACTIONS),
            custom_id=f"moddy:automod:cfg:tact:{cat}",
            placeholder=tp("type.actions", locale)))))
        rules = f"-# {tp('type.conflicts', locale)}"
        if self.notice:
            rules += f"\n-# {WARNING} {self.notice}"
        container.add_item(ui.TextDisplay(rules))

        if "timeout" in policy.actions:
            # A button that opens a Modal shows nothing: print the stored value.
            container.add_item(ui.TextDisplay(
                f"**{tp('type.timeout_title', locale)}**: `"
                f"{actions_mod.format_duration(policy.timeout_seconds, locale)}`"))
            container.add_item(row(TypeButton(
                "timeout", cat, label=tp("type.timeout_button", locale), emoji=TIME)))

        toggles = []
        if not is_profile and actions_mod.sanction_actions(policy.actions):
            container.add_item(ui.TextDisplay(f"-# {PENDING} {tp('type.approval_hint', locale)}"))
            on = policy.require_approval
            toggles.append(TypeButton(
                "approval", cat, label=tp("type.approval", locale),
                emoji=TOGGLE_ON if on else TOGGLE_OFF,
                style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary))
        on = policy.recidive
        toggles.append(TypeButton(
            "recidive", cat, label=tp("type.escalate", locale),
            emoji=TOGGLE_ON if on else TOGGLE_OFF,
            style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary))
        container.add_item(row(*toggles))
        container.add_item(ui.TextDisplay(
            f"-# {tp('type.note_auto' if policy.mode == 'auto' else 'type.note_custom', locale)}"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await common.gate(interaction)


# =========================================================================== #
# Modals
# =========================================================================== #
class TimeoutModal(BaseModal):
    """The exact timeout duration ("16h53", "1d 2h", "90m"), Modal V2."""

    def __init__(self, locale: str, cat: str = "insulte", seconds: Optional[int] = None):
        super().__init__(title=tp("timeout_modal.title", locale)[:45])
        self.locale = locale
        self.cat = cat
        self.duration = ui.TextInput(
            style=discord.TextStyle.short, max_length=40, required=True,
            default=common.duration_input(seconds or actions_mod.DEFAULT_TIMEOUT_SECONDS),
            placeholder=actions_mod.duration_input_hint(locale)[:100])
        self.add_item(ui.Label(
            text=tp("timeout_modal.label", locale)[:45],
            description=tp("timeout_modal.desc", locale)[:100],
            component=self.duration))

    async def on_submit(self, interaction: discord.Interaction):
        raw = (self.duration.value or "").strip()
        seconds = actions_mod.parse_duration(raw)
        if seconds is None:
            raise InvalidInput(
                title=tp("errors.title", self.locale),
                description=tp("errors.timeout_invalid", self.locale, value=raw[:40] or "—",
                               examples=actions_mod.duration_input_hint(self.locale)))
        if actions_mod.validate_timeout_seconds(seconds) is not None:
            raise InvalidInput(title=tp("errors.title", self.locale),
                               description=tp("errors.timeout_range", self.locale))

        def mutate(entry, _working, _locale):
            policy = common.policy_of(self.cat, entry)
            acts = list(policy.actions) if policy and "timeout" in policy.actions else None
            if acts is None:  # the type no longer times out: nothing to set
                return None
            entry.update(infractions.set_entry_actions(entry, acts, seconds))
            entry.pop("by_gravity", None)
            return None
        await _edit_entry(interaction, self.cat, mutate)


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
        _stored, working, _pending = await common.load_working(interaction, "sanctions")
        pid = existing["id"] if existing else custom_infractions.new_id(
            custom_infractions.ids(working["infractions_perso"]))
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

        # Re-read: the draft may have changed while the AI was thinking.
        stored, working, _pending = await common.load_working(interaction, "sanctions")
        current = working["infractions_perso"]
        if existing:
            if custom_infractions.find(current, existing["id"]) is None:
                await render_sanctions(interaction)
                return
            working["infractions_perso"] = [entry if e["id"] == pid else e for e in current]
        else:
            if len(current) >= custom_infractions.MAX_INFRACTIONS:
                await common.send_error(interaction, tp(
                    "list.limit", locale, max=custom_infractions.MAX_INFRACTIONS))
                return
            current.append(entry)
        await common.stash(interaction, "sanctions", stored, working)
        await render_type(interaction, custom_infractions.categorie_of(entry))
