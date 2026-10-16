"""Multi-action policy: vocabulary, conflicts, exact timeouts, schema migration,
barème integration and the approval helpers (all pure)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from automod import actions as act
from automod import approvals
from automod import bareme as b
from automod import infractions as inf
from automod.schemas import Decision, ImageMeta

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class V:
    def __init__(self, cat, grav="moyenne", conf="high"):
        self.categorie, self.gravite, self.confiance = cat, grav, conf


def calc(v, sanctions=None, **kw):
    cfg = b.ConfigBareme(sanctions=sanctions or {}, max_action=kw.pop("max_action", "ban"))
    kw.setdefault("severite_guild", 3)
    return b.calculer(v, kw.pop("history", []), b.MembreInfo(anciennete_jours=30),
                      config=cfg, now=NOW, **kw)


# -- conflict rules ------------------------------------------------------------

@pytest.mark.parametrize("wanted,clean", [
    (["kick", "ban"], ["ban"]),
    (["ban", "timeout", "warn", "delete"], ["delete", "ban"]),
    (["kick", "timeout", "warn"], ["kick"]),
    (["timeout", "warn", "delete"], ["delete", "warn", "timeout"]),
    ([], ["delete"]),
    (["nope"], ["delete"]),
    (["ban", "ban"], ["ban"]),
])
def test_conflicts_are_resolved(wanted, clean):
    assert act.resolve_conflicts(wanted)[0] == clean


def test_dropped_actions_are_reported():
    assert act.resolve_conflicts(["kick", "ban", "warn"])[1] == ["kick", "warn"]


def test_validate_actions():
    assert act.validate_actions(["delete", "ban", "kick"]) is None   # resolved on load
    assert act.validate_actions("ban") == "sanctions_actions_invalid"
    assert act.validate_actions([1]) == "sanctions_actions_invalid"
    assert act.validate_actions(["explode"]) == "sanctions_action_unknown"


def test_internal_stored_roundtrip():
    stored = ["delete", "warn", "timeout", "ban"]
    assert act.to_internal(stored) == ["supprimer", "warn", "mute", "ban"]
    assert act.to_stored(act.to_internal(stored)) == stored


# -- timeout parsing / formatting ---------------------------------------------------

@pytest.mark.parametrize("text,seconds", [
    ("16h53", 16 * 3600 + 53 * 60),
    ("16h 53m", 16 * 3600 + 53 * 60),
    ("1d2h", 86400 + 7200),
    ("90m", 5400),
    ("90", 5400),                       # a lone number is minutes
    ("1h30", 5400),
    ("2 jours 3 heures", 2 * 86400 + 3 * 3600),
    ("1 day, 2 hours and 5 minutes", 86400 + 7200 + 300),
    ("45s", 45),
    ("28d", 28 * 86400),
    (" 10 MIN ", 600),
])
def test_parse_duration(text, seconds):
    assert act.parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "1h1h", "h", "10 parsecs", None, 5, "1m 2h x"])
def test_parse_duration_rejects_garbage(text):
    assert act.parse_duration(text) is None


def test_timeout_bounds():
    assert act.validate_timeout_seconds(act.TIMEOUT_MIN_SECONDS) is None
    assert act.validate_timeout_seconds(act.TIMEOUT_MAX_SECONDS) is None
    assert act.validate_timeout_seconds(59) == "sanctions_timeout_range"
    assert act.validate_timeout_seconds(28 * 86400 + 1) == "sanctions_timeout_range"
    assert act.validate_timeout_seconds(True) == "sanctions_timeout_invalid"
    assert act.validate_timeout_seconds("60") == "sanctions_timeout_invalid"
    assert act.coerce_timeout_seconds(10 ** 9) == act.TIMEOUT_MAX_SECONDS
    assert act.coerce_timeout_seconds(5) == act.TIMEOUT_MIN_SECONDS
    assert act.coerce_timeout_seconds("x") == act.DEFAULT_TIMEOUT_SECONDS


@pytest.mark.parametrize("seconds,locale,text", [
    (16 * 3600 + 53 * 60, "fr", "16 h 53 min"),
    (86400 + 7200, "fr", "1 j 2 h"),
    (86400 + 7200, "en-US", "1 d 2 h"),
    (28 * 86400, "de", "28 T"),
    (90, "en-US", "1 min 30 s"),
    (45, "fr", "45 s"),
    (3600, "xx", "1 h"),
])
def test_format_duration(seconds, locale, text):
    assert act.format_duration(seconds, locale) == text


def test_format_then_parse_is_exact():
    for secs in (60, 3660, 16 * 3600 + 53 * 60, 86400 * 3 + 61):
        assert act.parse_duration(act.format_duration(secs, "en-US")) == secs


# -- stored schema: migration, normalisation, validation ---------------------------

def test_legacy_crans_are_migrated_without_losing_gravity_behaviour():
    legacy = {"insulte": {"mode": "perso", "crans": {"basse": 0, "moyenne": 2, "haute": 4, "critique": 7}}}
    out = inf.normalize_sanctions(legacy)["insulte"]
    assert "crans" not in out
    assert out["actions"] == ["delete", "timeout"] and out["timeout_seconds"] == 7200
    assert out["by_gravity"]["basse"]["actions"] == ["delete"]
    assert out["by_gravity"]["critique"]["actions"] == ["delete", "ban"]
    # The barème gives exactly what the legacy ladder gave, per gravity.
    for grav, cran in (("basse", 0), ("moyenne", 2), ("haute", 4), ("critique", 7)):
        new = calc(V("insulte", grav), {"insulte": out}, severite_guild=3)
        old = calc(V("insulte", grav), legacy, severite_guild=3)
        assert (new.cran, new.actions, new.duree_secondes) == (old.cran, old.actions, old.duree_secondes)


def test_migration_is_idempotent_and_leaves_auto_alone():
    once = inf.normalize_sanctions({"insulte": {"mode": "perso", "crans": {"moyenne": 3}}})
    twice = inf.normalize_sanctions(once)
    assert once == twice
    auto = inf.normalize_sanctions({"menace": {"mode": "auto", "crans": {"haute": 0}}})["menace"]
    assert "actions" not in auto and auto["mode"] == "auto"


def test_normalize_perso_actions_and_timeout():
    out = inf.normalize_sanctions({"insulte": {
        "mode": "perso", "actions": ["kick", "ban", "timeout"], "timeout_seconds": 99999999}})["insulte"]
    assert out["actions"] == ["ban"] and out["timeout_seconds"] is None
    out = inf.normalize_sanctions({"insulte": {
        "mode": "perso", "actions": ["timeout", "warn"], "timeout_seconds": 10 ** 9}})["insulte"]
    assert out["actions"] == ["warn", "timeout"] and out["timeout_seconds"] == act.TIMEOUT_MAX_SECONDS
    out = inf.normalize_sanctions({"insulte": {"mode": "perso", "actions": ["timeout"]}})["insulte"]
    assert out["timeout_seconds"] == act.DEFAULT_TIMEOUT_SECONDS


def test_require_approval_only_where_it_means_something():
    n = inf.normalize_sanctions
    assert n({"insulte": {"require_approval": True}})["insulte"]["require_approval"] is True
    assert n({"profil_pseudo": {"require_approval": True}})["profil_pseudo"]["require_approval"] is False
    only_delete = n({"insulte": {"mode": "perso", "actions": ["delete"], "require_approval": True}})
    assert only_delete["insulte"]["require_approval"] is False
    assert n({"insulte": {}})["insulte"]["require_approval"] is False


@pytest.mark.parametrize("entry,code", [
    ({"actions": "ban"}, "sanctions_actions_invalid"),
    ({"actions": ["fly"]}, "sanctions_action_unknown"),
    ({"timeout_seconds": "1h"}, "sanctions_timeout_invalid"),
    ({"timeout_seconds": 5}, "sanctions_timeout_range"),
    ({"timeout_seconds": 28 * 86400 + 1}, "sanctions_timeout_range"),
    ({"require_approval": "yes"}, "sanctions_flag_invalid"),
    ({"by_gravity": []}, "sanctions_by_gravity_invalid"),
    ({"by_gravity": {"nope": {}}}, "sanctions_by_gravity_invalid"),
    ({"by_gravity": {"haute": {"actions": ["fly"]}}}, "sanctions_action_unknown"),
])
def test_validate_new_fields(entry, code):
    assert inf.validate_sanctions({"insulte": entry}) == code


def test_validate_accepts_conflicts_and_valid_plans():
    ok = {"insulte": {"mode": "perso", "actions": ["kick", "ban", "delete"],
                      "timeout_seconds": 60972, "require_approval": True}}
    assert inf.validate_sanctions(ok) is None


def test_effective_policy_returns_the_default_as_a_value():
    p = inf.effective_policy("menace")                 # never configured
    assert p.mode == "auto" and p.actions == ("delete", "timeout")
    assert p.timeout_seconds == 12 * 3600 and p.require_approval is False
    assert inf.effective_policy("arnaque_scam").actions == ("delete", "ban")
    cfg = {"insulte": {"mode": "perso", "actions": ["delete", "timeout"], "timeout_seconds": 60780}}
    q = inf.effective_policy("insulte", cfg["insulte"])
    assert q.mode == "perso" and q.timeout_seconds == 60780
    assert inf.effective_policy("insulte", {"enabled": False}).actions == ("delete",)
    assert inf.effective_policy("nope") is None


def test_set_entry_actions_clears_gravity_overrides():
    entry = {"mode": "perso", "actions": ["delete"], "by_gravity": {"haute": {"actions": ["ban"]}},
             "require_approval": True}
    new = inf.set_entry_actions(entry, ["timeout", "kick"], 3600)
    assert "by_gravity" not in new and new["actions"] == ["kick"] and new["timeout_seconds"] is None
    assert new["require_approval"] is True and entry["by_gravity"]      # input untouched


# -- barème integration ---------------------------------------------------------------

def _perso(actions, seconds=None, **extra):
    return {"insulte": {"mode": "perso", "actions": actions, "timeout_seconds": seconds,
                        "recidive": extra.pop("recidive", True), **extra}}


def test_exact_timeout_survives_the_barème():
    r = calc(V("insulte"), _perso(["delete", "warn", "timeout"], 16 * 3600 + 53 * 60))
    assert r.actions == ["supprimer", "warn", "mute"]
    assert r.duree_secondes == 16 * 3600 + 53 * 60 and r.duree_heures == 17
    assert r.cran == 4         # a 16 h 53 timeout sits on the 48 h rung


def test_multi_actions_floor_kick_and_ban():
    assert calc(V("insulte"), _perso(["delete", "kick"])).actions == ["supprimer", "kick"]
    assert calc(V("insulte"), _perso(["delete", "ban"])).actions == ["supprimer", "ban"]


def test_recidivism_escalates_the_chosen_set_along_the_ladder():
    history = [b.SanctionPassee("insulte", "critique", NOW - timedelta(days=1), "manuel")] * 3
    r = calc(V("insulte"), _perso(["delete", "warn", "timeout"], 3600), history=history)
    assert r.cran > 4 and "supprimer" in r.actions and "warn" in r.actions
    off = calc(V("insulte"), _perso(["delete", "warn", "timeout"], 3600, recidive=False),
               history=history)
    assert off.duree_secondes == 3600


def test_ceiling_and_confidence_lower_the_plan():
    r = calc(V("insulte", conf="low"), _perso(["delete", "ban"]))
    assert r.cran == 1 and set(r.actions) == {"supprimer", "warn"}
    r = calc(V("insulte"), _perso(["delete", "kick"]), max_action="mute")
    assert "kick" not in r.actions and "mute" in r.actions and r.duree_secondes == 28 * 86400


def test_require_approval_flag_follows_the_final_actions():
    s = _perso(["delete", "ban"], require_approval=True)
    assert calc(V("insulte"), s).require_approval is True
    s2 = {"insulte": {"require_approval": True}}                 # auto mode
    assert calc(V("insulte", "basse"), s2).require_approval is True     # cran 1 = warn
    assert calc(V("insulte"), {"insulte": {"require_approval": True, "enabled": False}}
                ).require_approval is False
    # Nothing to approve when the result is deletion only.
    r = calc(V("contenu_nsfw", "basse"), {"contenu_nsfw": {"require_approval": True}})
    assert r.actions == ["supprimer"] and r.require_approval is False


def test_unconfirmed_heavy_sanction_keeps_the_light_actions():
    r = calc(V("insulte"), _perso(["delete", "warn", "ban"]))
    capped = b.appliquer_non_confirme(r)
    assert capped.cran == 4 and "mute" in capped.actions and capped.duree_secondes == 48 * 3600


def test_auto_mode_exposes_exact_seconds():
    r = calc(V("menace", "moyenne"))
    assert r.duree_heures == 12 and r.duree_secondes == 12 * 3600


# -- approvals: snapshot, permissions, expiry --------------------------------------------

def _decision():
    return Decision(
        message_id="1", auteur_id="2", sanctionnable=True, actions=["supprimer", "mute"],
        categorie="insulte", gravite="haute", raison="r", explication="e", confiance="high",
        signal_source="regex", score_detecteur=0.9, duree_secondes=3600, citation="x",
        image=ImageMeta(phash="ab", copies=[[1, 2]]))


def test_payload_roundtrip():
    d = _decision()
    r = calc(V("insulte", "haute"), _perso(["delete", "timeout"], 3600, require_approval=True))
    payload = approvals.build_payload(d, r, {"message_id": "1", "content": "hi"},
                                      locale="fr", deleted=True, attachments={"image": "a.jpg"})
    import json
    payload = json.loads(json.dumps(payload))               # what PostgreSQL stores
    d2 = approvals.decision_from_payload(payload["decision"])
    assert d2 == d and d2.image.copies == [[1, 2]]
    r2 = approvals.bareme_from_payload(payload["bareme"])
    assert (r2.cran, r2.actions, r2.duree_secondes, r2.require_approval) == \
        (r.cran, r.actions, r.duree_secondes, True)
    assert approvals.sanction_actions_of(payload) == ["mute"]
    assert payload["attachments"] == {"image": "a.jpg"} and payload["deleted"] is True


def _perms(**kw):
    base = dict(administrator=False, ban_members=False, kick_members=False,
                moderate_members=False)
    return SimpleNamespace(**{**base, **kw})


def test_only_the_matching_permission_resolves():
    assert approvals.can_resolve(_perms(moderate_members=True), ["warn", "mute"])
    assert not approvals.can_resolve(_perms(moderate_members=True), ["mute", "ban"])
    assert approvals.can_resolve(_perms(ban_members=True), ["ban"])
    assert approvals.can_resolve(_perms(kick_members=True), ["kick"])
    assert not approvals.can_resolve(_perms(), ["warn"])
    assert not approvals.can_resolve(None, ["warn"])
    admin = _perms(); admin.administrator = True
    assert approvals.can_resolve(admin, ["ban"])


def test_expiry():
    now = NOW
    assert approvals.is_expired(now - timedelta(seconds=1), now)
    assert not approvals.is_expired(approvals.expiry(now), now)
    assert not approvals.is_expired(None, now)


def test_timeout_verified():
    until = NOW + timedelta(hours=1)
    assert act.timeout_verified(until, until)
    assert not act.timeout_verified(None, until)
    assert not act.timeout_verified(NOW, until)                # stale member


def test_summarize_never_reports_an_unexplained_miss_as_done():
    s = act.summarize_outcome(["supprimer", "mute"], ["supprimer"], {})
    assert s["applied"] == ["supprimer"] and s["failed"] == {"mute": act.FAIL_DISCORD}
    s = act.summarize_outcome(["ban"], [], {"ban": act.FAIL_HIERARCHY})
    assert s["failed"] == {"ban": act.FAIL_HIERARCHY}


def test_button_custom_id_template_matches_and_is_namespaced():
    from utils.automod_approval_views import ApprovalButton
    m = ApprovalButton.__discord_ui_compiled_template__.fullmatch("moddy:automod:approval:ok:42")
    assert m and m["verb"] == "ok" and m["aid"] == "42"
    assert ApprovalButton.__discord_ui_compiled_template__.fullmatch("moddy:automod:approval:x:1") is None
