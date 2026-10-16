"""
Shared plumbing of the Automod ``/config`` panel.

The panel is split in screen modules (home + wizard, sanctions, monitoring /
exemptions / advanced) that all rely on this one:

* **two save models** — the *home* screen saves instantly (every control reads
  the stored config, changes one thing and writes it back through
  ``module_manager.save_module_config``). The four *main sub-views*
  (monitoring, sanctions, exemptions, advanced) use the classic module model:
  edits are held as a **draft** and only written when the admin presses Save
  (Save / Cancel appear only while there is something pending). Deeper screens
  (one infraction, the custom-infraction editor) have no Save of their own:
  they edit the draft of their parent sub-view (``sanctions``);
* **the draft** — an in-memory map ``(guild, user, section) -> projection``
  with a TTL (:func:`draft_get` / :func:`stash`). It only stores the keys the
  section owns, never a whole config, so Save merges it onto the freshly loaded
  stored config and never clobbers what the home or the dashboard wrote
  meanwhile. It lives in the process: after a restart (or after the TTL) the
  panel falls back to the stored config, i.e. unsaved edits are dropped;
* **the config schema** — :func:`normalize_cfg` is the single place that turns
  whatever is stored (legacy keys included) into the dict the screens edit;
* **plain wording** — a sanction is always shown as its effective actions
  ("Delete message + Timeout 16 h 53 min"), never as "Moddy default";
* **navigation** — :func:`go` opens a screen from anywhere without the screen
  modules importing each other.

Persistence: nothing here keeps state a restart could miss except the draft.
Every callback of every screen re-reads the config, the locale and the
permissions from the interaction (see docs/guides/PERSISTENT_VIEWS.md).
"""

from __future__ import annotations

import copy
import importlib
import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import discord
from discord import ui

from automod import actions as actions_mod
from automod import constants as ac
from automod import custom_infractions, infractions
from automod.presets import blank_config
from cogs.error_handler import BaseView
from modules.configs._common import ack, check_guild_perms, crumb, show
from utils import emojis
from utils.errors import build_error_card
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


def trail(locale: str, *segments: str) -> ui.TextDisplay:
    """Breadcrumb ``Config › Automod › …`` -- first item of every container."""
    return crumb(locale, MODULE_ID, *segments)


# --------------------------------------------------------------------------- #
# Sanction action icons
# --------------------------------------------------------------------------- #
# Product-approved exception (see the comment above utils/emojis.LOG_EMOJIS):
# the sanction ACTION icons of this panel come from the server-logs icon set.
# They are only used on the actions picker; the sanctions list has no icons.
_ACTION_ICON_NAMES = {
    "delete": "messageremove",
    "warn": "Warn",
    "timeout": "timeout",
    "kick": "kick",
    "ban": "ban",
}


def action_icon(action: str) -> str:
    """Icon of a sanction action (delete / warn / timeout / kick / ban)."""
    return emojis.log_emoji(_ACTION_ICON_NAMES.get(action, ""))


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
#: Features the shared exemption lists are written onto.
EXEMPT_FEATURES = ("content", "image_nsfw", "image_gore", "image_scam", "profile")
_PLAIN_FEATURES = ("content", "image_nsfw", "image_gore", "image_scam")
PROFILE_PARTS = ("avatar", "banner", "pseudo")


def normalize_cfg(stored: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Turn the stored config into the complete dict the screens edit.

    * the generic module ``state`` is resolved from the stored config, legacy
      ``dry_run`` / ``enabled`` flags included (see ``effective_state``), and
      the legacy keys themselves are **not** carried over, so the next save
      drops them without losing anything;
    * the removed free-text AI guidance (``indications`` / ``rules``) is dropped;
    * the legacy ``categories_desactivees`` kill-switch list is folded into
      ``sanctions[<id>].enabled = False`` (an explicit ``enabled`` already in
      ``sanctions`` wins, exactly like ``AutomodModule.load_config``);
    * every sanction entry is normalised (legacy ``crans`` migrated to
      ``actions``); entries that say nothing a missing entry would not, and
      entries of an infraction that no longer exists, are dropped;
    * the exemption lists of every feature are mirrored from ``content`` --
      the panel edits ONE shared list.
    """
    from modules.automod_ai import effective_state

    cfg = blank_config()
    cfg.pop("dry_run", None)
    cfg["state"] = "active"
    if not stored:
        return cfg
    cfg["state"] = effective_state(stored)
    notify = stored.get("notify_channel_id", stored.get("log_channel_id"))
    cfg["notify_channel_id"] = int(notify) if notify else None
    cfg["ignore_moderators"] = bool(stored.get("ignore_moderators", True))
    cfg["severity"] = ac.clamp_severity(stored.get("severity", ac.SEVERITY_DEFAULT))
    max_action = str(stored.get("max_action", "ban") or "ban")
    cfg["max_action"] = max_action if max_action in ("warn", "mute", "ban") else "ban"
    cfg["infractions_perso"] = custom_infractions.normalize(stored.get("infractions_perso"))

    known = set(custom_infractions.ids(cfg["infractions_perso"]))
    raw = {c: copy.deepcopy(e) for c, e in (stored.get("sanctions") or {}).items()
           if isinstance(e, dict)} if isinstance(stored.get("sanctions"), dict) else {}
    explicit = set(raw)
    for cat in stored.get("categories_desactivees") or []:
        cat = str(cat)
        if infractions.get(cat) is None:
            continue
        entry = raw.get(cat)
        if cat in explicit and "enabled" in (entry or {}):
            continue
        raw[cat] = {**(entry or {}), "enabled": False}
    sanctions: Dict[str, dict] = {}
    for cat, entry in infractions.normalize_sanctions(raw, known).items():
        if infractions.is_perso(cat) and infractions.perso_id(cat) not in known:
            continue
        if not entry_is_default(cat, entry):
            sanctions[cat] = entry
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


def with_state(cfg: Dict[str, Any], state: str) -> Dict[str, Any]:
    """``cfg`` with the generic ``state`` set and the legacy ``enabled`` / ``dry_run`` flags gone."""
    out = dict(cfg)
    out.pop("dry_run", None)
    out.pop("enabled", None)
    out["state"] = state
    return out


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
    """An expected error (refused input, refused save): the shared error card."""
    locale = i18n.get_user_locale(interaction)
    await send_ephemeral(interaction, view=build_error_card(
        title or tp("errors.title", locale), body, severity="warning"))


async def save_cfg(interaction: discord.Interaction, cfg: Dict[str, Any]) -> bool:
    """Validate and write ``cfg``. ``False`` = nothing was written.

    On refusal the reason is shown to the person who clicked and the stored
    config is untouched, so a screen can simply re-render from the database.
    """
    bot = interaction.client
    cfg = dict(cfg)
    cfg.pop("categories_desactivees", None)
    cfg.pop("dry_run", None)
    cfg.pop("enabled", None)
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
# Drafts (pending edits of the main sub-views)
# --------------------------------------------------------------------------- #
SECTIONS = ("monitoring", "sanctions", "exemptions", "advanced")

#: A draft nobody touched for this long is forgotten (seconds).
DRAFT_TTL = 30 * 60
_DRAFT_MAX = 512
_drafts: Dict[Tuple[int, int, str], Tuple[float, Dict[str, Any]]] = {}


def project(cfg: Dict[str, Any], section: str) -> Dict[str, Any]:
    """The part of ``cfg`` a section owns (JSON-able, canonical)."""
    feats = cfg["features"]
    if section == "monitoring":
        return {"features": {fid: bool(feats[fid]["enabled"]) for fid in EXEMPT_FEATURES},
                "profile": {p: bool(feats["profile"][p]) for p in PROFILE_PARTS}}
    if section == "exemptions":
        return {"ignore_moderators": bool(cfg["ignore_moderators"]),
                "roles": [int(r) for r in feats["content"]["exempt_roles"]],
                "channels": [int(c) for c in feats["content"]["exempt_channels"]]}
    if section == "advanced":
        return {"severity": int(cfg["severity"]), "max_action": cfg["max_action"]}
    if section == "sanctions":
        return {"sanctions": copy.deepcopy(cfg["sanctions"]),
                "infractions_perso": copy.deepcopy(cfg["infractions_perso"])}
    raise KeyError(section)


def apply_projection(cfg: Dict[str, Any], proj: Dict[str, Any], section: str) -> None:
    """Write a section's projection onto ``cfg`` (in place)."""
    feats = cfg["features"]
    if section == "monitoring":
        for fid, on in proj["features"].items():
            feats[fid]["enabled"] = bool(on)
        for part, on in proj["profile"].items():
            feats["profile"][part] = bool(on)
    elif section == "exemptions":
        cfg["ignore_moderators"] = bool(proj["ignore_moderators"])
        for fid in EXEMPT_FEATURES:
            feats[fid]["exempt_roles"] = list(proj["roles"])
            # Profiles are checked on member events: they have no channel.
            feats[fid]["exempt_channels"] = [] if fid == "profile" else list(proj["channels"])
    elif section == "advanced":
        cfg["severity"] = proj["severity"]
        cfg["max_action"] = proj["max_action"]
    elif section == "sanctions":
        cfg["sanctions"] = copy.deepcopy(proj["sanctions"])
        cfg["infractions_perso"] = copy.deepcopy(proj["infractions_perso"])


def _key(guild_id: Optional[int], user_id: Optional[int], section: str):
    return (int(guild_id or 0), int(user_id or 0), section)


def draft_get(guild_id: Optional[int], user_id: Optional[int], section: str
              ) -> Optional[Dict[str, Any]]:
    item = _drafts.get(_key(guild_id, user_id, section))
    if item is None:
        return None
    if time.monotonic() - item[0] > DRAFT_TTL:
        _drafts.pop(_key(guild_id, user_id, section), None)
        return None
    return copy.deepcopy(item[1])


def draft_set(guild_id: Optional[int], user_id: Optional[int], section: str,
              proj: Dict[str, Any]) -> None:
    now = time.monotonic()
    if len(_drafts) >= _DRAFT_MAX:
        for key in [k for k, (ts, _) in _drafts.items() if now - ts > DRAFT_TTL]:
            _drafts.pop(key, None)
        while len(_drafts) >= _DRAFT_MAX:  # still full: drop the oldest
            _drafts.pop(min(_drafts, key=lambda k: _drafts[k][0]), None)
    _drafts[_key(guild_id, user_id, section)] = (now, copy.deepcopy(proj))


def draft_clear(guild_id: Optional[int], user_id: Optional[int], section: str) -> None:
    _drafts.pop(_key(guild_id, user_id, section), None)


def draft_clear_guild(guild_id: Optional[int]) -> None:
    """Forget every draft of a guild (its configuration was just deleted)."""
    for key in [k for k in _drafts if k[0] == int(guild_id or 0)]:
        _drafts.pop(key, None)


async def load_working(interaction: discord.Interaction, section: str
                       ) -> Tuple[Dict[str, Any], Dict[str, Any], bool]:
    """``(stored, working, pending)`` for ``section``.

    ``stored`` is the config in the database; ``working`` is ``stored`` with the
    caller's draft of the section laid over it; ``pending`` is True when that
    differs from ``stored`` (a draft equal to the stored value is discarded).
    """
    stored = await load_cfg(interaction.client, interaction.guild_id)
    proj = draft_get(interaction.guild_id, interaction.user.id, section)
    working = copy.deepcopy(stored)
    if proj is None:
        return stored, working, False
    if proj == project(stored, section):
        draft_clear(interaction.guild_id, interaction.user.id, section)
        return stored, working, False
    apply_projection(working, proj, section)
    return stored, working, True


def stash(interaction: discord.Interaction, section: str, stored: Dict[str, Any],
          working: Dict[str, Any]) -> bool:
    """Keep ``working`` as the section's draft. Returns whether it is pending."""
    proj = project(working, section)
    if proj == project(stored, section):
        draft_clear(interaction.guild_id, interaction.user.id, section)
        return False
    draft_set(interaction.guild_id, interaction.user.id, section, proj)
    return True


async def save_section(interaction: discord.Interaction, section: str) -> bool:
    """Write the section's draft (merged onto the fresh stored config)."""
    _stored, working, pending = await load_working(interaction, section)
    if not pending:
        return True
    if not await save_cfg(interaction, working):
        return False
    draft_clear(interaction.guild_id, interaction.user.id, section)
    return True


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #
_SCREENS = {
    "home": ("modules.configs.automod_ai_config", "render_home"),
    "wizard_channel": ("modules.configs.automod_ai_config", "render_wizard_channel"),
    "wizard_level": ("modules.configs.automod_ai_config", "render_wizard_level"),
    "wizard_mode": ("modules.configs.automod_ai_config", "render_wizard_mode"),
    "monitoring": ("modules.configs.automod_ai_advanced_config", "render_monitoring"),
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


class SectionView(BaseView):
    """Frame of a main sub-view: Back / Save / Cancel over a draft.

    Subclasses set ``SECTION`` (draft key and ``go`` target) and ``SCREEN`` (the
    custom_id prefix), build their container in ``_build_view`` and end with
    ``self.add_item(self.footer())``. Persistent: yes. Auth: Manage Server on
    every click.
    """

    __persistent__ = True
    SECTION = ""
    SCREEN = ""

    def __init__(self, bot=None, guild_id: Optional[int] = None, locale: str = "en-US",
                 cfg: Optional[Dict[str, Any]] = None, pending: bool = False, **extra: Any):
        super().__init__()  # timeout=None
        self.bot = bot
        self.guild_id = guild_id
        self.locale = locale
        self.cfg = normalize_cfg(cfg)
        self.pending = pending
        self.extra = extra
        self._build_view()

    def _build_view(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def pending_notice(self) -> List[ui.TextDisplay]:
        """The "unsaved changes" line, when something is pending."""
        if not self.pending:
            return []
        return [ui.TextDisplay(f"{emojis.WARNING} {tp('pending.notice', self.locale)}")]

    def footer(self) -> ui.ActionRow:
        """Back (locked while pending) + Save / Cancel (only while pending)."""
        locale = self.locale
        is_shell = self.bot is None  # the shell registers every custom_id
        items = [button(self.on_back, custom_id=cid(self.SCREEN, "back"),
                        label=t("modules.config.buttons.back", locale=locale),
                        emoji=emojis.BACK, disabled=self.pending)]
        if self.pending or is_shell:
            items.append(button(
                self.on_save, custom_id=cid(self.SCREEN, "save"),
                label=t("modules.config.buttons.save", locale=locale),
                emoji=emojis.SAVE, style=discord.ButtonStyle.success))
            items.append(button(
                self.on_cancel, custom_id=cid(self.SCREEN, "cancel"),
                label=t("modules.config.buttons.cancel", locale=locale),
                emoji=emojis.UNDONE, style=discord.ButtonStyle.danger))
        return row(*items)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await gate(interaction)

    async def on_back(self, interaction: discord.Interaction):
        await ack(interaction)
        _s, _w, pending = await load_working(interaction, self.SECTION)
        await go(interaction, self.SECTION if pending else "home")

    async def on_save(self, interaction: discord.Interaction):
        await ack(interaction)
        saved = await save_section(interaction, self.SECTION)
        await go(interaction, self.SECTION)
        if saved:
            await interaction.followup.send(t(
                "modules.config.save.success", locale=i18n.get_user_locale(interaction)),
                ephemeral=True)

    async def on_cancel(self, interaction: discord.Interaction):
        await ack(interaction)
        draft_clear(interaction.guild_id, interaction.user.id, self.SECTION)
        await go(interaction, self.SECTION)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())


# --------------------------------------------------------------------------- #
# Status / summary
# --------------------------------------------------------------------------- #
def any_feature_on(cfg: Dict[str, Any]) -> bool:
    return any(cfg["features"][fid]["enabled"] for fid in EXEMPT_FEATURES)


def needs_setup(cfg: Dict[str, Any]) -> bool:
    """Moddy cannot run yet: no alert channel, or nothing monitored.

    Mirrors the running condition of ``AutomodModule.load_config``.
    """
    return not cfg.get("notify_channel_id") or not any_feature_on(cfg)


def profile_parts(cfg: Dict[str, Any]) -> List[str]:
    prof = cfg["features"]["profile"]
    if not prof["enabled"]:
        return []
    return [p for p in PROFILE_PARTS if prof.get(p)]


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
# Infraction types: names, groups
# --------------------------------------------------------------------------- #
FAMILY_ORDER: Tuple[str, ...] = ("message", "image", "profil", "perso")
#: Icon of each family heading of the sanctions list (the only icons there).
FAMILY_ICONS = {"message": emojis.MESSAGE, "image": emojis.IMAGE,
                "profil": emojis.USER, "perso": emojis.SETTINGS}


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
    """Whether ``entry`` says nothing a missing entry would not (Moddy's ladder)."""
    typ = infractions.get(cat)
    if typ is None:
        return False
    if entry.get("enabled", True) is False:
        return False
    if entry.get("mode", "auto") != "auto":
        return False
    if entry.get("require_approval") is True or entry.get("recidive", True) is False:
        return False
    action = entry.get("action_profil")
    return action in (None, typ.default_profile_action)


def finalize_entry(cat: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """``entry`` in its canonical stored form (what the module would load)."""
    return infractions.normalize_sanctions({cat: entry}).get(cat) or entry


def store_entry(cfg: Dict[str, Any], cat: str, entry: Dict[str, Any]) -> None:
    """Write ``entry`` for ``cat`` -- or drop it when it is plain Moddy defaults."""
    entry = finalize_entry(cat, entry)
    if entry_is_default(cat, entry):
        cfg["sanctions"].pop(cat, None)
    else:
        cfg["sanctions"][cat] = entry


def policy_of(cat: str, entry: Optional[Dict[str, Any]]) -> Optional[infractions.EffectivePolicy]:
    """What ``cat`` applies for a first offence of medium severity, defaults included."""
    return infractions.effective_policy(cat, entry or {})


def profile_action(cat: str, entry: Dict[str, Any]) -> Optional[str]:
    """The ``action_profil`` in force for a profile type (``None`` otherwise)."""
    typ = infractions.get(cat)
    if typ is None or typ.famille != "profil":
        return None
    return entry.get("action_profil") or typ.default_profile_action


def act_label(action: str, locale: str) -> str:
    return tp(f"act.{action}", locale)


def plan_text(actions: Iterable[str], timeout_seconds: Optional[int], locale: str) -> str:
    """``Delete message + Timeout 16 h 53 min``."""
    parts = []
    for action in actions:
        label = act_label(action, locale)
        if action == "timeout":
            label += " " + actions_mod.format_duration(
                timeout_seconds or actions_mod.DEFAULT_TIMEOUT_SECONDS, locale)
        parts.append(label)
    return " + ".join(parts)


def policy_text(cat: str, entry: Optional[Dict[str, Any]], locale: str) -> str:
    """The sanction of a type in plain words, always its effective value."""
    entry = entry or {}
    typ = infractions.get(cat)
    pol = policy_of(cat, entry)
    if typ is None or pol is None:
        return "—"
    if not pol.enabled:
        return tp("sanction.disabled_profile" if typ.famille == "profil"
                  else "sanction.disabled", locale)
    action = profile_action(cat, entry)
    if action in ("hold", "rename_warn", "alert"):
        return tp(f"profile_action.{action}", locale)
    text = plan_text(pol.actions, pol.timeout_seconds, locale)
    if pol.require_approval:
        text += " · " + tp("sanction.approval", locale)
    return text


def duration_input(seconds: Any) -> str:
    """A duration as the text a Modal input is pre-filled with (``16h 53m``).

    Locale-neutral units, so :func:`automod.actions.parse_duration` reads it back
    in every language.
    """
    total = max(0, int(seconds or 0))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{n}{u}" for n, u in ((days, "d"), (hours, "h"), (minutes, "m"), (secs, "s")) if n]
    return " ".join(parts) or "0m"


def sanction_line(cat: str, cfg: Dict[str, Any], locale: str) -> str:
    """One bullet of the sanctions list: bold name, effective sanction."""
    return f"* **{type_name(cat, cfg, locale)}** · {policy_text(cat, entry_of(cfg, cat), locale)}"


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
