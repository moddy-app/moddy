"""User preferences: icon theme + language override (utils/user_preferences.py)."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils import user_preferences as up
from utils.i18n import i18n

LOCALES = ["fr", "en-US", "es-ES", "pt-BR", "de"]
NEW_KEYS = [
    "theme.label", "theme.placeholder", "theme.options.dark", "theme.options.light",
    "language.label", "language.placeholder", "language.auto", "language.auto_description",
]


class FakeDB:
    def __init__(self):
        self.users = {}

    async def get_user(self, user_id):
        return {"user_id": user_id, "data": dict(self.users.get(user_id, {}))}

    async def update_user_data(self, user_id, path, value):
        assert path == "preferences"
        self.users.setdefault(user_id, {})[path] = value

    async def get_users_with_preferences(self):
        return [(u, d["preferences"]) for u, d in self.users.items() if "preferences" in d]


@pytest.fixture(autouse=True)
def clean_cache():
    up._cache.clear()
    yield
    up._cache.clear()


@pytest.fixture
def bot():
    return SimpleNamespace(db=FakeDB())


def test_defaults():
    assert up.get_icon_theme(1) == "dark"
    assert up.get_icon_theme(None) == "dark"
    assert up.get_language_override(1) is None
    assert up.get_language_override(None) is None


async def test_set_and_clear(bot):
    await up.set_icon_theme(bot, 1, "light")
    await up.set_language(bot, 1, "fr")
    assert up.get_icon_theme(1) == "light"
    assert up.get_language_override(1) == "fr"
    assert bot.db.users[1]["preferences"] == {"icon_theme": "light", "language": "fr"}

    await up.set_icon_theme(bot, 1, "dark")
    assert up.get_icon_theme(1) == "dark"
    assert bot.db.users[1]["preferences"] == {"language": "fr"}

    await up.set_language(bot, 1, "auto")
    assert 1 not in up._cache  # back to defaults: no cache entry
    assert bot.db.users[1]["preferences"] == {}


async def test_validation(bot):
    with pytest.raises(ValueError):
        await up.set_icon_theme(bot, 1, "blue")
    with pytest.raises(ValueError):
        await up.set_language(bot, 1, "klingon")
    assert bot.db.users == {}


async def test_load_all_keeps_only_valid_non_defaults(bot):
    bot.db.users = {
        1: {"preferences": {"icon_theme": "light"}},
        2: {"preferences": {"icon_theme": "dark"}},
        3: {"preferences": {"language": "xx"}},
        4: {"preferences": {"language": "de"}},
        5: {"reminder_timezone": "UTC"},
    }
    assert await up.load_all(bot) == 2
    assert up.get_icon_theme(1) == "light"
    assert up.get_language_override(4) == "de"
    assert up.get_language_override(3) is None


def _interaction(user_id, locale="en-US"):
    return SimpleNamespace(user=SimpleNamespace(id=user_id), locale=locale)


def test_get_user_locale_override():
    up._cache[7] = {"language": "de"}
    assert i18n.get_user_locale(_interaction(7, "en-US")) == "de"
    assert i18n.get_user_locale(_interaction(8, "fr")) == "fr"  # no override


def test_get_user_locale_unsupported_override_ignored():
    up._cache[7] = {"language": "xx"}
    assert i18n.get_user_locale(_interaction(7, "fr")) == "fr"


def test_get_user_locale_missing_user():
    assert i18n.get_user_locale(SimpleNamespace(locale="fr")) == "fr"


@pytest.mark.parametrize("locale", LOCALES)
def test_new_keys_in_all_locales(locale):
    data = json.loads((Path(__file__).parent.parent / "locales" / f"{locale}.json")
                      .read_text(encoding="utf-8"))
    node = data["commands"]["preferences"]
    for key in NEW_KEYS:
        cur = node
        for part in key.split("."):
            assert part in cur, f"{locale}: missing commands.preferences.{key}"
            cur = cur[part]
        assert isinstance(cur, str) and cur.strip()


def test_dynamic_item_templates_round_trip():
    from cogs.preferences import LanguageSelect, ThemeSelect

    for cls, action in ((ThemeSelect, "theme_select"), (LanguageSelect, "lang_select")):
        item = cls(123456789012345678, locale="en-US")
        cid = item.item.custom_id
        assert cid == f"moddy:pref:manage:{action}:123456789012345678"
        match = cls.__discord_ui_compiled_template__.fullmatch(cid)
        assert match and int(match["owner"]) == 123456789012345678
        # Bare shell (owner 0) must still match its own template
        shell = cls(0)
        assert cls.__discord_ui_compiled_template__.fullmatch(shell.item.custom_id)


def test_selects_mark_current_value_as_default():
    from cogs.preferences import LanguageSelect, ThemeSelect

    theme = ThemeSelect(1, current="light").item.options
    assert [o.value for o in theme if o.default] == ["light"]
    lang = LanguageSelect(1, current="pt-BR").item.options
    assert [o.value for o in lang if o.default] == ["pt-BR"]
    assert [o.value for o in LanguageSelect(1, current=None).item.options if o.default] == ["auto"]
    assert len(lang) == 6
