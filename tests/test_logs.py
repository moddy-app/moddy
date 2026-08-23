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
from serverlogs.service import LogService
from serverlogs.renderer import (
    KIND_COLORS, MAX_EMBED_TOTAL, LogEntry, build_transcript, fmt_duration,
    fmt_number, fmt_time, fmt_user,
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


def test_categories_and_events_use_the_logs_icon_set():
    """The logs may only draw from LOG_EMOJIS — custom emojis, one source."""
    from utils.emojis import LOG_EMOJIS

    known = set(LOG_EMOJIS.values())
    for category in registry.CATEGORIES.values():
        assert category.emoji in known, category.id       # shown in /config
        assert category.log_icon in known, category.id    # shown on a log
    for key in registry.EVENTS:
        assert registry.event_emoji(key) in known, key


def test_a_category_icon_is_never_the_one_a_log_falls_back_to():
    """/config identifies a category; a log says what happened."""
    shared = [c.id for c in registry.CATEGORIES.values()
              if c.emoji == c.log_icon]
    assert shared == [], shared


def test_every_event_has_a_hand_picked_icon():
    """The fallback exists for a future event, not as the normal case."""
    declared = set(registry._EVENT_ICONS)
    catalogue = {spec.event for spec in registry.EVENTS.values()}
    assert not catalogue - declared, f"no icon chosen for: {sorted(catalogue - declared)}"
    assert not declared - catalogue, f"icons for gone events: {sorted(declared - catalogue)}"


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

def test_entry_renders_a_heading_then_quoted_labelled_lines():
    entry = LogEntry("user_join", "en-US")
    entry.line("member_count", fmt_number(664))
    embed = entry.to_embed()
    heading, first_line = embed.description.split("\n")[:2]
    # Custom emojis only render in a description, never in an embed title.
    assert heading.startswith("### <:")
    assert embed.title is None
    assert first_line.startswith("> **")
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


def test_an_oversized_embed_is_cut_down_to_a_deliverable_size():
    """Discord rejects the whole message past its total budget, not just the field."""
    entry = LogEntry("channel_permissions_update", "en-US")
    for index in range(25):
        entry.block(f"role_{index}", "y" * 900)
    embed = entry.to_embed()
    assert len(embed) <= MAX_EMBED_TOTAL
    assert len(embed.fields) < 25
    assert "-#" in (embed.description or "")


def test_an_embed_within_budget_is_left_alone():
    entry = LogEntry("message_delete", "en-US")
    entry.block("message", "hello")
    embed = entry.to_embed()
    assert embed.description.count("\n") == 0, "only the heading, no extra note"
    assert len(embed.fields) == 1


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


# --------------------------------------------------------------------------- #
# Merging one act told several times
# --------------------------------------------------------------------------- #

def test_every_merge_family_member_is_a_real_event():
    for event in ("mute_add", "user_timed_out", "user_roles_update", "kick_add"):
        assert registry.merge_family(event) is not None
        assert registry.keys_for(event), event


def test_ordinary_events_are_never_held_back():
    """Two deletions from the same author must stay two logs."""
    assert registry.merge_family("message_delete") is None
    assert registry.merge_family("channel_create") is None


def _merge_service(module):
    """A service whose configuration is fixed and whose delivery is recorded."""
    bot = types.SimpleNamespace(get_channel=lambda _id: None, user=None,
                                module_manager=None)
    service = LogService(bot)

    async def _module(_guild=None):
        return module

    service.module = _module
    sent = []
    service.dispatcher.enqueue = lambda channel_id, embed, files=None: sent.append(
        (channel_id, embed))
    return service, sent


def _mute_entries():
    """The three logs a Moddy mute really produces (case, timeout, mirror)."""
    case = LogEntry("mute_add", "en-US")
    case.subject_id = 7
    case.line("case", "`N6A8Q2`").line("reason", "bonjour")

    timeout = LogEntry("user_timed_out", "en-US")
    timeout.subject_id = 7
    timeout.line("until", "tomorrow").line("expires", "in a month")
    timeout.line("reason", "[N6A8Q2] (Permanent) : bonjour")

    mirror = LogEntry("mute_add", "en-US")
    mirror.subject_id = 7
    mirror.line("until", "tomorrow")
    return case, timeout, mirror


@pytest.mark.asyncio
async def test_one_act_sent_to_one_channel_becomes_one_log():
    module = config_from_raw(None, 1, {
        "categories": {
            "moderation": {"channel_ids": [222], "disabled_events": []},
            "users": {"channel_ids": [222], "disabled_events": []},
        },
    })
    service, sent = _merge_service(module)
    guild = types.SimpleNamespace(id=1, preferred_locale="en-US")

    for entry in _mute_entries():
        await service.submit(guild, entry)
    assert sent == [], "mergeable events must wait for their siblings"

    await service.flush(guild, (1, "member_mute", 7))
    await service.close()

    assert len(sent) == 1
    _channel, embed = sent[0]
    description = embed.description
    # The richest entry won, and it kept what the others knew.
    assert "N6A8Q2" in description        # from the case
    assert "in a month" in description    # from the timeout
    assert description.count("Reason") == 1, "the same reason must not be repeated"
    assert "Merged with" in description


@pytest.mark.asyncio
async def test_merging_never_empties_a_channel_that_wanted_the_other_event():
    """Categories split across channels: each channel keeps its own log."""
    module = config_from_raw(None, 1, {
        "categories": {
            "moderation": {"channel_ids": [222], "disabled_events": []},
            "users": {"channel_ids": [333], "disabled_events": []},
        },
    })
    service, sent = _merge_service(module)
    guild = types.SimpleNamespace(id=1, preferred_locale="en-US")

    case, timeout, _mirror = _mute_entries()
    await service.submit(guild, case)
    await service.submit(guild, timeout)
    await service.flush(guild, (1, "member_mute", 7))
    await service.close()

    assert sorted(channel for channel, _ in sent) == [222, 333]
    for _channel, embed in sent:
        assert "Merged with" not in (embed.description or "")


@pytest.mark.asyncio
async def test_the_option_can_be_turned_off():
    module = config_from_raw(None, 1, {
        "categories": {
            "moderation": {"channel_ids": [222], "disabled_events": []},
            "users": {"channel_ids": [222], "disabled_events": []},
        },
        "merge_duplicates": False,
    })
    service, sent = _merge_service(module)
    guild = types.SimpleNamespace(id=1, preferred_locale="en-US")

    for entry in _mute_entries():
        await service.submit(guild, entry)
    await service.close()

    assert len(sent) == 3, "with merging off, every event is its own log"


def test_merging_is_on_by_default_and_round_trips():
    module = config_from_raw(None, 1, {})
    assert module.merge_duplicates is True
    assert config_from_raw(None, 1, {"merge_duplicates": False}).merge_duplicates is False
    assert module.to_config()["merge_duplicates"] is True


def test_absorbing_keeps_the_subject_and_the_executor():
    winner = LogEntry("mute_add", "en-US")
    winner.line("case", "`N6A8Q2`")
    loser = LogEntry("user_timed_out", "en-US")
    loser.line("until", "tomorrow")
    class _Bot:
        id = 9
        display_avatar = None

        def __str__(self):
            return "Moddy#3735"

    loser.actor(_Bot())

    embed = winner.absorb(loser).to_embed()
    assert "tomorrow" in embed.description
    assert embed.footer.text == "Moddy#3735"


# --------------------------------------------------------------------------- #
# Icons
# --------------------------------------------------------------------------- #

def test_the_config_panel_draws_only_from_the_logs_icon_set():
    """Category and event icons come from the logs set; generic UI chrome
    (going back a screen, Options, clearing the config) uses the bot's
    normal icons instead, like every other /config panel."""
    from modules.configs import logs_config
    from utils.emojis import BACK, DELETE, LOG_EMOJIS, NOTE, SETTINGS

    generic_ui = {"_ICON_BACK": BACK, "_ICON_OPTIONS": SETTINGS,
                  "_ICON_CLEAR": DELETE}
    allowed_logs = set(LOG_EMOJIS.values()) | {NOTE}
    icons = {name: value for name, value in vars(logs_config).items()
             if name.startswith("_ICON_")}
    assert icons, "the panel declares no icon constant any more"
    for name, value in icons.items():
        if name in generic_ui:
            assert value == generic_ui[name], f"{name} should be the bot's normal icon"
        else:
            assert value in allowed_logs, f"{name} is not in the logs icon set"


def test_the_event_checklist_shows_one_icon_per_event():
    from modules.configs.logs_config import LogsCategoryEvents
    from utils.emojis import LOG_EMOJIS

    item = LogsCategoryEvents("channels", 0)
    known = {value.split(":")[2].rstrip(">") for value in LOG_EMOJIS.values()}
    for option in item.item.options:
        assert option.emoji is not None, option.value
        assert str(option.emoji.id) in known, option.value
