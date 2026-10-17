"""Automod runtime: hierarchy protection, verified/truthful actions, case
reality and the moderator-approval flow (Discord and the database faked)."""

from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

import discord
import pytest

from automod import actions as act
from automod import approvals
from automod import bareme as ab
from automod.schemas import Decision
from modules.automod_ai import AutomodModule

pytestmark = pytest.mark.asyncio


class Perms(types.SimpleNamespace):
    def __getattr__(self, name):
        return False


class Member:
    def __init__(self, guild, uid=5, top=10, stale=False, error=None):
        self.id, self.guild, self.top_role = uid, guild, top
        self.display_name = "Alice"
        self.timed_out_until = None
        self._stale, self._error = stale, error
        self.edits = []

    def __eq__(self, other):
        return other is self

    def __hash__(self):
        return id(self)

    async def edit(self, **kw):
        if self._error:
            raise self._error
        self.edits.append(kw)
        if self._stale:
            return types.SimpleNamespace(timed_out_until=None)     # Discord ignored it
        return types.SimpleNamespace(timed_out_until=kw["timed_out_until"])


class Guild:
    id = 1
    owner_id = 9999
    name = "G"

    def __init__(self, perms=("moderate_members", "ban_members", "kick_members"), top=100,
                 ban_error=None):
        self.owner = types.SimpleNamespace(id=9999)
        self.me = types.SimpleNamespace(
            top_role=top, guild_permissions=Perms(**{p: True for p in perms}))
        self.bans, self.kicks, self._ban_error = [], [], ban_error
        self.members = {}

    def get_member(self, uid):
        return self.members.get(uid)

    async def ban(self, user, **kw):
        if self._ban_error:
            raise self._ban_error
        self.bans.append((user, kw))

    async def kick(self, user, **kw):
        self.kicks.append((user, kw))


class Cases:
    def __init__(self, fail=False):
        self.recorded, self.fail = [], fail

    async def record_sanction(self, *a, **kw):
        if self.fail:
            return None
        self.recorded.append(kw["action"].value)
        return {"id": "case1", "reference": "ABC123", "sanction_id": "s1"}


class DB:
    def __init__(self):
        self.sanctions, self.events, self.rows = [], [], {}

    async def add_sanction(self, case_id, action, *a, **kw):
        self.sanctions.append(action)

    async def add_event(self, *a, **kw):
        self.events.append((a, kw))

    async def create_automod_approval(self, **kw):
        self.rows[1] = {"id": 1, "status": "pending", **kw}
        return 1

    async def set_automod_approval_card(self, *a, **kw):
        pass

    async def set_automod_approval_result(self, aid, result):
        self.rows[aid]["result"] = result

    async def claim_automod_approval(self, aid, status, by):
        row = self.rows.get(aid)
        if not row or row["status"] != "pending":
            return None
        row["status"] = status
        return row


def make_module(guild, cases=None, db=None):
    bot = types.SimpleNamespace(
        user=types.SimpleNamespace(id=777, name="Moddy"), db=db or DB(),
        cases=cases, get_guild=lambda gid: guild, _moddy_initiated_sanctions={})
    mod = AutomodModule(bot, 1)
    return mod, bot


def decision(actions, seconds=3600, cat="insulte"):
    return Decision(
        message_id="10", auteur_id="5", sanctionnable=True, actions=list(actions),
        categorie=cat, gravite="moyenne", raison="rude", explication="why",
        confiance="high", signal_source="regex", score_detecteur=0.9,
        duree_secondes=seconds, citation="x")


# -- hierarchy ----------------------------------------------------------------------

async def test_protected_members():
    g = Guild(top=50)
    assert AutomodModule._is_protected(Member(g, top=50))            # equal role
    assert AutomodModule._is_protected(Member(g, top=80))            # higher role
    assert AutomodModule._is_protected(Member(g, uid=9999, top=1))   # the owner
    assert not AutomodModule._is_protected(Member(g, top=10))
    g.me = None
    assert AutomodModule._is_protected(Member(g, top=1))             # cannot even tell


async def test_protected_member_is_skipped_before_anything_happens():
    g = Guild(top=50)
    boss = Member(g, top=80)
    g.members[5] = boss
    mod, bot = make_module(g, Cases())
    mod.enabled = True
    posted = []

    async def boom(*a, **k):
        posted.append(a)

    mod._notify_channel = boom
    mod._execute_incident = boom
    msg = types.SimpleNamespace(guild=g, author=boss, channel=types.SimpleNamespace(id=2),
                                id=10, content="x", created_at=datetime.now(timezone.utc),
                                webhook_id=None)
    msg.author.bot = False
    await mod.on_message(msg)                       # skipped before the funnel
    await mod.apply_decision(msg, decision(["supprimer", "ban"]))   # and before applying
    assert posted == [] and bot.cases.recorded == []


# -- truthful, verified actions -------------------------------------------------------

async def test_timeout_is_applied_and_verified():
    g = Guild()
    m = Member(g)
    mod, _ = make_module(g)
    applied, failed = await mod._apply_discord_sanction(g, m, decision(["mute"], 16 * 3600 + 3180), None, ["mute"])
    assert applied == ["mute"] and failed == {}
    until = m.edits[0]["timed_out_until"]
    delta = until - datetime.now(timezone.utc)
    assert abs(delta - timedelta(seconds=16 * 3600 + 3180)) < timedelta(seconds=5)


async def test_a_timeout_discord_did_not_apply_is_not_reported_as_done():
    g = Guild()
    mod, _ = make_module(g)
    applied, failed = await mod._apply_discord_sanction(
        g, Member(g, stale=True), decision(["mute"]), None, ["mute"])
    assert applied == [] and failed == {"mute": act.FAIL_UNVERIFIED}


@pytest.mark.parametrize("kwargs,member_kw,code", [
    (dict(perms=("ban_members",)), {}, act.FAIL_PERMISSION),
    (dict(), {"error": discord.Forbidden(types.SimpleNamespace(status=403, reason=""), "no")},
     act.FAIL_HIERARCHY),
    (dict(), {"error": discord.HTTPException(types.SimpleNamespace(status=500, reason=""), "x")},
     act.FAIL_DISCORD),
])
async def test_failed_timeout_reports_why(kwargs, member_kw, code):
    g = Guild(**kwargs)
    mod, _ = make_module(g)
    applied, failed = await mod._apply_discord_sanction(
        g, Member(g, **member_kw), decision(["mute"]), None, ["mute"])
    assert applied == [] and failed == {"mute": code}


async def test_ban_supersedes_kick_and_timeout_and_absent_members_can_still_be_banned():
    g = Guild()
    mod, _ = make_module(g)
    m = Member(g)
    applied, failed = await mod._apply_discord_sanction(
        g, m, decision(["kick", "mute", "ban"]), None, ["kick", "mute", "ban"])
    assert applied == ["ban"] and g.kicks == [] and m.edits == []
    g2 = Guild()
    mod2, _ = make_module(g2)
    applied, _ = await mod2._apply_discord_sanction(
        g2, None, decision(["ban"]), None, ["ban"], user_id=5)
    assert applied == ["ban"] and g2.bans[0][0].id == 5
    applied, failed = await mod2._apply_discord_sanction(
        g2, None, decision(["mute"]), None, ["mute"], user_id=5)
    assert failed == {"mute": act.FAIL_ABSENT}


async def test_kick_and_failed_ban():
    g = Guild()
    mod, _ = make_module(g)
    m = Member(g)
    applied, _ = await mod._apply_discord_sanction(g, m, decision(["kick"]), None, ["kick"])
    assert applied == ["kick"] and g.kicks[0][0] is m
    g2 = Guild(ban_error=discord.Forbidden(types.SimpleNamespace(status=403, reason=""), "no"))
    mod2, _ = make_module(g2)
    applied, failed = await mod2._apply_discord_sanction(
        g2, Member(g2), decision(["ban"]), None, ["ban"])
    assert applied == [] and failed == {"ban": act.FAIL_HIERARCHY}


async def test_case_only_records_what_really_happened():
    g = Guild(perms=("ban_members",))            # cannot time out
    m = Member(g)
    cases = Cases()
    mod, bot = make_module(g, cases)
    d = decision(["warn", "mute"])
    bareme = ab.ResultatBareme(cran=3, actions=["warn", "mute"], duree_heures=1,
                               categorie="insulte", gravite="moyenne", points_recidive=0.0)
    out = await mod._execute_incident(g, m, d, bareme, {"content": "x"}, message=None,
                                      delete_message=False)
    assert out.applied == ["warn"]                          # only the warn is real
    assert out.failed == {"mute": act.FAIL_PERMISSION}
    assert cases.recorded == ["warn"] and out.case_ref == "ABC123"
    assert out.primary_action == "warn"


async def test_an_applied_kick_opens_a_case():
    g = Guild(perms=("kick_members",))
    m = Member(g)
    cases = Cases()
    mod, bot = make_module(g, cases)
    d = decision(["kick"])
    bareme = ab.ResultatBareme(cran=6, actions=["kick"], duree_heures=0,
                               categorie="insulte", gravite="haute", points_recidive=0.0)
    out = await mod._execute_incident(g, m, d, bareme, {"content": "x"}, message=None,
                                      delete_message=False)
    assert out.applied == ["kick"]
    assert cases.recorded == ["kick"] and out.case_ref == "ABC123"
    assert out.primary_action == "kick" and "case" not in out.failed


async def test_nothing_applied_means_no_case_and_no_dm_trigger():
    g = Guild(perms=())
    cases = Cases()
    mod, _ = make_module(g, cases)
    d = decision(["mute"])
    bareme = ab.ResultatBareme(cran=3, actions=["mute"], duree_heures=1,
                               categorie="insulte", gravite="moyenne", points_recidive=0.0)
    out = await mod._execute_incident(g, Member(g), d, bareme, {}, delete_message=False)
    assert out.applied == [] and out.case_id is None and out.primary_action is None
    assert cases.recorded == [] and out.failed == {"mute": act.FAIL_PERMISSION}


async def test_a_warn_is_not_announced_when_its_case_cannot_be_recorded():
    g = Guild()
    mod, _ = make_module(g, Cases(fail=True))
    bareme = ab.ResultatBareme(cran=1, actions=["warn"], duree_heures=0, categorie="insulte",
                               gravite="moyenne", points_recidive=0.0)
    out = await mod._execute_incident(g, Member(g), decision(["warn"]), bareme, {},
                                      delete_message=False)
    assert out.applied == [] and out.failed == {"warn": act.FAIL_CASE}


async def test_timeout_applied_but_case_failed_is_reported():
    g = Guild()
    mod, _ = make_module(g, Cases(fail=True))
    bareme = ab.ResultatBareme(cran=3, actions=["mute"], duree_heures=1, categorie="insulte",
                               gravite="moyenne", points_recidive=0.0)
    out = await mod._execute_incident(g, Member(g), decision(["mute"]), bareme, {},
                                      delete_message=False)
    assert out.applied == ["mute"] and out.failed == {"case": act.FAIL_CASE}


# -- the alert card tells the truth ---------------------------------------------------------

def _texts(view):
    out = []

    def walk(node):
        text = getattr(node, "content", None)
        if isinstance(text, str):
            out.append(text)
        for child in getattr(node, "children", []) or []:
            walk(child)
    for item in view.children:
        walk(item)
    return "\n".join(out)


async def test_card_lists_applied_and_failed_actions():
    from utils.automod_approval_views import render_alert_card
    d = decision(["supprimer", "mute", "ban"])
    outcome = approvals.IncidentOutcome(applied=["supprimer", "mute"],
                                        failed={"ban": act.FAIL_HIERARCHY},
                                        case_ref="ABC123", primary_action="mute")
    view = render_alert_card(locale="en-US", decision=d, bareme=None,
                             snapshot={"channel_id": 2, "message_id": "10", "content": "x", "ts": 1},
                             outcome=outcome, seconds=16 * 3600 + 3180)
    text = _texts(view)
    assert "Timeout (16 h 53 min)" in text
    assert "Not applied" in text and "Ban" in text and "role too high" in text
    # The failed ban is never part of the 'Actions' line.
    actions_line = next(l for l in text.splitlines() if l.startswith("- **Actions"))
    assert "Ban" not in actions_line


async def test_pending_card_has_persistent_buttons():
    from utils.automod_approval_views import render_alert_card
    view = render_alert_card(
        locale="en-US", decision=decision(["supprimer", "ban"]), bareme=None,
        snapshot={"channel_id": 2, "message_id": "10", "content": "x", "ts": 1},
        status="pending", planned=["supprimer", "ban"], approval_id=42)
    ids = []

    def walk(node):
        cid = getattr(node, "custom_id", None)
        if cid:
            ids.append(cid)
        for c in getattr(node, "children", []) or []:
            walk(c)
    for item in view.children:
        walk(item)
    assert ids == ["moddy:automod:approval:ok:42", "moddy:automod:approval:no:42"]
    assert "Waiting for a moderator" in _texts(view)


# -- approval flow --------------------------------------------------------------------------

def _row(guild_decision, bareme, status="approved"):
    payload = approvals.build_payload(
        guild_decision, bareme, {"channel_id": 2, "message_id": "10", "content": "rude",
                                 "author_name": "Alice", "ts": 1}, locale="en-US", deleted=True)
    return {"id": 1, "guild_id": 1, "user_id": 5, "status": status, "payload": payload}


async def test_approve_applies_the_stored_plan_and_updates_the_card(monkeypatch):
    g = Guild()
    m = Member(g)
    g.members[5] = m
    cases = Cases()
    mod, bot = make_module(g, cases)
    bot.db.rows[1] = {"id": 1}
    d = decision(["supprimer", "warn", "mute"])
    bareme = ab.ResultatBareme(cran=3, actions=d.actions, duree_heures=1, duree_secondes=3600,
                               categorie="insulte", gravite="moyenne", points_recidive=0.0,
                               require_approval=True)
    sent = []

    async def fake_dm(*a, **k):
        sent.append(a)

    mod._send_sanction_dm = fake_dm
    import modules.automod_ai as module_file

    async def fetch(guild, uid):
        return m
    monkeypatch.setattr(module_file, "get_or_fetch_member", fetch)
    view = await mod.apply_approval(_row(d, bareme), types.SimpleNamespace(id=321))
    assert m.edits and cases.recorded == ["mute"] and bot.db.sanctions == ["warn"]
    text = _texts(view)
    assert "Sanction approved" in text and "<@321>" in text
    assert bot.db.rows[1]["result"]["applied"] == ["supprimer", "warn", "mute"]
    assert len(sent) == 1                                # the member is told, once


async def test_approve_does_nothing_to_a_member_promoted_above_moddy(monkeypatch):
    g = Guild(top=50)
    boss = Member(g, top=90)
    mod, bot = make_module(g, Cases())
    bot.db.rows[1] = {"id": 1}
    import modules.automod_ai as module_file

    async def fetch(guild, uid):
        return boss
    monkeypatch.setattr(module_file, "get_or_fetch_member", fetch)
    d = decision(["ban"])
    bareme = ab.ResultatBareme(cran=7, actions=["ban"], duree_heures=0, categorie="insulte",
                               gravite="moyenne", points_recidive=0.0)
    view = await mod.apply_approval(_row(d, bareme), types.SimpleNamespace(id=321))
    assert g.bans == []
    assert "Not applied" in _texts(view) and "role too high" in _texts(view)


async def test_reject_applies_nothing():
    g = Guild()
    mod, bot = make_module(g, Cases())
    d = decision(["ban"])
    bareme = ab.ResultatBareme(cran=7, actions=["ban"], duree_heures=0, categorie="insulte",
                               gravite="moyenne", points_recidive=0.0)
    view = await mod.close_approval(_row(d, bareme, "rejected"), "rejected",
                                    types.SimpleNamespace(id=321))
    assert g.bans == [] and bot.cases.recorded == []
    assert "rejected" in _texts(view).lower()
    expired = await mod.close_approval(_row(d, bareme, "expired"), "expired", None)
    assert "Expired" in _texts(expired)


async def test_double_click_cannot_resolve_twice():
    db = DB()
    await db.create_automod_approval(guild_id=1, user_id=5, payload={}, expires_at=None)
    assert await db.claim_automod_approval(1, "approved", 1) is not None
    assert await db.claim_automod_approval(1, "rejected", 2) is None


async def test_approval_required_parks_the_sanction_and_applies_nothing(monkeypatch):
    g = Guild()
    m = Member(g)
    g.members[5] = m
    sent = []

    class Channel:
        id = 2

        def permissions_for(self, me):
            return types.SimpleNamespace(send_messages=True)

        async def send(self, **kw):
            sent.append(kw)
            return types.SimpleNamespace(id=99, channel=self)

    channel = Channel()
    g.get_channel = lambda cid: channel
    monkeypatch.setattr(discord, "TextChannel", Channel)
    cases = Cases()
    mod, bot = make_module(g, cases)
    mod.notify_channel_id = 2
    deleted = []

    class Msg:
        id = 10
        guild = g
        author = m
        content = "you are rude"
        jump_url = "u"
        created_at = datetime.now(timezone.utc)
        channel = types.SimpleNamespace(id=3)

        async def delete(self):
            deleted.append(True)

    async def no_context(message):
        return ""
    mod._build_context_text = no_context
    d = decision(["supprimer", "ban"])
    bareme = ab.ResultatBareme(cran=7, actions=d.actions, duree_heures=0,
                               categorie="insulte", gravite="moyenne", points_recidive=0.0,
                               require_approval=True)
    snapshot = mod._message_snapshot(Msg(), d)
    await mod._request_approval(Msg(), d, bareme, m, snapshot)
    assert deleted == [True]                       # the content goes at once
    assert g.bans == [] and cases.recorded == []   # the sanction waits
    assert bot.db.rows[1]["status"] == "pending"
    assert bot.db.rows[1]["payload"]["deleted"] is True
    assert len(sent) == 1 and "Waiting for a moderator" in _texts(sent[0]["view"]) \
        or "En attente" in _texts(sent[0]["view"])
