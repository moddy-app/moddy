"""Validation of the slash command localization files (locales/commands/*.json).

Discord rejects the *whole* command sync when a single localized name is
invalid, so these checks matter: they run offline and need nothing but the
JSON files.

    pip install -r requirements-dev.txt && pytest tests/test_command_localizations.py
"""

import json
from pathlib import Path

import pytest

from utils.command_translator import (
    MAX_DESCRIPTION_LENGTH,
    _is_valid_context_menu_name,
    _is_valid_name,
)

LOCALES_DIR = Path(__file__).parent.parent / 'locales' / 'commands'
REFERENCE_LOCALE = 'en-US'

LOCALE_FILES = sorted(LOCALES_DIR.glob('*.json'))
LOCALE_CODES = [p.stem for p in LOCALE_FILES]


def load(locale: str) -> dict:
    with open(LOCALES_DIR / f'{locale}.json', 'r', encoding='utf-8') as f:
        return json.load(f)


def test_locales_directory_is_not_empty():
    assert LOCALE_FILES, "no command localization file found"


def test_every_locale_is_a_known_discord_locale():
    import discord

    known = {locale.value for locale in discord.Locale}
    unknown = set(LOCALE_CODES) - known
    assert not unknown, f"unknown Discord locales: {sorted(unknown)}"


@pytest.mark.parametrize('locale', LOCALE_CODES)
def test_names_follow_discord_rules(locale):
    data = load(locale)

    for qualified, entry in data.get('commands', {}).items():
        name = entry.get('name')
        assert name, f"[{locale}] missing name for '{qualified}'"
        assert _is_valid_name(name), f"[{locale}] invalid command name '{name}' for '{qualified}'"

        for param, node in entry.get('parameters', {}).items():
            pname = node.get('name')
            if pname:
                assert _is_valid_name(pname), f"[{locale}] invalid parameter name '{pname}' ({qualified}.{param})"

    for param, node in data.get('common', {}).get('parameters', {}).items():
        pname = node.get('name')
        if pname:
            assert _is_valid_name(pname), f"[{locale}] invalid common parameter name '{pname}' ({param})"

    for menu, node in data.get('context_menus', {}).items():
        name = node.get('name')
        assert name, f"[{locale}] missing context menu name for '{menu}'"
        assert _is_valid_context_menu_name(name), f"[{locale}] invalid context menu name '{name}' for '{menu}'"


@pytest.mark.parametrize('locale', LOCALE_CODES)
def test_descriptions_fit_discord_limit(locale):
    data = load(locale)

    for qualified, entry in data.get('commands', {}).items():
        description = entry.get('description')
        assert description, f"[{locale}] missing description for '{qualified}'"
        assert len(description) <= MAX_DESCRIPTION_LENGTH, (
            f"[{locale}] description too long ({len(description)}) for '{qualified}'"
        )

        for param, node in entry.get('parameters', {}).items():
            desc = node.get('description')
            if desc:
                assert len(desc) <= MAX_DESCRIPTION_LENGTH, (
                    f"[{locale}] parameter description too long ({len(desc)}) for '{qualified}.{param}'"
                )

    for param, node in data.get('common', {}).get('parameters', {}).items():
        desc = node.get('description')
        if desc:
            assert len(desc) <= MAX_DESCRIPTION_LENGTH, (
                f"[{locale}] common parameter description too long ({len(desc)}) for '{param}'"
            )


@pytest.mark.parametrize('locale', LOCALE_CODES)
def test_top_level_names_are_unique(locale):
    """Discord rejects two commands sharing a localized name in the same locale."""
    data = load(locale)
    seen = {}
    for qualified, entry in data.get('commands', {}).items():
        if ' ' in qualified:  # sub-commands only need to be unique inside their group
            continue
        name = entry['name']
        assert name not in seen, f"[{locale}] duplicate command name '{name}' ({seen.get(name)} / {qualified})"
        seen[name] = qualified


@pytest.mark.parametrize('locale', LOCALE_CODES)
def test_subcommand_names_are_unique_within_their_group(locale):
    data = load(locale)
    seen = {}
    for qualified, entry in data.get('commands', {}).items():
        if ' ' not in qualified:
            continue
        group = qualified.rsplit(' ', 1)[0]
        key = (group, entry['name'])
        assert key not in seen, f"[{locale}] duplicate sub-command name '{entry['name']}' in group '{group}'"
        seen[key] = qualified


@pytest.mark.parametrize('locale', LOCALE_CODES)
def test_parameter_names_are_unique_within_a_command(locale):
    data = load(locale)
    common = data.get('common', {}).get('parameters', {})

    for qualified, entry in data.get('commands', {}).items():
        params = entry.get('parameters', {})
        names = []
        for param, node in params.items():
            names.append(node.get('name') or param)
        # Parameters not overridden by the command fall back to the common table.
        for param, node in common.items():
            if param not in params:
                names.append(node.get('name') or param)
        duplicates = {n for n in names if names.count(n) > 1}
        assert not duplicates, f"[{locale}] duplicate parameter names {duplicates} in '{qualified}'"


@pytest.mark.parametrize('locale', [code for code in LOCALE_CODES if code != REFERENCE_LOCALE])
def test_keys_match_the_reference_locale(locale):
    """Every locale covers exactly the commands declared in the reference file."""
    reference = load(REFERENCE_LOCALE)
    data = load(locale)

    missing = set(reference['commands']) - set(data.get('commands', {}))
    extra = set(data.get('commands', {})) - set(reference['commands'])
    assert not missing, f"[{locale}] missing commands: {sorted(missing)}"
    assert not extra, f"[{locale}] unknown commands: {sorted(extra)}"

    ref_menus = set(reference.get('context_menus', {}))
    menus = set(data.get('context_menus', {}))
    assert not ref_menus - menus, f"[{locale}] missing context menus: {sorted(ref_menus - menus)}"
    assert not menus - ref_menus, f"[{locale}] unknown context menus: {sorted(menus - ref_menus)}"

    for qualified, entry in reference['commands'].items():
        ref_params = set(entry.get('parameters', {}))
        params = set(data['commands'][qualified].get('parameters', {}))
        assert not ref_params - params, (
            f"[{locale}] '{qualified}' is missing parameters: {sorted(ref_params - params)}"
        )
