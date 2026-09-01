"""Cards and buttons for the Brocoli channel.

Two things live here:

- the **answer card**, rebuilt on every edit as Brocoli's turn progresses;
- the **confirmation card**, which is where a write actually gets decided.

The confirmation buttons are :class:`discord.ui.DynamicItem` s. They have to be:
the decision is scoped to a conversation *and* an action, neither of which
``interaction.guild_id`` or ``interaction.user.id`` can supply, so a static
``custom_id`` would not be enough to re-derive the state after a restart (see
``docs/PERSISTENT_VIEWS.md``). Everything the callback needs is encoded in the
``custom_id`` and re-read from the interaction — ``self`` is a fresh shell on
every single click, not just after a restart.

A pending action expires backend-side (``AI_ACTION_TTL``, 900 s by default). The
buttons therefore stay clickable forever, and it is the backend that answers
"expired" — refusing in the UI on a timer we do not own would drift from the
real deadline.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord import ui

from cogs.error_handler import BaseView
from config import COLORS
from utils.emojis import CHECK, DONE, ERROR, INFO, LOADING, SETTINGS, UNDONE
from utils.i18n import t

logger = logging.getLogger('moddy.brocoli')

_CID_PREFIX = "moddy:brocoli:decision"

# Discord hard-caps a TextDisplay's content; a long answer is truncated with a
# marker rather than silently cut mid-sentence by the API.
MAX_TEXT = 3900

# How many diff lines a confirmation card shows before summarising the rest.
# The backend caps its own diff at 200 entries; showing them all would make the
# card unreadable and hit the component limit.
MAX_DIFF_LINES = 8


def _truncate(text: str, limit: int = MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def loading_card(locale: str = "en-US") -> ui.LayoutView:
    """The card posted before every answer, while Brocoli works.

    Deliberately bare: one container with **no accent colour** and a single line.
    It is the first thing the member sees after hitting enter, so it has to say
    "heard you, working" and nothing else — a header, a colour bar and a tool
    line would all be noise on a message that lives for a few seconds.

    Same shape as the answer card, so the edit into the reply is a content
    swap rather than a layout jump.
    """
    view = BaseView()
    container = ui.Container()
    container.add_item(ui.TextDisplay(f"{LOADING} {t('brocoli.loading', locale=locale)}"))
    view.add_item(container)
    return view


def answer_card(
    text: str,
    *,
    locale: str = "en-US",
    thinking: bool = False,
    tool: Optional[str] = None,
) -> ui.LayoutView:
    """Brocoli's reply, in the state it is currently in.

    While ``thinking``, the card keeps the loading line at the top so the member
    can tell a finished answer from one still being written — the same message
    is edited in place as the turn progresses, rather than a burst of new ones.
    """
    view = BaseView()
    # No accent while working, so the card is visually identical to the loading
    # one it replaces; the colour appears when the answer is final.
    container = ui.Container() if thinking else ui.Container(accent_colour=COLORS["primary"])

    if thinking:
        container.add_item(
            ui.TextDisplay(f"{LOADING} {t('brocoli.loading', locale=locale)}")
        )
    if text:
        container.add_item(ui.TextDisplay(_truncate(text)))
    if tool:
        # Tool names are shown in the member's language, never raw
        # (`get_module_config` means nothing to anyone but us).
        container.add_item(ui.TextDisplay(f"-# {tool}"))

    view.add_item(container)
    return view


# Notices we have wording for. `kind` often comes straight from a
# `BrocoliError.code`, so an unknown one must degrade to something readable
# rather than print `[brocoli.notice.x.title]` in a member's channel.
_NOTICES = {
    "quota": (COLORS["warning"], INFO),
    "unavailable": (COLORS["error"], ERROR),
    "busy": (COLORS["warning"], LOADING),
    "forbidden": (COLORS["error"], ERROR),
    "expired": (COLORS["warning"], INFO),
    "not_configured": (COLORS["error"], ERROR),
    "created": (COLORS["success"], DONE),
    "exists": (COLORS["primary"], INFO),
}


def notice_card(kind: str, locale: str = "en-US", **params) -> ui.LayoutView:
    """A short state message: quota reached, assistant down, turn already running."""
    if kind not in _NOTICES:
        logger.warning("[Brocoli] no wording for notice '%s', falling back", kind)
        kind = "unavailable"
    accent, icon = _NOTICES[kind]

    view = BaseView()
    container = ui.Container(accent_colour=accent)
    container.add_item(
        ui.TextDisplay(f"### {icon} {t(f'brocoli.notice.{kind}.title', locale=locale)}")
    )
    container.add_item(
        ui.TextDisplay(t(f"brocoli.notice.{kind}.description", locale=locale, **params))
    )
    view.add_item(container)
    return view


def _render_diff(diff: list, locale: str) -> list[str]:
    """Turn the backend's flat diff into lines a human reads.

    The backend renders a changed list in full rather than aligning it element
    by element, so a line can be long — it is truncated here, not dropped: a
    confirmation that hides what changes is worse than a verbose one.
    """
    lines: list[str] = []
    for entry in diff[:MAX_DIFF_LINES]:
        path = str(entry.get("path", "?"))
        op = entry.get("op")
        if op == "added":
            lines.append(f"`{path}` — {t('brocoli.diff.added', locale=locale)}")
        elif op == "removed":
            lines.append(f"`{path}` — {t('brocoli.diff.removed', locale=locale)}")
        else:
            before = _short(entry.get("before"))
            after = _short(entry.get("after"))
            lines.append(f"`{path}` — `{before}` → `{after}`")

    remaining = len(diff) - MAX_DIFF_LINES
    if remaining > 0:
        lines.append(f"-# {t('brocoli.diff.more', locale=locale, count=remaining)}")
    return lines


def _short(value, limit: int = 60) -> str:
    text = "∅" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class DecisionButton(
    ui.DynamicItem[ui.Button],
    template=rf"{_CID_PREFIX}:(?P<verdict>approve|deny):(?P<conversation_id>[0-9a-f\-]+):(?P<action_id>[0-9a-f\-]+)",
):
    """Approve or refuse one pending Brocoli action.

    Authorisation is re-derived on every click and is deliberately strict: only
    the member who asked may decide. The backend checks this too (a conversation
    belongs to whoever opened it), but a button that visibly does nothing for
    everyone else is better than one that looks available and 403s.
    """

    def __init__(
        self,
        verdict: str,
        conversation_id: str,
        action_id: str,
        *,
        locale: str = "en-US",
        risk: str = "low",
    ):
        approve = verdict == "approve"
        super().__init__(
            ui.Button(
                label=t(
                    "brocoli.decision.approve" if approve else "brocoli.decision.deny",
                    locale=locale,
                )[:80],
                # A `critical` action gets a red confirm button: the colour is
                # the last chance to notice that this one is not routine.
                style=(
                    discord.ButtonStyle.danger
                    if approve and risk == "critical"
                    else discord.ButtonStyle.success
                    if approve
                    else discord.ButtonStyle.secondary
                ),
                emoji=discord.PartialEmoji.from_str(CHECK if approve else UNDONE),
                custom_id=f"{_CID_PREFIX}:{verdict}:{conversation_id}:{action_id}",
            )
        )
        self.verdict = verdict
        self.conversation_id = conversation_id
        self.action_id = action_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match, /):
        return cls(
            match["verdict"],
            match["conversation_id"],
            match["action_id"],
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("BrocoliChat")
        if cog is None:
            # The cog was unloaded (hot reload, disabled feature). Say so
            # instead of failing silently on a button that looks alive.
            await interaction.response.send_message(
                view=notice_card("unavailable", _locale(interaction)),
                ephemeral=True,
            )
            return

        await cog.handle_decision(
            interaction,
            conversation_id=self.conversation_id,
            action_id=self.action_id,
            approve=self.verdict == "approve",
        )


def _locale(interaction: discord.Interaction) -> str:
    return str(interaction.locale) if interaction.locale else "en-US"


def confirmation_card(
    payload: dict,
    conversation_id: str,
    *,
    locale: str = "en-US",
) -> ui.LayoutView:
    """The card that asks a human before Brocoli writes anything.

    Built from the backend's ``permission_request`` payload. ``params`` is never
    sent by the backend — it carries the full config — so everything shown here
    comes from ``preview``, which exists for exactly this.
    """
    preview = payload.get("preview") or {}
    risk = payload.get("risk", "low")
    action_id = payload.get("action_id", "")

    view = BaseView()
    container = ui.Container(
        accent_colour=COLORS["error"] if risk == "critical" else COLORS["warning"]
    )
    container.add_item(
        ui.TextDisplay(f"### {SETTINGS} {t('brocoli.confirm.title', locale=locale)}")
    )

    summary = preview.get("summary")
    if summary:
        container.add_item(ui.TextDisplay(summary))

    if risk == "critical":
        # Spelled out, because `auto` mode does not skip this one and the member
        # may not expect to be asked.
        container.add_item(
            ui.TextDisplay(f"-# {ERROR} {t('brocoli.confirm.critical', locale=locale)}")
        )

    if preview.get("valid") is False:
        errors = preview.get("errors") or []
        container.add_item(
            ui.TextDisplay(
                f"{ERROR} **{t('brocoli.confirm.invalid', locale=locale)}**\n"
                + "\n".join(f"- {e}" for e in errors[:5])
            )
        )

    diff = preview.get("diff")
    if diff:
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay("\n".join(_render_diff(diff, locale))))

    row = ui.ActionRow()
    row.add_item(
        DecisionButton("approve", conversation_id, action_id, locale=locale, risk=risk)
    )
    row.add_item(DecisionButton("deny", conversation_id, action_id, locale=locale))
    container.add_item(row)

    view.add_item(container)
    return view


def decided_card(approved: bool, summary: str, locale: str = "en-US") -> ui.LayoutView:
    """What the confirmation card becomes once someone has decided.

    The buttons are removed rather than disabled: a disabled button still reads
    as "this could be clicked", and the decision is already recorded.
    """
    view = BaseView()
    container = ui.Container(
        accent_colour=COLORS["success"] if approved else COLORS["neutral"]
    )
    container.add_item(
        ui.TextDisplay(
            f"### {DONE if approved else UNDONE} "
            + t(
                "brocoli.confirm.approved" if approved else "brocoli.confirm.denied",
                locale=locale,
            )
        )
    )
    if summary:
        container.add_item(ui.TextDisplay(f"-# {summary}"))
    view.add_item(container)
    return view


class BrocoliDecisionPersistence(BaseView):
    """Marker view: registers the decision buttons' dynamic item at startup."""

    __persistent__ = True

    @classmethod
    def register_persistent(cls, bot) -> None:
        bot.add_dynamic_items(DecisionButton)
