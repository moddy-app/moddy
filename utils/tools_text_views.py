"""
Views of the text utility tools — see docs/features/TOOLS_TEXT.md.

Cards are built with ``utils/tool_card.py``; the logic they show lives in
``tools/`` (wiktionary, regex_test, diffing, textstats, text_escape).

Persistence (docs/guides/PERSISTENT_VIEWS.md): every interactive component is a
``DynamicItem`` whose ``custom_id`` carries what it needs.

* ``/define`` "other senses" select: the word and the Wiktionary edition sit in
  the ``custom_id``; the entry itself is re-read from the shared cache.
* ``/regex`` and ``/diff`` "Edit" buttons: the pattern + test text (or the two
  texts) do not fit in a ``custom_id``, so a 10-char token points to a payload
  stored with ``save_state`` (24 h). An expired token answers ``expired_card``.

``ToolsTextPersistence`` is the marker view that registers them at start-up.
The Modals V2 of /regex, /diff and /textstats live here too, so the "Edit"
buttons can reopen them without importing the cog.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import discord
from discord import ui

from cogs.error_handler import BaseModal, BaseView
from tools import diffing
from tools import regex_test as rx
from tools import text_escape as esc
from tools import textstats as ts
from tools import wiktionary as wk
from utils import tool_card as tc
from utils.emojis import ANALYTICS, DICTIONARY, DIFFERENCE, EDIT, REGEX, TEXT, WIKTIONARY, OPENAI
from utils.i18n import i18n, t

NO_MENTIONS = tc.NO_MENTIONS

# CPU work (regex scan, difflib, syllable counting) never runs on the event
# loop and never more than two at a time.
_CPU = asyncio.Semaphore(2)

#: Visible budgets (a whole message holds 4000 characters of text).
REGEX_TEXT_LIMIT = 1500
DIFF_LIMIT = 2800
DEFINITIONS_SHOWN = 3
STATE_REGEX = "regex"
STATE_DIFF = "diff"


def _view() -> BaseView:
    return BaseView()


def _row(*items: ui.Item) -> ui.ActionRow:
    row = ui.ActionRow()
    for item in items:
        row.add_item(item)
    return row


async def _in_thread(func, *args):
    async with _CPU:
        return await asyncio.to_thread(func, *args)


def is_ephemeral(interaction: discord.Interaction) -> bool:
    message = interaction.message
    return bool(message is not None and message.flags.ephemeral)


def owns_message(interaction: discord.Interaction) -> bool:
    """True when the clicker is the member who ran the command (or the card is private)."""
    message = interaction.message
    if message is None or message.flags.ephemeral:
        return True
    meta = getattr(message, "interaction_metadata", None)
    owner = getattr(getattr(meta, "user", None), "id", None)
    return owner is None or owner == interaction.user.id


async def deliver(interaction: discord.Interaction, view: ui.LayoutView, *,
                  files: Sequence[discord.File] = (), ephemeral: bool = True,
                  edit_in_place: bool = False) -> None:
    """Edit the card the click came from, or send a new message."""
    if edit_in_place and not interaction.response.is_done():
        await interaction.response.edit_message(view=view, attachments=list(files),
                                                allowed_mentions=NO_MENTIONS)
        return
    await tc.respond(interaction, view=view, ephemeral=ephemeral, files=files)


# --------------------------------------------------------------------------- #
# /define
# --------------------------------------------------------------------------- #


def wiki_language(locale: str) -> str:
    """The Wiktionary edition matching a Moddy locale (English when unsupported)."""
    base = (locale or "en").split("-")[0].lower()
    return base if base in wk.WIKI_LANGUAGES else "en"


def pos_label(meaning: wk.Meaning, locale: str) -> str:
    if meaning.pos != "other":
        text = i18n.get(f"tools.define.pos.{meaning.pos}", locale=locale)
        if not text.startswith("["):
            return text
    label = (meaning.label or "").strip()
    return label[:1].upper() + label[1:] if label else t("tools.define.pos.other", locale=locale)


def sense_labels(entry: wk.Entry, locale: str) -> List[str]:
    labels = [pos_label(m, locale) for m in entry.meanings]
    return wk.dedupe_labels(entry.meanings, labels)


def build_define_card(entry: wk.Entry, index: int, locale: str, *, word: str,
                      requester_id: Optional[int] = None) -> ui.LayoutView:
    """One sense of a Wiktionary entry, with a select for the other senses."""
    index = index if 0 <= index < len(entry.meanings) else 0
    meaning = entry.meanings[index]
    labels = sense_labels(entry, locale)

    card = tc.ToolCard(title=t("tools.define.title", locale=locale), icon=DICTIONARY)
    card.text(f"**{esc.escape_text(entry.word, at_line_start=False)}** · *{esc.escape_text(labels[index], at_line_start=False)}*")
    shown = meaning.definitions[:DEFINITIONS_SHOWN]
    if shown:
        card.text("\n".join(f"{n}. {wk.render_definition(d)}" for n, d in enumerate(shown, 1)))
    extra = len(meaning.definitions) - len(shown)
    if meaning.synonyms:
        names = ", ".join(esc.escape_text(s, at_line_start=False) for s in meaning.synonyms[:wk.MAX_SYNONYMS])
        card.text(f"**{t('tools.define.synonyms', locale=locale)}** · {names}")
    if extra > 0:
        card.hint(t("tools.define.more", locale=locale, count=tc.inline(extra)))
    if entry.source == "rest" and entry.lang != "en":
        card.hint(t("tools.define.fallback_note", locale=locale,
                    lang=t(f"tools.define.lang.{entry.lang}", locale=locale)))

    wiki = entry.lang if entry.source == "parse" else "en"
    card.footer(tc.source_line(locale, (WIKTIONARY, "Wiktionary"), verb="provided_by"),
                t("tools.define.licence", locale=locale, wiki=wiki.upper()),
                tc.requested_by(locale, requester_id))

    rows: List[ui.Item] = []
    if len(entry.meanings) > 1:
        rows.append(_row(DefineSenseSelect(entry.lang, word, labels=labels, meanings=entry.meanings,
                                           current=index, locale=locale)))
    link = tc.link_button(t("tools.define.view_on", locale=locale), entry.url, icon=WIKTIONARY)
    rows.append(_row(link))
    return card.build(view=_view(), rows=rows)


class DefineSenseSelect(
    ui.DynamicItem[ui.Select],
    template=r"moddy:tools:define:sense:(?P<lang>fr|en|es|pt|de):(?P<word>.{1,50})",
):
    """"Other senses": switches the card to another part of speech, in place."""

    def __init__(self, lang: str, word: str, *, labels: Sequence[str] = (),
                 meanings: Sequence[wk.Meaning] = (), current: int = 0, locale: str = "en-US"):
        options = []
        for i, label in enumerate(list(labels)[:25]):
            first = meanings[i].definitions[0] if i < len(meanings) and meanings[i].definitions else ""
            description = tc.truncate(wk.split_labels(first)[1] or first, 100)[0] or None
            options.append(discord.SelectOption(label=label[:100], value=str(i),
                                                description=description, default=(i == current)))
        super().__init__(ui.Select(
            placeholder=t("tools.define.senses_placeholder", locale=locale)[:150],
            options=options or [discord.SelectOption(label="…", value="0")],
            min_values=1, max_values=1,
            custom_id=f"moddy:tools:define:sense:{lang}:{word}"[:100],
        ))
        self.lang = lang
        self.word = word

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Select,
                             match: re.Match):
        return cls(match["lang"], match["word"])

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        values = (interaction.data or {}).get("values") or ["0"]
        try:
            index = int(values[0])
        except ValueError:
            index = 0
        api = getattr(interaction.client, "public_api", None)
        if api is None:
            raise tc.ToolError("unavailable", tool="define")
        try:
            entry = await wk.get_entry(api, self.word, self.lang)
        except Exception:  # noqa: BLE001 — a cache/network miss must not look like a bug
            raise tc.ToolError("unavailable", tool="define") from None
        if entry is None:
            raise tc.ToolError("not_found", tool="define", word=self.word)
        public = not is_ephemeral(interaction)
        requester = interaction.user.id if public and owns_message(interaction) else None
        view = build_define_card(entry, index, locale, word=self.word, requester_id=requester)
        if owns_message(interaction):
            await interaction.response.edit_message(view=view, allowed_mentions=NO_MENTIONS)
        else:
            await interaction.response.send_message(view=view, ephemeral=True,
                                                    allowed_mentions=NO_MENTIONS)


# --------------------------------------------------------------------------- #
# /tldr
# --------------------------------------------------------------------------- #


def build_tldr_card(summary: str, locale: str, *, channel_id: int, count: int,
                    requester_id: Optional[int] = None) -> ui.LayoutView:
    card = tc.ToolCard(title=t("tools.tldr.title", locale=locale), icon=TEXT)
    card.text(tc.truncate(summary, 3000)[0])
    card.details([tc.detail(t("tools.tldr.scope", locale=locale),
                            t("tools.tldr.scope_value", locale=locale, count=tc.inline(count),
                              channel=f"<#{channel_id}>"), raw=True)])
    card.hint(t("tools.tldr.ai_note", locale=locale))
    card.footer(tc.source_line(locale, (OPENAI, "OpenAI"), verb="summarized_by"),
                tc.requested_by(locale, requester_id))
    return card.build()


# --------------------------------------------------------------------------- #
# /regex
# --------------------------------------------------------------------------- #

_FLAG_KEYS = rx.FLAG_ORDER


def _flag_label(key: str, locale: str) -> str:
    return t(f"tools.regex.flags.{key}", locale=locale)


def regex_state(pattern: str, text: str, flags: Sequence[str]) -> Dict[str, Any]:
    return {"p": pattern, "t": text, "f": list(rx.normalize_flags(flags))}


async def compute_regex(pattern: str, text: str, flags: Sequence[str]) -> rx.RegexResult:
    """Run the matcher off the loop; map engine failures to ``ToolError``."""
    if not pattern:
        raise tc.ToolError("empty_pattern", tool="regex")
    if not text:
        raise tc.ToolError("empty_text", tool="regex")
    try:
        return await _in_thread(rx.run, pattern, text, tuple(flags))
    except rx.RegexCompileError as exc:
        if exc.message == "pattern too long":
            raise tc.ToolError("pattern_too_long", tool="regex", max=rx.MAX_PATTERN_LENGTH) from None
        if exc.message == "pattern too complex":
            raise tc.ToolError("too_complex", tool="regex") from None
        context = rx.error_context(pattern, exc.position)
        detail = exc.message + (f" ({context})" if context else "")
        raise tc.ToolError("invalid_pattern", tool="regex", detail=detail) from None
    except rx.RegexTimeout:
        raise tc.ToolError("timeout", tool="regex") from None


def build_regex_card(pattern: str, text: str, flags: Sequence[str], result: rx.RegexResult,
                     locale: str, *, token: str,
                     requester_id: Optional[int] = None) -> Tuple[ui.LayoutView, List[discord.File]]:
    flags = rx.normalize_flags(flags)
    files: List[discord.File] = []
    found = result.total > 0
    card = tc.ToolCard(title=t("tools.regex.title", locale=locale), icon=REGEX,
                       accent=tc.ACCENT_SUCCESS if found else tc.ACCENT_WARNING)
    if found:
        if result.capped:
            summary = t("tools.regex.count_capped", locale=locale, count=tc.inline(result.total))
        else:
            key = "one" if result.total == 1 else "other"
            summary = t(f"tools.regex.count_{key}", locale=locale, count=tc.inline(result.total))
        card.text(f"**{summary}**")
        segments = rx.highlight_segments(text, result.spans)
        highlighted, clipped = esc.clip_segments(segments, REGEX_TEXT_LIMIT, {"match": ("**__", "__**")})
        card.text(highlighted)
        headers = (t("tools.regex.col_index", locale=locale), t("tools.regex.col_position", locale=locale),
                   t("tools.regex.col_match", locale=locale), t("tools.regex.col_groups", locale=locale))
        table = rx.match_table(result.matches, headers)
        if table:
            card.code(table)
        if clipped or result.total > 10 or result.capped:
            from utils.automod_render import make_text_file
            files.append(make_text_file(rx.export_text(pattern, flags, text, result), "regex.txt"))
            card.file("regex.txt")
            card.hint(t("tools.regex.truncated", locale=locale))
    else:
        card.text(f"**{t('tools.regex.no_match', locale=locale)}**")
        card.hint(t("tools.regex.no_match_hint", locale=locale))

    shown_pattern, _ = tc.truncate(pattern, 120)
    lines = [tc.detail(t("tools.regex.pattern", locale=locale), shown_pattern)]
    if flags:
        lines.append(tc.detail(t("tools.regex.flags_label", locale=locale), " ".join(flags)))
    card.details(lines)
    card.footer(tc.source_line(locale, verb="computed_locally"), tc.requested_by(locale, requester_id))
    return card.build(view=_view(), rows=[_row(RegexEditButton(token, locale))]), files


class RegexModal(BaseModal):
    """Pattern, test text and flags. Also reopened, pre-filled, by the Edit button."""

    def __init__(self, locale: str, *, ephemeral: bool, edit_in_place: bool = False,
                 prefill: Optional[Dict[str, Any]] = None):
        super().__init__(title=t("tools.regex.modal_title", locale=locale)[:45])
        self.ephemeral = ephemeral
        self.edit_in_place = edit_in_place
        prefill = prefill or {}
        self.add_item(ui.TextDisplay(t("tools.regex.modal_help", locale=locale)))
        self.pattern = ui.Label(
            text=t("tools.regex.pattern_label", locale=locale)[:45],
            component=ui.TextInput(
                style=discord.TextStyle.short, required=True, min_length=1,
                max_length=rx.MAX_PATTERN_LENGTH, default=(prefill.get("p") or None),
                placeholder=t("tools.regex.pattern_placeholder", locale=locale)[:100]))
        self.text = ui.Label(
            text=t("tools.regex.text_label", locale=locale)[:45],
            component=ui.TextInput(
                style=discord.TextStyle.paragraph, required=True, min_length=1,
                max_length=rx.MAX_TEXT_LENGTH, default=(prefill.get("t") or None),
                placeholder=t("tools.regex.text_placeholder", locale=locale)[:100]))
        chosen = set(prefill.get("f") or ())
        self.flags = ui.Label(
            text=t("tools.regex.flags_label", locale=locale)[:45],
            component=ui.CheckboxGroup(
                required=False, min_values=0, max_values=len(_FLAG_KEYS),
                options=[discord.CheckboxGroupOption(label=_flag_label(k, locale)[:100], value=k,
                                                     default=(k in chosen)) for k in _FLAG_KEYS]))
        for item in (self.pattern, self.text, self.flags):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await submit_regex(
            interaction, self.pattern.component.value or "", self.text.component.value or "",
            list(self.flags.component.values or ()), ephemeral=self.ephemeral,
            edit_in_place=self.edit_in_place)


async def submit_regex(interaction: discord.Interaction, pattern: str, text: str,
                       flags: Sequence[str], *, ephemeral: bool, edit_in_place: bool) -> None:
    locale = i18n.get_user_locale(interaction)
    bot = interaction.client
    state = regex_state(pattern, text, flags)
    token = await tc.save_state(bot, STATE_REGEX, state)
    try:
        result = await compute_regex(pattern, text, state["f"])
    except tc.ToolError as exc:
        card = tc.tool_error_card(exc, locale, buttons=[RegexEditButton(token, locale)])
        await deliver(interaction, card, ephemeral=True, edit_in_place=edit_in_place)
        return
    view, files = build_regex_card(pattern, text, state["f"], result, locale, token=token,
                                   requester_id=None if ephemeral else interaction.user.id)
    await deliver(interaction, view, files=files, ephemeral=ephemeral, edit_in_place=edit_in_place)


class RegexEditButton(ui.DynamicItem[ui.Button], template=r"moddy:tools:regex:edit:(?P<token>[A-Za-z0-9]{10})"):
    """"Edit": reopens the modal with the stored pattern, text and flags."""

    def __init__(self, token: str, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("tools.common.buttons.edit", locale=locale)[:80],
            style=discord.ButtonStyle.secondary, emoji=tc.button_emoji(EDIT),
            custom_id=f"moddy:tools:regex:edit:{token}"))
        self.token = token

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: re.Match):
        return cls(match["token"])

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        state = await tc.load_state(interaction.client, STATE_REGEX, self.token)
        if not isinstance(state, dict):
            await tc.respond(interaction, view=tc.expired_card(locale), ephemeral=True)
            return
        await interaction.response.send_modal(RegexModal(
            locale, ephemeral=True, edit_in_place=is_ephemeral(interaction), prefill=state))


# --------------------------------------------------------------------------- #
# /diff
# --------------------------------------------------------------------------- #

MODE_LINES = "lines"
MODE_WORDS = "words"


def diff_state(before: str, after: str, mode: str) -> Dict[str, Any]:
    return {"b": before, "a": after, "m": mode if mode in (MODE_LINES, MODE_WORDS) else MODE_LINES}


async def compute_diff(before: str, after: str, mode: str):
    if not before and not after:
        raise tc.ToolError("empty_input", tool="diff")
    if mode == MODE_WORDS:
        return await _in_thread(diffing.diff_words, before, after)
    return await _in_thread(diffing.diff_lines, before, after)


def build_diff_card(result, mode: str, locale: str, *, token: str,
                    requester_id: Optional[int] = None) -> Tuple[ui.LayoutView, List[discord.File]]:
    from utils.automod_render import make_text_file

    files: List[discord.File] = []
    identical = result.identical
    card = tc.ToolCard(title=t("tools.diff.title", locale=locale), icon=DIFFERENCE,
                       accent=tc.ACCENT_NEUTRAL if identical else tc.ACCENT_INFO)
    if identical:
        card.text(f"**{t('tools.diff.identical', locale=locale)}**")
    elif mode == MODE_WORDS:
        marks = {"removed": ("~~", "~~"), "added": ("**", "**")}
        body, clipped = esc.clip_segments(result.segments, DIFF_LIMIT, marks)
        card.text(body)
        if clipped:
            files.append(make_text_file(result.full, "diff.txt"))
            card.file("diff.txt")
            card.hint(t("tools.diff.truncated", locale=locale))
        card.hint(t("tools.diff.legend_words", locale=locale))
    else:
        block, file = tc.fit_or_attach(result.text, limit=DIFF_LIMIT, filename="diff.diff", lang="diff")
        card.text(block)
        if file is not None:
            files.append(make_text_file(result.full, "diff.diff"))
            card.file("diff.diff")
            card.hint(t("tools.diff.truncated", locale=locale))
    card.details([
        tc.detail(t("tools.diff.changes", locale=locale), diffing.stats_line(result.added, result.removed)),
        tc.detail(t("tools.diff.mode_label", locale=locale), t(f"tools.diff.mode.{mode}", locale=locale)),
    ])
    card.footer(tc.source_line(locale, verb="computed_locally"), tc.requested_by(locale, requester_id))
    return card.build(view=_view(), rows=[_row(DiffEditButton(token, locale))]), files


class DiffModal(BaseModal):
    def __init__(self, locale: str, *, ephemeral: bool, edit_in_place: bool = False,
                 prefill: Optional[Dict[str, Any]] = None):
        super().__init__(title=t("tools.diff.modal_title", locale=locale)[:45])
        self.ephemeral = ephemeral
        self.edit_in_place = edit_in_place
        prefill = prefill or {}
        self.add_item(ui.TextDisplay(t("tools.diff.modal_help", locale=locale)))
        self.before = ui.Label(
            text=t("tools.diff.before_label", locale=locale)[:45],
            component=ui.TextInput(style=discord.TextStyle.paragraph, required=False,
                                   max_length=diffing.MAX_TEXT_LENGTH, default=(prefill.get("b") or None)))
        self.after = ui.Label(
            text=t("tools.diff.after_label", locale=locale)[:45],
            component=ui.TextInput(style=discord.TextStyle.paragraph, required=False,
                                   max_length=diffing.MAX_TEXT_LENGTH, default=(prefill.get("a") or None)))
        current = prefill.get("m") or MODE_LINES
        self.mode = ui.Label(
            text=t("tools.diff.mode_label", locale=locale)[:45],
            component=ui.RadioGroup(required=True, options=[
                discord.RadioGroupOption(label=t(f"tools.diff.mode.{key}", locale=locale)[:100], value=key,
                                         description=t(f"tools.diff.mode_hint.{key}", locale=locale)[:100],
                                         default=(key == current))
                for key in (MODE_LINES, MODE_WORDS)]))
        for item in (self.before, self.after, self.mode):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await submit_diff(
            interaction, self.before.component.value or "", self.after.component.value or "",
            self.mode.component.value or MODE_LINES, ephemeral=self.ephemeral,
            edit_in_place=self.edit_in_place)


async def submit_diff(interaction: discord.Interaction, before: str, after: str, mode: str, *,
                      ephemeral: bool, edit_in_place: bool) -> None:
    locale = i18n.get_user_locale(interaction)
    state = diff_state(before, after, mode)
    try:
        result = await compute_diff(before, after, state["m"])
    except tc.ToolError as exc:
        await deliver(interaction, tc.tool_error_card(exc, locale), ephemeral=True,
                      edit_in_place=False)
        return
    token = await tc.save_state(interaction.client, STATE_DIFF, state)
    view, files = build_diff_card(result, state["m"], locale, token=token,
                                  requester_id=None if ephemeral else interaction.user.id)
    await deliver(interaction, view, files=files, ephemeral=ephemeral, edit_in_place=edit_in_place)


class DiffEditButton(ui.DynamicItem[ui.Button], template=r"moddy:tools:diff:edit:(?P<token>[A-Za-z0-9]{10})"):
    """"Edit": reopens the modal with the two stored texts and the mode."""

    def __init__(self, token: str, locale: str = "en-US"):
        super().__init__(ui.Button(
            label=t("tools.common.buttons.edit", locale=locale)[:80],
            style=discord.ButtonStyle.secondary, emoji=tc.button_emoji(EDIT),
            custom_id=f"moddy:tools:diff:edit:{token}"))
        self.token = token

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button,
                             match: re.Match):
        return cls(match["token"])

    @tc.guarded
    async def callback(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        state = await tc.load_state(interaction.client, STATE_DIFF, self.token)
        if not isinstance(state, dict):
            await tc.respond(interaction, view=tc.expired_card(locale), ephemeral=True)
            return
        await interaction.response.send_modal(DiffModal(
            locale, ephemeral=True, edit_in_place=is_ephemeral(interaction), prefill=state))


# --------------------------------------------------------------------------- #
# /textstats
# --------------------------------------------------------------------------- #

MAX_TEXTSTATS_INPUT = 6000


def build_textstats_card(stats: ts.TextStats, locale: str, *,
                         requester_id: Optional[int] = None) -> ui.LayoutView:
    n = lambda value: tc.format_number(value, locale)  # noqa: E731
    card = tc.ToolCard(title=t("tools.textstats.title", locale=locale), icon=ANALYTICS)
    card.text(f"**{t('tools.textstats.words_big', locale=locale, count=tc.inline(n(stats.words)))}**")
    lines = [
        tc.detail(t("tools.textstats.characters", locale=locale),
                  t("tools.textstats.characters_value", locale=locale, total=tc.inline(n(stats.characters)),
                    no_spaces=tc.inline(n(stats.characters_no_spaces))), raw=True),
        (t("tools.textstats.sentences", locale=locale), n(stats.sentences)),
        (t("tools.textstats.paragraphs", locale=locale), n(stats.paragraphs)),
    ]
    if stats.sentences:
        lines.append((t("tools.textstats.avg", locale=locale), n(stats.avg_words_per_sentence)))
    lines.append((t("tools.textstats.reading", locale=locale), ts.format_duration(stats.reading_seconds)))
    lines.append((t("tools.textstats.speaking", locale=locale), ts.format_duration(stats.speaking_seconds)))
    if stats.language:
        lines.append((t("tools.textstats.language", locale=locale),
                      t(f"tools.textstats.languages.{stats.language}", locale=locale)))
    card.details(lines)

    if stats.readability:
        score = stats.readability
        card.section(
            t("tools.textstats.readability", locale=locale),
            f"{ts.score_bar(score.score)} {tc.inline(f'{score.score:g}/100')} "
            f"**{t(f'tools.textstats.levels.{score.level}', locale=locale)}**\n"
            f"-# {t('tools.textstats.formula', locale=locale, name=t(f'tools.textstats.formulas.{score.formula}', locale=locale))}")
    elif not stats.enough_words:
        card.hint(t("tools.textstats.not_enough", locale=locale, min=tc.inline(ts.MIN_WORDS_READABILITY)))
    elif not stats.language:
        card.hint(t("tools.textstats.no_language", locale=locale))

    if stats.top_words:
        top = " · ".join(f"{tc.inline(word)} ×{count}" for word, count in stats.top_words)
        card.text(f"**{t('tools.textstats.top_words', locale=locale)}** · {top}")
    card.footer(tc.source_line(locale, verb="computed_locally"), tc.requested_by(locale, requester_id))
    return card.build()


async def compute_textstats(text: str) -> ts.TextStats:
    if not (text or "").strip():
        raise tc.ToolError("empty_input", tool="textstats")
    return await _in_thread(ts.analyze, text)


class TextstatsModal(BaseModal):
    """Opened by /textstats without a text."""

    def __init__(self, locale: str, *, ephemeral: bool):
        super().__init__(title=t("tools.textstats.modal_title", locale=locale)[:45])
        self.ephemeral = ephemeral
        self.add_item(ui.TextDisplay(t("tools.textstats.modal_help", locale=locale)))
        self.text = ui.Label(
            text=t("tools.textstats.text_label", locale=locale)[:45],
            component=ui.TextInput(style=discord.TextStyle.paragraph, required=True, min_length=1,
                                   max_length=4000,
                                   placeholder=t("tools.textstats.text_placeholder", locale=locale)[:100]))
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        locale = i18n.get_user_locale(interaction)
        try:
            stats = await compute_textstats(self.text.component.value or "")
        except tc.ToolError as exc:
            await tc.respond(interaction, view=tc.tool_error_card(exc, locale), ephemeral=True)
            return
        await tc.respond(interaction, view=build_textstats_card(
            stats, locale, requester_id=None if self.ephemeral else interaction.user.id),
            ephemeral=self.ephemeral)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


class ToolsTextPersistence(BaseView):
    """Marker view: registers the dynamic items at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(DefineSenseSelect, RegexEditButton, DiffEditButton)


PERSISTENT_VIEWS: list = [ToolsTextPersistence]
DYNAMIC_ITEM_CASES: list = [
    (DefineSenseSelect, ("fr", "chat")),
    (RegexEditButton, ("abcDEF1234",)),
    (DiffEditButton, ("abcDEF1234",)),
]
