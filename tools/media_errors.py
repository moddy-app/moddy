"""Expected failures of the media tools (/image, /convert).

Pure Python (no Discord): the cog/service layer turns a :class:`MediaError`
into a ``utils.tool_card.ToolError`` whose ``code`` maps to
``tools.image.errors.<code>`` / ``tools.convert.errors.<code>``.
"""

from __future__ import annotations

from typing import Any, Dict


class MediaError(Exception):
    """An expected failure: ``code`` is the i18n key, ``params`` fill its placeholders."""

    def __init__(self, code: str, **params: Any):
        super().__init__(code)
        self.code = code
        self.params: Dict[str, Any] = params
