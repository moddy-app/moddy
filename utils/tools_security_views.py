"""
Views of the security & dev utility tools — see docs/features/TOOLS_SECURITY.md.

Cards are built with ``utils/tool_card.py``; the logic they show lives in
``tools/`` (hashing, generators, jwt_decode, color). Every interactive
component is persistent (docs/guides/PERSISTENT_VIEWS.md): the three
"again" buttons are ``DynamicItem`` s whose ``custom_id`` carries only their
parameters (``type`` + ``length``, ``version`` + ``count``, nothing for the
random colour), so no state is stored anywhere and they survive a restart.
Their callbacks re-derive everything from the ``custom_id`` and the
interaction. ``ToolsSecurityPersistence`` is the marker view that registers
them at start-up.
"""

from __future__ import annotations

import asyncio
import io
import re
from typing import Dict, List, Optional, Sequence, Tuple

import discord
from discord import ui

from cogs.error_handler import BaseView
from tools import color as colors
from tools import generators as gen
from tools import hashing as hs
from tools import jwt_decode as jwtd
from utils import tool_card as tc
from utils.emojis import (
    COLOR, DATA_OBJECT, DONE, ERROR, GPP_MAYBE, HIBP, NUMBERS, PASSWORD, REFRESH,
    TAG, TOKEN, WARNING,
)
from utils.i18n import i18n, t

NO_MENTIONS = tc.NO_MENTIONS

# Pillow work (preview image) never runs on the event loop, and never more than
# two at a time: the container is small and memory is what Railway bills.
_CPU = asyncio.Semaphore(2)

PWNED_SITE_URL = "https://haveibeenpwned.com/Passwords"
COLOR_FILENAME = "color.png"
JWT_FILENAME = "jwt.json"

#: Visible part of each JSON block of /jwt before the full text is attached.
_JWT_HEADER_LIMIT = 700
_JWT_PAYLOAD_LIMIT = 1700
_CLAIM_VALUE_LIMIT = 120


def _view() -> BaseView:
    return BaseView()  # timeout=None; fills with ToolCard.build(view=...)


def _row(button: ui.Button) -> ui.ActionRow:
    row = ui.ActionRow()
    row.add_item(button)
    return row


# --------------------------------------------------------------------------- #
# /pwned
# --------------------------------------------------------------------------- #


def build_pwned_card(count: int, locale: str) -> ui.LayoutView:
    """Verdict of a password check (``count`` = times seen in breaches)."""
    command = tc.inline("/password")
    found = count > 0
    card = tc.ToolCard(
        title=t("tools.pwned.title", locale=locale), icon=GPP_MAYBE,
        accent=tc.ACCENT_ERROR if found else tc.ACCENT_SUCCESS)
    if found:
        card.text(tc.with_icon(ERROR, f"**{t('tools.pwned.found', locale=locale)}**")
                  + "\n" + t("tools.pwned.found_count", locale=locale,
                             count=tc.inline(tc.format_number(count, locale))))
        card.text(t("tools.pwned.found_advice", locale=locale, command=command))
    else:
        card.text(tc.with_icon(DONE, f"**{t('tools.pwned.not_found', locale=locale)}**"))
        card.hint(t("tools.pwned.not_found_note", locale=locale, command=command))
    card.footer(tc.source_line(locale, (HIBP, "Have I Been Pwned"), verb="checked_by"))
    row = tc.link_row(tc.link_button(t("tools.pwned.open_site", locale=locale), PWNED_SITE_URL))
    return card.build(rows=[row] if row else [])


# --------------------------------------------------------------------------- #
# /password
# --------------------------------------------------------------------------- #


def build_password_card(password: str, kind: str, length: int, locale: str) -> ui.LayoutView:
    bits = gen.entropy_bits(kind, length)
    level = gen.strength(bits)
    accent = {"weak": tc.ACCENT_ERROR, "fair": tc.ACCENT_WARNING,
              "good": tc.ACCENT_INFO}.get(level, tc.ACCENT_SUCCESS)
    bar = tc.progress_bar(gen.strength_cells(bits), 10, cells=10)

    kind_label = t(f"tools.password.kinds.{kind}", locale=locale)
    if kind == gen.KIND_PASSPHRASE:
        summary = t("tools.password.summary_words", locale=locale, kind=kind_label,
                    words=gen.word_count_for(length))
    else:
        summary = t("tools.password.summary_chars", locale=locale, kind=kind_label,
                    length=gen.clamp_length(length))

    card = tc.ToolCard(title=t("tools.password.title", locale=locale), icon=PASSWORD, accent=accent)
    card.code(password)
    card.text(t("tools.password.entropy", locale=locale, bar=bar,
                label=t(f"tools.password.strength.{level}", locale=locale),
                bits=tc.inline(t("tools.password.bits", locale=locale, n=int(bits)))))
    card.hint(summary)
    card.hint(t("tools.password.hint", locale=locale))
    card.footer(tc.source_line(locale, verb="computed_locally"))
    return card.build(view=_view(), rows=[_row(PasswordRegenButton(kind, length, locale))])


class PasswordRegenButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:tools:password:regen:(?P<kind>strong|clear|passphrase):(?P<length>\d+)",
):
    """"Regenerate": a fresh password with the same parameters, in place."""

    def __init__(self, kind: str, length: int, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("tools.common.buttons.regenerate", locale=locale)[:80],
            style=discord.ButtonStyle.secondary,
            emoji=tc.button_emoji(REFRESH),
            custom_id=f"moddy:tools:password:regen:{kind}:{int(length)}",
        ))
        self.kind = kind
        self.length = int(length)

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: re.Match):
        return cls(match["kind"], int(match["length"]))

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        kind, length = self.kind, gen.clamp_length(self.length)
        view = build_password_card(gen.generate_password(kind, length), kind, length, locale)
        await interaction.response.edit_message(view=view)


# --------------------------------------------------------------------------- #
# /uuid
# --------------------------------------------------------------------------- #


def build_uuid_card(uuids: Sequence[str], version: str, locale: str) -> ui.LayoutView:
    card = tc.ToolCard(title=t("tools.uuid.title", locale=locale), icon=NUMBERS)
    card.code("\n".join(uuids))
    card.hint(t(f"tools.uuid.version.{version}", locale=locale))
    card.footer(tc.source_line(locale, verb="computed_locally"))
    return card.build(view=_view(), rows=[_row(UuidRegenButton(version, len(uuids), locale))])


class UuidRegenButton(
    ui.DynamicItem[ui.Button],
    template=r"moddy:tools:uuid:regen:(?P<version>v4|v7):(?P<count>\d+)",
):
    """"Regenerate": new UUIDs, same version and count, in place."""

    def __init__(self, version: str, count: int, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("tools.common.buttons.regenerate", locale=locale)[:80],
            style=discord.ButtonStyle.secondary,
            emoji=tc.button_emoji(REFRESH),
            custom_id=f"moddy:tools:uuid:regen:{version}:{int(count)}",
        ))
        self.version = version
        self.count = int(count)

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: re.Match):
        return cls(match["version"], int(match["count"]))

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        uuids = gen.generate_uuids(self.version, self.count)
        await interaction.response.edit_message(view=build_uuid_card(uuids, self.version, locale))


# --------------------------------------------------------------------------- #
# /hash
# --------------------------------------------------------------------------- #


def build_hash_card(digests: Dict[str, str], *, subject: str, is_text: bool, locale: str,
                    match: Optional[str] = None, compared: bool = False,
                    requester_id: Optional[int] = None) -> ui.LayoutView:
    """One labelled code block per algorithm; the comparison verdict on top."""
    accent = tc.ACCENT_INFO
    if compared:
        accent = tc.ACCENT_SUCCESS if match else tc.ACCENT_ERROR
    card = tc.ToolCard(title=t("tools.hash.title", locale=locale), icon=TAG, accent=accent)
    if compared:
        if match:
            card.text(tc.with_icon(DONE, "**" + t("tools.hash.match", locale=locale,
                                                  algo=hs.algorithm_label(match)) + "**"))
        else:
            card.text(tc.with_icon(ERROR, "**" + t("tools.hash.no_match", locale=locale) + "**"))
    card.details([(t("tools.hash.subject", locale=locale), subject)])
    for key, label in hs.ALGORITHMS:
        card.section(label, tc.code_block(digests[key]))
    if is_text:
        card.hint(t("tools.hash.utf8_note", locale=locale))
    card.footer(tc.source_line(locale, verb="computed_locally"),
                tc.requested_by(locale, requester_id))
    return card.build()


# --------------------------------------------------------------------------- #
# /jwt
# --------------------------------------------------------------------------- #

_STATUS_ICON = {jwtd.STATUS_VALID: DONE, jwtd.STATUS_EXPIRED: ERROR, jwtd.STATUS_NOT_YET: WARNING}


def build_jwt_card(decoded: jwtd.DecodedJwt, locale: str) -> Tuple[ui.LayoutView, List[discord.File]]:
    """Header + payload as JSON, humanised claims, and the honest caveat."""
    accent = tc.ACCENT_INFO
    if decoded.status == jwtd.STATUS_EXPIRED:
        accent = tc.ACCENT_ERROR
    elif decoded.status == jwtd.STATUS_NOT_YET:
        accent = tc.ACCENT_WARNING
    if decoded.alg_none:
        accent = tc.ACCENT_WARNING

    card = tc.ToolCard(title=t("tools.jwt.title", locale=locale), icon=TOKEN, accent=accent)

    if decoded.status != jwtd.STATUS_NONE:
        when = decoded.times.get("nbf") if decoded.status == jwtd.STATUS_NOT_YET \
            else decoded.times.get("exp")
        line = tc.with_icon(_STATUS_ICON.get(decoded.status),
                            f"**{t(f'tools.jwt.status.{decoded.status}', locale=locale)}**")
        if when:
            line += " · " + tc.discord_timestamp(when, "R")
        card.text(line)
    if decoded.alg_none:
        card.text(tc.with_icon(WARNING, "**" + t("tools.jwt.alg_none", locale=locale) + "**"))

    header_json = jwtd.pretty_json(decoded.header)
    payload_json = jwtd.pretty_json(decoded.payload)
    header_block, header_file = tc.fit_or_attach(header_json, limit=_JWT_HEADER_LIMIT,
                                                 filename=JWT_FILENAME, lang="json")
    payload_block, payload_file = tc.fit_or_attach(payload_json, limit=_JWT_PAYLOAD_LIMIT,
                                                   filename=JWT_FILENAME, lang="json")
    card.section(t("tools.jwt.header", locale=locale), header_block, icon=DATA_OBJECT)
    card.section(t("tools.jwt.payload", locale=locale), payload_block, icon=DATA_OBJECT)

    files: List[discord.File] = []
    if header_file or payload_file:
        from utils.automod_render import make_text_file
        full = ("// header\n" + header_json + "\n\n// payload\n" + payload_json)
        files.append(make_text_file(full, JWT_FILENAME))
        card.file(JWT_FILENAME)
        card.hint(t("tools.jwt.truncated", locale=locale))

    card.details(_claim_lines(decoded, locale))
    for issue in decoded.issues:
        _, _, claim = issue.partition(":")
        card.hint(t("tools.jwt.bad_time", locale=locale, claim=tc.inline(claim)))

    card.hint(t("tools.jwt.unverified", locale=locale))
    card.footer(tc.source_line(locale, verb="computed_locally"))
    return card.build(), files


def _claim_lines(decoded: jwtd.DecodedJwt, locale: str) -> List[str]:
    lines: List[str] = []
    for name in jwtd.TIME_CLAIMS:
        epoch = decoded.times.get(name)
        if epoch is not None:
            value = f"{tc.discord_timestamp(epoch, 'f')} ({tc.discord_timestamp(epoch, 'R')})"
            lines.append(tc.detail(t(f"tools.jwt.claims.{name}", locale=locale), value, raw=True))
    claims = decoded.payload if isinstance(decoded.payload, dict) else {}
    for name in ("iss", "sub", "aud"):
        value = jwtd.audience(claims.get(name)) if name == "aud" else claims.get(name)
        if value in (None, ""):
            continue
        text, _ = tc.truncate(str(value), _CLAIM_VALUE_LIMIT)
        lines.append(tc.detail(t(f"tools.jwt.claims.{name}", locale=locale), text))
    return lines


# --------------------------------------------------------------------------- #
# /color
# --------------------------------------------------------------------------- #


async def render_color_png(rgb: colors.RGB) -> bytes:
    """The preview image, rendered in a worker thread behind the semaphore."""
    async with _CPU:
        return await asyncio.to_thread(colors.render_preview, rgb)


async def build_color_message(rgb: colors.RGB, locale: str, *, alpha: Optional[float] = None,
                              requester_id: Optional[int] = None
                              ) -> Tuple[ui.LayoutView, List[discord.File]]:
    png = await render_color_png(rgb)
    files = [discord.File(io.BytesIO(png), filename=COLOR_FILENAME)]
    return build_color_card(rgb, locale, alpha=alpha, requester_id=requester_id), files


def _levels(contrast: colors.Contrast, locale: str) -> str:
    mark = lambda ok: DONE if ok else ERROR  # noqa: E731
    return t("tools.color.levels", locale=locale, aa=mark(contrast.aa), aaa=mark(contrast.aaa),
             aa_large=mark(contrast.aa_large), aaa_large=mark(contrast.aaa_large))


def contrast_rows(rgb: colors.RGB) -> List[Tuple[str, colors.Contrast]]:
    """``[(i18n key suffix, contrast)]``: the colour as text, then as a background."""
    return [
        ("dark", colors.wcag(rgb, colors.DISCORD_DARK_BG)),
        ("light", colors.wcag(rgb, colors.DISCORD_LIGHT_BG)),
        ("white", colors.wcag(rgb, colors.WHITE)),
        ("black", colors.wcag(rgb, colors.BLACK)),
    ]


def build_color_card(rgb: colors.RGB, locale: str, *, alpha: Optional[float] = None,
                     requester_id: Optional[int] = None) -> ui.LayoutView:
    card = tc.ToolCard(title=t("tools.color.title", locale=locale), icon=COLOR,
                       accent=colors.rgb_to_int(rgb))
    card.media(f"attachment://{COLOR_FILENAME}")
    card.code("\n".join(f"{label:<8}{value}" for label, value in colors.conversions(rgb)))

    lines = []
    named = colors.closest_name(rgb)
    if named:
        name, exact = named
        key = "exact" if exact else "closest"
        lines.append(tc.detail(t(f"tools.color.{key}", locale=locale), name))
    if alpha is not None and alpha < 1:
        lines.append(f"-# {t('tools.color.alpha_note', locale=locale, alpha=f'{alpha:.2f}')}")
    card.details(lines)

    rows = []
    for key, contrast in contrast_rows(rgb):
        rows.append(tc.detail(t(f"tools.color.contrast_{key}", locale=locale),
                              colors.format_ratio(contrast.ratio))
                    + f"\n-# {_levels(contrast, locale)}")
    card.section(t("tools.color.contrast_title", locale=locale), "\n".join(rows))
    card.footer(tc.source_line(locale, verb="computed_locally"),
                tc.requested_by(locale, requester_id))
    return card.build(view=_view(), rows=[_row(ColorRandomButton(locale))])


class ColorRandomButton(ui.DynamicItem[ui.Button], template=r"moddy:tools:color:random"):
    """"Random colour": a new card. Edits an ephemeral card in place; on a
    public card (someone else's message) it answers privately instead."""

    def __init__(self, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("tools.color.random_button", locale=locale)[:80],
            style=discord.ButtonStyle.secondary,
            emoji=tc.button_emoji(REFRESH),
            custom_id="moddy:tools:color:random",
        ))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: re.Match):
        return cls()

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        rgb = colors.int_to_rgb(colors.random_color())
        view, files = await build_color_message(rgb, locale)
        message = interaction.message
        if message is not None and message.flags.ephemeral:
            await interaction.response.edit_message(view=view, attachments=files)
        else:
            await interaction.response.send_message(view=view, files=files, ephemeral=True,
                                                    allowed_mentions=NO_MENTIONS)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


class ToolsSecurityPersistence(BaseView):
    """Marker view: registers the three dynamic buttons at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(PasswordRegenButton, UuidRegenButton, ColorRandomButton)


PERSISTENT_VIEWS: list = [ToolsSecurityPersistence]
DYNAMIC_ITEM_CASES: list = [
    (PasswordRegenButton, ("strong", 20)),
    (UuidRegenButton, ("v7", 10)),
    (ColorRandomButton, ()),
]
