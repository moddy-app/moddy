"""Lookup tools: /status, /company, /username (docs/features/TOOLS_LOOKUP.md).

Pure logic, card building (walking ``view.walk_children()``), the persistent
custom_id contract and the cog flows with a fake ``public_api`` — nothing
leaves the process.
"""

from __future__ import annotations

import asyncio
import json
import re
import types
from datetime import date
from pathlib import Path

import discord
import pytest
from discord import ui

from services.public_api import PublicAPIError, PublicResponse
from tools import company as co
from tools import status_registry as sr
from tools import whatsmyname as wmn
from utils import tool_card as tc
from utils import tools_lookup_views as views

LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")
ROOT = Path(__file__).resolve().parent.parent


def texts(view):
    return [c.content for c in view.walk_children() if isinstance(c, ui.TextDisplay)]


def joined(view):
    return "\n".join(texts(view))


def buttons(view):
    out = []
    for child in view.walk_children():
        child = child.item if isinstance(child, ui.DynamicItem) else child
        if isinstance(child, ui.Button):
            out.append(child)
    return out


def cid_buttons(view, prefix):
    return [b for b in buttons(view) if (b.custom_id or "").startswith(prefix)]


def dyn_ids(view):
    """custom_ids of every interactive child, DynamicItem or not."""
    out = []
    for child in view.walk_children():
        cid = getattr(child, "custom_id", None)
        if cid:
            out.append(cid)
    return out


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

STATUS_PAYLOAD = {
    "page": {"url": "https://www.cloudflarestatus.com", "updated_at": "2026-10-19T10:00:00Z"},
    "status": {"indicator": "minor", "description": "Minor Service Outage"},
    "components": [
        {"id": "g1", "name": "Network", "group": True, "status": "partial_outage"},
        {"id": "c1", "name": "Paris", "group": False, "group_id": "g1", "status": "partial_outage"},
        {"id": "c2", "name": "Lyon", "group": False, "group_id": "g1", "status": "degraded_performance"},
        {"id": "c3", "name": "API", "group": False, "group_id": None, "status": "operational"},
        {"id": "c4", "name": "Dashboard", "group": False, "group_id": None, "status": "major_outage"},
    ],
    "incidents": [
        {"id": "i1", "name": "Increased latency", "status": "investigating", "impact": "minor",
         "started_at": "2026-10-19T09:00:00Z", "shortlink": "https://stspg.io/abc",
         "incident_updates": [
             {"body": "Old <b>update</b>", "created_at": "2026-10-19T09:05:00Z"},
             {"body": "We are looking &amp; fixing it<br/>Thanks", "created_at": "2026-10-19T09:30:00Z"}]},
        {"id": "i2", "name": "Done", "status": "resolved", "impact": "minor"},
    ],
    "scheduled_maintenances": [
        {"id": "m1", "name": "Database upgrade", "status": "scheduled",
         "scheduled_for": "2026-10-25T01:00:00Z", "scheduled_until": "2026-10-25T03:00:00Z"},
    ],
}

OK_PAYLOAD = {"page": {"url": "https://www.githubstatus.com"}, "status": {"indicator": "none"},
              "components": [{"id": "x", "name": "Git", "group": False, "status": "operational"}]}


class FakeAPI:
    def __init__(self, json_by_url=None, text=None, error=None):
        self.json_by_url = json_by_url or {}
        self.text = text
        self.error = error
        self.calls = []

    async def get_json(self, url, **kw):
        self.calls.append(url)
        if self.error:
            raise self.error
        for key, value in self.json_by_url.items():
            if url.startswith(key):
                return value
        raise PublicAPIError("not_found", url=url)

    async def get_text(self, url, **kw):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.text

    def set_host_limit(self, host, limit):
        pass

    async def request(self, method, url, **kw):  # used by /username
        raise NotImplementedError


class FakeResponse:
    def __init__(self):
        self.sent = []
        self._done = False
        self.edited = None

    def is_done(self):
        return self._done

    async def send_message(self, **kw):
        self._done = True
        self.sent.append(kw)

    async def defer(self, **kw):
        self._done = True
        self.sent.append({"defer": kw})

    async def edit_message(self, **kw):
        self._done = True
        self.edited = kw


class FakeInteraction:
    def __init__(self, bot=None, *, locale="en-US", user_id=42, message=None, client=None):
        self.locale = locale
        self.user = types.SimpleNamespace(id=user_id)
        self.response = FakeResponse()
        self.followups = []
        self.originals = []
        self.extras = {}
        self.message = message
        self.client = client if client is not None else bot
        self.followup = types.SimpleNamespace(send=self._followup)

    async def _followup(self, **kw):
        self.followups.append(kw)

    async def edit_original_response(self, **kw):
        self.originals.append(kw)


def make_bot(api):
    return types.SimpleNamespace(public_api=api, db=None, redis=None)


def make_cog(api):
    from cogs.tools_lookup import ToolsLookup
    return ToolsLookup(make_bot(api))


@pytest.fixture(autouse=True)
def _clean_caches():
    sr.clear_cache()
    wmn.reset_dataset()
    tc._LOCAL_STATE.clear()
    yield


# --------------------------------------------------------------------------- #
# /status — registry and parsing
# --------------------------------------------------------------------------- #

def test_registry_keys_unique_and_urls_https():
    keys = [s.key for s in sr.SERVICES]
    assert len(keys) == len(set(keys)) >= 12
    assert all(s.summary_url.startswith("https://") and s.summary_url.endswith("/api/v2/summary.json")
               for s in sr.SERVICES)
    assert len(sr.SERVICES) <= 25          # a Discord select holds 25 options


def test_find_and_resolve_services():
    assert [s.key for s in sr.find_services("git")][:1] == ["github"]
    assert sr.find_services("EPIC")[0].key == "epicgames"
    assert sr.resolve_service("Epic Games").key == "epicgames"
    assert sr.resolve_service("discord").key == "discord"
    assert sr.resolve_service("zzzz-nothing") is None
    assert len(sr.find_services("")) == len(sr.SERVICES)


def test_parse_summary_components_incidents_maintenance():
    summary = sr.parse_summary("cloudflare", STATUS_PAYLOAD, now=1000.0)
    assert summary.indicator == sr.IND_MINOR and summary.reachable
    assert summary.components_total == 4              # group header not counted
    assert {i.name for i in summary.issues} == {"Paris", "Lyon", "Dashboard"}
    groups = sr.group_issues(summary.issues)
    assert groups[0].status == sr.COMP_MAJOR           # worst first (stand-alone Dashboard)
    network = next(g for g in groups if g.name == "Network")
    assert network.status == sr.COMP_PARTIAL and len(network.items) == 2
    assert len(summary.incidents) == 1                 # the resolved one is dropped
    assert summary.incidents[0].update == "We are looking & fixing it\nThanks"
    assert summary.maintenances[0].name == "Database upgrade"
    assert summary.page_url == "https://www.cloudflarestatus.com"


def test_parse_summary_is_defensive():
    summary = sr.parse_summary("x", {"status": "oops", "components": [None, 3], "incidents": "no"})
    assert summary.indicator == sr.IND_UNKNOWN and summary.issues == []


def test_overview_order_and_stats():
    a = sr.ServiceSummary(key="github", indicator=sr.IND_NONE)
    b = sr.ServiceSummary(key="discord", indicator=sr.IND_MAJOR)
    c = sr.ServiceSummary(key="reddit", indicator=sr.IND_UNKNOWN, error="timeout")
    d = sr.ServiceSummary(key="npm", indicator=sr.IND_MAINTENANCE)
    ordered = [s.key for s in sr.overview_order([a, b, c, d])]
    assert ordered == ["discord", "npm", "github", "reddit"]
    stats = sr.overview_stats([a, b, c, d])
    assert (stats.total, stats.problems, stats.maintenance, stats.unknown, stats.worst) == (4, 1, 1, 1, sr.IND_MAJOR)


async def test_fetch_summary_cache_unreachable_and_force():
    api = FakeAPI({"https://www.githubstatus.com": OK_PAYLOAD})
    service = sr.get_service("github")
    first = await sr.fetch_summary(api, service)
    assert first.indicator == sr.IND_NONE
    await sr.fetch_summary(api, service)
    assert len(api.calls) == 1                         # served from the 60 s cache
    await sr.fetch_summary(api, service, force=True)
    assert len(api.calls) == 1                         # a refresh cannot hammer the page
    down = FakeAPI(error=PublicAPIError("timeout", url="x"))
    summary = await sr.fetch_summary(down, sr.get_service("discord"))
    assert not summary.reachable and summary.error == "timeout" and summary.indicator == sr.IND_UNKNOWN


# --------------------------------------------------------------------------- #
# /status — cards and persistence
# --------------------------------------------------------------------------- #

def _all_summaries(problem=True):
    out = []
    for service in sr.SERVICES:
        payload = STATUS_PAYLOAD if (problem and service.key == "cloudflare") else OK_PAYLOAD
        out.append(sr.parse_summary(service.key, payload))
    return out


@pytest.mark.parametrize("locale", LOCALES)
def test_overview_card(locale):
    view = views.build_status_overview(_all_summaries(), locale, requester_id=7)
    body = joined(view)
    assert "Cloudflare" in body and "GitHub" in body
    assert body.index("Cloudflare") < body.index("GitHub")     # problems first
    assert "<@7>" in body
    cids = dyn_ids(view)
    assert "moddy:tools:status:detail" in cids and "moddy:tools:status:refresh" in cids
    select = next(c for c in view.walk_children() if isinstance(c, ui.Select))
    assert len(select.options) == len(sr.SERVICES)
    assert {o.value for o in select.options} == {s.key for s in sr.SERVICES}


def test_overview_all_good_is_green_and_ephemeral_has_no_requester():
    view = views.build_status_overview(_all_summaries(problem=False), "en-US")
    container = next(c for c in view.children if isinstance(c, ui.Container))
    assert container.accent_colour.value == tc.ACCENT_SUCCESS
    assert "running normally" in joined(view)
    assert "requested by" not in joined(view)


def test_status_detail_card_sections_and_buttons():
    service = sr.get_service("cloudflare")
    summary = sr.parse_summary("cloudflare", STATUS_PAYLOAD, now=1000.0)
    view = views.build_status_detail(service, summary, "en-US")
    body = joined(view)
    assert "Affected components" in body and "Ongoing incidents" in body and "Maintenance" in body
    assert "Increased latency" in body and "We are looking & fixing it Thanks" in body
    assert "Database upgrade" in body and "Dashboard" in body
    assert "Provided by" in body and "Cloudflare Status" in body
    assert body.count("###") == 1
    cids = dyn_ids(view)
    assert "moddy:tools:status:refresh:cloudflare" in cids
    link = [b for b in buttons(view) if b.url]
    assert link and link[0].url == "https://www.cloudflarestatus.com"
    assert len(body) < tc.MESSAGE_TEXT_BUDGET


def test_status_detail_unreachable_and_all_good():
    service = sr.get_service("discord")
    down = sr.ServiceSummary(key="discord", error="timeout", page_url=service.base_url)
    assert "could not be read" in joined(views.build_status_detail(service, down, "en-US"))
    ok = sr.parse_summary("github", OK_PAYLOAD)
    assert "No problem reported" in joined(views.build_status_detail(sr.get_service("github"), ok, "fr")) \
        or "Aucun problème" in joined(views.build_status_detail(sr.get_service("github"), ok, "fr"))


def test_status_detail_caps_long_lists():
    comps = [{"id": f"c{i}", "name": f"Comp {i}", "group": False, "group_id": None,
              "status": "major_outage"} for i in range(60)]
    payload = {"page": {"url": "https://x"}, "status": {"indicator": "major"}, "components": comps,
               "incidents": [{"id": str(i), "name": f"Inc {i}", "status": "investigating", "impact": "major",
                              "incident_updates": [{"body": "x" * 2000, "created_at": "2026-10-19T09:30:00Z"}]}
                             for i in range(8)]}
    summary = sr.parse_summary("cloudflare", payload)
    view = views.build_status_detail(sr.get_service("cloudflare"), summary, "en-US")
    assert len(joined(view)) < tc.MESSAGE_TEXT_BUDGET
    assert "more" in joined(view)


def test_status_refresh_button_round_trip():
    item = views.StatusRefreshButton("epicgames")
    cid = item.item.custom_id
    match = views.StatusRefreshButton.__discord_ui_compiled_template__.fullmatch(cid)
    assert match and match["key"] == "epicgames"


# --------------------------------------------------------------------------- #
# /status — command
# --------------------------------------------------------------------------- #

async def test_status_command_overview_and_detail_and_unknown():
    api = FakeAPI({s.base_url: OK_PAYLOAD for s in sr.SERVICES})
    cog = make_cog(api)
    inter = FakeInteraction(cog.bot)
    await cog.status_command.callback(cog, inter, None)
    assert inter.response.sent[0]["defer"]["ephemeral"] is True    # ephemeral by default
    assert "All `16` services" in joined(inter.originals[0]["view"])

    inter = FakeInteraction(cog.bot)
    await cog.status_command.callback(cog, inter, "git")
    assert "GitHub" in joined(inter.originals[0]["view"])

    inter = FakeInteraction(cog.bot)
    await cog.status_command.callback(cog, inter, "doesnotexist")
    assert inter.response.sent[0]["ephemeral"] is True
    assert "doesnotexist" in joined(inter.response.sent[0]["view"])


async def test_status_autocomplete_returns_keys():
    cog = make_cog(FakeAPI())
    choices = await cog.status_autocomplete(FakeInteraction(cog.bot), "dis")
    assert choices[0].value == "discord" and choices[0].name == "Discord"
    assert all(len(c.name) <= 100 and len(c.value) <= 100 for c in choices)


async def test_status_select_and_refresh_callbacks():
    api = FakeAPI({s.base_url: OK_PAYLOAD for s in sr.SERVICES})
    bot = make_bot(api)
    select = views.StatusOverviewSelect()
    inter = FakeInteraction(bot)
    type(select).values = property(lambda self: ["github"])   # stub the posted value
    try:
        await select.callback(inter)
    finally:
        del type(select).values
    assert inter.followups and "GitHub" in joined(inter.followups[0]["view"])
    assert inter.followups[0]["ephemeral"] is True


# --------------------------------------------------------------------------- #
# /company
# --------------------------------------------------------------------------- #

COMPANY_RAW = {
    "siren": "552032534", "nom_complet": "DANONE (DANONE)", "nom_raison_sociale": "DANONE",
    "sigle": None, "etat_administratif": "A", "date_creation": "1899-01-01",
    "nature_juridique": "5599", "activite_principale": "70.10Z", "categorie_entreprise": "GE",
    "tranche_effectif_salarie": "53", "annee_tranche_effectif_salarie": "2023",
    "nombre_etablissements": 20, "nombre_etablissements_ouverts": 5,
    "siege": {"siret": "55203253400646", "adresse": "17 BD HAUSSMANN 75009 PARIS",
              "code_postal": "75009", "libelle_commune": "PARIS", "etat_administratif": "A",
              "liste_enseignes": ["DANONE"]},
    "dirigeants": [{"nom": "FABER", "prenoms": "EMMANUEL", "qualite": "Président"},
                   {"denomination": "KPMG SA", "qualite": "Commissaire aux comptes"},
                   {"nom": "[Données non diffusibles]", "prenoms": "", "qualite": "x"},
                   {"nom": "B", "prenoms": "C", "qualite": "Administrateur"},
                   {"nom": "D", "prenoms": "E", "qualite": "Administrateur"}],
    "complements": {"est_association": False, "est_ess": False},
}


def test_siren_siret_checksums():
    assert co.siren_valid("552032534") and not co.siren_valid("552032535")
    assert co.siret_valid("55203253400646")
    assert co.siret_valid("35600000000048") and co.siret_valid("35600000000047")   # La Poste: Luhn or digit sum divisible by 5
    q = co.classify_query("552 032 534")
    assert (q.kind, q.value, q.valid) == (co.KIND_SIREN, "552032534", True)
    assert co.classify_query("552032535").valid is False
    assert co.classify_query("55203253400646").kind == co.KIND_SIRET
    assert co.classify_query("  danone  paris ").value == "danone paris"
    assert co.format_siren("552032534") == "552 032 534"
    assert co.format_siret("55203253400646") == "552 032 534 00646"


def test_code_tables_and_age():
    assert co.legal_form_label("5710") == "Société par actions simplifiée (SAS)"
    assert co.legal_form_label("5799").startswith("Société par actions simplifiée")   # category fallback
    assert co.legal_form_label("") is None
    assert co.naf_section("62.01Z") == "J" and co.naf_division_label("70.10Z").startswith("Activités des sièges")
    assert co.headcount_bracket("12") == ("range", 20, 49)
    assert co.age_parts(date(2020, 3, 15), date(2026, 10, 19)) == (6, 7)
    assert co.age_parts(date(2026, 10, 25), date(2026, 10, 19)) == (0, 0)


def test_parse_company_and_directors():
    company = co.parse_company(COMPANY_RAW)
    assert company.siren == "552032534" and company.active
    assert company.head_office.city == "PARIS"
    assert [d.name for d in company.directors] == ["Emmanuel Faber", "KPMG SA", "C B"]
    assert company.directors_total == 5 and company.directors[1].is_company
    assert co.parse_company({"nom_complet": "no siren"}) is None
    result = co.parse_search({"results": [COMPANY_RAW, "junk"], "total_results": 41})
    assert result.total == 41 and len(result.companies) == 1
    assert co.parse_search("nonsense").companies == []


@pytest.mark.parametrize("locale", LOCALES)
def test_company_card(locale):
    company = co.parse_company(COMPANY_RAW)
    view = views.build_company_card(company, locale, today=date(2026, 10, 19), requester_id=9)
    body = joined(view)
    assert "552 032 534" in body and "DANONE" in body
    assert "Annuaire des Entreprises" in body
    assert "70.10Z" in body and "Emmanuel Faber" in body
    assert "<@9>" in body and body.count("###") == 1
    link = [b for b in buttons(view) if b.url]
    assert link[0].url == "https://annuaire-entreprises.data.gouv.fr/entreprise/552032534"
    container = next(c for c in view.children if isinstance(c, ui.Container))
    assert container.accent_colour.value == tc.ACCENT_SUCCESS


def test_company_card_closed_and_siret_match():
    raw = dict(COMPANY_RAW, etat_administratif="C", date_fermeture="2020-05-01")
    raw["matching_etablissements"] = [{"siret": "55203253400999", "adresse": "1 RUE X 31000 TOULOUSE",
                                       "etat_administratif": "F"}]
    company = co.parse_company(raw, searched_siret="55203253400999")
    view = views.build_company_card(company, "en-US", searched_siret="55203253400999",
                                    today=date(2026, 10, 19))
    body = joined(view)
    assert "552 032 534 00999" in body and "TOULOUSE" in body
    assert "<t:" in body                                   # closing date rendered by Discord
    container = next(c for c in view.children if isinstance(c, ui.Container))
    assert container.accent_colour.value == tc.ACCENT_ERROR


def test_company_results_view_is_stateless():
    other = dict(COMPANY_RAW, siren="123456782", nom_complet="DANONE SERVICES")
    result = co.parse_search({"results": [COMPANY_RAW, other], "total_results": 12})
    view = views.build_company_results(result, "danone", "fr")
    select = next(c for c in view.walk_children() if isinstance(c, ui.Select))
    assert select.custom_id == "moddy:tools:company:pick"
    assert [o.value for o in select.options] == ["552032534", "123456782"]
    assert all(len(o.label) <= 100 and len(o.description or "") <= 100 for o in select.options)
    assert "`12`" in joined(view)


class FakeCompanyAPI(FakeAPI):
    def __init__(self, payload=None, error=None):
        super().__init__(error=error)
        self.payload = payload
        self.params = []

    async def get_json(self, url, **kw):
        self.params.append(kw.get("params"))
        if self.error:
            raise self.error
        return self.payload


async def test_company_command_flows():
    # several matches -> list + select
    api = FakeCompanyAPI({"results": [COMPANY_RAW, dict(COMPANY_RAW, siren="123456782")], "total_results": 2})
    cog = make_cog(api)
    inter = FakeInteraction(cog.bot)
    await cog.company_command.callback(cog, inter, "danone")
    assert any(isinstance(c, ui.Select) for c in inter.originals[0]["view"].walk_children())

    # a SIREN goes straight to the record
    api = FakeCompanyAPI({"results": [COMPANY_RAW], "total_results": 1})
    cog = make_cog(api)
    inter = FakeInteraction(cog.bot)
    await cog.company_command.callback(cog, inter, "552 032 534")
    assert api.params[0]["q"] == "552032534"
    assert "552 032 534" in joined(inter.originals[0]["view"])

    # too short, bad checksum: ephemeral error before any request
    for bad in ("ab", "552032535"):
        inter = FakeInteraction(cog.bot)
        before = len(api.params)
        await cog.company_command.callback(cog, inter, bad)
        assert inter.response.sent[0]["ephemeral"] is True and len(api.params) == before

    # nothing found / API down
    cog = make_cog(FakeCompanyAPI({"results": [], "total_results": 0}))
    inter = FakeInteraction(cog.bot)
    await cog.company_command.callback(cog, inter, "zzzzzz")
    assert "zzzzzz" in joined(inter.originals[0]["view"])
    cog = make_cog(FakeCompanyAPI(error=PublicAPIError("rate_limited", status=429, url="x")))
    inter = FakeInteraction(cog.bot)
    await cog.company_command.callback(cog, inter, "danone")
    assert "Too many requests" in joined(inter.originals[0]["view"])


async def test_company_search_retries_a_transient_error(monkeypatch):
    calls = []

    class Flaky(FakeCompanyAPI):
        async def get_json(self, url, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise PublicAPIError("http_error", status=503, url=url)
            return {"results": [COMPANY_RAW], "total_results": 1}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(co.asyncio, "sleep", no_sleep)
    result = await co.search_companies(Flaky(), co.classify_query("danone"))
    assert len(calls) == 2 and result.total == 1


async def test_company_pick_callback_looks_the_siren_up():
    api = FakeCompanyAPI({"results": [COMPANY_RAW], "total_results": 1})
    bot = make_bot(api)
    select = views.CompanyPickSelect()
    type(select).values = property(lambda self: ["552032534"])
    try:
        inter = FakeInteraction(bot)
        await select.callback(inter)
    finally:
        del type(select).values
    assert "DANONE" in joined(inter.followups[0]["view"])


# --------------------------------------------------------------------------- #
# /username — pure logic
# --------------------------------------------------------------------------- #

DATASET = {"sites": [
    {"name": "GitHub", "uri_check": "https://github.com/{account}", "uri_pretty": "https://github.com/{account}",
     "e_code": 200, "e_string": "Repositories", "m_code": 404, "m_string": "Not Found", "cat": "coding", "valid": True},
    {"name": "Soft404", "uri_check": "https://soft.example/{account}", "e_code": 200, "e_string": "profile",
     "m_string": "nothing here", "cat": "social", "valid": True},
    {"name": "AdultSite", "uri_check": "https://adult.example/{account}", "e_code": 200, "e_string": "x",
     "cat": "xx NSFW xx", "valid": True},
    {"name": "Broken", "uri_check": "https://broken.example/{account}", "e_code": 200, "cat": "misc", "valid": False},
    {"name": "NoPlaceholder", "uri_check": "https://noplace.example/", "e_code": 200, "cat": "misc"},
    {"name": "Poster", "uri_check": "https://api.example/check", "post_body": "user={account}", "e_code": 200,
     "e_string": "ok", "cat": "tech", "uri_pretty": "https://api.example/u/{account}", "valid": True},
]}


def test_validate_username():
    assert wmn.validate_username(" @john_doe ") == "john_doe"
    for raw, code in (("", "empty"), ("@", "empty"), ("x" * 41, "too_long"), ("a b", "invalid"),
                      ("a/b", "invalid"), ("é", "invalid")):
        with pytest.raises(wmn.UsernameError) as exc:
            wmn.validate_username(raw)
        assert exc.value.code == code


def test_dataset_filters_nsfw_invalid_and_placeholderless():
    names = [s.name for s in wmn.parse_dataset(DATASET).sites]
    assert names == ["GitHub", "Soft404", "Poster"]


def test_detection_rule_and_requests():
    ds = wmn.parse_dataset(DATASET)
    github, soft, poster = ds.sites
    assert wmn.is_found(github, 200, "Repositories 12")
    assert not wmn.is_found(github, 404, "Repositories")
    assert not wmn.is_found(github, 200, "no repos")
    assert not wmn.is_found(soft, 200, "profile ... nothing here")     # soft-404 page
    spec = wmn.build_request(github, "a b/c")
    assert spec.url == "https://github.com/a%20b%2Fc" and spec.method == "GET"
    spec = wmn.build_request(poster, "bob")
    assert spec.method == "POST" and spec.data == b"user=bob"
    assert spec.profile_url == "https://api.example/u/bob"


async def test_run_search_collects_found_and_counts_unchecked():
    ds = wmn.parse_dataset(DATASET)

    class SearchAPI(FakeAPI):
        async def request(self, method, url, **kw):
            if "github" in url:
                return PublicResponse(status=200, body=b"Repositories", url=url, headers={})
            if "soft" in url:
                raise PublicAPIError("timeout", url=url)
            return PublicResponse(status=200, body=b"nothing", url=url, headers={})

    progress = wmn.Progress()
    outcome = await wmn.run_search(SearchAPI(), ds, "bob", progress=progress, seed=1)
    assert [p.site for p in outcome.found] == ["GitHub"]
    assert (outcome.total, outcome.checked, outcome.unchecked) == (3, 2, 1)
    assert progress.done == progress.total == 3 and not outcome.timed_out


async def test_run_search_deadline_cuts_slow_sites():
    ds = wmn.parse_dataset(DATASET)

    class Slow(FakeAPI):
        async def request(self, method, url, **kw):
            await asyncio.sleep(5)

    outcome = await wmn.run_search(Slow(), ds, "bob", deadline=0.05)
    assert outcome.timed_out and outcome.found == [] and outcome.checked == 0


def test_cooldowns_and_slots():
    now = [100.0]
    cd = wmn.Cooldowns(60, clock=lambda: now[0])
    assert cd.remaining(1) == 0
    cd.hit(1)
    assert cd.remaining(1) == 60 and cd.remaining(2) == 0
    now[0] += 45
    assert round(cd.remaining(1)) == 15
    cd.clear(1)
    assert cd.remaining(1) == 0
    slots = wmn.SearchSlots(2)
    assert slots.try_enter() and slots.try_enter() and not slots.try_enter()
    slots.leave()
    assert slots.try_enter()
    assert wmn.format_wait(72) == "1 min 12 s" and wmn.format_wait(0.2) == "1 s"


def test_paginate_splits_and_keeps_every_line():
    lines = [f"- [Site{i}](<https://example.com/{i}>)" for i in range(200)]
    pages = wmn.paginate([("**Social**", lines[:120]), ("**Gaming**", lines[120:])], budget=800,
                         continued=lambda t_: t_ + " (cont.)")
    assert len(pages) > 3
    flat = [line for page in pages for _, chunk in page for line in chunk]
    assert flat == lines
    assert all(sum(len(h) + sum(len(l) + 1 for l in chunk) for h, chunk in page) <= 900 for page in pages)
    assert any("(cont.)" in head for page in pages for head, _ in page)
    assert wmn.paginate([], budget=100) == [[]]


def test_lines_cannot_break_markdown_and_export_text():
    p = wmn.Profile(site="Evil](x) *site*", url="https://a.example/x y", cat="social")
    line = wmn.profile_line(p)
    assert line == "- [Evilx site](<https://a.example/x%20y>)"
    assert wmn.profile_line(wmn.Profile("Plain", "", "misc")) == "- Plain"
    text = wmn.export_text("bob", [p, wmn.Profile("GitHub", "https://github.com/bob", "coding")],
                           generated_at="now")
    assert "Username: bob" in text and "GitHub: https://github.com/bob" in text
    assert "CC BY-SA 4.0" in text and text.index("[coding]") > text.index("Profiles found: 2")


def test_profiles_json_round_trip():
    profiles = [wmn.Profile("A", "u", "social"), wmn.Profile("B", "", "misc")]
    assert wmn.profiles_from_json(wmn.profiles_to_json(profiles)) == profiles


# --------------------------------------------------------------------------- #
# /username — cards, persistence, command
# --------------------------------------------------------------------------- #

def _payload(n=3, *, timed_out=False, total=700, checked=640):
    cats = ["social", "gaming", "coding"]
    profiles = [wmn.Profile(f"Site{i}", f"https://example.com/{i}", cats[i % 3]) for i in range(n)]
    return {"u": "bob", "p": wmn.profiles_to_json(profiles), "n": total, "c": checked, "t": timed_out}


@pytest.mark.parametrize("locale", LOCALES)
def test_username_result_card(locale):
    view = views.build_username_result(_payload(), "AbCdEfGh12", 0, locale, requester_id=5)
    body = joined(view)
    assert "`bob`" in body and "Site0" in body and "WhatsMyName" in body and "<@5>" in body
    assert "<https://example.com/0>" in body
    note = tc.t("tools.username.note", locale=locale)
    assert note in body                                   # the honest datacenter note
    assert "moddy:tools:username:export:AbCdEfGh12" in dyn_ids(view)
    assert not any(c.startswith("moddy:tools:username:prev") for c in dyn_ids(view))   # single page


def test_username_pagination_buttons_and_budget():
    payload = _payload(400)
    view = views.build_username_result(payload, "AbCdEfGh12", 0, "en-US")
    assert len(joined(view)) < tc.MESSAGE_TEXT_BUDGET
    ids = dyn_ids(view)
    assert "moddy:tools:username:next:AbCdEfGh12:1" in ids and "moddy:tools:username:prev:AbCdEfGh12:0" in ids
    prev = cid_buttons(view, "moddy:tools:username:prev")[0]
    assert prev.disabled
    last = len(views.username_pages(wmn.profiles_from_json(payload["p"]), "en-US")) - 1
    view = views.build_username_result(payload, "AbCdEfGh12", 999, "en-US")      # clamped
    assert f"Page {last + 1}/{last + 1}" in joined(view)
    nxt = cid_buttons(view, "moddy:tools:username:next")[0]
    assert nxt.disabled


def test_username_none_cut_and_unchecked_texts():
    none = views.build_username_result(_payload(0), "AbCdEfGh12", 0, "en-US")
    assert "No profile found" in joined(none)
    assert not any("export" in c for c in dyn_ids(none))
    cut = views.build_username_result(_payload(2, timed_out=True), "AbCdEfGh12", 0, "en-US")
    assert "cut after 45 s" in joined(cut) and "`60` sites" in joined(cut)
    some = views.build_username_result(_payload(2, total=10, checked=8), "AbCdEfGh12", 0, "en-US")
    assert "`2` sites did not answer" in joined(some)


def test_username_dynamic_items_round_trip():
    for item in (views.UsernamePageButton("AbCdEfGh12", 3, "next"), views.UsernamePageButton("AbCdEfGh12", 0, "prev"),
                 views.UsernameExportButton("AbCdEfGh12")):
        cid = item.item.custom_id
        assert len(cid) <= 100
        assert type(item).__discord_ui_compiled_template__.fullmatch(cid)


async def test_username_buttons_callbacks_page_and_export_and_expiry():
    bot = make_bot(FakeAPI())
    token = await tc.save_state(bot, views.STATE_KIND_USERNAME, _payload(400), ttl=60)
    inter = FakeInteraction(bot, client=bot)
    await views.UsernamePageButton(token, 1, "next").callback(inter)
    assert "Page 2/" in joined(inter.response.edited["view"])

    inter = FakeInteraction(bot, client=bot)
    await views.UsernameExportButton(token).callback(inter)
    sent = inter.response.sent[0]
    assert sent["ephemeral"] is True and sent["files"][0].filename == "username-bob.txt"
    content = sent["files"][0].fp.read().decode()
    assert "Site0" in content and "WhatsMyName" in content

    inter = FakeInteraction(bot, client=bot)
    await views.UsernamePageButton("ZZZZZZZZZZ", 1, "next").callback(inter)       # expired token
    assert "expired" in joined(inter.response.sent[0]["view"])


class UsernameAPI(FakeAPI):
    def __init__(self):
        super().__init__(text=json.dumps(DATASET))

    async def request(self, method, url, **kw):
        if "github" in url:
            return PublicResponse(status=200, body=b"Repositories", url=url, headers={})
        return PublicResponse(status=404, body=b"", url=url, headers={})


async def test_username_command_happy_path_cooldown_busy_and_validation():
    cog = make_cog(UsernameAPI())
    inter = FakeInteraction(cog.bot)
    await cog.username_command.callback(cog, inter, "@bob")
    assert inter.response.sent[0]["ephemeral"] is True
    assert "bob" in joined(inter.response.sent[0]["view"])                 # loading card
    result = inter.originals[-1]["view"]
    assert "GitHub" in joined(result) and "datacenter" in joined(result)
    assert cog.slots.active == 0                                             # slot released

    # same user again: cooldown
    inter = FakeInteraction(cog.bot)
    await cog.username_command.callback(cog, inter, "bob")
    assert "Wait" in joined(inter.response.sent[0]["view"])

    # another user while the pool is full: busy, and no cooldown consumed
    cog.slots.active = cog.slots.size
    inter = FakeInteraction(cog.bot, user_id=99)
    await cog.username_command.callback(cog, inter, "bob")
    assert "already running" in joined(inter.response.sent[0]["view"])
    assert cog.cooldowns.remaining(99) == 0
    cog.slots.active = 0

    # invalid input
    inter = FakeInteraction(cog.bot, user_id=100)
    await cog.username_command.callback(cog, inter, "no spaces allowed")
    assert inter.response.sent[0]["ephemeral"] is True and not inter.originals


async def test_username_command_dataset_failure_does_not_burn_the_cooldown():
    api = UsernameAPI()
    api.error = PublicAPIError("network", url="x")
    cog = make_cog(api)
    inter = FakeInteraction(cog.bot)
    await cog.username_command.callback(cog, inter, "bob")
    assert "could not be loaded" in joined(inter.originals[-1]["view"])
    assert cog.cooldowns.remaining(42) == 0 and cog.slots.active == 0


async def test_username_progress_edits_are_sent_while_searching(monkeypatch):
    import cogs.tools_lookup as module

    class SlowAPI(UsernameAPI):
        async def request(self, method, url, **kw):
            await asyncio.sleep(0.12)
            return PublicResponse(status=404, body=b"", url=url, headers={})

    monkeypatch.setattr(module, "PROGRESS_INTERVAL", 0.03)
    cog = make_cog(SlowAPI())
    inter = FakeInteraction(cog.bot)
    await cog.username_command.callback(cog, inter, "bob")
    frames = [joined(o["view"]) for o in inter.originals[:-1]]
    assert frames and all("/" in f for f in frames)


# --------------------------------------------------------------------------- #
# Persistence registry
# --------------------------------------------------------------------------- #

def test_theme_lists_are_wired_into_the_shared_registry():
    from utils.tool_views_registry import dynamic_item_cases, persistent_view_classes
    for cls in views.PERSISTENT_VIEWS:
        assert cls in persistent_view_classes() and cls.__persistent__ is True
        assert cls().is_persistent()
    for case in views.DYNAMIC_ITEM_CASES:
        assert case in dynamic_item_cases()


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #

def _flat(node, prefix=""):
    out = {}
    for key, value in node.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flat(value, path + "."))
        else:
            out[path] = value
    return out


def _locale_tools(locale):
    data = json.loads((ROOT / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
    return {k: _flat(data["tools"][k]) for k in ("status", "company", "username")}


def test_lookup_keys_identical_in_the_five_locales():
    reference = _locale_tools("en-US")
    for locale in LOCALES:
        got = _locale_tools(locale)
        for tool in reference:
            assert set(got[tool]) == set(reference[tool]), (locale, tool)
            for key, text in got[tool].items():
                assert text.strip(), (locale, tool, key)
                placeholders = lambda s: set(re.findall(r"\{(\w+)\}", s))  # noqa: E731
                assert placeholders(text) == placeholders(reference[tool][key]), (locale, tool, key)


def test_every_literal_key_used_in_code_exists():
    reference = _locale_tools("en-US")
    source = (ROOT / "utils" / "tools_lookup_views.py").read_text() + (ROOT / "cogs" / "tools_lookup.py").read_text()
    for tool, key in re.findall(r"tools\.(status|company|username)\.([a-z_.]+)[\"']", source):
        assert key in reference[tool], f"tools.{tool}.{key}"
    # error codes raised with ToolError(..., tool="x")
    for code, tool in re.findall(r'ToolError\("([a-z_]+)", tool="(status|company|username)"', source):
        assert f"errors.{code}" in reference[tool], (tool, code)
    for cat in wmn.CATEGORY_ORDER:
        assert f"categories.{cat}" in reference["username"]
    for kind in ("none", "zero", "range", "plus"):
        assert f"headcount.{kind}" in reference["company"]
    for ind in sr.INDICATOR_ORDER:
        assert f"states.{ind}" in reference["status"]
    for comp in sr.COMPONENT_SEVERITY:
        assert f"components.{comp}" in reference["status"]


def test_commands_are_localized_in_all_32_locales():
    files = sorted((ROOT / "locales" / "commands").glob("*.json"))
    assert len(files) == 32
    for path in files:
        commands = json.loads(path.read_text(encoding="utf-8"))["commands"]
        for name, param in (("status", "service"), ("company", "search"), ("username", "username")):
            entry = commands[name]
            assert entry["name"] and entry["description"] and param in entry["parameters"], (path.stem, name)
    fr = json.loads((ROOT / "locales" / "commands" / "fr.json").read_text(encoding="utf-8"))["commands"]
    assert fr["company"]["name"] == "entreprise" and fr["username"]["name"] == "pseudo"
