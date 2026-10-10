"""Tests for the infraction catalogue and the configurable sanctions (barème)."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from automod import bareme as b
from automod import constants as ac
from automod import infractions as inf

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

LEGACY_PLANCHER = {
    "insulte": (1, 1, 2, 3), "harcelement": (1, 2, 3, 5), "menace": (2, 3, 6, 7),
    "haine_discrimination": (2, 4, 6, 7), "incitation_automutilation": (3, 5, 7, 7),
    "harcelement_sexuel": (3, 5, 7, 7), "doxxing": (4, 6, 7, 7),
    "arnaque_scam": (7, 7, 7, 7), "violation_indications": (0, 1, 2, 4),
    "contenu_nsfw": (0, 2, 3, 5), "contenu_choquant": (0, 1, 2, 4),
}


@dataclass
class V:
    categorie: str = "insulte"
    gravite: str = "moyenne"
    confiance: str = "high"


def calc(v, *, anciennete=30.0, passees=(), sev=3, **cfg):
    return b.calculer(v, list(passees), b.MembreInfo(anciennete_jours=anciennete),
                      severite_guild=sev, config=b.ConfigBareme(**cfg), now=NOW)


def perso_cfg(cat, crans, **extra):
    return {cat: {"mode": "perso", "crans": crans, **extra}}


# -- catalogue ---------------------------------------------------------------

def test_legacy_floors_identical():
    for cat, floors in LEGACY_PLANCHER.items():
        for g, want in zip(inf.GRAVITES, floors):
            assert b.PLANCHER[(cat, g)] == want
            assert inf.INFRACTIONS[cat].plancher[g] == want
    assert len(b.PLANCHER) == 44


def test_flags_derived():
    assert b.CATEGORIES_SENSIBLES == {"incitation_automutilation", "doxxing", "harcelement_sexuel"}
    assert b.CATEGORIES_BAN_DIRECT == {"arnaque_scam"}


def test_nano_categories_unchanged_and_in_catalogue():
    # "infraction_perso" is nano's opaque marker for a server-defined infraction
    # (resolved to ``perso:<id>`` by the engine): the only non-catalogue entry.
    cats = set(ac.CATEGORIES) - {ac.CATEGORIE_PERSO}
    assert cats == set(LEGACY_PLANCHER)
    assert all(c in inf.INFRACTIONS for c in cats)
    assert not any(c.startswith("profil_") for c in ac.CATEGORIES)


def test_families_and_profile_floors():
    assert len(inf.ids_by_famille("message")) == 9
    assert inf.ids_by_famille("image") == ["contenu_nsfw", "contenu_choquant"]
    assert inf.ids_by_famille("profil") == [
        "profil_avatar_nsfw", "profil_avatar_choquant", "profil_pseudo"]
    assert inf.INFRACTIONS["profil_pseudo"].plancher == dict(basse=1, moyenne=1, haute=2, critique=3)
    assert inf.INFRACTIONS["profil_avatar_nsfw"].plancher == inf.INFRACTIONS["contenu_nsfw"].plancher


def test_emoji_keys_exist():
    from utils import emojis
    for t in inf.INFRACTIONS.values():
        assert hasattr(emojis, t.emoji_key), t.id
    assert hasattr(emojis, inf.get("perso:abc").emoji_key)


def test_perso_ids():
    assert inf.is_perso("perso:abc12") and inf.perso_id("perso:abc12") == "abc12"
    for bad in ("perso:", "perso:ABC", "perso:abcdefghijklm", "perso:a-b", "insulte", None, 3):
        assert not inf.is_perso(bad)
        assert inf.perso_id(bad) is None
    t = inf.get("perso:x1")
    assert t.famille == "perso" and t.plancher == inf.PERSO_DEFAULT_PLANCHER
    assert inf.get("nope") is None and inf.get("insulte").id == "insulte"
    assert inf.PERSO_DEFAULT_PLANCHER == inf.INFRACTIONS["violation_indications"].plancher


# -- normalize / validate ----------------------------------------------------

def test_normalize_drops_garbage_and_clamps():
    raw = {
        "insulte": {"mode": "perso", "crans": {"basse": 99, "moyenne": -3, "haute": "x"}},
        "unknown": {}, "perso:ok1": {}, "perso:BAD": {}, "menace": "nope",
        "profil_pseudo": {"action_profil": "zzz", "enabled": False},
    }
    out = inf.normalize_sanctions(raw)
    assert set(out) == {"insulte", "perso:ok1", "profil_pseudo"}
    assert out["insulte"]["crans"] == {"basse": 7, "moyenne": 0, "haute": 2, "critique": 3}
    assert out["insulte"]["recidive"] is True and out["insulte"]["enabled"] is True
    assert out["profil_pseudo"]["action_profil"] == "rename_warn"
    assert out["profil_pseudo"]["enabled"] is False
    assert "action_profil" not in out["insulte"]
    assert inf.normalize_sanctions(None) == {} and inf.normalize_sanctions([1]) == {}


def test_normalize_known_perso_ids():
    out = inf.normalize_sanctions({"perso:a": {}, "perso:b": {}}, known_perso_ids=["a"])
    assert set(out) == {"perso:a"}


@pytest.mark.parametrize("raw,code", [
    ("x", "sanctions_invalid"),
    ({"zzz": {}}, "sanctions_unknown_id"),
    ({"insulte": 1}, "sanctions_entry_invalid"),
    ({"insulte": {"mode": "x"}}, "sanctions_mode_invalid"),
    ({"insulte": {"enabled": "yes"}}, "sanctions_flag_invalid"),
    ({"insulte": {"crans": []}}, "sanctions_crans_invalid"),
    ({"insulte": {"crans": {"nope": 1}}}, "sanctions_crans_invalid"),
    ({"insulte": {"crans": {"basse": True}}}, "sanctions_crans_invalid"),
    ({"insulte": {"crans": {"basse": 8}}}, "sanctions_cran_range"),
    ({"insulte": {"action_profil": "hold"}}, "sanctions_action_profil_invalid"),
    ({"profil_pseudo": {"action_profil": "bad"}}, "sanctions_action_profil_invalid"),
])
def test_validate_errors(raw, code):
    assert inf.validate_sanctions(raw) == code


def test_validate_ok():
    assert inf.validate_sanctions(None) is None
    assert inf.validate_sanctions({}) is None
    assert inf.validate_sanctions({
        "insulte": {"mode": "perso", "crans": {"basse": 0, "critique": 7}, "recidive": False},
        "profil_pseudo": {"action_profil": "alert"}, "perso:ab": {"enabled": False},
    }) is None


# -- barème: auto mode unchanged ---------------------------------------------

def test_auto_entry_identical_to_no_entry():
    v = V("menace", "haute")
    base = calc(v)
    withe = calc(v, sanctions={"menace": {"mode": "auto", "crans": {"haute": 0}}})
    assert (base.cran, [c.code for c in base.composantes]) == (withe.cran, [c.code for c in withe.composantes])


def test_unknown_category_default_floor_and_perso_default():
    assert calc(V("???", "haute")).cran == 0
    r = calc(V("perso:abc", "critique"), anciennete=30)
    assert r.cran == 4 and r.composantes[0].code == "plancher"
    assert calc(V("insulte", "inconnue")).cran == 0


# -- barème: perso mode ------------------------------------------------------

def test_perso_floor_and_code():
    s = perso_cfg("insulte", dict(basse=0, moyenne=4, haute=5, critique=6))
    r = calc(V("insulte", "moyenne"), sanctions=s)
    assert r.cran == 4
    assert r.composantes[0].code == "plancher_perso" and r.composantes[0].delta == 4


def test_perso_recidive_toggle():
    passees = [b.SanctionPassee("insulte", "critique", NOW - timedelta(days=1))] * 3
    s_on = perso_cfg("insulte", dict(basse=1, moyenne=1, haute=1, critique=1))
    s_off = perso_cfg("insulte", dict(basse=1, moyenne=1, haute=1, critique=1), recidive=False)
    on = calc(V(), sanctions=s_on, passees=passees)
    off = calc(V(), sanctions=s_off, passees=passees)
    assert on.cran > 1 and any(c.code == "recidive" for c in on.composantes)
    assert off.cran == 1 and not any(c.code == "recidive" for c in off.composantes)


def test_perso_skips_severity_veteran_fresh():
    s = perso_cfg("insulte", dict(basse=2, moyenne=2, haute=2, critique=2))
    # severity 1 would shift -1, veteran -1, fresh account +1 in auto mode
    assert calc(V(), sanctions=s, sev=1, anciennete=200).cran == 2
    assert calc(V(), sanctions=s, sev=5, anciennete=1).cran == 2
    codes = {c.code for c in calc(V(), sanctions=s, sev=5, anciennete=1).composantes}
    assert not codes & {"severite", "veteran", "compte_recent"}
    assert calc(V(), sev=5, anciennete=1).cran != 2  # auto mode does apply them


def test_perso_confidence_cap_and_max_action():
    s = perso_cfg("insulte", dict(basse=7, moyenne=7, haute=7, critique=7))
    assert calc(V(confiance="low"), sanctions=s).cran == 1
    assert calc(V(confiance="medium"), sanctions=s).cran == 4
    r = calc(V(confiance="high"), sanctions=s, max_action="mute")
    assert r.cran == 6 and r.needs_review
    assert calc(V(confiance="high"), sanctions=s, max_action="warn").cran == 1
    assert calc(V(confiance="high"), sanctions=s).cran == 7


def test_perso_ban_direct_exempt_from_medium_cap_but_not_low():
    s = perso_cfg("arnaque_scam", dict(basse=7, moyenne=7, haute=7, critique=7))
    assert calc(V("arnaque_scam", confiance="medium"), sanctions=s).cran == 7
    assert calc(V("arnaque_scam", confiance="low"), sanctions=s).cran == 1


def test_perso_then_unconfirmed_still_applies():
    s = perso_cfg("menace", dict(basse=7, moyenne=7, haute=7, critique=7))
    r = b.appliquer_non_confirme(calc(V("menace", "haute"), sanctions=s))
    assert r.cran == ac.CONFIRM_UNCONFIRMED_CRAN and r.needs_review


def test_perso_custom_infraction_id():
    s = perso_cfg("perso:abc", dict(basse=3, moyenne=3, haute=3, critique=3))
    r = calc(V("perso:abc", "basse"), sanctions=s)
    assert r.cran == 3 and r.composantes[0].code == "plancher_perso"


# -- disabled ----------------------------------------------------------------

def test_disabled_via_sanctions_and_legacy():
    for kw in ({"sanctions": {"menace": {"enabled": False}}},
               {"categories_desactivees": ("menace",)}):
        r = calc(V("menace", "critique"), **kw)
        assert r.cran == 0 and r.composantes[0].code == "categorie_desactivee"
    both = calc(V("menace", "critique"), sanctions={"menace": {"enabled": False, "mode": "perso"}})
    assert both.cran == 0


def test_merge_legacy_disabled():
    raw = {"menace": {"enabled": True}, "doxxing": {"mode": "perso"}}
    norm = inf.normalize_sanctions(raw)
    out = inf.merge_legacy_disabled(norm, ["menace", "doxxing", "insulte", "weird"], raw)
    assert out["menace"]["enabled"] is True          # explicit wins
    assert out["doxxing"]["enabled"] is False and out["doxxing"]["mode"] == "perso"
    assert out["insulte"]["enabled"] is False
    assert "weird" not in out


# -- profile action ----------------------------------------------------------

def test_action_profil_defaults_and_override():
    assert calc(V("profil_avatar_nsfw", "haute")).action_profil == "hold"
    assert calc(V("profil_avatar_choquant", "haute")).action_profil == "hold"
    assert calc(V("profil_pseudo", "haute")).action_profil == "rename_warn"
    assert calc(V("insulte")).action_profil is None
    r = calc(V("profil_pseudo", "haute"), sanctions={"profil_pseudo": {"action_profil": "alert"}})
    assert r.action_profil == "alert"
    assert b.appliquer_non_confirme(calc(V("profil_pseudo", "critique"), sanctions={
        "profil_pseudo": {"mode": "perso", "crans": dict(basse=7, moyenne=7, haute=7, critique=7),
                          "action_profil": "bareme"}})).action_profil == "bareme"


# -- module wiring + i18n ----------------------------------------------------

def test_load_config_migrates_legacy():
    import asyncio
    from modules.automod_ai import AutomodModule
    m = AutomodModule(bot=None, guild_id=1)
    cfg = {"categories_desactivees": ["menace", "doxxing"],
           "sanctions": {"doxxing": {"enabled": True}, "insulte": {"mode": "perso"}}}
    assert asyncio.run(m.load_config(cfg)) is True
    assert m.sanctions["menace"]["enabled"] is False
    assert m.sanctions["doxxing"]["enabled"] is True
    assert m.sanctions["insulte"]["mode"] == "perso"
    assert m.categories_desactivees == ["menace"]
    assert m.get_default_config()["sanctions"] == {}


def test_new_i18n_keys_exist_in_every_locale():
    from utils.i18n import i18n
    keys = ["modules.automod_ai.bareme.floor_custom"]
    for iid in list(inf.INFRACTIONS) + ["perso"]:
        keys += [f"modules.automod_ai.infractions.{iid}.name",
                 f"modules.automod_ai.infractions.{iid}.desc"]
    for loc in ("fr", "en-US", "es-ES", "pt-BR", "de"):
        for key in keys:
            assert not i18n.get(key, locale=loc).startswith("["), (loc, key)


def test_floor_custom_renders():
    from utils.automod_render import bareme_breakdown
    out = bareme_breakdown([b.Composante("plancher_perso", 3, "insulte/basse")], "fr")
    assert "personnalisé" in out and "3" in out
