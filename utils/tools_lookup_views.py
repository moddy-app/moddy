"""
Views of the lookup utility tools — see docs/features/TOOLS_LOOKUP.md.

Cards are built with ``utils/tool_card.py``. Every interactive component is
persistent (docs/guides/PERSISTENT_VIEWS.md): list each persistent class in
``PERSISTENT_VIEWS`` and each DynamicItem in ``DYNAMIC_ITEM_CASES`` (class,
sample constructor args) — both are picked up by utils/tool_views_registry.py,
so the shared registry and tests never need editing.
"""

from __future__ import annotations

PERSISTENT_VIEWS: list = []
DYNAMIC_ITEM_CASES: list = []
