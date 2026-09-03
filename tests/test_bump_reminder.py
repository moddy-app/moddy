"""Bump Reminder — detection, config, cards and i18n.

Everything here is pure Python: no gateway, no database. The detection tests
replay the **real captured payload** of each of the seven directories
(``tests/data/bump_payloads.json``) through the real code, which is the only way
to be honest about a feature whose entire job is reading somebody else's message
format.

Three properties matter more than the rest, and each has its own class:

- a genuine bump is recognised, in whatever language it arrives (`TestSuccess`)
- a cooldown reply never is — a reminder fired an hour early pings a channel for
  nothing (`TestFailure`)
- one directory's markers never fire on another's message (`TestCrossTalk`)
"""

import json
import pathlib
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bumpreminder import (
    BUMP_BOTS,
    MAX_INTERVAL,
    MIN_INTERVAL,
    bot_by_app_id,
    bot_by_key,
    detect,
    evaluate,
    format_interval,
    parse_interval,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")

PAYLOADS = json.loads((ROOT / "tests" / "data" / "bump_payloads.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Turning a captured payload back into something Message-shaped
# --------------------------------------------------------------------------- #
def _dt(raw):
    return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None


def _node(raw):
    """Components keep their raw key names — the harvester reads by duck typing."""
    if isinstance(raw, dict):
        return SimpleNamespace(**{k: _node(v) for k, v in raw.items()})
    if isinstance(raw, list):
        return [_node(v) for v in raw]
    return raw


def _embed(raw):
    return SimpleNamespace(
        title=raw.get("title"),
        description=raw.get("description"),
        url=raw.get("url"),
        timestamp=_dt(raw.get("timestamp")),
        author=_node(raw["author"]) if raw.get("author") else None,
        footer=_node(raw["footer"]) if raw.get("footer") else None,
        image=_node(raw["image"]) if raw.get("image") else None,
        thumbnail=_node(raw["thumbnail"]) if raw.get("thumbnail") else None,
        fields=[_node(f) for f in raw.get("fields") or ()],
    )


def message(payload, *, author_id=None, is_bot=True, channel_id=42, guild_id=7):
    author_id = int(author_id or payload["authorId"])
    interaction = payload.get("interaction")
    metadata = payload.get("interactionMetadata")
    return SimpleNamespace(
        id=int(payload["id"]),
        content=payload.get("content") or "",
        author=SimpleNamespace(id=author_id, bot=is_bot),
        application_id=int(payload.get("applicationId") or author_id),
        embeds=[_embed(e) for e in payload.get("embeds") or ()],
        components=[_node(c) for c in payload.get("components") or ()],
        attachments=[_node(a) for a in payload.get("attachments") or ()],
        channel=SimpleNamespace(id=channel_id),
        guild=SimpleNamespace(id=guild_id),
        interaction=SimpleNamespace(
            name=interaction["commandName"],
            user=SimpleNamespace(id=int(interaction["user"])),
        ) if interaction else None,
        interaction_metadata=SimpleNamespace(
            user=SimpleNamespace(id=int(metadata["user"])),
        ) if metadata else None,
    )


def sent_at(payload):
    return datetime.fromtimestamp(payload["createdTimestamp"] / 1000, tz=timezone.utc)


def embed_reply(key, text, image=None, timestamp=None):
    """A payload of ``key`` rewritten as a one-embed reply."""
    payload = json.loads(json.dumps(PAYLOADS[key]))
    payload["components"] = []
    embed = {"type": "rich", "description": text}
    if image:
        embed["image"] = {"url": image}
    if timestamp:
        embed["timestamp"] = timestamp
    payload["embeds"] = [embed]
    return payload


def v2_reply(key, text=None, media=None, custom_id=None):
    """A payload of ``key`` rewritten as a Components V2 reply."""
    payload = json.loads(json.dumps(PAYLOADS[key]))
    payload["embeds"] = []
    children = []
    if text:
        children.append({"type": 10, "content": text})
    if media:
        children.append({"type": 12, "items": [{"media": {"url": media}}]})
    if custom_id:
        children.append({"type": 1, "components": [
            {"type": 2, "style": 1, "label": "x", "custom_id": custom_id}]})
    payload["components"] = [{"type": 17, "components": children}]
    return payload


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #
class TestRegistry:

    def test_every_directory_is_uniquely_addressable(self):
        assert len({spec.key for spec in BUMP_BOTS}) == len(BUMP_BOTS)
        assert len({spec.app_id for spec in BUMP_BOTS}) == len(BUMP_BOTS)

    def test_a_payload_exists_for_every_directory(self):
        """A directory nobody captured a real reply for cannot be trusted."""
        assert {spec.key for spec in BUMP_BOTS} == set(PAYLOADS)

    @pytest.mark.parametrize("spec", BUMP_BOTS, ids=lambda s: s.key)
    def test_default_interval_is_in_range(self, spec):
        assert MIN_INTERVAL <= spec.default_interval <= MAX_INTERVAL

    @pytest.mark.parametrize("spec", BUMP_BOTS, ids=lambda s: s.key)
    def test_the_captured_command_is_declared(self, spec):
        """The registry's command names must cover what the payload actually used."""
        assert PAYLOADS[spec.key]["interaction"]["commandName"] in spec.command_names

    @pytest.mark.parametrize("spec", BUMP_BOTS, ids=lambda s: s.key)
    def test_every_directory_can_be_detected_at_all(self, spec):
        """A directory with no marker and no private-refusal flag is dead code."""
        assert (spec.refusal_is_ephemeral or spec.success_text
                or spec.success_media or spec.success_custom_id)

    def test_lookup_by_app_id(self):
        for spec in BUMP_BOTS:
            assert bot_by_app_id(spec.app_id) is spec
        assert bot_by_app_id(1) is None


# --------------------------------------------------------------------------- #
# A real bump is recognised
# --------------------------------------------------------------------------- #
class TestSuccess:

    @pytest.mark.parametrize("key", sorted(PAYLOADS), ids=sorted(PAYLOADS))
    def test_the_captured_reply_is_a_bump(self, key):
        spec = bot_by_key(key)
        payload = PAYLOADS[key]
        hit = detect(message(payload), spec.default_interval, now=sent_at(payload))
        assert hit is not None, f"{key}: the real reply was not recognised"
        assert hit.bot is spec

    @pytest.mark.parametrize("key", sorted(PAYLOADS), ids=sorted(PAYLOADS))
    def test_the_bumper_is_identified(self, key):
        payload = PAYLOADS[key]
        spec = bot_by_key(key)
        hit = detect(message(payload), spec.default_interval, now=sent_at(payload))
        assert hit.bumper_id == int(payload["interactionMetadata"]["user"])

    def test_the_bumper_survives_missing_interaction_data(self):
        """French.gg suffixes its own button with the bumper's id — our fallback."""
        payload = json.loads(json.dumps(PAYLOADS["frenchgg"]))
        expected = int(payload["interactionMetadata"]["user"])
        payload.pop("interactionMetadata")
        payload.pop("interaction")
        hit = detect(message(payload), 7200, now=sent_at(payload))
        assert hit is not None and hit.bumper_id == expected

    @pytest.mark.parametrize("key", sorted(PAYLOADS), ids=sorted(PAYLOADS))
    def test_a_non_bump_command_is_ignored(self, key):
        """Same bot, same channel, a different command: not our business."""
        payload = json.loads(json.dumps(PAYLOADS[key]))
        payload["interaction"]["commandName"] = "help"
        assert detect(message(payload), 7200, now=sent_at(payload)) is None

    def test_a_human_saying_bump_is_ignored(self):
        payload = dict(PAYLOADS["disboard"], content="allez tout le monde, /bump !", embeds=[])
        assert detect(message(payload, is_bot=False), 7200, now=sent_at(payload)) is None

    def test_an_unknown_bot_is_ignored(self):
        payload = dict(PAYLOADS["disboard"], authorId="999999999999999999")
        assert detect(message(payload), 7200, now=sent_at(payload)) is None

    @pytest.mark.parametrize("text", [
        "表示順をアップしたよ :) DISBOARDでサーバーをチェックしてね",
        "범프 완료! DISBOARD에서 서버를 확인하세요.",
        "Сервер поднят! Посмотрите на DISBOARD.",
        "Bump tamamlandı! Sunucuyu DISBOARD'da gör.",
    ], ids=["ja", "ko", "ru", "tr"])
    def test_disboard_is_language_proof(self, text):
        """DISBOARD refuses privately, so anything visible went through.

        That is what lets it work in languages no phrase list here covers —
        see ``BumpBot.refusal_is_ephemeral``.
        """
        payload = embed_reply("disboard", text)
        assert detect(message(payload), 7200, now=sent_at(payload)) is not None


# --------------------------------------------------------------------------- #
# A refusal never is
# --------------------------------------------------------------------------- #
class TestFailure:
    """A reminder armed off a failed bump pings a channel for nothing.

    So every one of these must come back ``None``. Each is a plausible cooldown
    reply in one of the two languages these directories actually answer in.
    """

    CASES = [
        ("disboard", embed_reply(
            "disboard", "Veuillez attendre encore 47 minutes.",
            image="https://disboard.org/images/bot-command-image-notification.png")),
        ("dsmonitoring", embed_reply("dsmonitoring", "You have already liked this server today.")),
        ("dsmonitoring", embed_reply("dsmonitoring", "Vous avez déjà aimé le serveur récemment.")),
        ("dinvites", v2_reply("dinvites", media="https://cdn.discordapp.com/a/b/cooldown.png?ex=1")),
        ("dinvites", v2_reply("dinvites", media="https://cdn.discordapp.com/a/b/error.png?ex=1")),
        ("dl", v2_reply("dl", text="❌ Tu dois attendre avant de bump à nouveau.")),
        ("dl", v2_reply("dl", text="You must wait before bumping again.")),
        ("beemp", embed_reply("beemp", "> Beemp already done, please wait a bit.")),
        ("beemp", embed_reply("beemp", "> Beemp impossible, vous devez attendre 12 minutes.")),
        ("dtop", v2_reply("dtop", text="Vous devez attendre avant le prochain boost.")),
        ("frenchgg", v2_reply("frenchgg", text="Vous avez déjà bump ce serveur, patientez 1h.")),
        ("frenchgg", v2_reply("frenchgg", text="You already bumped this server, please wait.")),
    ]

    @pytest.mark.parametrize("key,payload", CASES,
                             ids=[f"{k}-{i}" for i, (k, _) in enumerate(CASES)])
    def test_a_cooldown_reply_arms_nothing(self, key, payload):
        assert detect(message(payload), 7200, now=sent_at(payload)) is None

    def test_a_failure_marker_outranks_a_success_marker(self):
        """Success wording plus a failure asset is a message we do not understand.

        Staying quiet is the safe way to be wrong: a missed reminder costs one
        bump window, a false one pings the whole channel for nothing.
        """
        payload = v2_reply("dtop", text="# Boost envoyé",
                           media="https://cdn.discordapp.com/a/b/boost-error.png")
        assert detect(message(payload), 3600, now=sent_at(payload)) is None

    def test_disboard_still_needs_the_bump_command(self):
        """Visible-means-success applies to a /bump reply, not to anything DISBOARD posts.

        Strip the interaction data and the shortcut is withheld: an unmarked
        message from the directory could be an announcement, another command's
        reply, anything. It falls back to the ordinary markers.
        """
        import json as _json
        payload = _json.loads(_json.dumps(PAYLOADS["disboard"]))
        payload["embeds"] = [{"type": "rich", "description": "Merci d'utiliser DISBOARD !"}]
        payload.pop("interaction")
        payload.pop("interactionMetadata")
        assert detect(message(payload), 7200, now=sent_at(payload)) is None

        # …but a real success phrase still gets through without it.
        payload["embeds"] = [{"type": "rich", "description": "Bump effectué !"}]
        assert detect(message(payload), 7200, now=sent_at(payload)) is not None

    def test_a_private_refusal_directory_still_honours_its_own_markers(self):
        """DISBOARD skips the shared blocklist, not its own failure asset."""
        payload = embed_reply(
            "disboard", "Bump effectué !",
            image="https://disboard.org/images/bot-command-image-notification.png")
        assert detect(message(payload), 7200, now=sent_at(payload)) is None


# --------------------------------------------------------------------------- #
# Directories are never confused for one another
# --------------------------------------------------------------------------- #
class TestCrossTalk:

    MARKER_BASED = [spec for spec in BUMP_BOTS if not spec.refusal_is_ephemeral]

    @pytest.mark.parametrize("spec", MARKER_BASED, ids=lambda s: s.key)
    def test_markers_do_not_fire_on_another_directory(self, spec):
        """Every other directory's real reply, through this one's markers.

        In production the author id already decides, so this can never happen —
        which is exactly why it is worth asserting: it keeps the markers
        *specific* rather than letting them rot into "contains the word bump".
        DISBOARD is excluded because its detection is deliberately not
        marker-based (``refusal_is_ephemeral``).
        """
        for key, payload in PAYLOADS.items():
            if key == spec.key:
                continue
            hit = evaluate(message(payload), spec, spec.default_interval,
                           now=sent_at(payload))
            assert hit is None, f"{spec.key} markers matched {key}'s reply"


# --------------------------------------------------------------------------- #
# When the next bump is owed
# --------------------------------------------------------------------------- #
class TestNextDue:

    def test_the_configured_interval_is_the_default(self):
        payload = PAYLOADS["dl"]
        now = sent_at(payload)
        hit = detect(message(payload), 5400, now=now)
        assert not hit.stated_by_bot
        assert hit.due_at == now + timedelta(seconds=5400)

    def test_a_directory_stating_its_own_cooldown_wins(self):
        """DiscordTop prints "next boost <t:…>" — it is the authority, not us."""
        payload = PAYLOADS["dtop"]
        hit = detect(message(payload), 99999, now=sent_at(payload))
        assert hit.stated_by_bot
        assert hit.due_at == datetime.fromtimestamp(1788434578, tz=timezone.utc)

    def test_an_embed_timestamp_can_state_it_too(self):
        """DSMonitoring stamps the embed with the next like, four hours out."""
        payload = PAYLOADS["dsmonitoring"]
        hit = detect(message(payload), 99999, now=sent_at(payload))
        assert hit.stated_by_bot
        assert hit.due_at == datetime.fromisoformat("2026-09-03T14:26:35+00:00")

    def test_a_timestamp_meaning_now_is_rejected(self):
        """DiscordL's footer stamp is the *current* time, not the next bump.

        Believing it would schedule a reminder three seconds out. This is the
        freshness window earning its place.
        """
        payload = PAYLOADS["dl"]
        now = sent_at(payload)
        hit = detect(message(payload), 3600, now=now)
        assert not hit.stated_by_bot
        assert hit.due_at == now + timedelta(seconds=3600)

    def test_a_stated_time_beyond_a_day_is_rejected(self):
        payload = v2_reply("dtop", text="Prochain boost <t:1900000000:R>.",
                           media="https://cdn.discordapp.com/a/b/boost-success.png")
        now = sent_at(payload)
        hit = detect(message(payload), 3600, now=now)
        assert hit is not None and not hit.stated_by_bot
        assert hit.due_at == now + timedelta(seconds=3600)


# --------------------------------------------------------------------------- #
# Intervals as a human types them
# --------------------------------------------------------------------------- #
class TestIntervalParsing:

    @pytest.mark.parametrize("raw,expected", [
        ("2h", 7200), ("2H", 7200), ("2h30", 9000), ("1h05", 3900),
        ("90m", 5400), ("90 min", 5400), ("120", 7200), ("5m", 300),
        (" 2h ", 7200), ("24h", 86400),
    ])
    def test_readable_forms(self, raw, expected):
        assert parse_interval(raw) == expected

    @pytest.mark.parametrize("raw", [
        "", "   ", "0", "4m", "25h", "abc", "2j", "-2h", "2h70x", None,
    ])
    def test_unreadable_or_out_of_range_is_none(self, raw):
        """One return value to check: unreadable and out-of-range are both None."""
        assert parse_interval(raw) is None

    @pytest.mark.parametrize("seconds,text", [
        (7200, "2h"), (5400, "1h30"), (3600, "1h"), (300, "5m"), (86400, "24h"),
    ])
    def test_formatting_round_trips(self, seconds, text):
        assert format_interval(seconds) == text
        assert parse_interval(text) == seconds


# --------------------------------------------------------------------------- #
# Config normalization and validation
# --------------------------------------------------------------------------- #
class TestConfig:

    def test_an_empty_config_is_valid(self):
        from modules.bump_reminder import normalize_config
        assert normalize_config(None) == {"version": 1, "reminders": []}
        assert normalize_config({}) == {"version": 1, "reminders": []}

    def test_missing_keys_are_filled_from_the_directory(self):
        from modules.bump_reminder import normalize_config
        entry = normalize_config({"reminders": [{"bot": "disboard", "channel_id": 5}]})["reminders"][0]
        assert entry["interval"] == bot_by_key("disboard").default_interval
        assert entry["ping_mode"] == "button"
        assert entry["role_ids"] == []
        assert entry["enabled"] is True
        assert entry["id"].startswith("br_")

    def test_a_retired_directory_is_dropped_not_raised(self):
        """A listing Moddy stops supporting must not brick the whole panel."""
        from modules.bump_reminder import normalize_config
        config = normalize_config({"reminders": [
            {"bot": "disboard", "channel_id": 5},
            {"bot": "some_dead_listing", "channel_id": 6},
        ]})
        assert [e["bot"] for e in config["reminders"]] == ["disboard"]

    def test_an_absurd_interval_falls_back_to_the_default(self):
        from modules.bump_reminder import normalize_config
        entry = normalize_config({"reminders": [
            {"bot": "dtop", "channel_id": 5, "interval": 3},
        ]})["reminders"][0]
        assert entry["interval"] == bot_by_key("dtop").default_interval

    def test_several_reminders_may_share_one_directory(self):
        """Premium buys extra channels for the same listing, not extra listings."""
        from modules.bump_reminder import count_by_bot, normalize_config
        config = normalize_config({"reminders": [
            {"id": "br_1", "bot": "disboard", "channel_id": 5},
            {"id": "br_2", "bot": "disboard", "channel_id": 6},
        ]})
        assert len(config["reminders"]) == 2
        assert count_by_bot(config["reminders"]) == {"disboard": 2}

    def test_quota_constants_are_ordered(self):
        from modules.bump_reminder import FREE_REMINDERS_PER_BOT, PREMIUM_REMINDERS_PER_BOT
        assert 1 <= FREE_REMINDERS_PER_BOT < PREMIUM_REMINDERS_PER_BOT


# --------------------------------------------------------------------------- #
# The module's own routing
# --------------------------------------------------------------------------- #
class TestWatching:

    def _module(self, entries):
        import asyncio
        from modules.bump_reminder import BumpReminderModule
        module = BumpReminderModule(SimpleNamespace(), 7)
        asyncio.run(module.load_config({"reminders": entries}))
        return module

    def test_a_bump_in_a_watched_channel_is_taken(self):
        module = self._module([{"bot": "disboard", "channel_id": 42}])
        import asyncio
        payload = PAYLOADS["disboard"]
        found = asyncio.run(module.on_message(message(payload, channel_id=42),
                                              bot_by_key("disboard")))
        assert found is not None and found["entry"]["bot"] == "disboard"

    def test_a_bump_elsewhere_is_left_alone(self):
        """The reminder belongs to a channel the server chose; Moddy answers there."""
        module = self._module([{"bot": "disboard", "channel_id": 42}])
        import asyncio
        payload = PAYLOADS["disboard"]
        assert asyncio.run(module.on_message(message(payload, channel_id=99),
                                             bot_by_key("disboard"))) is None

    def test_two_directories_can_share_one_channel(self):
        """A single #bump channel for every listing is the normal setup."""
        module = self._module([
            {"bot": "disboard", "channel_id": 42},
            {"bot": "dl", "channel_id": 42},
        ])
        import asyncio
        for key in ("disboard", "dl"):
            found = asyncio.run(module.on_message(
                message(PAYLOADS[key], channel_id=42), bot_by_key(key)))
            assert found is not None and found["entry"]["bot"] == key

    def test_one_bump_arms_every_channel_of_that_directory(self):
        """The cooldown is the server's, so all of its channels are owed a call."""
        module = self._module([
            {"bot": "disboard", "channel_id": 42},
            {"bot": "disboard", "channel_id": 43},
            {"bot": "dl", "channel_id": 44},
        ])
        assert len(module.entries_for_bot("disboard")) == 2
        assert len(module.entries_for_bot("dl")) == 1

    def test_a_paused_reminder_watches_nothing(self):
        module = self._module([{"bot": "disboard", "channel_id": 42, "enabled": False}])
        assert module.enabled is False
        assert module.entries_for_bot("disboard") == []


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #
def _block(locale):
    data = json.loads((ROOT / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
    return data["modules"]["bump_reminder"]


def _flatten(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flatten(value, f"{prefix}.{key}" if prefix else key)
    else:
        yield prefix


class TestTranslations:

    @pytest.mark.parametrize("locale", [l for l in LOCALES if l != "en-US"])
    def test_every_locale_has_the_same_keys(self, locale):
        reference = set(_flatten(_block("en-US")))
        translated = set(_flatten(_block(locale)))
        assert reference - translated == set(), f"locales/{locale}.json is missing keys"
        assert translated - reference == set(), f"locales/{locale}.json has keys English does not"

    @pytest.mark.parametrize("locale", LOCALES)
    def test_every_key_the_code_uses_resolves(self, locale):
        """A key that resolves to ``[the.key]`` ships that literal to a user."""
        from utils.i18n import i18n, t
        i18n.load_translations()

        pattern = re.compile(r"""['"](modules\.bump_reminder[a-z_.]*)['"]""")
        keys = set()
        for name in ("modules/bump_reminder.py",
                     "modules/configs/bump_reminder_config.py",
                     "utils/bump_views.py", "cogs/bump_reminder.py"):
            keys |= set(pattern.findall((ROOT / name).read_text(encoding="utf-8")))
        for mode in ("auto", "button", "never"):
            keys.add(f"modules.bump_reminder.ping.{mode}")
            keys.add(f"modules.bump_reminder.ping.{mode}_description")

        missing = [k for k in sorted(keys)
                   if (v := t(k, locale=locale)).startswith("[") and v.endswith("]")]
        assert not missing, f"locales/{locale}.json is missing: {missing}"

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_config_menu_can_name_the_module(self, locale):
        """`/config`'s dropdown reads modules.<id>.description and caps it at 100."""
        from utils.i18n import i18n, t
        i18n.load_translations()
        description = t("modules.bump_reminder.description", locale=locale)
        assert not description.startswith("[")
        assert description == description.strip()
        # Discord caps a SelectOption description at 100 characters. cogs/config.py
        # slices to fit, so going over does not raise — it silently truncates the
        # sentence mid-word in the module picker.
        assert len(description) <= 100, f"{locale}: {len(description)} chars"

    @pytest.mark.parametrize("locale", LOCALES)
    def test_modal_labels_fit_discords_limits(self, locale):
        """Modal V2: title <= 45, Label.text <= 45, Label.description <= 100."""
        from utils.i18n import i18n, t
        i18n.load_translations()
        block = _block(locale)
        assert len(block["modal"]["title"]) <= 45
        for field in ("bot", "channel", "roles", "ping", "interval"):
            assert len(block["modal"][f"{field}_label"]) <= 45, f"{locale}: {field}_label"


# --------------------------------------------------------------------------- #
# Components V2 constraints
# --------------------------------------------------------------------------- #
_CID_RE = re.compile(r"^moddy:[a-z0-9_]+:[a-z0-9_]+:[a-z0-9_]+(:.+)?$")


def _custom_ids(items, found):
    for item in items:
        custom_id = getattr(item, "custom_id", None)
        if isinstance(custom_id, str):
            found.append(custom_id)
        for attribute in ("children", "component"):
            nested = getattr(item, attribute, None)
            if nested is None:
                continue
            _custom_ids(nested if isinstance(nested, list) else [nested], found)
    return found


class TestComponents:

    def _modal(self, locale="fr"):
        from modules.configs.bump_reminder_config import BumpReminderModal

        async def _noop(*args, **kwargs):
            pass

        return BumpReminderModal(locale, bot_key="disboard", callback_func=_noop,
                                 channel_id=1, role_ids=[2], ping_mode="button",
                                 interval=7200, available=["dl"])

    def test_the_modal_fits_the_five_component_ceiling(self):
        assert len(self._modal().children) == 5

    def test_the_modal_serialises(self):
        """Catches an illegal component *shape* before Discord 400s on it."""
        assert len(self._modal().to_dict()["components"]) == 5

    def test_every_custom_id_is_namespaced_and_fits(self):
        from modules.configs.bump_reminder_config import (
            BumpReminderConfigView, ManageBumpReminderView)
        views = [BumpReminderConfigView(), ManageBumpReminderView(), self._modal()]
        found = []
        for view in views:
            _custom_ids(view.children, found)
        assert found
        for custom_id in found:
            assert _CID_RE.match(custom_id), custom_id
            assert len(custom_id) <= 100, custom_id

    def test_no_content_is_ever_sent_with_a_layout_view(self):
        """Discord 400s on ``content=`` alongside IS_COMPONENTS_V2.

        Which is why the reminder's mentions ride in a top-level TextDisplay
        instead — a real ping, in the same message, outside the container.
        """
        for name in ("cogs/bump_reminder.py", "utils/bump_views.py",
                     "modules/bump_reminder.py"):
            source = (ROOT / name).read_text(encoding="utf-8")

            # A raw Discord send may never carry both. (``send_channel`` is the
            # notification API — its ``content`` is a NotificationContent
            # payload, an unrelated thing that never reaches Discord's field.)
            for call in re.findall(r"(?<!_channel)\.send\((.*?)\n\s*\)", source, re.S):
                assert not ("view=" in call and "content=" in call), f"{name}: {call}"

            # And a notification's content is always that payload, never a
            # bare string somebody hoped Discord would render.
            for call in re.findall(r"send_channel\((.*?)\n\s*\)", source, re.S):
                assert not re.search(r"content=[\"']", call), f"{name}: {call}"

    def test_the_reminder_card_carries_its_mentions_outside_the_container(self):
        """The ping must sit at the view's top level, above the container.

        Inside the container it would read as part of the card; as a separate
        message it would be a ghost ping nobody can scroll back to.
        """
        import discord
        from utils.bump_views import build_reminder_card
        from utils.i18n import i18n
        i18n.load_translations()

        view = build_reminder_card(
            bot_by_key("disboard"), locale="fr", role_ids=[1234],
            bumper_id=5678, mention_bumper=True, elapsed=7200)
        first = view.children[0]
        assert isinstance(first, discord.ui.TextDisplay)
        assert "<@&1234>" in first.content and "<@5678>" in first.content
        assert isinstance(view.children[1], discord.ui.Container)

    def test_the_reminder_card_shows_the_last_bumper_without_pinging_them(self):
        """The "last bumped by" line renders a mention whatever the ping mode.

        It stays informative because notifying is decided by allowed_mentions,
        not by what the card draws — so a server that turned the bumper ping off
        still gets the credit line, and nobody gets buzzed.
        """
        from utils.bump_views import build_reminder_card
        from utils.i18n import i18n
        i18n.load_translations()

        view = build_reminder_card(
            bot_by_key("disboard"), locale="fr", role_ids=[1234],
            bumper_id=5678, mention_bumper=False, elapsed=7200)
        top = view.children[0].content
        assert "<@5678>" not in top, "the bumper must not be in the ping line"
        body = "\n".join(
            child.content for child in view.children[1].children
            if hasattr(child, "content"))
        assert "<@5678>" in body, "the credit line should still name them"

    def test_allowed_mentions_never_opens_roles_wholesale(self):
        """Only resolved role objects, so a deleted role simply drops out."""
        import discord
        from utils.bump_views import reminder_mentions

        alive = SimpleNamespace(id=1234)
        guild = SimpleNamespace(get_role=lambda rid: alive if rid == 1234 else None)
        roles, allowed = reminder_mentions(guild, [1234, 9999], None, False)

        assert roles == [alive]
        assert allowed.roles == [alive]
        assert allowed.everyone is False
        assert allowed.users == []

    def test_the_optin_button_only_appears_when_it_can_be_used(self):
        from utils.bump_views import BumpOptInButton, build_thanks_card
        from utils.i18n import i18n
        i18n.load_translations()

        due = datetime.now(timezone.utc) + timedelta(hours=2)

        def has_button(view):
            return any(
                isinstance(item, BumpOptInButton)
                for child in view.children
                for row in getattr(child, "children", [])
                for item in getattr(row, "children", [])
            )

        spec = bot_by_key("disboard")
        assert has_button(build_thanks_card(spec, 42, due, locale="fr", ping_mode="button"))
        assert not has_button(build_thanks_card(spec, 42, due, locale="fr", ping_mode="auto"))
        assert not has_button(build_thanks_card(spec, 42, due, locale="fr", ping_mode="never"))
        # Nobody to arm it for.
        assert not has_button(build_thanks_card(spec, None, due, locale="fr", ping_mode="button"))
