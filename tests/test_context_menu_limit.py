"""Discord caps an application at 5 message context menus (and 5 user ones).

Going over makes the extension that registers the sixth one fail to load at
startup (``CommandLimitReached``). Every menu declared in ``cogs/`` is a message
menu today, so counting the declarations is enough to catch the mistake in CI
instead of in production. A new tool gets a slash command, or is merged into an
existing menu (see docs/features/VOICE_TRANSCRIPTION.md).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
MESSAGE_MENU_LIMIT = 5
_DECLARATION = re.compile(r"app_commands\.ContextMenu\(|@app_commands\.context_menu\(|@\w+\.context_menu\(")


def _declared():
    found = {}
    for path in sorted((ROOT / "cogs").rglob("*.py")):
        count = len(_DECLARATION.findall(path.read_text(encoding="utf-8")))
        if count:
            found[path.name] = count
    return found


def test_message_context_menus_stay_under_the_discord_limit():
    declared = _declared()
    assert sum(declared.values()) <= MESSAGE_MENU_LIMIT, declared


def test_every_declared_menu_is_localized():
    import json
    names = set()
    for path in (ROOT / "cogs").rglob("*.py"):
        names.update(re.findall(r'ContextMenu\(\s*name="([^"]+)"', path.read_text(encoding="utf-8")))
    for locale_file in (ROOT / "locales" / "commands").glob("*.json"):
        menus = set(json.loads(locale_file.read_text(encoding="utf-8")).get("context_menus", {}))
        assert menus == names, (locale_file.name, menus ^ names)
