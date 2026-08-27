"""Support requests: cards, replies, the config-help entry point, the beta card.

Everything here runs offline — no gateway, no database. What is being checked
is the part that breaks silently in production: a custom_id that stops matching
its template after a rename, a card that raises on a half-filled request, and a
translated announcement that renders `{user}` instead of a name.

    pip install -r requirements-dev.txt && pytest tests/test_support_requests.py
"""

import datetime
import uuid

import discord
import pytest

from db.repositories.support_requests import (
    KIND_BUG, KIND_CONFIG_HELP, STATUS_OPEN, STATUS_RESOLVED,
)
from notifications.models import ContentAuthor, NotificationSource
from notifications.render import build_attribution_line, resolve_source_context
from utils import support_request_views as views
from utils.beta_announcement import (
    BetaTranslateButton, beta_content, build_beta_view, format_servers,
    owner_server_map,
)
from utils.install_welcome import build_welcome_view, welcome_content


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def make_request(**overrides):
    request = {
        "id": uuid.uuid4(),
        "kind": KIND_BUG,
        "user_id": 111,
        "guild_id": 222,
        "guild_name": "Test server",
        "locale": "fr",
        "subject": "The welcome message is sent twice",
        "body": "Every new member gets it twice.",
        "details": {"steps": "1. join"},
        "status": STATUS_OPEN,
        "claimed_by": None,
        "claimed_at": None,
        "resolved_by": None,
        "resolved_at": None,
        "channel_id": 1,
        "message_id": 2,
        "created_at": datetime.datetime.now(datetime.timezone.utc),
        "updated_at": None,
    }
    request.update(overrides)
    return request


class FakeGuild:
    def __init__(self, guild_id, name, owner_id):
        self.id = guild_id
        self.name = name
        self.owner_id = owner_id


class FakeBot:
    def __init__(self, guilds):
        self.guilds = guilds


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("kind", [KIND_BUG, KIND_CONFIG_HELP])
def test_request_card_renders_for_both_kinds(kind):
    view = views.build_request_card(request=make_request(kind=kind), locale="en-US")
    assert view.children


def test_request_card_survives_a_minimal_request():
    """A config-help request has no subject, no guild id and no details."""
    request = make_request(kind=KIND_CONFIG_HELP, subject=None, guild_id=None,
                           guild_name=None, details={}, body="Please help")
    assert views.build_request_card(request=request, locale="en-US").children


def test_resolved_card_disables_its_actions():
    request = make_request(status=STATUS_RESOLVED, resolved_by=9, claimed_by=9)
    view = views.build_request_card(request=request, locale="en-US")
    buttons = [child for row in view.children
               for child in getattr(row, "children", [])
               if isinstance(getattr(child, "item", None), discord.ui.Button)]
    assert buttons, "the card must still carry its action row"
    assert all(button.item.disabled for button in buttons)


def test_card_shows_the_exchange():
    messages = [{"author": "staff", "author_id": 9, "body": "Looking into it",
                 "created_at": None}]
    view = views.build_request_card(request=make_request(), messages=messages,
                                    locale="en-US")
    text = " ".join(
        child.content for container in view.children
        for child in getattr(container, "children", [])
        if isinstance(child, discord.ui.TextDisplay)
    )
    assert "Looking into it" in text


def test_reply_dm_carries_the_reply_button():
    request = make_request()
    view = views.build_reply_dm(request=request, body="Fixed in the next deploy",
                                locale="fr")
    ids = _custom_ids(view)
    assert f"moddy:support:ureply:{request['id']}" in ids


# --------------------------------------------------------------------------- #
# Persistence contract
# --------------------------------------------------------------------------- #

def _custom_ids(view):
    ids = []
    for row in view.children:
        for child in getattr(row, "children", []):
            item = getattr(child, "item", child)
            if getattr(item, "custom_id", None):
                ids.append(item.custom_id)
    return ids


@pytest.mark.parametrize("item, cls", [
    (views.SupportClaimButton(str(uuid.uuid4())), views.SupportClaimButton),
    (views.SupportReplyButton(str(uuid.uuid4())), views.SupportReplyButton),
    (views.SupportResolveButton(str(uuid.uuid4())), views.SupportResolveButton),
    (views.SupportUserReplyButton(str(uuid.uuid4())), views.SupportUserReplyButton),
    (views.ConfigHelpButton(), views.ConfigHelpButton),
    (views.ConfigHelpButton(guild_id=42), views.ConfigHelpButton),
    (BetaTranslateButton(str(uuid.uuid4())), BetaTranslateButton),
])
def test_custom_ids_match_their_template(item, cls):
    """A dynamic item whose id no longer matches its own template is a button
    that silently stops working after a restart."""
    template = cls.__discord_ui_compiled_template__
    assert template.fullmatch(item.item.custom_id)


def test_config_help_button_keeps_its_guild():
    button = views.ConfigHelpButton(guild_id=42)
    match = views.ConfigHelpButton.__discord_ui_compiled_template__.fullmatch(
        button.item.custom_id)
    assert match["guild"] == "42"


# --------------------------------------------------------------------------- #
# Beta announcement (temporary campaign)
# --------------------------------------------------------------------------- #

def test_beta_body_keeps_its_placeholders_in_the_template():
    content = beta_content("en-US")
    assert "{user}" in content.body and "{servers}" in content.body


def test_beta_body_resolves_every_placeholder():
    rendered = beta_content("fr").render({"user": "Jules", "servers": "**A**"})
    assert "{user}" not in rendered.body and "{servers}" not in rendered.body
    assert "Jules" in rendered.body


@pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
def test_beta_message_exists_in_every_locale(locale):
    content = beta_content(locale)
    assert content.title and "notifications.beta" not in content.title
    assert len(content.body) > 200


def test_beta_email_strips_custom_emojis():
    mail = beta_content("en-US").to_email({"user": "J", "servers": "**S**"})
    assert mail["subject"] and "<:" not in mail["subject"]
    assert "<:" not in mail["text"]
    assert mail["links"]


def test_beta_view_translate_button_is_optional():
    variables = {"user": "Jules", "servers": "**A**"}
    with_id = build_beta_view(variables=variables,
                              notification_id=str(uuid.uuid4()))
    without = build_beta_view(variables=variables)
    assert any("beta:translate" in cid for cid in _custom_ids(with_id))
    assert not any("beta:translate" in cid for cid in _custom_ids(without))


def test_owner_map_groups_servers_per_owner():
    bot = FakeBot([FakeGuild(1, "A", 10), FakeGuild(2, "B", 10), FakeGuild(3, "C", 20)])
    owners = owner_server_map(bot)
    assert sorted(owners) == [10, 20]
    assert len(owners[10]) == 2


@pytest.mark.parametrize("count, expected_joiner", [(1, None), (2, " and "), (3, " and ")])
def test_format_servers_reads_as_a_sentence(count, expected_joiner):
    guilds = [FakeGuild(i, f"Server {i}", 1) for i in range(count)]
    text = format_servers(guilds, locale="en-US")
    assert text.count("**") == count * 2
    if expected_joiner:
        assert expected_joiner in text


def test_format_servers_falls_back_when_there_is_none():
    assert format_servers([], locale="en-US")


# --------------------------------------------------------------------------- #
# Install welcome
# --------------------------------------------------------------------------- #

def test_install_welcome_names_the_server():
    rendered = welcome_content("fr").render({"server": "Mon serveur"})
    assert "Mon serveur" in rendered.body
    assert "{server}" not in rendered.body


def test_install_welcome_offers_the_config_help_button():
    view = build_welcome_view(guild=FakeGuild(42, "Test", 1), locale="en-US")
    assert "moddy:support:confighelp:42" in _custom_ids(view)


# --------------------------------------------------------------------------- #
# Attribution
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
@pytest.mark.parametrize("service, name", [
    ("moddy_team", "Moddy Team"),
    ("support", "Moddy Support"),
])
async def test_moddy_attribution_carries_the_badge(service, name):
    """"Sent by the Moddy Team✓" is the one line proving a DM is not a fake."""
    source = NotificationSource.service(service, author=ContentAuthor.STAFF)
    ctx = await resolve_source_context(None, source, locale="en-US")
    line = build_attribution_line(ctx, locale="en-US")
    assert ctx["badge"]
    assert line and ctx["badge"] in line
    # The article stays outside the bold: "the **Moddy Team**", not "**the …**".
    assert f"the **{name}**" in line


@pytest.mark.asyncio
async def test_a_plain_service_gets_no_badge_and_no_article():
    source = NotificationSource.service("reminder")
    ctx = await resolve_source_context(None, source, locale="en-US")
    line = build_attribution_line(ctx, locale="en-US")
    assert not ctx["badge"]
    assert line and "the **" not in line


@pytest.mark.asyncio
async def test_a_verified_server_still_gets_its_check():
    """The guild badge is the oldest half of the attribution line; the
    Moddy-service badge must not have displaced it."""
    class DB:
        async def get_guild(self, guild_id):
            return {"attributes": {"VERIFIED": True}}

    class Bot:
        db = DB()

        def get_guild(self, guild_id):
            return FakeGuild(guild_id, "Verified server", 1)

    ctx = await resolve_source_context(Bot(), NotificationSource.guild(42),
                                       locale="en-US")
    line = build_attribution_line(ctx, locale="en-US")
    assert ctx["verified"] and ctx["badge"]
    assert line and ctx["badge"] in line


@pytest.mark.asyncio
async def test_staff_authored_notifications_are_not_reportable():
    """There is nothing for the abuse team to judge about Moddy's own words."""
    source = NotificationSource.service("support", author=ContentAuthor.STAFF)
    ctx = await resolve_source_context(None, source, locale="en-US")
    assert ctx["reportable"] is False


# --------------------------------------------------------------------------- #
# House style
# --------------------------------------------------------------------------- #

def _link_buttons(view):
    return [child.item if hasattr(child, "item") else child
            for row in view.children
            for child in getattr(row, "children", [])
            if getattr(getattr(child, "item", child), "style", None)
            is discord.ButtonStyle.link]


@pytest.mark.parametrize("view", [
    build_beta_view(variables={"user": "J", "servers": "**S**"}),
    build_welcome_view(guild=FakeGuild(1, "Test", 1), locale="en-US"),
    views.build_reply_dm(request=make_request(), body="ok", locale="en-US"),
    views.build_receipt(kind=KIND_BUG, request=make_request(), locale="en-US"),
])
def test_link_buttons_carry_no_icon(view):
    """House rule: a link button is its label, nothing else."""
    buttons = _link_buttons(view)
    assert buttons
    assert all(button.emoji is None for button in buttons)


@pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
def test_commands_are_written_in_the_house_format(locale):
    """**`/config`**, never a </config:id> mention: the id dies on a
    re-registration and renders as raw text."""
    for content in (welcome_content(locale), beta_content(locale)):
        assert "**`/config`**" in content.body
        assert "**`/bug-report`**" in content.body
        assert "</config:" not in content.body


@pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
def test_references_are_always_code(locale):
    from utils.i18n import t
    assert "`{reference}`" in t("support.reply.reference", locale=locale,
                                reference="{reference}")
