"""Capitalisation convention of the command localization files.

Within every locale: slash command / group / parameter names are all lowercase
(Discord rule), context-menu names start with an uppercase letter, and every
description starts with an uppercase letter. Scripts without case (CJK, Thai,
Devanagari) are naturally exempt.
"""

import json
from pathlib import Path

import pytest

LOCALES_DIR = Path(__file__).parent.parent / 'locales' / 'commands'
LOCALE_CODES = sorted(p.stem for p in LOCALES_DIR.glob('*.json'))


def _load(locale):
    with open(LOCALES_DIR / f'{locale}.json', encoding='utf-8') as f:
        return json.load(f)


def _nodes(data):
    for key, entry in data.get('commands', {}).items():
        yield 'command', key, entry
        for p, node in entry.get('parameters', {}).items():
            yield 'parameter', f'{key}.{p}', node
    for p, node in data.get('common', {}).get('parameters', {}).items():
        yield 'parameter', f'common.{p}', node
    for key, node in data.get('context_menus', {}).items():
        yield 'menu', key, node


@pytest.mark.parametrize('locale', LOCALE_CODES)
def test_command_casing_convention(locale):
    errors = []
    for kind, key, node in _nodes(_load(locale)):
        name = node.get('name')
        if name:
            if kind == 'menu':
                if name[0].islower():
                    errors.append(f"menu '{key}': name '{name}' must start uppercase")
            elif name != name.lower():
                errors.append(f"{kind} '{key}': name '{name}' must be lowercase")
        desc = node.get('description')
        if desc and desc[0].islower():
            errors.append(f"{kind} '{key}': description '{desc}' must start uppercase")
    assert not errors, f"[{locale}] " + "; ".join(errors)
