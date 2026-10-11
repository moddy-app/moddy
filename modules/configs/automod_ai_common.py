"""
Shared plumbing of the Automod AI ``/config`` panel.

The panel is split in three screen modules (home + wizard, sanctions, monitoring
/ guidance / exemptions / advanced) that all rely on this one:

* **instant save** — there is no working copy: every control reads the stored
  config, changes one thing and writes it back through
  ``module_manager.save_module_config`` (which validates it and refreshes the
  live module). A refused change writes nothing and says why;
* **the config schema** — :func:`normalize_cfg` is the single place that turns
  whatever is stored (legacy keys included) into the dict the screens edit;
* **plain wording** — sanctions are always described as "Mute 12 h", never as
  a rung of the internal scale (:func:`sanction_summary`);
* **navigation** — :func:`go` opens a screen from anywhere without the screen
  modules importing each other.

Persistence: nothing here keeps state. Every callback of every screen re-reads
the config, the locale and the permissions from the interaction (see
docs/PERSISTENT_VIEWS.md), so a panel keeps working after a restart.
"""

from __future__ import annotations

import copy
import importlib
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

import discord
from discord import ui

from automod import constants as ac
from automod import custom_infractions, infractions
from automod.presets import blank_config
from modules.configs._common import ack, check_guild_perms, show
from utils import emojis
from utils.components_v2 import create_error_message
from utils.i18n import i18n, t

logger = logging.getLogger("moddy.modules.automod_ai_config")

MODULE_ID = "automod_ai"

# --------------------------------------------------------------------------- #
# custom_id scheme: moddy:automod:cfg:<screen>:<action>[:<param>]
# --------------------------------------------------------------------------- #
CID_ROOT = "moddy:automod:cfg"


def cid(screen: str, action: str) -> str:
    """Static custom_id of a control of a registered (guild-scoped) screen."""
    return f"{CID_ROOT}:{screen}:{action}"


#: Id pattern of an infraction type inside a DynamicItem template: a built-in
#: id (``insulte``) or ``perso:<id>`` for a server-defined one.
TYPE_ID_RE = r"[a-z_]+(?::[a-z0-9]{1,12})?"


def tp(key: str, locale: str = "en-US", **kwargs: Any) -> str:
    """Panel string: ``modules.automod_ai.panel.<key>``."""
    return t(f"modules.automod_ai.panel.{key}", locale=locale, **kwargs)


# --------------------------------------------------------------------------- #
# Sanction icons
# --------------------------------------------------------------------------- #
# Product-approved exception (see the comment above utils/emojis.LOG_EMOJIS):
# the sanction ACTION icons of this panel come from the server-logs icon set.
# Everything else in the panel uses the regular constants of utils/emojis.py.
_ACTION_ICON_NAMES = {
    "delete": "messageremove",
    "warn": "Warn",
    "mute": "timeout",
    "kick": "kick",
    "ban": "ban",
}


def action_icon(action: str) -> str:
    """Icon of a sanction action (delete / warn / mute / kick / ban)."""
    return emojis.log_emoji(_ACTION_ICON_NAMES.get(action, ""))


def cran_action(cran: int) -> str:
    """Action of a rung of the scale: 0 delete, 1 warn, 2..6 mute, 7 ban."""
    if cran <= 0:
        return "delete"
    if cran == 1:
        return "warn"
    return "ban" if cran >= 7 else "mute"


def cran_icon(cran: int) -> str:
    return action_icon(cran_action(cran))


def cran_label(cran: int, locale: str) -> str:
    """"Mute 12 h", "Ban", "Delete only"… — the scale in plain words."""
    return tp(f"cran.{max(0, min(7, int(cran)))}", locale)


#: Rungs offered in the per-gravity pickers, mildest first.
CRAN_CHOICES: Tuple[int, ...] = tuple(range(infractions.CRAN_MIN, infractions.CRAN_MAX + 1))


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
#: Features the shared exemption lists are written onto.
EXEMPT_FEATURES = ("content", "image_nsfw", "image_gore", "image_scam", "profile")
_PLAIN_FEATURES = ("content", "image_nsfw", "image_gore", "image_scam")


def normalize_cfg(stored: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Turn the stored config into the complete dict the screens edit.

    * legacy keys (``rules``, ``log_channel_id``) are read transparently;
    * the legacy ``categories_desactivees`` kill-switch list is folded into
      ``sanctions[<id>].enabled = False`` (an explicit ``enabled`` already in
      ``sanctions`` wins, exactly like ``AutomodModule.load_config``) and is
      **not** carried over, so the next save drops it without losing anything;
    * sanctions of an infraction that no longer exists are dropped, and an
      entry that would fail validation is replaced by its normalised form;
    * the exemption lists of every feature are mirrored from ``content`` —
      the panel edits ONE shared list.
    """
    cfg = blank_config()
    if not stored:
        return cfg
    cfg["enabled"] = bool(stored.get("enabled", False))
    cfg["indications"] = str(stored.get("indications", stored.get("rules", "")) or "")
    notify = stored.get("notify_channel_id", stored.get("log_channel_id"))
    cfg["notify_channel_id"] = int(notify) if notify else None
    cfg["ignore_moderators"] = bool(stored.get("ignore_moderators", True))
    cfg["severity"] = ac.clamp_severity(stored.get("severity", ac.SEVERITY_DEFAULT))
    max_action = str(stored.get("max_action", "ban") or "ban")
    cfg["max_action"] = max_action if max_action in ("warn", "mute", "ban") else "ban"
    cfg["dry_run"] = bool(stored.get("dry_run", False))
    cfg["infractions_perso"] = custom_infractions.normalize(stored.get("infractions_perso"))

    known = set(custom_infractions.ids(cfg["infractions_perso"]))
    raw = stored.get("sanctions") if isinstance(stored.get("sanctions"), dict) else {}
    sanctions: Dict[str, dict] = {}
    for cat, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        if cat in infractions.INFRACTIONS:
            pass
        elif infractions.is_perso(cat) and infractions.perso_id(cat) in known:
            pass
        else:
            continue
        if infractions.validate_sanctions({cat: entry}) is not None:
            entry = infractions.normalize_sanctions({cat: entry}).get(cat)
            if entry is None:
                continue
        sanctions[cat] = copy.deepcopy(entry)
    for cat in stored.get("categories_desactivees") or []:
        cat = str(cat)
        if infractions.get(cat) is None:
            continue
        if infractions.is_perso(cat) and infractions.perso_id(cat) not in known:
            continue
        entry = sanctions.get(cat)
        if isinstance(entry, dict) and "enabled" in entry:
            continue
        sanctions[cat] = {**(entry or {}), "enabled": False}
    cfg["sanctions"] = sanctions

    features = stored.get("features", {}) or {}
    content = features.get("content", {}) or {}
    roles = [int(r) for r in content.get("exempt_roles", []) or []]
    channels = [int(c) for c in content.get("exempt_channels", []) or []]
    for fid in _PLAIN_FEATURES:
        feat = features.get(fid, {}) or {}
        cfg["features"][fid] = {
            "enabled": bool(feat.get("enabled", False)),
            "exempt_roles": list(roles),
            "exempt_channels": list(channels),
        }
    cfg["features"]["image_scam"]["scan_all"] = bool(
        (features.get("image_scam", {}) or {}).get("scan_all", False))
    profile = features.get("profile", {}) or {}
    cfg["features"]["profile"] = {
        "enabled": bool(profile.get("enabled", False)),
        "exempt_roles": list(roles),
        "exempt_channels": [],
        "avatar": bool(profile.get("avatar", True)),
        "banner": bool(profile.get("banner", True)),
        "pseudo": bool(profile.get("pseudo", True)),
    }
    return cfg


async def load_cfg(bot, guild_id: int) -> Dict[str, Any]:
    """The guild's stored config, normalised (blank config when none)."""
    stored = await bot.module_manager.get_module_config(guild_id, MODULE_ID)
    return normalize_cfg(stored)


async def send_ephemeral(interaction: discord.Interaction, **kwargs: Any) -> None:
    """Send a message whether or not the interaction was already answered."""
    if interaction.response.is_done():
        await interaction.followup.send(ephemeral=True, **kwargs)
    else:
        await interaction.response.send_message(ephemeral=True, **kwargs)


async def send_error(interaction: discord.Interaction, body: str,
                     title: Optional[str] = None) -> None:
    locale = i18n.get_user_locale(interaction)
    await send_ephemeral(interaction, view=create_error_message(
        title or tp("errors.title", locale), body))


async def save_cfg(interaction: discord.Interaction, cfg: Dict[str, Any]) -> bool:
    """Validate and write ``cfg`` (instant save). ``False`` = nothing was written.

    On refusal the reason is shown to the person who clicked and the stored
    config is untouched, so a screen can simply re-render from the database.
    """
    bot = interaction.client
    cfg = dict(cfg)
    cfg.pop("categories_desactivees", None)
    ok, error = await bot.module_manager.save_module_config(
        interaction.guild_id, MODULE_ID, cfg, actor_id=interaction.user.id)
    if ok:
        return True
    locale = i18n.get_user_locale(interaction)
    await send_error(interaction, tp("errors.save_failed", locale, error=error or "—"))
    return False


async def respond(interaction: discord.Interaction, view: discord.ui.LayoutView) -> None:
    """Show ``view`` in place of the panel, whether or not it was deferred."""
    await show(interaction, view)


async def gate(interaction: discord.Interaction) -> bool:
    """Manage Server, re-checked on every click (never a stored user id)."""
    return await check_guild_perms(interaction)


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #
_SCREENS = {
    "home": ("modules.configs.automod_ai_config", "render_home"),
    "wizard_channel": ("modules.configs.automod_ai_config", "render_wizard_channel"),
    "wizard_level": ("modules.configs.automod_ai_config", "render_wizard_level"),
    "wizard_mode": ("modules.configs.automod_ai_config", "render_wizard_mode"),
    "monitoring": ("modules.configs.automod_ai_advanced_config", "render_monitoring"),
    "indications": ("modules.configs.automod_ai_advanced_config", "render_indications"),
    "exemptions": ("modules.configs.automod_ai_advanced_config", "render_exemptions"),
    "advanced": ("modules.configs.automod_ai_advanced_config", "render_advanced"),
    "precedents": ("modules.configs.automod_ai_precedents_view", "render_precedents"),
    "sanctions": ("modules.configs.automod_ai_sanctions_config", "render_sanctions"),
    "type": ("modules.configs.automod_ai_sanctions_config", "render_type"),
}


async def go(interaction: discord.Interaction, screen: str, **kwargs: Any) -> None:
    """Open ``screen`` in place of the current one (lazy import: no cycles)."""
    # Acknowledge first: every screen loads state from the database.
    await ack(interaction)
    module_name, func_name = _SCREENS[screen]
    func = getattr(importlib.import_module(module_name), func_name)
    await func(interaction, **kwargs)


def screen_names() -> Iterable[str]:
    return _SCREENS.keys()


# --------------------------------------------------------------------------- #
# Status / summary
# --------------------------------------------------------------------------- #
def any_feature_on(cfg: Dict[str, Any]) -> bool:
    return any(cfg["features"][fid]["enabled"] for fid in EXEMPT_FEATURES)


def status_of(cfg: Dict[str, Any]) -> str:
    """``setup`` | ``off`` | ``simulation`` | ``active`` — the home status line.

    ``setup`` means "Moddy cannot run yet": no alert channel, or nothing
    monitored. It mirrors the running condition of ``AutomodModule.load_config``.
    """
    if not cfg.get("notify_channel_id") or not any_feature_on(cfg):
        return "setup"
    if not cfg.get("enabled"):
        return "off"
    return "simulation" if cfg.get("dry_run") else "active"


def profile_parts(cfg: Dict[str, Any]) -> List[str]:
    prof = cfg["features"]["profile"]
    if not prof["enabled"]:
        return []
    return [p for p in ("avatar", "banner", "pseudo") if prof.get(p)]


def monitored(cfg: Dict[str, Any], cat: str) -> bool:
    """Whether a detection that can produce ``cat`` is currently switched on."""
    feats = cfg["features"]
    if cat == "contenu_nsfw":
        return feats["image_nsfw"]["enabled"]
    if cat == "contenu_choquant":
        return feats["image_gore"]["enabled"]
    if cat == "arnaque_scam":
        return feats["content"]["enabled"] or feats["image_scam"]["enabled"]
    if cat in ("profil_avatar_nsfw", "profil_avatar_choquant"):
        return "avatar" in profile_parts(cfg) or "banner" in profile_parts(cfg)
    if cat == "profil_pseudo":
        return "pseudo" in profile_parts(cfg)
    return feats["content"]["enabled"]


# --------------------------------------------------------------------------- #
# Infraction types: names, groups, icons
# --------------------------------------------------------------------------- #
FAMILY_ORDER: Tuple[str, ...] = ("message", "image", "profil", "perso")


def type_ids(cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    """Every type of the guild, grouped by family, in display order."""
    groups = {fam: infractions.ids_by_famille(fam) for fam in FAMILY_ORDER}
    groups["perso"] = [custom_infractions.categorie_of(e) for e in cfg["infractions_perso"]]
    return groups


def type_exists(cfg: Dict[str, Any], cat: str) -> bool:
    if cat in infractions.INFRACTIONS:
        return True
    return infractions.is_perso(cat) and custom_infractions.find(cfg["infractions_perso"], cat) is not None


def safe_text(value: str) -> str:
    """Server-authored text made safe to print: no live mention, no backtick."""
    return str(value).replace("`", "'").replace("@", "@​").strip()


def type_name(cat: str, cfg: Dict[str, Any], locale: str) -> str:
    from utils.automod_render import infraction_label
    return infraction_label(cat, locale, cfg["infractions_perso"])


def type_desc(cat: str, cfg: Dict[str, Any], locale: str) -> str:
    if infractions.is_perso(cat):
        entry = custom_infractions.find(cfg["infractions_perso"], cat)
        return safe_text(entry["description"]) if entry else ""
    return t(f"modules.automod_ai.infractions.{cat}.desc", locale=locale)


def type_emoji(cat: str) -> str:
    typ = infractions.get(cat)
    return getattr(emojis, typ.emoji_key, emojis.INFO) if typ else emojis.INFO


def resolve_type(cfg: Dict[str, Any], cat: str) -> Optional[infractions.InfractionType]:
    """The catalogue type of ``cat`` if the guild really has it."""
    return infractions.get(cat) if type_exists(cfg, cat) else None


# --------------------------------------------------------------------------- #
# Sanction entries
# --------------------------------------------------------------------------- #
def entry_of(cfg: Dict[str, Any], cat: str) -> Dict[str, Any]:
    entry = cfg["sanctions"].get(cat)
    return entry if isinstance(entry, dict) else {}


def entry_is_default(cat: str, entry: Dict[str, Any]) -> bool:
    """Whether ``entry`` says nothing a missing entry would not (Moddy defaults)."""
    typ = infractions.get(cat)
    if typ is None:
        return False
    if entry.get("enabled", True) is False:
        return False
    if entry.get("mode", "auto") != "auto":
        return False
    action = entry.get("action_profil")
    return action in (None, typ.default_profile_action)


def store_entry(cfg: Dict[str, Any], cat: str, entry: Dict[str, Any]) -> None:
    """Write ``entry`` for ``cat`` — or drop it when it is plain Moddy defaults."""
    if entry_is_default(cat, entry):
        cfg["sanctions"].pop(cat, None)
    else:
        cfg["sanctions"][cat] = entry


def effective_crans(cat: str, entry: Dict[str, Any]) -> Dict[str, int]:
    """Cran per gravity this entry applies (the type's defaults unless custom)."""
    typ = infractions.get(cat)
    floors = dict(typ.plancher) if typ else dict(infractions.PERSO_DEFAULT_PLANCHER)
    if entry.get("mode") == "perso" and isinstance(entry.get("crans"), dict):
        return {g: int(entry["crans"].get(g, floors[g])) for g in infractions.GRAVITES}
    return floors


def profile_action(cat: str, entry: Dict[str, Any]) -> Optional[str]:
    """The ``action_profil`` in force for a profile type (``None`` otherwise)."""
    typ = infractions.get(cat)
    if typ is None or typ.famille != "profil":
        return None
    return entry.get("action_profil") or typ.default_profile_action


def _crans_text(crans: Dict[str, int], locale: str) -> str:
    values = [crans[g] for g in infractions.GRAVITES]
    if len(set(values)) == 1:
        return tp("sanction.always", locale, sanction=cran_label(values[0], locale))
    return " · ".join(
        f"{tp(f'gravity.{g}', locale)}: {cran_label(crans[g], locale)}"
        for g in infractions.GRAVITES)


def default_summary(cat: str, locale: str) -> str:
    """What Moddy does by default, per gravity (shown under "Moddy default")."""
    return _crans_text(effective_crans(cat, {}), locale)


def sanction_summary(cat: str, entry: Dict[str, Any], locale: str) -> Tuple[str, str]:
    """``(icon, text)``: the current sanction of a type in plain words.

    ``icon`` is the sanction-action icon (``""`` when there is nothing to
    point at); ``text`` never contains markdown.
    """
    typ = infractions.get(cat)
    entry = entry or {}
    is_profile = typ is not None and typ.famille == "profil"
    if entry.get("enabled", True) is False:
        return "", tp("sanction.disabled_profile" if is_profile else "sanction.disabled", locale)

    action = profile_action(cat, entry)
    if action == "hold":
        return action_icon("mute"), tp("profile_action.hold", locale)
    if action == "rename_warn":
        return action_icon("warn"), tp("profile_action.rename_warn", locale)
    if action == "alert":
        return "", tp("profile_action.alert", locale)

    crans = effective_crans(cat, entry)
    top = max(crans.values())
    if entry.get("mode") != "perso":
        return "", tp("sanction.default", locale)
    text = _crans_text(crans, locale)
    if entry.get("recidive", True) is False:
        text += " · " + tp("sanction.no_escalation", locale)
    return cran_icon(top), text


def sanction_line(cat: str, cfg: Dict[str, Any], locale: str) -> str:
    """One markdown line of the sanctions list: type icon, name, sanction."""
    icon, text = sanction_summary(cat, entry_of(cfg, cat), locale)
    name = type_name(cat, cfg, locale)
    suffix = f"{icon} {text}" if icon else text
    return f"{type_emoji(cat)} **{name}** · {suffix}"


# --------------------------------------------------------------------------- #
# Guild objects for select defaults
# --------------------------------------------------------------------------- #
def resolve_channels(bot, guild_id: Optional[int], ids: Iterable[Any]) -> List[discord.abc.GuildChannel]:
    guild = bot.get_guild(guild_id) if (bot is not None and guild_id) else None
    if guild is None:
        return []
    out = [guild.get_channel_or_thread(int(i)) for i in ids if i]
    return [c for c in out if c is not None]


def resolve_roles(bot, guild_id: Optional[int], ids: Iterable[Any]) -> List[discord.Role]:
    guild = bot.get_guild(guild_id) if (bot is not None and guild_id) else None
    if guild is None:
        return []
    out = [guild.get_role(int(i)) for i in ids if i]
    return [r for r in out if r is not None]


def count_components(view: discord.ui.LayoutView) -> int:
    """Number of components Discord counts for a Components V2 message."""
    return sum(1 for _ in view.walk_children())


def total_text_length(view: discord.ui.LayoutView) -> int:
    """Characters of every TextDisplay (Discord caps a message at 4000)."""
    return sum(len(getattr(c, "content", "") or "")
               for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay))


# --------------------------------------------------------------------------- #
# Component factories (every control gets its callback and a stable custom_id)
# --------------------------------------------------------------------------- #
def emoji_of(value: Optional[str]) -> Optional[discord.PartialEmoji]:
    return discord.PartialEmoji.from_str(value) if value else None


def button(callback, *, custom_id: str, label: Optional[str] = None,
           emoji: Optional[str] = None,
           style: discord.ButtonStyle = discord.ButtonStyle.secondary,
           disabled: bool = False) -> ui.Button:
    btn = ui.Button(label=label, emoji=emoji_of(emoji), style=style,
                    custom_id=custom_id, disabled=disabled)
    btn.callback = callback
    return btn


def select(callback, *, custom_id: str, options: List[discord.SelectOption],
           placeholder: Optional[str] = None, min_values: int = 1,
           max_values: int = 1) -> ui.Select:
    sel = ui.Select(placeholder=placeholder, options=options, min_values=min_values,
                    max_values=max_values, custom_id=custom_id)
    sel.callback = callback
    return sel


def row(*items: ui.Item) -> ui.ActionRow:
    action_row = ui.ActionRow()
    for item in items:
        action_row.add_item(item)
    return action_row


def back_row(callback, screen: str, locale: str) -> ui.ActionRow:
    """The Back button every sub-screen ends with."""
    return row(button(callback, custom_id=cid(screen, "back"),
                      label=t("modules.config.buttons.back", locale=locale),
                      emoji=emojis.BACK))


def title(locale: str, key: str, emoji: str = emojis.SHIELD) -> ui.TextDisplay:
    return ui.TextDisplay(f"### {emoji} {tp(key, locale)}")


async def back_to_modules(interaction: discord.Interaction) -> None:
    """Back to the ``/config`` module list."""
    from cogs.config import ConfigMainView
    locale = i18n.get_user_locale(interaction)
    view = ConfigMainView(interaction.client, interaction.guild_id, interaction.user.id, locale)
    await show(interaction, view)
