"""
OCR UI — loading card, result card and error card for ``/ocr`` and the
Transcribe menu on an image.

Bare on purpose, like the transcription cards: a title, the extracted text in a
code block (so the layout of the image survives), one dim meta line. Nothing
extracted from an image may ping: every send goes out with
``AllowedMentions.none()``. The cards have no interactive component.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import discord
from discord import ui

from config import COLORS
from utils.automod_render import make_text_file
from utils.emojis import DOWNLOAD, ROBOT_WORKING, TEXT
from utils.i18n import i18n, t
from utils.transcription_views import format_language

ACCENT = COLORS["primary"]
NO_MENTIONS = discord.AllowedMentions.none()
OCR_FILE_NAME = "ocr.txt"


def _code_block(text: str) -> str:
    # A literal ``` inside the text would close the block early.
    return "```\n" + (text or "").replace("```", "`​``") + "\n```"


def render_loading_card(locale: str) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(ACCENT))
    container.add_item(ui.TextDisplay(f"{ROBOT_WORKING} **{t('ocr.loading', locale=locale)}**"))
    view.add_item(container)
    return view


def render_ocr_card(*, text: str, locale: str, language: Optional[str] = None,
                    requester_id: Optional[int] = None, truncated: bool = False) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(ACCENT))
    container.add_item(ui.TextDisplay(f"### {TEXT} {t('ocr.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(_code_block(text)))
    if truncated:
        container.add_item(ui.TextDisplay(f"-# {DOWNLOAD} {t('ocr.truncated', locale=locale)}"))
        container.add_item(ui.File(f"attachment://{OCR_FILE_NAME}"))
    meta: List[str] = []
    language_name = format_language(language, locale)
    if language_name:
        meta.append(f"`{language_name}`")
    if requester_id:
        meta.append(t("ocr.requested_by", locale=locale, user=f"<@{requester_id}>"))
    meta.append(t("ocr.powered_by", locale=locale))
    container.add_item(ui.TextDisplay(f"-# {' • '.join(meta)}"))
    view.add_item(container)
    return view


def build_ocr_message(result, *, locale: str,
                      requester_id: Optional[int] = None) -> Tuple[ui.LayoutView, List[discord.File]]:
    """Card + attachments for a finished OCR (``services.ocr_service.OcrResult``)."""
    view = render_ocr_card(text=result.text, locale=locale, language=result.language,
                           requester_id=requester_id, truncated=result.truncated)
    files: List[discord.File] = []
    if result.truncated:
        files.append(make_text_file(result.full_text or result.text, OCR_FILE_NAME))
    return view, files


def render_error_card(code: str, locale: str, **params) -> ui.LayoutView:
    from utils.components_v2 import create_error_message
    formatted = {k: f"`{v}`" for k, v in params.items()}
    description = i18n.get(f"ocr.errors.{code}", locale=locale, **formatted)
    if description.startswith("["):
        description = t("ocr.errors.failed", locale=locale)
    return create_error_message(t("ocr.error_title", locale=locale), description)
