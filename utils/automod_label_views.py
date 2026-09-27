"""
Automod labeling queue — the Moddy team card and its controls (docs/AUTOMOD_AI.md §9).

The card sits in ``config.MODDY_AUTOMOD_LABEL_CHANNEL_ID`` (Moddy team guild) and
is always rendered in English, like the other team panels. It shows what the
bot saw and did, the image (always spoilered — NSFW above all), and three
labels:

* **Sanctionable** — teaches the bot (hash ``block`` / embedding reference);
  never applies anything.
* **Not sanctionable** — teaches the bot the other way (hash ``allow`` /
  server precedent) and, if the bot sanctioned, **revokes** that sanction.
* **Skip** — no effect.

A category select corrects the category before labeling, and "Blocklist
terms" opens a Modal V2 to add terms to the (learned) blocklist.

Persistence: every control is a :class:`discord.ui.DynamicItem` whose
``custom_id`` carries the item uuid, registered through
:class:`LabelPersistence`; the card is rebuilt from the DB row on each click.
The Modal is one-shot (documented exclusion in docs/PERSISTENT_VIEWS.md).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.error_handler import BaseModal, BaseView
from utils.components_v2 import create_error_message, create_success_message
from utils.emojis import DONE, ERROR, IMAGE, SHIELD, TEXT, WARNING
from utils.i18n import t

logger = logging.getLogger("moddy.automod_label_views")

_UUID = r"[0-9a-fA-F-]{36}"
PANEL_LOCALE = "en-US"
STAFF_NODE = "automod_label"

_VERDICT_BY_CODE = {"yes": "sanctionnable", "no": "non_sanctionnable", "skip": "ignore"}
_ACCENT = {
    None: 0x3661FF,
    "sanctionnable": 0xED4245,
    "non_sanctionnable": 0x57F287,
    "ignore": 0x99AAB5,
}
_KIND_EMOJI = {"texte": TEXT, "image_scam": IMAGE, "image_nsfw": IMAGE}

#: Categories the team can pick for a text / scam item (NSFW is image-only).
TEXT_CATEGORIES = (
    "insulte", "menace", "harcelement", "harcelement_sexuel",
    "haine_discrimination", "incitation_automutilation", "doxxing",
    "arnaque_scam", "violation_indications",
)


def _l(key: str, **kw) -> str:
    return t(f"automod_label.{key}", locale=PANEL_LOCALE, **kw)


def _guarded(callback):
    """Route errors of a dynamic item to the central handler (no live BaseView)."""
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as exc:  # noqa: BLE001
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, exc, self.__class__.__name__)
    return wrapper


def _short(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# =========================================================================== #
# Card
# =========================================================================== #

def render_label_card(row: Dict[str, Any], *, image_filename: Optional[str] = None) -> ui.LayoutView:
    details = row.get("details") or {}
    verdict = row.get("verdict")
    kind = row.get("kind") or "texte"
    item_id = row["id"]

    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(_ACCENT.get(verdict, 0x3661FF)))
    container.add_item(ui.TextDisplay(
        f"### {SHIELD} {_l('title')} — {_KIND_EMOJI.get(kind, TEXT)} {_l(f'kind.{kind}')}"))

    motif = row.get("motif") or "sanction"
    why = _l(f"motif.{motif}")
    if details.get("doute"):
        why += f" · `{details['doute']}`"
    applied = details.get("applied") or []
    applied_txt = ", ".join(f"`{a}`" for a in applied) if applied else _l("nothing_applied")
    lines = [
        f"- **{_l('server')}:** `{_short(details.get('guild_name') or '', 60)}` (`{row.get('guild_id')}`)",
        f"- **{_l('author')}:** <@{row.get('author_id')}> (`{row.get('author_id')}`)",
        f"- **{_l('channel')}:** <#{row.get('channel_id')}>"
        + (f" · [{_l('jump')}]({details['jump_url']})" if details.get("jump_url") else ""),
        f"- **{_l('why')}:** {why}",
        f"- **{_l('decision')}:** `{details.get('categorie') or '—'}` · "
        f"`{details.get('gravite') or '—'}` · {_l('confidence')} `{details.get('confiance') or '—'}` · "
        f"`{details.get('decideur') or '—'}` (`{details.get('signal_source') or '—'}` "
        f"`{details.get('score', 0)}`)",
        f"- **{_l('applied')}:** {applied_txt}",
    ]
    if details.get("cran") is not None:
        lines.append(f"- **{_l('cran')}:** `{details['cran']}`")
    if int(row.get("occurrences") or 1) > 1:
        lines.append(f"- **{_l('seen')}:** `{row['occurrences']}`")
    image = details.get("image") or {}
    if image.get("safesearch"):
        ss = image["safesearch"]
        lines.append(f"- **SafeSearch:** adult `{ss.get('adult')}` · racy `{ss.get('racy')}` "
                     f"· violence `{ss.get('violence')}`")
    if image.get("ancres"):
        lines.append(f"- **{_l('anchors')}:** " + ", ".join(f"`{a}`" for a in image["ancres"][:8]))
    if image.get("hash_match"):
        hm = image["hash_match"]
        lines.append(f"- **{_l('hash_match')}:** #`{hm.get('id')}` ({_l('distance')} `{hm.get('distance')}`)")
    if image.get("cross_post", 0) > 1:
        lines.append(f"- **{_l('cross_post')}:** `{image['cross_post']}`")
    if details.get("raison"):
        lines.append(f"- **{_l('reason')}:** {_short(details['raison'], 300)}")
    container.add_item(ui.TextDisplay("\n".join(lines)))
    view.add_item(container)

    # What the bot judged — spoilered (it is the offending content).
    content = (row.get("contenu") or "").strip()
    if content or image_filename:
        quote = ui.Container(accent_colour=discord.Colour(_ACCENT.get(verdict, 0x3661FF)),
                             spoiler=True)
        if content:
            label = _l("ocr_text") if kind == "image_scam" else _l("message")
            quote.add_item(ui.TextDisplay(f"**{label}**\n>>> {_short(content, 1500)}"))
        if image_filename:
            quote.add_item(ui.MediaGallery(discord.MediaGalleryItem(
                f"attachment://{image_filename}", spoiler=True)))
        view.add_item(quote)

    footer = ui.Container(accent_colour=discord.Colour(_ACCENT.get(verdict, 0x3661FF)))
    if verdict:
        labeler = f"<@{row.get('labeled_by')}>"
        verdict_name = _l(f"verdict.{verdict}")
        outcome = f"{DONE} " + _l("labeled", verdict=verdict_name, user=labeler)
        if row.get("revoked"):
            outcome += f"\n{WARNING} {_l('revoked')}"
        if row.get("categorie_humaine"):
            outcome += f"\n-# {_l('category')}: `{row['categorie_humaine']}`"
        footer.add_item(ui.TextDisplay(outcome))
    else:
        footer.add_item(ui.TextDisplay(f"-# {_l('hint')}"))
        if kind != "image_nsfw":
            current = row.get("categorie_humaine") or details.get("categorie") or ""
            footer.add_item(ui.ActionRow(LabelCategorySelect(item_id, current=current)))
        buttons = ui.ActionRow(
            LabelVerdictButton("yes", item_id),
            LabelVerdictButton("no", item_id),
            LabelVerdictButton("skip", item_id),
        )
        if kind != "image_nsfw":
            buttons.add_item(LabelTermsButton(item_id))
        footer.add_item(buttons)
    view.add_item(footer)
    return view


def render_revocation_notice(locale: str, *, author_id: int, case_ref: str) -> ui.LayoutView:
    """Posted in the server's automod alert channel when the team revokes."""
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(0x57F287))
    container.add_item(ui.TextDisplay(
        f"### {DONE} {t('modules.automod_ai.label_revoked.title', locale=locale)}"))
    container.add_item(ui.TextDisplay(t(
        "modules.automod_ai.label_revoked.body", locale=locale,
        user=f"<@{author_id}>", user_id=f"`{author_id}`", case_ref=f"`{case_ref}`")))
    view.add_item(container)
    return view


# =========================================================================== #
# Controls (persistent)
# =========================================================================== #

async def _guard_staff(interaction: discord.Interaction) -> bool:
    from utils.staff_permissions import has_staff_node
    if await has_staff_node(interaction.client, interaction.user.id, STAFF_NODE):
        return True
    await interaction.response.send_message(view=create_error_message(
        _l("errors.title"), _l("errors.no_permission")), ephemeral=True)
    return False


async def _load(interaction: discord.Interaction, item_id: str) -> Optional[Dict[str, Any]]:
    db = getattr(interaction.client, "db", None)
    row = await db.get_label_item(item_id) if db else None
    if row is None:
        await interaction.response.send_message(view=create_error_message(
            _l("errors.title"), _l("errors.gone")), ephemeral=True)
    return row


def card_image_filename(phash: str) -> str:
    """Name of the image attached to a card. discord.py prefixes spoilered
    files with ``SPOILER_``, and the card's ``attachment://`` reference must
    match the stored name on every later edit."""
    return f"SPOILER_image_{phash}.jpg"


def _card_filename(row: Dict[str, Any]) -> Optional[str]:
    details = row.get("details") or {}
    phash = (details.get("image") or {}).get("phash")
    return card_image_filename(phash) if phash and details.get("image_attached") else None


class LabelVerdictButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:amlabel:(?P<code>yes|no|skip):(?P<item>{_UUID})",
):
    _STYLE = {
        "yes": discord.ButtonStyle.danger,
        "no": discord.ButtonStyle.success,
        "skip": discord.ButtonStyle.secondary,
    }
    _EMOJI = {"yes": ERROR, "no": DONE, "skip": WARNING}

    def __init__(self, code: str, item_id: str):
        super().__init__(ui.Button(
            label=_l(f"button.{code}")[:80],
            style=self._STYLE[code],
            emoji=discord.PartialEmoji.from_str(self._EMOJI[code]),
            custom_id=f"moddy:amlabel:{code}:{item_id}",
        ))
        self.code = code
        self.item_id = item_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["code"], match["item"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await _guard_staff(interaction):
            return
        row = await _load(interaction, self.item_id)
        if row is None:
            return
        if row.get("verdict"):
            await interaction.response.send_message(view=create_error_message(
                _l("errors.title"), _l("errors.already")), ephemeral=True)
            return
        verdict = _VERDICT_BY_CODE[self.code]
        categorie = row.get("categorie_humaine") or (row.get("details") or {}).get("categorie")
        if verdict == "sanctionnable" and row["kind"] == "texte" and not categorie:
            await interaction.response.send_message(view=create_error_message(
                _l("errors.title"), _l("errors.need_category")), ephemeral=True)
            return
        # Effects can take a few seconds (embedding, revocation, DMs).
        await interaction.response.defer()
        service = getattr(interaction.client, "automod_labels", None)
        updated = await service.label(self.item_id, verdict=verdict,
                                      labeler_id=interaction.user.id) if service else None
        if updated is None:
            await interaction.followup.send(view=create_error_message(
                _l("errors.title"), _l("errors.already")), ephemeral=True)
            return
        await interaction.edit_original_response(
            view=render_label_card(updated, image_filename=_card_filename(updated)))


class LabelCategorySelect(
    ui.DynamicItem[ui.Select],
    template=rf"moddy:amlabel:cat:(?P<item>{_UUID})",
):
    def __init__(self, item_id: str, current: str = ""):
        options = [
            discord.SelectOption(label=c, value=c, default=(c == current))
            for c in TEXT_CATEGORIES
        ]
        super().__init__(ui.Select(
            placeholder=_l("category_placeholder"),
            options=options, min_values=1, max_values=1,
            custom_id=f"moddy:amlabel:cat:{item_id}",
        ))
        self.item_id = item_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["item"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await _guard_staff(interaction):
            return
        value = (self.item.values or [""])[0]
        if value not in TEXT_CATEGORIES:
            return
        row = await interaction.client.db.set_label_item_category(self.item_id, value)
        if row is None:
            await interaction.response.send_message(view=create_error_message(
                _l("errors.title"), _l("errors.already")), ephemeral=True)
            return
        await interaction.response.edit_message(
            view=render_label_card(row, image_filename=_card_filename(row)))


class LabelTermsButton(
    ui.DynamicItem[ui.Button],
    template=rf"moddy:amlabel:terms:(?P<item>{_UUID})",
):
    def __init__(self, item_id: str):
        super().__init__(ui.Button(
            label=_l("button.terms")[:80],
            style=discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(TEXT),
            custom_id=f"moddy:amlabel:terms:{item_id}",
        ))
        self.item_id = item_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(match["item"])

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if not await _guard_staff(interaction):
            return
        row = await _load(interaction, self.item_id)
        if row is None:
            return
        current = row.get("categorie_humaine") or (row.get("details") or {}).get("categorie") or ""
        await interaction.response.send_modal(LabelTermsModal(interaction.client, self.item_id,
                                                              current_category=current))


class LabelTermsModal(BaseModal):
    """Add blocklist terms from a label card (Modal V2)."""

    def __init__(self, bot, item_id: Optional[str], *, current_category: str = ""):
        super().__init__(title=_l("terms.title")[:45])
        self.bot = bot
        self.item_id = item_id
        self.terms = ui.Label(
            text=_l("terms.label")[:45],
            description=_l("terms.description")[:100],
            component=ui.TextInput(
                style=discord.TextStyle.paragraph, max_length=1000,
                placeholder=_l("terms.placeholder")[:100],
            ),
        )
        self.mode = ui.Label(
            text=_l("terms.mode")[:45],
            component=ui.RadioGroup(options=[
                discord.RadioGroupOption(label=_l("terms.mode_words")[:100], value="words",
                                         description=_l("terms.mode_words_desc")[:100],
                                         default=True),
                discord.RadioGroupOption(label=_l("terms.mode_compact")[:100], value="compact",
                                         description=_l("terms.mode_compact_desc")[:100]),
            ]),
        )
        self.category = ui.Label(
            text=_l("category")[:45],
            component=ui.Select(options=[
                discord.SelectOption(label=c, value=c, default=(c == current_category))
                for c in TEXT_CATEGORIES
            ], min_values=1, max_values=1),
        )
        self.add_item(self.terms)
        self.add_item(self.mode)
        self.add_item(self.category)

    async def on_submit(self, interaction: discord.Interaction):
        from utils.staff_permissions import has_staff_node
        if not await has_staff_node(interaction.client, interaction.user.id, STAFF_NODE):
            await interaction.response.send_message(view=create_error_message(
                _l("errors.title"), _l("errors.no_permission")), ephemeral=True)
            return
        raw = self.terms.component.value or ""
        terms = [line.strip() for line in re.split(r"[\n,]", raw) if line.strip()][:25]
        mode = self.mode.component.value or "words"
        categorie = (self.category.component.values or [""])[0]
        if not terms or categorie not in TEXT_CATEGORIES:
            await interaction.response.send_message(view=create_error_message(
                _l("errors.title"), _l("terms.empty")), ephemeral=True)
            return
        service = getattr(interaction.client, "automod_labels", None)
        added = await service.add_terms(self.item_id, terms, categorie, mode,
                                        interaction.user.id) if service else 0
        await interaction.response.send_message(view=create_success_message(
            _l("terms.done_title"), _l("terms.done", n=added, category=categorie)),
            ephemeral=True)


class LabelPersistence(BaseView):
    """Marker view: registers the labeling queue dynamic items at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(LabelVerdictButton, LabelCategorySelect, LabelTermsButton)
