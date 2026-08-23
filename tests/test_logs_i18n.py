"""Every server-log string must exist in every locale.

The logs system has 163 events across 18 categories and roughly a hundred
field labels, all resolved at render time. A missing key does not crash: it
renders as ``[modules.logs.…]`` in a production log channel. These tests are
what stops that from shipping.

The field/value keys are discovered by reading the listener sources, so a new
``entry.line("something")`` fails here until it is translated — which is
exactly the point.
"""

import json
import re
from pathlib import Path

import pytest

from serverlogs import registry

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")

#: Keys built at runtime from a variable rather than a literal, so the source
#: scan cannot see them. Kept explicit rather than loosening the regex.
DYNAMIC_FIELD_KEYS = {
    "keyword_filter", "regex_patterns", "presets", "allow_list",
}

_LINE_RE = re.compile(r"\.(?:line|block)\(\s*[\"']([a-z_]+)[\"']")
_LABEL_RE = re.compile(r"label\s*=\s*[\"']([a-z_]+)[\"']\s*[,)]")
_VALUE_RE = re.compile(r"modules\.logs\.values\.([a-z_]+)[\"']")


def _load(locale: str) -> dict:
    with open(ROOT / "locales" / f"{locale}.json", encoding="utf-8") as handle:
        return json.load(handle)


def _logs_block(locale: str) -> dict:
    block = _load(locale).get("modules", {}).get("logs")
    assert block, f"locales/{locale}.json has no modules.logs block"
    return block


def _sources():
    for path in (ROOT / "serverlogs").rglob("*.py"):
        yield path.read_text(encoding="utf-8")


def _used_field_keys() -> set:
    keys = set(DYNAMIC_FIELD_KEYS)
    for source in _sources():
        keys |= set(_LINE_RE.findall(source))
        keys |= set(_LABEL_RE.findall(source))
    return keys


def _used_value_keys() -> set:
    keys = set()
    for source in _sources():
        keys |= set(_VALUE_RE.findall(source))
    return keys


@pytest.mark.parametrize("locale", LOCALES)
def test_every_event_has_a_name_and_a_title(locale):
    block = _logs_block(locale)
    missing = []
    for spec in registry.EVENTS.values():
        if not block.get("events", {}).get(spec.category, {}).get(spec.event):
            missing.append(f"events.{spec.key}")
        if not block.get("titles", {}).get(spec.category, {}).get(spec.event):
            missing.append(f"titles.{spec.key}")
    assert not missing, f"{locale} is missing: {missing[:20]} ({len(missing)} total)"


@pytest.mark.parametrize("locale", LOCALES)
def test_every_category_is_described(locale):
    block = _logs_block(locale)
    missing = [
        category.id for category in registry.CATEGORIES.values()
        if not block.get("categories", {}).get(category.id, {}).get("name")
        or not block.get("categories", {}).get(category.id, {}).get("description")
    ]
    assert not missing, f"{locale} has undescribed categories: {missing}"


@pytest.mark.parametrize("locale", LOCALES)
def test_every_field_label_used_by_a_listener_is_translated(locale):
    fields = _logs_block(locale).get("fields", {})
    missing = sorted(key for key in _used_field_keys() if key not in fields)
    assert not missing, f"{locale} is missing field labels: {missing}"


@pytest.mark.parametrize("locale", LOCALES)
def test_every_standalone_value_is_translated(locale):
    values = _logs_block(locale).get("values", {})
    missing = sorted(key for key in _used_value_keys() if key not in values)
    assert not missing, f"{locale} is missing values: {missing}"


@pytest.mark.parametrize("locale", LOCALES)
def test_every_discord_permission_is_translated(locale):
    import discord

    permissions = _logs_block(locale).get("permissions", {})
    missing = sorted(name for name in discord.Permissions.VALID_FLAGS
                     if name not in permissions)
    assert not missing, f"{locale} is missing permission names: {missing}"


@pytest.mark.parametrize("locale", LOCALES)
def test_no_stale_event_translations(locale):
    """A renamed event must not leave its old translation behind."""
    block = _logs_block(locale)
    stale = []
    for section in ("events", "titles"):
        for category_id, events in block.get(section, {}).items():
            category = registry.CATEGORIES.get(category_id)
            if category is None:
                stale.append(f"{section}.{category_id}")
                continue
            stale.extend(f"{section}.{category_id}.{event}" for event in events
                         if event not in category.events)
    assert not stale, f"{locale} has stale entries: {stale}"
