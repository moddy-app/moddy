"""Advanced server logs — registry, routing, rendering and delivery.

These cover the parts that decide whether a log is emitted at all and what
it contains; the Discord listeners themselves are thin forwarders (see
``cogs/logs.py``) and are exercised through the builders' inputs.
"""

import asyncio
import types
from datetime import datetime, timezone

import discord
import pytest

from modules.logs import LogsModule, config_from_raw
from serverlogs import registry
from serverlogs.dispatcher import LogDispatcher, MAX_EMBEDS_PER_MESSAGE
from serverlogs.renderer import (
    KIND_COLORS, LogEntry, build_transcript, fmt_duration, fmt_number,
    fmt_time, fmt_user,
)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

def test_every_event_belongs_to_its_category():
    for key, spec in registry.EVENTS.items():
        assert key == f"{spec.category}.{spec.event}"
        assert spec.event in registry.CATEGORIES[spec.category].events


def test_event_kinds_are_known():
    valid = set(KIND_COLORS)
    assert {spec.kind for spec in registry.EVENTS.values()} <= valid


def test_categories_have_unique_events():
    for category in registry.CATEGORIES.values():
        assert len(category.events) == len(set(category.events)), category.id


def test_shared_events_resolve_to_several_categories():
    """A ban is both a server event and a moderation event."""
    assert set(registry.keys_for("ban_add")) == {"server.ban_add", "moderation.ban_add"}
    assert registry.keys_for("user_join") == ("server.user_join",)


def test_category_order_is_stable():
    ordered = [c.id for c in registry.ordered_categories()]
    assert ordered[0] == "server"
    assert len(ordered) == len(registry.CATEGORIES)


# --------------------------------------------------------------------------- #
# Configuration and routing
# --------------------------------------------------------------------------- #

def _module(**overrides) -> LogsModule:
    raw = {
        "categories": {
            "server": {"channel_ids": [111], "disabled_events": []},
            "moderation": {"channel_ids": [222], "disabled_events": []},
        },
    }
    raw.update(overrides)
    return config_from_raw(None, 1, raw)


def test_routing_fans_out_to_every_configured_category():
    module = _module()
    assert module.channels_for("ban_add") == [111, 222]
    assert module.channels_for("user_join") == [111]


def test_an_excluded_event_is_not_routed():
    module = _module(categories={
        "server": {"channel_ids": [111], "disabled_events": ["user_join"]},
    })
    assert module.channels_for("user_join") == []
    assert module.channels_for("user_leave") == [111]


def test_a_category_with_no_channel_routes_nothing():
    module = config_from_raw(None, 1, {"categories": {
        "server": {"channel_ids": [], "disabled_events": []}}})
    assert module.channels_for("user_join") == []
    assert module.enabled is False


def test_a_new_registry_event_is_enabled_by_default():
    """Only exclusions are stored, so events added later start logged."""
    module = _module(categories={
        "channels": {"channel_ids": [333], "disabled_events": ["channel_create"]},
    })
    for event in registry.CATEGORIES["channels"].events:
        expected = [] if event == "channel_create" else [333]
        assert module.channels_for(event) == expected, event


def test_unknown_categories_and_events_are_dropped():
    module = config_from_raw(None, 1, {"categories": {
        "server": {"channel_ids": [111], "disabled_events": ["not_an_event"]},
        "not_a_category": {"channel_ids": [999]},
    }})
    assert "not_a_category" not in module.categories
    assert module.categories["server"].disabled_events == set()


def test_snowflakes_stored_as_strings_are_accepted():
    """The dashboard writes 64-bit ids as JSON strings."""
    module = config_from_raw(None, 1, {"categories": {
        "server": {"channel_ids": ["111", "111", "222"]}}})
    assert module.channels_for("user_join") == [111, 222]


def test_round_trip_through_the_stored_schema():
    module = _module(ignore_bots=True, ignored_channel_ids=[7], locale="fr")
    restored = config_from_raw(None, 1, module.to_config())
    assert restored.to_config() == module.to_config()
    assert restored.ignore_bots is True
    assert restored.locale == "fr"


def test_an_empty_category_is_not_persisted():
    module = _module()
    module.category("emojis")  # touched but never configured
    assert "emojis" not in module.to_config()["categories"]


# --------------------------------------------------------------------------- #
# Ignore lists
# --------------------------------------------------------------------------- #

def test_ignored_channel_covers_the_parent_category():
    module = _module(ignored_channel_ids=[500])
    parent = types.SimpleNamespace(id=500, category=None, parent=None)
    channel = types.SimpleNamespace(id=42, category=parent, parent=None)
    assert module.is_ignored_channel(channel) is True
    assert module.is_ignored_channel(
        types.SimpleNamespace(id=43, category=None, parent=None)) is False


def test_ignored_roles_and_bots():
    module = _module(ignore_bots=True, ignored_role_ids=[9])
    bot_user = types.SimpleNamespace(bot=True, roles=[])
    staff = types.SimpleNamespace(bot=False, roles=[types.SimpleNamespace(id=9)])
    human = types.SimpleNamespace(bot=False, roles=[types.SimpleNamespace(id=1)])
    assert module.is_ignored_actor(bot_user) is True
    assert module.is_ignored_actor(staff) is True
    assert module.is_ignored_actor(human) is False
    assert module.is_ignored_actor(None) is False


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def test_entry_renders_quoted_labelled_lines():
    entry = LogEntry("user_join", "en-US")
    entry.line("member_count", fmt_number(664))
    embed = entry.to_embed()
    assert embed.description.startswith("> **")
    assert "`664`" in embed.description
    assert embed.colour.value == KIND_COLORS[registry.KIND_CREATE]


def test_moderation_events_use_the_moderation_accent():
    assert LogEntry("ban_add", "en-US").to_embed().colour.value == \
        KIND_COLORS[registry.KIND_MODERATION]
    assert LogEntry("channel_delete", "en-US").to_embed().colour.value == \
        KIND_COLORS[registry.KIND_DELETE]


def test_empty_values_are_skipped():
    entry = LogEntry("user_join", "en-US")
    entry.line("reason", None)
    entry.line("member_count", "")
    assert entry.is_empty


def test_a_long_block_moves_to_an_attachment():
    entry = LogEntry("message_delete", "en-US")
    entry.block("message", "x" * 3000, filename="message.txt")
    assert len(entry.files) == 1
    assert entry.files[0].filename == "message.txt"
    field = entry.to_embed().fields[0]
    assert len(field.value) <= 1024


def test_a_short_block_stays_inline():
    entry = LogEntry("message_delete", "en-US")
    entry.block("message", "hello")
    assert entry.files == []
    assert entry.to_embed().fields[0].value == "hello"


def test_markdown_in_names_is_escaped():
    user = types.SimpleNamespace(id=5, name="**boss**", display_avatar=None)
    assert "\\*\\*boss\\*\\*" in fmt_user(user)
    assert "(<@5>)" in fmt_user(user)


def test_relative_timestamps_are_discord_formatted():
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert fmt_time(moment) == f"<t:{int(moment.timestamp())}:R>"
    assert fmt_time(None) == "—"


def test_durations_read_in_the_reader_language():
    assert fmt_duration(0, "en-US")          # "Disabled", not empty
    assert "1" in fmt_duration(3600, "en-US")
    assert fmt_duration(None, "en-US") == "—"


def test_transcript_lists_every_message():
    messages = [
        types.SimpleNamespace(
            author=types.SimpleNamespace(id=1, __str__=lambda self: "alice"),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            content="first", attachments=[], embeds=[], stickers=[]),
        types.SimpleNamespace(
            author=None, created_at=None, content="",
            attachments=[], embeds=[], stickers=[]),
    ]
    file = build_transcript(messages, guild_name="Guild", channel_name="general",
                            header="Transcript")
    body = file.fp.read().decode("utf-8")
    assert "Messages: 2" in body
    assert "first" in body
    assert "(no text content)" in body
    assert file.filename == "transcript-general.txt"


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_dispatcher_batches_embeds_into_one_call():
    sent = []

    class _Channel:
        id = 1
        guild = None

        async def send(self, **kwargs):
            sent.append(kwargs)

    bot = types.SimpleNamespace(get_channel=lambda _id: _Channel(), user=None)
    dispatcher = LogDispatcher(bot)
    # No webhook is resolvable without a guild, so delivery falls back to send.
    dispatcher._webhooks[1] = None

    for index in range(MAX_EMBEDS_PER_MESSAGE + 2):
        dispatcher.enqueue(1, discord.Embed(title=str(index)))
    await asyncio.sleep(1.2)
    await dispatcher.close()

    assert sent, "nothing was delivered"
    assert sum(len(call["embeds"]) for call in sent) == MAX_EMBEDS_PER_MESSAGE + 2
    assert max(len(call["embeds"]) for call in sent) > 1, "no batching happened"


@pytest.mark.asyncio
async def test_dispatcher_drops_the_oldest_under_a_flood():
    bot = types.SimpleNamespace(get_channel=lambda _id: None, user=None)
    instance = LogDispatcher(bot)
    queue = asyncio.Queue(maxsize=2)
    instance._queues[1] = queue

    for index in range(5):
        instance.enqueue(1, discord.Embed(title=str(index)))

    assert queue.qsize() <= 2
    assert instance._dropped[1] == 3
    await instance.close()
