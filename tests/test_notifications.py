"""Centralized notification system — model, attribution and report rules.

What these guard, in order of how badly they would hurt in production:

* **Content de-duplication.** The whole storage design rests on identical
  templates hashing identically *before* placeholder substitution. If that
  breaks, every welcome DM writes its own body row.
* **Reproducibility.** A notification must render to the exact wording its
  recipient saw, months later, from the template plus its variables — that is
  what the staff "See the message" button shows a reviewer.
* **Attribution rules.** Which buttons a DM carries, and above all when the
  report flag is dead: Moddy-authored wording, official notices, and messages
  from Moddy's own servers are never reportable.
* **Report authorization.** Only the addressee reports; no staffer reviews
  their own report.

The pure-Python core is tested directly; the Discord surface is tested through
stubs (no gateway, no database).
"""

from __future__ import annotations

import pytest

from notifications.models import (
    SERVICES, ContentAuthor, NotificationContent, NotificationSource,
    RecipientType, SourceKind, get_service, strip_custom_emojis, substitute,
)
from notifications.render import build_attribution_line, resolve_source_context
from notifications.service import NotificationService

_UUID = "0f7d9c62-3b4e-4a1f-9c2d-5e6f70819a2b"


# --------------------------------------------------------------------------- #
# Placeholders
# --------------------------------------------------------------------------- #

def test_placeholders_are_substituted_from_variables():
    assert substitute("Welcome {user} on {server}",
                      {"user": "@bob", "server": "Moddy"}) == "Welcome @bob on Moddy"


def test_an_unknown_placeholder_is_left_visible():
    """Silently blanking it would hide the mistake from whoever wrote it."""
    assert substitute("Hi {nope}", {"user": "x"}) == "Hi {nope}"


def test_a_stray_brace_never_raises():
    """Server-written text is arbitrary: a lone '{' must not break a delivery."""
    assert substitute("100% { of the time", {"user": "x"}) == "100% { of the time"


def test_a_none_variable_renders_empty():
    assert substitute("[{maybe}]", {"maybe": None}) == "[]"


# --------------------------------------------------------------------------- #
# Content hashing
# --------------------------------------------------------------------------- #

def _welcome(body="Welcome {user} on {server}!"):
    return NotificationContent(title="Server", body=body, icon="<:a:1>",
                               template_id="welcome_dm.wdm_1")


def test_identical_templates_share_one_hash():
    assert _welcome().template_hash() == _welcome().template_hash()


def test_the_hash_ignores_the_resolved_values():
    """Ten thousand members, one stored body — the point of the design."""
    template = _welcome()
    first = template.render({"user": "@bob", "server": "Moddy"})
    second = template.render({"user": "@alice", "server": "Moddy"})
    assert first.body != second.body
    assert template.template_hash() == _welcome().template_hash()


def test_a_different_template_hashes_differently():
    assert _welcome().template_hash() != _welcome("Goodbye {user}").template_hash()


def test_a_content_survives_a_round_trip_through_the_database_shape():
    content = NotificationContent(
        title="T", body="B {x}", icon="<:i:1>", accent_color=0x3661FF,
        sections=[{"title": "S", "body": "{x}"}],
        links=[{"label": "L", "url": "https://moddy.app"}],
        footer="F", template_id="t.1")
    assert NotificationContent.from_dict(content.to_dict()).template_hash() \
        == content.template_hash()


def test_the_exact_wording_can_be_rebuilt_later():
    stored = NotificationContent.from_dict(_welcome().to_dict())
    rebuilt = stored.render({"user": "@bob", "server": "Moddy"})
    assert rebuilt.body == "Welcome @bob on Moddy!"


# --------------------------------------------------------------------------- #
# Cross-platform rendering
# --------------------------------------------------------------------------- #

def test_the_mail_shape_carries_a_subject_a_body_and_the_links():
    content = NotificationContent(
        title="Account suspended", body="Your account {user} is suspended.",
        sections=[{"title": "Reason", "body": "{reason}"}],
        links=[{"label": "Appeal", "url": "https://moddy.app/support"}],
        footer="Moddy")
    mail = content.to_email({"user": "bob", "reason": "spam"})
    assert mail["subject"] == "Account suspended"
    assert "Your account bob is suspended." in mail["text"]
    assert "spam" in mail["text"]
    assert mail["links"][0]["url"] == "https://moddy.app/support"


def test_custom_emojis_are_stripped_outside_discord():
    """`<:done:123>` is literal noise in an inbox."""
    content = NotificationContent(title="<:done:1> Done", body="ok")
    assert content.to_email()["subject"] == "Done"
    assert strip_custom_emojis("a <a:spin:2> b") == "a b"


def test_the_dashboard_shape_keeps_the_markdown_and_the_icon():
    content = NotificationContent(title="T", body="**bold** {x}", icon="<:i:1>")
    data = content.to_dashboard({"x": "v"})
    assert data["body"] == "**bold** v"
    assert data["icon"] == "<:i:1>"


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #

def test_an_official_notice_carries_no_attribution_at_all():
    source = NotificationSource.official("global_sanctions")
    assert source.has_attribution is False
    assert source.base_reportable is False


def test_guild_written_content_is_reportable():
    assert NotificationSource.guild(42).base_reportable is True


def test_moddy_written_content_is_not_reportable():
    """A sanction notice is Moddy's wording: there is nothing to judge."""
    source = NotificationSource.service_guild("automod_ai", 42)
    assert source.has_attribution is True
    assert source.base_reportable is False


def test_a_source_survives_a_round_trip_through_its_row():
    source = NotificationSource.service_guild(
        "welcome_dm", 42, author=ContentAuthor.GUILD, actor_id=7)
    row = {
        "kind": source.kind.value, "source_service": source.service_id,
        "source_guild_id": source.guild_id, "author": source.author.value,
        "actor_id": source.actor_id,
    }
    assert NotificationSource.from_row(row) == source


def test_every_registered_service_resolves_to_a_real_emoji():
    for service_id, service in SERVICES.items():
        assert get_service(service_id) is service
        assert service.emoji.startswith("<")


def test_an_unknown_service_id_is_tolerated():
    assert get_service("does_not_exist") is None
    assert get_service(None) is None


# --------------------------------------------------------------------------- #
# Attribution context (stubbed bot)
# --------------------------------------------------------------------------- #

class FakeGuild:
    def __init__(self, gid=42, name="Test Server"):
        self.id = gid
        self.name = name


class FakeDB:
    def __init__(self, attributes=None):
        self._attributes = attributes or {}

    async def get_guild(self, guild_id):
        return {"guild_id": guild_id, "attributes": self._attributes}


class FakeBot:
    def __init__(self, guild=None, attributes=None):
        self._guild = guild
        self.db = FakeDB(attributes)

    def get_guild(self, guild_id):
        return self._guild if self._guild and self._guild.id == guild_id else None


async def test_a_plain_server_is_reportable_and_unbadged():
    ctx = await resolve_source_context(
        FakeBot(FakeGuild()), NotificationSource.guild(42))
    assert ctx["guild_name"] == "Test Server"
    assert ctx["reportable"] is True
    assert ctx["verified"] is False
    assert ctx["badge"] == ""


async def test_a_verified_server_gets_the_check():
    ctx = await resolve_source_context(
        FakeBot(FakeGuild(), {"VERIFIED": True}), NotificationSource.guild(42))
    assert ctx["verified"] is True
    assert ctx["badge"]  # a hyperlinked badge, per the CLAUDE.md rule
    assert ctx["reportable"] is True
    # …and it is the badge that reaches the attribution line.
    assert ctx["badge"] in build_attribution_line(ctx, locale="en-US")


async def test_an_official_moddy_server_greys_the_flag_out():
    """Reporting Moddy to Moddy is a loop with no exit."""
    ctx = await resolve_source_context(
        FakeBot(FakeGuild(), {"OFFICIAL": True}), NotificationSource.guild(42))
    assert ctx["official"] is True
    assert ctx["verified"] is True
    assert ctx["reportable"] is False
    assert ctx["report_block"] == "official_guild"


async def test_moddy_authored_content_says_why_the_flag_is_dead():
    ctx = await resolve_source_context(
        FakeBot(FakeGuild()), NotificationSource.service_guild("altguard", 42))
    assert ctx["reportable"] is False
    assert ctx["report_block"] == "moddy_authored"
    assert ctx["service_name"]


async def test_an_unreachable_guild_degrades_instead_of_raising():
    ctx = await resolve_source_context(FakeBot(None), NotificationSource.guild(42))
    assert ctx["guild_name"]  # "Unknown server", not a crash
    assert ctx["reportable"] is True


async def test_a_broken_guild_object_costs_the_badge_not_the_message():
    """This runs on the delivery path: nothing in it may raise."""
    class Exploding:
        def get_guild(self, guild_id):
            raise RuntimeError("cache is on fire")
        db = None

    ctx = await resolve_source_context(Exploding(), NotificationSource.guild(42))
    assert ctx["guild_name"]      # falls back to "Unknown server"
    assert ctx["badge"] == ""


# --------------------------------------------------------------------------- #
# Service behaviour without a database
# --------------------------------------------------------------------------- #

class NoDbBot:
    db = None

    def get_guild(self, guild_id):
        return None


class Recipient:
    def __init__(self):
        self.id = 7
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return None


async def test_a_dm_still_goes_out_when_the_database_is_down():
    """A recording failure must never swallow the message itself.

    And, since the attribution is plain text built from the source rather than
    a button needing the stored uuid, the recipient is still told who wrote to
    them — a failed INSERT costs the record, not the accountability.
    """
    service = NotificationService(FakeBot(FakeGuild()))
    service.db  # the guild lookup works; create_notification does not exist
    recipient = Recipient()
    result = await service.send_dm(
        recipient,
        content=NotificationContent(title="T", body="B"),
        source=NotificationSource.guild(42),
    )
    assert result.delivered
    assert result.notification_id is None          # nothing was recorded…
    rendered = str(recipient.sent[0]["view"].to_components())
    assert "Sent by" in rendered and "Test Server" in rendered   # …but it is attributed


async def test_the_moddy_logo_is_the_small_square_mark():
    """One logo everywhere: the inline-sized rounded square."""
    from utils.emojis import MODDY_SQUARE_MIN
    assert SERVICES["moddy"].emoji == MODDY_SQUARE_MIN


async def test_may_report_is_the_recipient_only():
    service = NotificationService(NoDbBot())

    class Interaction:
        def __init__(self, user_id):
            self.user = type("U", (), {"id": user_id})()
            self.guild_id = None

    record = {"recipient_type": RecipientType.DISCORD_USER.value, "recipient_id": 7}
    assert await service.may_report(record, Interaction(7)) is True
    assert await service.may_report(record, Interaction(8)) is False


async def test_a_server_notice_is_reported_by_a_server_manager():
    service = NotificationService(NoDbBot())

    class Member:
        def __init__(self, manage):
            self.id = 7
            self.guild_permissions = type(
                "P", (), {"manage_guild": manage, "administrator": False})()

    class Interaction:
        def __init__(self, member, guild_id):
            self.user = member
            self.guild_id = guild_id

    record = {"recipient_type": RecipientType.DISCORD_GUILD.value, "recipient_id": 42}
    assert await service.may_report(record, Interaction(Member(True), 42)) is True
    assert await service.may_report(record, Interaction(Member(False), 42)) is False
    assert await service.may_report(record, Interaction(Member(True), 99)) is False


async def test_a_row_recorded_as_unreportable_can_never_become_reportable():
    """A server marked official afterwards must not resurrect old flags."""
    service = NotificationService(FakeBot(FakeGuild()))
    record = {
        "kind": SourceKind.GUILD.value, "author": ContentAuthor.GUILD.value,
        "source_service": None, "source_guild_id": 42, "actor_id": None,
        "reportable": False,
    }
    ctx = await service.source_context(record)
    assert ctx["reportable"] is False


# --------------------------------------------------------------------------- #
# Attribution line
#
# One greyed line at the bottom of every attributable DM, in place of the
# buttons an earlier iteration carried. It is the only thing a recipient sees
# about where their message came from, so its shape is load-bearing.
# --------------------------------------------------------------------------- #

def test_a_server_source_names_the_server_with_a_link_and_its_id():
    from notifications.render import build_attribution_line

    line = build_attribution_line({
        "guild_id": 42, "guild_name": "Test Server", "badge": "",
        "service_name": "Welcome message",
    }, locale="en-US")
    assert line == ("-# Sent by [**Test Server**](https://discord.com/channels/42) (`42`)")


def test_the_verification_badge_follows_the_name():
    """CLAUDE.md rule #7: the badge sits right after the bold name."""
    from notifications.render import build_attribution_line

    line = build_attribution_line({
        "guild_id": 42, "guild_name": "Test Server", "badge": "[<:v:1>](https://d)",
    }, locale="en-US")
    assert "[**Test Server**](https://discord.com/channels/42)[<:v:1>](https://d)" in line


def test_a_service_only_source_names_the_service():
    """A reminder has no server to point at — the service is the origin."""
    from notifications.render import build_attribution_line

    line = build_attribution_line({"service_name": "Reminders"}, locale="en-US")
    assert line == "-# Sent by **Reminders**"


def test_a_source_with_nothing_to_name_gets_no_line():
    from notifications.render import build_attribution_line

    assert build_attribution_line({}, locale="en-US") is None


def test_the_line_is_greyed_out_in_every_locale():
    """`-#` is what makes it a footnote rather than part of the message."""
    from notifications.render import build_attribution_line

    for locale in ("fr", "en-US", "es-ES", "pt-BR", "de"):
        line = build_attribution_line(
            {"guild_id": 42, "guild_name": "S", "badge": ""}, locale=locale)
        assert line.startswith("-# ")
        assert "https://discord.com/channels/42" in line
        assert "`42`" in line


async def test_the_line_lands_inside_the_last_container():
    """Under the card's text, not floating as its own component."""
    from discord import ui
    from notifications.service import _append_footer_line

    view = ui.LayoutView(timeout=None)
    container = ui.Container(ui.TextDisplay("body"))
    view.add_item(container)
    _append_footer_line(view, "-# Sent by **Moddy**")

    assert len(list(view.children)) == 1  # no new top-level component
    assert list(container.children)[-1].content == "-# Sent by **Moddy**"


async def test_a_view_without_a_container_still_gets_the_line():
    from discord import ui
    from notifications.service import _append_footer_line

    view = ui.LayoutView(timeout=None)
    view.add_item(ui.TextDisplay("body"))
    _append_footer_line(view, "-# Sent by **Moddy**")
    assert list(view.children)[-1].content == "-# Sent by **Moddy**"


async def test_an_official_notice_says_nothing_about_its_origin():
    """A suspension IS Moddy speaking; there is no third party to name."""
    service = NotificationService(NoDbBot())
    recipient = Recipient()
    await service.send_dm(
        recipient,
        content=NotificationContent(title="T", body="B"),
        source=NotificationSource.official("global_sanctions"),
    )
    view = recipient.sent[0]["view"]
    rendered = str(view.to_components())
    assert "Sent by" not in rendered


# --------------------------------------------------------------------------- #
# i18n completeness
#
# A missing key does not crash: it renders as `[notifications.…]` inside a DM
# that thousands of people receive. These tests are what stops that shipping.
# --------------------------------------------------------------------------- #

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")

#: Keys built from a variable at runtime, so a source scan cannot see them.
_DYNAMIC_KEYS = {
    "notifications.report.status.pending",
    "notifications.report.status.claimed",
    "notifications.report.status.accepted",
    "notifications.report.status.refused",
    "notifications.report.outcome.accepted.title",
    "notifications.report.outcome.accepted.description",
    "notifications.report.outcome.refused.title",
    "notifications.report.outcome.refused.description",
    "notifications.review.buttons.accept",
    "notifications.review.buttons.refuse",
    "notifications.review.decision.accept.title",
    "notifications.review.decision.accept.recap",
    "notifications.review.decision.accept.done.title",
    "notifications.review.decision.accept.done.description",
    "notifications.review.decision.refuse.title",
    "notifications.review.decision.refuse.recap",
    "notifications.review.decision.refuse.done.title",
    "notifications.review.decision.refuse.done.description",
    "notifications.log.title.created",
    "notifications.log.title.claimed",
    "notifications.log.title.accepted",
    "notifications.log.title.refused",
    "staff.com.send.audience.user",
    "staff.com.send.audience.guild",
    "staff.com.send.audience.users",
    "staff.com.send.audience.guilds",
    "staff.com.send.errors.dms_closed.title",
    "staff.com.send.errors.dms_closed.description",
    "staff.com.send.errors.failed.title",
    "staff.com.send.errors.failed.description",
}

_SOURCES = (
    "utils/notification_views.py",
    "notifications/render.py",
    "notifications/service.py",
    "staff/commands/mod/notification.py",
    "staff/commands/com/send.py",
    "staff/commands/com/_send_modal.py",
)

_KEY_RE = re.compile(r"[\"']((?:notifications|staff\.(?:notif|com))\.[a-zA-Z0-9_.]+)[\"']")


def _used_keys() -> set:
    keys = set(_DYNAMIC_KEYS)
    for relative in _SOURCES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        for key in _KEY_RE.findall(source):
            # f-string keys carrying a `{placeholder}` segment are covered by
            # _DYNAMIC_KEYS above; the scan sees only their literal prefix.
            if key.endswith("."):
                continue
            keys.add(key)
    # `template_id` values look like keys but name a template, not a string.
    return {k for k in keys
            if k not in {"staff.com.send", "notifications.report.outcome"}}


def _lookup(data: dict, key: str):
    current = data
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


@pytest.mark.parametrize("locale", LOCALES)
def test_every_notification_string_exists_in_every_locale(locale):
    data = json.loads((ROOT / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
    missing = sorted(k for k in _used_keys() if not isinstance(_lookup(data, k), str))
    assert not missing, f"locales/{locale}.json is missing: {missing}"


@pytest.mark.parametrize("locale", LOCALES)
def test_modal_titles_fit_discords_limit(locale):
    """Discord rejects a modal whose title is over 45 characters."""
    data = json.loads((ROOT / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
    for key in ("notifications.review.decision.accept.title",
                "notifications.review.decision.refuse.title",
                "staff.com.send.modal.title"):
        assert len(_lookup(data, key)) <= 45, f"{key} too long in {locale}"
