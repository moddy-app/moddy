"""
Slash command localization for Moddy.

Discord lets an application ship a *localized* name and description for every
slash command, group, parameter and choice. The user then sees the command in
their own Discord language (``interaction.locale``) — ``/traduire`` for a French
user, ``/translate`` for an English one — while the code keeps using the
original English identifiers.

discord.py exposes this through :class:`discord.app_commands.Translator`:
``translate()`` is called once per (string, locale, context) at sync time, and
whatever it returns is uploaded to Discord as ``name_localizations`` /
``description_localizations``.

Translations live in ``locales/commands/<discord-locale>.json`` — one file per
Discord locale (``fr.json``, ``de.json``, ``pt-BR.json``, ...). Structure:

.. code-block:: json

    {
      "common": {
        "parameters": {
          "incognito": {"name": "incognito", "description": "..."}
        }
      },
      "commands": {
        "ping": {"name": "ping", "description": "..."},
        "interserver report": {
          "name": "signaler",
          "description": "...",
          "parameters": {"message_id": {"name": "id-message", "description": "..."}}
        }
      },
      "context_menus": {"Translate": {"name": "Traduire"}},
      "choices": {"EN-US": {"name": "..."}}
    }

Commands are keyed by their **qualified name** (``"interserver report"``), so a
sub-command never collides with a top-level one. Parameters are resolved
command-first, then from ``common.parameters`` — that keeps the ubiquitous
``incognito`` option defined once per language.

Anything missing simply falls back to the original English string (returning
``None`` tells discord.py "no localization"), so a partially translated file is
always safe.

Discord's validation rules are enforced *before* returning a value, because a
single invalid localized name makes the whole command sync fail:

- command / group / parameter names: 1-32 chars, lowercase, only letters,
  digits, ``-`` and ``_`` (plus Devanagari and Thai marks)
- descriptions: 1-100 chars
- context menu names: 1-32 chars, spaces and uppercase allowed

Invalid entries are logged and ignored rather than raised.

See → docs/COMMAND_LOCALIZATION.md
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

import discord
from discord import app_commands
from discord.app_commands import TranslationContextLocation as Location
from discord.app_commands import locale_str

logger = logging.getLogger('moddy.command_translator')

LOCALES_DIR = Path(__file__).parent.parent / 'locales' / 'commands'

# Discord's slash command name rule (see the API docs). ``re.UNICODE`` is the
# default in Python 3, so ``\w`` already covers accented/CJK letters; the
# combining marks are needed for Devanagari (hi) and Thai (th).
NAME_PATTERN = re.compile(r'^[-_\wऀ-ॿ฀-๿]{1,32}$', re.UNICODE)
CONTEXT_MENU_NAME_PATTERN = re.compile(r'^[\w\s\'\"\-ऀ-ॿ฀-๿]{1,32}$', re.UNICODE)

MAX_DESCRIPTION_LENGTH = 100


def _is_valid_name(name: str) -> bool:
    """Check a localized command/group/parameter name against Discord's rules."""
    if not NAME_PATTERN.match(name):
        return False
    # Discord requires lowercase names. Scripts without casing (CJK, Thai...)
    # are unaffected by ``lower()`` so this check is safe for every language.
    return name == name.lower()


def _is_valid_context_menu_name(name: str) -> bool:
    """Context menu entries may contain spaces and uppercase letters."""
    return bool(CONTEXT_MENU_NAME_PATTERN.match(name))


class ModdyCommandTranslator(app_commands.Translator):
    """Serves the localized command metadata stored in ``locales/commands/``."""

    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #

    async def load(self) -> None:
        """Called by discord.py when the translator is attached to the tree."""
        self.load_translations()

    def load_translations(self) -> None:
        """Load every ``locales/commands/*.json`` file (sync, safe to re-run)."""
        self._data.clear()

        if not LOCALES_DIR.exists():
            logger.warning(f"Command localization folder not found: {LOCALES_DIR}")
            return

        valid_locales = {locale.value for locale in discord.Locale}

        for path in sorted(LOCALES_DIR.glob('*.json')):
            locale_code = path.stem
            if locale_code not in valid_locales:
                logger.warning(f"Unknown Discord locale '{locale_code}' ({path.name}), skipped")
                continue
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self._data[locale_code] = json.load(f)
            except Exception as e:
                logger.error(f"Could not load command localizations from {path.name}: {e}")

        logger.info(f"Command localizations loaded for {len(self._data)} locales")

    @property
    def supported_locales(self) -> set:
        return set(self._data)

    # ------------------------------------------------------------------ #
    # Translation
    # ------------------------------------------------------------------ #

    async def translate(
        self,
        string: locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContext,
    ) -> Optional[str]:
        """Return the localized string, or ``None`` to keep the English one."""
        data = self._data.get(locale.value)
        if data is None:
            return None

        location = context.location
        original = string.message

        if location in (Location.command_name, Location.group_name):
            return self._command_field(data, context.data, 'name', original)

        if location in (Location.command_description, Location.group_description):
            return self._command_field(data, context.data, 'description', original)

        if location is Location.parameter_name:
            return self._parameter_field(data, context.data, 'name', original)

        if location is Location.parameter_description:
            return self._parameter_field(data, context.data, 'description', original)

        if location is Location.choice_name:
            entry = data.get('choices', {}).get(str(getattr(context.data, 'value', '')))
            if isinstance(entry, dict):
                return self._clean_description(entry.get('name'), locale, original)
            return None

        return None

    # ------------------------------------------------------------------ #
    # Lookup helpers
    # ------------------------------------------------------------------ #

    def _command_field(self, data: Dict[str, Any], command: Any, field: str, original: str) -> Optional[str]:
        """Resolve a command / group / context menu name or description."""
        if isinstance(command, app_commands.ContextMenu):
            entry = data.get('context_menus', {}).get(command.name)
            if not isinstance(entry, dict):
                return None
            value = entry.get(field)
            if not isinstance(value, str) or not value:
                return None
            if not _is_valid_context_menu_name(value):
                logger.warning(f"Invalid localized context menu name '{value}' for '{command.name}'")
                return None
            return value

        qualified = getattr(command, 'qualified_name', None) or getattr(command, 'name', original)
        entry = data.get('commands', {}).get(qualified)
        if not isinstance(entry, dict):
            return None

        value = entry.get(field)
        if not isinstance(value, str) or not value:
            return None

        if field == 'name':
            if not _is_valid_name(value):
                logger.warning(f"Invalid localized command name '{value}' for '{qualified}'")
                return None
            return value

        return self._clean_description(value, None, qualified)

    def _parameter_field(self, data: Dict[str, Any], parameter: Any, field: str, original: str) -> Optional[str]:
        """Resolve a parameter name/description (command entry first, then common)."""
        param_name = getattr(parameter, 'name', original)
        command = getattr(parameter, 'command', None)
        qualified = getattr(command, 'qualified_name', None)

        entry = None
        if qualified:
            command_entry = data.get('commands', {}).get(qualified)
            if isinstance(command_entry, dict):
                candidate = command_entry.get('parameters', {}).get(param_name)
                if isinstance(candidate, dict) and candidate.get(field):
                    entry = candidate

        if entry is None:
            candidate = data.get('common', {}).get('parameters', {}).get(param_name)
            if isinstance(candidate, dict) and candidate.get(field):
                entry = candidate

        if entry is None:
            return None

        value = entry.get(field)
        if not isinstance(value, str) or not value:
            return None

        if field == 'name':
            if not _is_valid_name(value):
                logger.warning(f"Invalid localized parameter name '{value}' for '{qualified} {param_name}'")
                return None
            return value

        return self._clean_description(value, None, f"{qualified} {param_name}")

    @staticmethod
    def _clean_description(value: Optional[str], _locale: Any, label: str) -> Optional[str]:
        """Trim a description to Discord's 100 character limit."""
        if not isinstance(value, str) or not value:
            return None
        if len(value) > MAX_DESCRIPTION_LENGTH:
            logger.warning(f"Localized description too long for '{label}', truncated")
            return value[:MAX_DESCRIPTION_LENGTH]
        return value
