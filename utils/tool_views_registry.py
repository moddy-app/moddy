"""
One place that gathers the persistent views of the utility tools.

Each tool theme owns a views module exposing two lists:

* ``PERSISTENT_VIEWS`` — ``BaseView`` subclasses with ``__persistent__ = True``
  (registered by ``utils/persistent_views.py``);
* ``DYNAMIC_ITEM_CASES`` — ``(DynamicItemClass, sample_args)`` pairs checked by
  ``tests/test_persistent_views.py`` (template matches its own custom_id,
  <= 100 chars, no overlap).

Adding a tool view = appending to its theme's lists; this registry and the
tests stay untouched.
"""

from __future__ import annotations

import importlib
from typing import List, Tuple

TOOL_VIEW_MODULES: Tuple[str, ...] = (
    "utils.tools_text_views",
    "utils.tools_security_views",
    "utils.tools_web_views",
    "utils.tools_lookup_views",
    "utils.tools_media_views",
    "utils.tools_inspect_views",
    "utils.music_links_views",
    "utils.translate_button_views",
)


def _modules():
    return [importlib.import_module(name) for name in TOOL_VIEW_MODULES]


def persistent_view_classes() -> List[type]:
    classes: List[type] = []
    for module in _modules():
        classes.extend(getattr(module, "PERSISTENT_VIEWS", ()))
    return classes


def dynamic_item_cases() -> List[tuple]:
    cases: List[tuple] = []
    for module in _modules():
        cases.extend(getattr(module, "DYNAMIC_ITEM_CASES", ()))
    return cases
