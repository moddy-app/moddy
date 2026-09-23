"""Member Applications — config, card rendering, service lifecycle, gateway, i18n.

No gateway, no database: the service runs against an in-memory stand-in for
the ``member_applications`` repository and a fake HTTP client answering the two
join request routes, so the properties that matter are checked on the real
code:

- an application reaches the channel **once**, however many paths see it
  (`TestIngest`);
- the first decision wins, and Moddy's own decision echoed back by the gateway
  never overwrites the moderator who clicked (`TestDecide`, `TestIngest`);
- a malformed list from Discord is "unknown", never "empty" — it must not
  close every pending card (`TestSync`);
- the card always fits Discord's 4000-character budget and never pings from
  an applicant's answer (`TestCard`).
"""

import json
import os
import pathlib
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

import discord  # noqa: E402

from db.repositories.member_applications import (  # noqa: E402
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_SUBMITTED,
    STATUS_WITHDRAWN,
)
from modules.member_applications import (  # noqa: E402
    MAX_PRESET_REASONS,
    MAX_PRESET_REASON_LENGTH,
    MODULE_ID,
    MemberApplicationsModule,
    normalize_reasons,
)
from services import member_application_service as svc  # noqa: E402
from utils import member_application_views as views  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")
GUILD_ID = 111111111111111111
BOT_ID = 999999999999999999
USER_ID = 222222222222222222
MOD_ID = 333333333333333333


@pytest.fixture(autouse=True, scope="module")
def _translations():
    from utils.i18n import i18n
    i18n.load_translations()


# --------------------------------------------------------------------------- #
# Stand-ins
# --------------------------------------------------------------------------- #
class FakeDB:
    """In-memory ``MemberApplicationRepository`` with the same contract."""

    pool = object()

    def __init__(self):
        self.rows = {}

    async def claim_member_application(self, request_id, guild_id, user_id, request, submitted_at):
        if request_id in self.rows:
            return None
        row = {
            "request_id": request_id, "guild_id": guild_id, "user_id": user_id,
            "status": STATUS_SUBMITTED, "request": request,
            "channel_id": None, "message_id": None, "reviewed_by": None,
            "reviewed_at": None, "rejection_reason": None, "decided_in": None,
            "submitted_at": submitted_at,
        }
        self.rows[request_id] = row
        return dict(row)

    async def get_member_application(self, request_id):
        row = self.rows.get(request_id)
        return dict(row) if row else None

    async def set_member_application_message(self, request_id, channel_id, message_id):
        self.rows[request_id].update(channel_id=channel_id, message_id=message_id)

    async def list_pending_member_applications(self, guild_id):
        return [dict(r) for r in self.rows.values()
                if r["guild_id"] == guild_id and r["status"] == STATUS_SUBMITTED]

    async def resolve_member_application(self, request_id, status, *, reviewed_by,
                                         rejection_reason, decided_in):
        row = self.rows.get(request_id)
        if row is None or row["status"] != STATUS_SUBMITTED:
            return None
        row.update(status=status, reviewed_by=reviewed_by, rejection_reason=rejection_reason,
                   decided_in=decided_in, reviewed_at=datetime.now(timezone.utc))
        return dict(row)

    async def count_member_applications(self, guild_id, user_id, *, exclude_request_id=None):
        counts = {}
        for r in self.rows.values():
            if r["guild_id"] == guild_id and r["user_id"] == user_id \
                    and r["request_id"] != exclude_request_id:
                counts[r["status"]] = counts.get(r["status"], 0) + 1
        return counts

    async def get_user(self, user_id):
        return {"attributes": {}, "data": {}}


class FakeHTTP:
    """Answers the two join request routes a bot token may call."""

    def __init__(self, lists=None, action_error=None):
        self.lists = lists or {}
        self.action_error = action_error
        self.calls = []

    async def request(self, route, **kwargs):
        self.calls.append((route.method, route.path, kwargs))
        if route.method == "GET":
            status = kwargs["params"]["status"]
            value = self.lists.get(status, [])
            if value is None:
                return {"total": 1}  # the malformed answer developers reported
            return {"guild_join_requests": value, "total": len(value)}
        if self.action_error is not None:
            raise self.action_error
        return {"id": "1", "application_status": kwargs["json"]["action"]}


class FakeNotifications:
    def __init__(self):
        self.sent = []

    async def send_channel(self, channel, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(delivered=True, message=SimpleNamespace(id=5000 + len(self.sent)),
                               error=None)


async def _loaded_module(**config):
    module = MemberApplicationsModule(None, GUILD_ID)
    base = module.get_default_config()
    base.update({"channel_id": 444, **config})
    await module.load_config(base)
    return module


def _bot(db=None, http=None, module=None):
    async def get_module_instance(guild_id, module_id):
        return module
    return SimpleNamespace(
        db=db or FakeDB(),
        http=http or FakeHTTP(),
        user=SimpleNamespace(id=BOT_ID),
        notifications=FakeNotifications(),
        module_manager=SimpleNamespace(get_module_instance=get_module_instance),
        stats=None,
    )


class FakeChannel(discord.TextChannel):
    """Passes the isinstance check without a real guild state."""

    def __init__(self):  # noqa: D401 — deliberately not calling super()
        self.id = 444
        self.edits = []

    def permissions_for(self, member):
        return SimpleNamespace(view_channel=True, send_messages=True)

    def get_partial_message(self, message_id):
        channel = self

        class _Msg:
            async def edit(self, **kwargs):
                channel.edits.append((message_id, kwargs))
        return _Msg()


def _guild(channel=None):
    channel = channel or FakeChannel()
    return SimpleNamespace(
        id=GUILD_ID, name="Test", me=SimpleNamespace(id=BOT_ID),
        get_channel=lambda cid: channel if cid == channel.id else None,
        get_role=lambda rid: None,
        features=[],
    ), channel


def _request(rid=1000, status=STATUS_SUBMITTED, **extra):
    return {
        "id": str(rid), "guild_id": str(GUILD_ID), "user_id": str(USER_ID),
        "application_status": status,
        "created_at": "2026-09-20T10:00:00.000000+00:00",
        "user": {"id": str(USER_ID), "username": "alice", "global_name": "Alice",
                 "avatar": None, "public_flags": 0},
        "form_responses": [
            {"field_type": "TERMS", "label": "Rules", "values": ["Be nice"], "response": True},
            {"field_type": "TEXT_INPUT", "label": "Age?", "response": "18"},
            {"field_type": "MULTIPLE_CHOICE", "label": "Found us via", "choices": ["Friend", "Ad"],
             "response": 1},
        ],
        **extra,
    }


@pytest.fixture(autouse=True)
def _server_language(monkeypatch):
    async def guild_locale(bot, guild):
        return "en-US"
    import utils.guild_language
    monkeypatch.setattr(utils.guild_language, "guild_locale", guild_locale)


# --------------------------------------------------------------------------- #
# Module config
# --------------------------------------------------------------------------- #
class TestConfig:

    async def test_configured_means_active(self):
        """No on/off switch: a stored channel is the module running."""
        module = MemberApplicationsModule(None, GUILD_ID)
        assert "enabled" not in module.get_default_config()
        await module.load_config({"channel_id": None})
        assert module.enabled is False
        await module.load_config({"channel_id": 444})
        assert module.enabled is True
        # A legacy "enabled": false left by an older build does not switch it off.
        await module.load_config({"enabled": False, "channel_id": 444})
        assert module.enabled is True

    def test_reasons_are_trimmed_deduplicated_and_capped(self):
        raw = ["  too   new ", "too new", "", "x" * 300] + [f"r{i}" for i in range(20)]
        reasons = normalize_reasons(raw)
        assert reasons[0] == "too new"
        assert reasons.count("too new") == 1
        assert len(reasons) == MAX_PRESET_REASONS
        assert all(len(r) <= MAX_PRESET_REASON_LENGTH for r in reasons)
        assert normalize_reasons("a\n\nb\n") == ["a", "b"]

    async def test_reviewers_are_kick_members_or_chosen_roles(self):
        module = await _loaded_module(reviewer_role_ids=[77])
        perms = lambda **kw: SimpleNamespace(administrator=False, kick_members=False, **{k: v for k, v in kw.items()})  # noqa: E731
        kicker = SimpleNamespace(guild_permissions=SimpleNamespace(administrator=False, kick_members=True), roles=[])
        reviewer = SimpleNamespace(guild_permissions=perms(), roles=[SimpleNamespace(id=77)])
        nobody = SimpleNamespace(guild_permissions=perms(), roles=[SimpleNamespace(id=78)])
        assert module.can_review(kicker)
        assert module.can_review(reviewer)
        assert not module.can_review(nobody)

    def test_never_asks_for_administrator(self):
        assert "administrator" not in MemberApplicationsModule.REQUIRED_BOT_PERMISSIONS
        assert MemberApplicationsModule.REQUIRED_BOT_PERMISSIONS == ["kick_members"]

    async def test_validation_requires_a_channel(self):
        module = MemberApplicationsModule(SimpleNamespace(get_guild=lambda gid: None), GUILD_ID)
        ok, error = await module.validate_config({"channel_id": None})
        assert not ok and error
        ok, _ = await module.validate_config({"channel_id": 444, "rejection_reasons": []})
        assert ok


# --------------------------------------------------------------------------- #
# Card rendering
# --------------------------------------------------------------------------- #
def _texts(view):
    out = []

    def walk(items):
        for item in items:
            if isinstance(item, discord.ui.TextDisplay):
                out.append(item.content)
            for attr in ("children",):
                nested = getattr(item, attr, None)
                if nested:
                    walk(nested)
            accessory = getattr(item, "accessory", None)
            if accessory is not None:
                walk([accessory])
    walk(view.children)
    return out


def _custom_ids(view):
    ids = []

    def walk(items):
        for item in items:
            cid = getattr(item, "custom_id", None)
            if isinstance(cid, str):
                ids.append(cid)
            nested = getattr(item, "children", None)
            if nested:
                walk(nested)
    walk(view.children)
    return ids


class TestCard:

    def test_answers_render_each_field_type(self):
        blocks = views.render_answers(_request()["form_responses"], "en-US")
        joined = "\n".join(blocks)
        assert "**Server rules:** Accepted" in joined
        assert "**Age?**\n> 18" in joined
        assert "> Ad" in joined  # choice index 1

    def test_unanswered_question_says_so(self):
        blocks = views.render_answers(
            [{"field_type": "PARAGRAPH", "label": "Why?", "response": ""}], "en-US")
        assert blocks == ["**Why?**\n-# No answer"]

    def test_answers_cannot_ping(self):
        blocks = views.render_answers(
            [{"field_type": "PARAGRAPH", "label": "Q", "response": "@everyone <@&1> hi"}], "en-US")
        assert "@everyone" not in blocks[0].replace("@​everyone", "")

    def test_long_forms_fit_the_budget(self):
        fields = [{"field_type": "PARAGRAPH", "label": f"Question {i}", "response": "x" * 1000}
                  for i in range(5)]
        total = len("\n\n".join(views.render_answers(fields, "en-US")))
        assert total <= views.ANSWERS_BUDGET + 200

    async def test_pending_card_has_both_buttons_and_fits(self):
        db = FakeDB()
        row = await db.claim_member_application(1000, GUILD_ID, USER_ID, _request(), None)
        guild, _ = _guild()
        view = await views.build_card(_bot(db), guild, row, locale="en-US",
                                      mention_role_ids=[55])
        ids = _custom_ids(view)
        assert "moddy:member_apps:card:approve:1000" in ids
        assert "moddy:member_apps:card:reject:1000" in ids
        texts = _texts(view)
        assert texts[0] == "<@&55>"
        assert sum(len(t) for t in texts) <= 4000

    async def test_buttons_sit_outside_the_container(self):
        db = FakeDB()
        row = await db.claim_member_application(1000, GUILD_ID, USER_ID, _request(), None)
        guild, _ = _guild()
        view = await views.build_card(_bot(db), guild, row, locale="en-US")
        container, buttons = view.children
        assert isinstance(container, discord.ui.Container)
        assert isinstance(buttons, discord.ui.ActionRow)
        assert not [c for c in _custom_ids(container) if ":card:" in c]

    async def test_information_is_labelled_lines_without_emojis(self):
        db = FakeDB()
        row = await db.claim_member_application(1000, GUILD_ID, USER_ID, _request(), None)
        guild, _ = _guild()
        texts = _texts(await views.build_card(_bot(db), guild, row, locale="fr"))
        identity = next(t for t in texts if t.startswith("**Membre :**"))
        labels = [line.split(":**")[0] + ":**" for line in identity.splitlines()]
        assert labels == ["**Membre :**", "**Nom affiché :**", "**Nom d'utilisateur :**",
                          "**ID :**", "**Créé le :**", "**Candidatures précédentes :**"]
        # Custom emojis only in the title; none on any information line.
        for text in texts:
            if not text.startswith("### "):
                assert "<:" not in text and "<a:" not in text, text

    async def test_no_separator_between_answers(self):
        db = FakeDB()
        row = await db.claim_member_application(1000, GUILD_ID, USER_ID, _request(), None)
        guild, _ = _guild()
        view = await views.build_card(_bot(db), guild, row, locale="en-US")
        container = view.children[0]
        answers = [c for c in container.children
                   if isinstance(c, discord.ui.TextDisplay) and c.content.startswith("**Answers**")]
        assert len(answers) == 1
        assert "**Age?**" in answers[0].content and "**Found us via**" in answers[0].content

    async def test_decided_card_has_no_buttons_and_names_the_reviewer(self):
        db = FakeDB()
        await db.claim_member_application(1000, GUILD_ID, USER_ID, _request(), None)
        row = await db.resolve_member_application(
            1000, STATUS_REJECTED, reviewed_by=MOD_ID, rejection_reason="Too new",
            decided_in="moddy")
        guild, _ = _guild()
        view = await views.build_card(_bot(db), guild, row, locale="en-US")
        assert not [c for c in _custom_ids(view) if ":card:" in c]
        text = "\n".join(_texts(view))
        assert f"<@{MOD_ID}>" in text and "`Too new`" in text

    async def test_history_counts_earlier_applications(self):
        db = FakeDB()
        await db.claim_member_application(1, GUILD_ID, USER_ID, _request(1), None)
        await db.resolve_member_application(1, STATUS_REJECTED, reviewed_by=None,
                                            rejection_reason=None, decided_in="moddy")
        row = await db.claim_member_application(2, GUILD_ID, USER_ID, _request(2), None)
        guild, _ = _guild()
        text = "\n".join(_texts(await views.build_card(_bot(db), guild, row, locale="en-US")))
        assert "**Earlier applications:** `1`, `1` rejected" in text

    @pytest.mark.parametrize("item_cls,action", [(views.ApproveButton, "approve"),
                                                 (views.RejectButton, "reject")])
    def test_buttons_match_their_own_template(self, item_cls, action):
        item = item_cls(1234567890123456789)
        assert re.fullmatch(item_cls.__discord_ui_compiled_template__, item.custom_id)
        assert item.custom_id == f"moddy:member_apps:card:{action}:1234567890123456789"


class TestRejectModal:

    def test_typed_reason_wins_over_preset(self):
        modal = views.RejectModal(1, locale="en-US", applicant="A", presets=["Too new"])
        modal.reason_input._value = "  Spam account  "
        modal.preset_select._values = ["0"]
        assert modal.chosen_reason() == "Spam account"

    def test_preset_used_when_nothing_typed(self):
        modal = views.RejectModal(1, locale="en-US", applicant="A", presets=["Too new", "Bot"])
        modal.reason_input._value = ""
        modal.preset_select._values = ["1"]
        assert modal.chosen_reason() == "Bot"

    def test_no_preset_select_without_presets(self):
        modal = views.RejectModal(1, locale="en-US", applicant="A", presets=[])
        assert modal.preset_select is None
        assert len(modal.children) == 2  # intro + reason; Modal V2 allows 5

    @pytest.mark.parametrize("locale", LOCALES)
    def test_modal_fits_discord_limits(self, locale):
        modal = views.RejectModal(1, locale=locale, applicant="A", presets=["x"])
        assert len(modal.title) <= 45
        assert len(modal.children) <= 5


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class TestIngest:

    async def test_one_card_however_many_times_it_is_seen(self):
        module = await _loaded_module()
        bot = _bot(module=module)
        service = svc.MemberApplicationService(bot)
        guild, _ = _guild()
        await service.ingest(guild, _request())
        await service.ingest(guild, _request())
        assert len(bot.notifications.sent) == 1
        assert bot.db.rows[1000]["message_id"] == 5001

    async def test_card_retried_when_the_first_post_failed(self):
        module = await _loaded_module()
        bot = _bot(module=module)
        service = svc.MemberApplicationService(bot)
        guild, _ = _guild()
        await bot.db.claim_member_application(1000, GUILD_ID, USER_ID, _request(), None)
        await service.ingest(guild, _request())
        assert len(bot.notifications.sent) == 1

    async def test_disabled_module_does_nothing(self):
        bot = _bot(module=None)
        guild, _ = _guild()
        await svc.MemberApplicationService(bot).ingest(guild, _request())
        assert bot.db.rows == {}

    async def test_decision_in_discord_closes_the_card(self):
        module = await _loaded_module()
        bot = _bot(module=module)
        service = svc.MemberApplicationService(bot)
        guild, channel = _guild()
        await service.ingest(guild, _request())
        await service.ingest(guild, _request(status=STATUS_APPROVED,
                                             actioned_by_user={"id": str(MOD_ID)}))
        row = bot.db.rows[1000]
        assert row["status"] == STATUS_APPROVED and row["reviewed_by"] == MOD_ID
        assert row["decided_in"] == "discord"
        assert channel.edits and channel.edits[0][0] == 5001

    async def test_moddy_own_decision_echo_is_ignored(self):
        module = await _loaded_module()
        bot = _bot(module=module)
        service = svc.MemberApplicationService(bot)
        guild, _ = _guild()
        await service.ingest(guild, _request())
        await service.ingest(guild, _request(status=STATUS_APPROVED,
                                             actioned_by_user={"id": str(BOT_ID)}))
        assert bot.db.rows[1000]["status"] == STATUS_SUBMITTED

    async def test_decided_request_never_seen_pending_posts_nothing(self):
        module = await _loaded_module()
        bot = _bot(module=module)
        guild, _ = _guild()
        await svc.MemberApplicationService(bot).ingest(guild, _request(status=STATUS_REJECTED))
        assert bot.notifications.sent == [] and bot.db.rows == {}

    def test_gateway_status_fills_a_partial_request(self):
        requests = svc.requests_from_gateway("update", {
            "guild_id": "1", "status": "SUBMITTED", "request": {"id": "5"}})
        assert requests[0]["application_status"] == "SUBMITTED"
        assert svc.requests_from_gateway("update", {"guild_id": "1"}) == []


def _http_error(cls, status):
    response = SimpleNamespace(status=status, reason="x")
    return cls(response, {"code": 0, "message": "x"})


class TestDecide:

    async def _pending(self, http=None):
        module = await _loaded_module()
        bot = _bot(module=module, http=http)
        guild, _ = _guild()
        await bot.db.claim_member_application(1000, GUILD_ID, USER_ID, _request(), None)
        return bot, guild, svc.MemberApplicationService(bot)

    async def test_reject_sends_reason_and_records_reviewer(self):
        bot, guild, service = await self._pending()
        error, row = await service.decide(guild, 1000, STATUS_REJECTED,
                                          reviewer_id=MOD_ID, rejection_reason="r" * 400)
        assert error is None
        method, path, kwargs = bot.http.calls[-1]
        assert method == "PATCH" and path == "/guilds/{guild_id}/requests/{request_id}"
        assert kwargs["json"]["action"] == STATUS_REJECTED
        assert len(kwargs["json"]["rejection_reason"]) == 160
        assert row["reviewed_by"] == MOD_ID and row["decided_in"] == "moddy"

    async def test_approve_never_sends_a_reason(self):
        bot, guild, service = await self._pending()
        await service.decide(guild, 1000, STATUS_APPROVED, reviewer_id=MOD_ID,
                             rejection_reason="ignored")
        assert "rejection_reason" not in bot.http.calls[-1][2]["json"]

    async def test_second_decision_loses(self):
        bot, guild, service = await self._pending()
        await service.decide(guild, 1000, STATUS_APPROVED, reviewer_id=MOD_ID)
        error, row = await service.decide(guild, 1000, STATUS_REJECTED, reviewer_id=1)
        assert error == svc.ERR_ALREADY and row["status"] == STATUS_APPROVED

    async def test_other_guild_cannot_decide(self):
        bot, _, service = await self._pending()
        other = SimpleNamespace(id=5)
        error, _ = await service.decide(other, 1000, STATUS_APPROVED, reviewer_id=MOD_ID)
        assert error == svc.ERR_NOT_FOUND

    @pytest.mark.parametrize("exc,expected,status", [
        (_http_error(discord.Forbidden, 403), svc.ERR_FORBIDDEN, STATUS_SUBMITTED),
        (_http_error(discord.NotFound, 404), svc.ERR_GONE, STATUS_WITHDRAWN),
        (_http_error(discord.HTTPException, 400), svc.ERR_DISCORD, STATUS_SUBMITTED),
    ])
    async def test_api_errors_map_to_reasons(self, exc, expected, status):
        bot, guild, service = await self._pending(http=FakeHTTP(action_error=exc))
        error, _ = await service.decide(guild, 1000, STATUS_APPROVED, reviewer_id=MOD_ID)
        assert error == expected
        assert bot.db.rows[1000]["status"] == status


class TestSync:

    async def test_new_applications_get_cards(self):
        module = await _loaded_module()
        bot = _bot(module=module, http=FakeHTTP({STATUS_SUBMITTED: [_request(1), _request(2)]}))
        guild, _ = _guild()
        await svc.MemberApplicationService(bot).sync_guild(guild)
        assert len(bot.notifications.sent) == 2

    async def test_malformed_list_closes_nothing(self):
        module = await _loaded_module()
        bot = _bot(module=module, http=FakeHTTP({STATUS_SUBMITTED: None}))
        guild, _ = _guild()
        await bot.db.claim_member_application(1, GUILD_ID, USER_ID, _request(1), None)
        await svc.MemberApplicationService(bot).sync_guild(guild)
        assert bot.db.rows[1]["status"] == STATUS_SUBMITTED

    async def test_orphans_are_resolved_or_withdrawn(self):
        module = await _loaded_module()
        http = FakeHTTP({
            STATUS_SUBMITTED: [],
            STATUS_APPROVED: [_request(1, status=STATUS_APPROVED,
                                       actioned_by_user={"id": str(MOD_ID)})],
            STATUS_REJECTED: [],
        })
        bot = _bot(module=module, http=http)
        guild, _ = _guild()
        for rid in (1, 2):
            await bot.db.claim_member_application(rid, GUILD_ID, USER_ID, _request(rid), None)
        await svc.MemberApplicationService(bot).sync_guild(guild)
        assert bot.db.rows[1]["status"] == STATUS_APPROVED
        assert bot.db.rows[2]["status"] == STATUS_WITHDRAWN

    async def test_list_paginates_with_after(self):
        page = [_request(i) for i in range(1, svc.PAGE_SIZE + 1)]

        class Paged(FakeHTTP):
            async def request(self, route, **kwargs):
                self.calls.append((route.method, route.path, kwargs))
                after = kwargs["params"].get("after")
                return {"guild_join_requests": page if after is None else [_request(500)]}

        http = Paged()
        requests, complete = await svc.list_join_requests(SimpleNamespace(http=http), GUILD_ID)
        assert complete and len(requests) == svc.PAGE_SIZE + 1
        assert http.calls[1][2]["params"]["after"] == svc.PAGE_SIZE


# --------------------------------------------------------------------------- #
# Gateway wiring
# --------------------------------------------------------------------------- #
class TestGateway:

    def test_parsers_installed_and_removed(self):
        from cogs.member_applications import GATEWAY_EVENTS, MemberApplications

        dispatched = []
        parsers = {"MESSAGE_CREATE": object()}
        bot = SimpleNamespace(_connection=SimpleNamespace(parsers=parsers),
                              dispatch=lambda *a: dispatched.append(a))
        cog = MemberApplications.__new__(MemberApplications)
        cog.bot = bot
        cog._installed_parsers = {}
        cog._install_parsers()
        assert set(GATEWAY_EVENTS) <= set(parsers)

        parsers["GUILD_JOIN_REQUEST_UPDATE"]({"guild_id": "1"})
        assert dispatched == [("join_request_event", "update", {"guild_id": "1"})]

        cog._remove_parsers()
        assert set(parsers) == {"MESSAGE_CREATE"}

    def test_a_native_parser_is_never_replaced(self):
        from cogs.member_applications import MemberApplications

        native = object()
        parsers = {"GUILD_JOIN_REQUEST_CREATE": native}
        cog = MemberApplications.__new__(MemberApplications)
        cog.bot = SimpleNamespace(_connection=SimpleNamespace(parsers=parsers), dispatch=None)
        cog._installed_parsers = {}
        cog._install_parsers()
        cog._remove_parsers()
        assert parsers["GUILD_JOIN_REQUEST_CREATE"] is native


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #
def _block(locale):
    data = json.loads((ROOT / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
    return data["modules"][MODULE_ID]


def _flatten(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flatten(value, f"{prefix}.{key}" if prefix else key)
    else:
        yield prefix


_SOURCES = ("modules/member_applications.py", "modules/configs/member_applications_config.py",
            "utils/member_application_views.py", "services/member_application_service.py",
            "cogs/member_applications.py")


class TestTranslations:

    @pytest.mark.parametrize("locale", [l for l in LOCALES if l != "en-US"])
    def test_every_locale_has_the_same_keys(self, locale):
        reference = set(_flatten(_block("en-US")))
        assert set(_flatten(_block(locale))) == reference

    @pytest.mark.parametrize("locale", LOCALES)
    def test_every_key_the_code_uses_resolves(self, locale):
        from utils.i18n import t

        literal = re.compile(r"""['"](modules\.member_applications\.[a-z_.]+[a-z_])['"]""")
        # A key followed by ".{" is a dynamic prefix — covered explicitly below.
        prefixed = re.compile(r"""\{_P\}\.([a-z_.]+[a-z_])(?![a-z_]|\.\{)""")
        card_prefixed = re.compile(r"""\{_C\}\.([a-z_.]+[a-z_])(?![a-z_]|\.\{)""")
        keys = set()
        for name in _SOURCES:
            source = (ROOT / name).read_text(encoding="utf-8")
            keys |= set(literal.findall(source))
            keys |= {f"modules.member_applications.config.{k}" for k in prefixed.findall(source)}
            keys |= {f"modules.member_applications.card.{k}" for k in card_prefixed.findall(source)}
        # `_P` / `_C` themselves — key prefixes, not keys.
        keys.discard("modules.member_applications.config")
        keys.discard("modules.member_applications.card")
        # Built dynamically in the code.
        for label in ("member", "display_name", "username", "id", "created", "history",
                      "terms", "status", "reviewer", "decided_at", "via", "reason"):
            keys.add(f"modules.member_applications.card.fields.{label}")
        for key in ("terms_accepted", "terms_refused"):
            keys.add(f"modules.member_applications.card.values.{key}")
        for key in ("pending", "approved", "rejected", "withdrawn"):
            keys.add(f"modules.member_applications.card.status.{key}")
        for code in (svc.ERR_NOT_FOUND, svc.ERR_ALREADY, svc.ERR_FORBIDDEN,
                     svc.ERR_GONE, svc.ERR_DISCORD):
            keys.add(f"modules.member_applications.errors.{code}")

        assert len(keys) > 40
        missing = [k for k in sorted(keys)
                   if (v := t(k, locale=locale)).startswith("[") and v.endswith("]")]
        assert not missing, f"locales/{locale}.json is missing: {missing}"

    @pytest.mark.parametrize("locale", LOCALES)
    def test_config_menu_description_fits(self, locale):
        description = _block(locale)["description"]
        assert description == description.strip() and len(description) <= 100

    @pytest.mark.parametrize("locale", LOCALES)
    def test_modal_labels_fit(self, locale):
        block = _block(locale)
        reject = block["reject_modal"]
        assert len(reject["title"]) <= 45
        for field in ("preset", "reason"):
            assert len(reject[f"{field}_label"]) <= 45
            assert len(reject[f"{field}_description"]) <= 100
        assert len(reject["reason_placeholder"]) <= 100
        reasons = block["config"]["reasons"]
        assert len(reasons["modal_title"]) <= 45
        assert len(reasons["modal_label"]) <= 45
        assert len(reasons["modal_description"]) <= 100
        assert len(reasons["modal_placeholder"]) <= 100

    @pytest.mark.parametrize("locale", LOCALES)
    def test_button_labels_fit(self, locale):
        card = _block(locale)["card"]
        assert len(card["approve"]) <= 80 and len(card["reject"]) <= 80


# --------------------------------------------------------------------------- #
# /config panel: the draft lives in the message
# --------------------------------------------------------------------------- #
def _as_message(view):
    """What Discord sends back as ``interaction.message.components``."""
    from discord.components import _component_factory
    return SimpleNamespace(components=[_component_factory(p) for p in view.to_components()])


class TestConfigDraft:
    """Save must write what the panel shows, whichever process answers."""

    def _panel(self, config):
        from modules.configs.member_applications_config import MemberApplicationsConfigView
        bot = SimpleNamespace(get_guild=lambda gid: None, get_channel=lambda cid: None)
        return MemberApplicationsConfigView(bot, GUILD_ID, 1, "fr", current_config=config)

    def test_unsaved_changes_round_trip_through_the_message(self):
        from modules.configs.member_applications_config import draft_from_message
        view = self._panel({"channel_id": 444})
        view.working_config = {
            "channel_id": 555, "ping_role_ids": [1, 2], "reviewer_role_ids": [3],
            "rejection_reasons": ["Compte trop récent", "dyvion_ *pas* d'accord"],
        }
        view.has_changes = True
        view._build_view()
        assert draft_from_message(_as_message(view)) == view.working_config

    def test_empty_selections_and_no_reasons(self):
        from modules.configs.member_applications_config import draft_from_message
        view = self._panel(None)
        assert draft_from_message(_as_message(view)) == {
            "channel_id": None, "ping_role_ids": [], "reviewer_role_ids": [],
            "rejection_reasons": [],
        }

    def test_another_message_is_not_mistaken_for_the_panel(self):
        from modules.configs.member_applications_config import draft_from_message
        assert draft_from_message(SimpleNamespace(components=[])) is None
        assert draft_from_message(None) is None

    async def test_a_shell_saves_the_draft_not_the_stored_config(self):
        """The reported bug: 'OK' on save, nothing changed."""
        from modules.configs.member_applications_config import MemberApplicationsConfigView
        view = self._panel({"channel_id": 444})
        view.working_config = {"channel_id": 555, "ping_role_ids": [], "reviewer_role_ids": [],
                               "rejection_reasons": []}
        view._build_view()
        shell = MemberApplicationsConfigView()  # what a restarted / other process has
        interaction = SimpleNamespace(message=_as_message(view), guild_id=GUILD_ID)
        draft = await shell._fresh_working_config(interaction)
        assert draft["channel_id"] == 555

    def test_reasons_never_hold_a_backtick(self):
        assert normalize_reasons(["no `code` here"]) == ["no 'code' here"]


class TestInlineCode:

    def test_username_is_shown_exactly(self):
        from modules.member_applications import inline_code
        assert inline_code("dyvion_") == "`dyvion_`"
        assert inline_code("a`b") == "`aˋb`"

    def test_card_username_has_no_escape(self):
        lines = views.identity_lines({"username": "dyvion_"}, USER_ID, "**Dyvion**", {}, "fr")
        assert "**Nom d'utilisateur :** `dyvion_`" in lines
