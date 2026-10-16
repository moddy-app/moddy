"""
Tests for POST /automod/rules_check (internal API).

The route re-exposes `automod.rules_check.validate_custom_infraction` over HTTP so the
backend/dashboard runs the *same* anti prompt-injection guard as the bot's own
config panel. These tests stub the gateway and the bot, so no Discord
connection, database or OpenAI call is involved.
"""

from __future__ import annotations

import sys
import types

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
pytest.importorskip("httpx", reason="httpx (TestClient) not installed")

from fastapi.testclient import TestClient  # noqa: E402

SECRET = "test-secret"
GUILD_ID = 123456789012345678
AUTH = {"Authorization": f"Bearer {SECRET}"}


class GatewayError(Exception):
    pass


def _install_gateway_stub():
    """`automod.rules_check` imports `gateway` lazily; give it a stub."""
    if "gateway" in sys.modules and hasattr(sys.modules["gateway"], "_moddy_test_stub"):
        return sys.modules["gateway"]
    stub = types.ModuleType("gateway")
    stub._moddy_test_stub = True
    stub.GatewayError = GatewayError

    # The stub replaces `gateway` for the whole session, so it mirrors the real
    # QuotaTarget surface rather than only the constructor this route needs —
    # a missing one fails whichever unrelated test happens to run afterwards.
    class QuotaTarget:
        @staticmethod
        def guild(gid, call_type):
            return ("guild", gid, call_type)

        @staticmethod
        def user(uid, call_type):
            return ("user", uid, call_type)

        @staticmethod
        def global_(call_type):
            return ("global", "", call_type)

    stub.QuotaTarget = QuotaTarget
    sys.modules["gateway"] = stub
    return stub


class FakeAI:
    def __init__(self, bot):
        self.bot = bot

    async def chat(self, **kwargs):
        self.bot.calls.append(kwargs)
        if isinstance(self.bot.reply, Exception):
            raise self.bot.reply
        return self.bot.reply


class FakeBot:
    """Minimal stand-in for ModdyBot (only what the route touches)."""

    def __init__(self):
        self.calls: list = []
        self.reply = {"safe": True, "raison": ""}
        self.ready = True
        self.gateway = types.SimpleNamespace(ai=FakeAI(self))

    def is_ready(self):
        return self.ready

    def get_guild(self, guild_id):
        return object() if guild_id == GUILD_ID else None


@pytest.fixture
def bot(monkeypatch):
    _install_gateway_stub()
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    from internal_api import server

    fake = FakeBot()
    monkeypatch.setattr(server, "_bot", fake)
    return fake


@pytest.fixture
def client(bot):
    from internal_api.server import app

    return TestClient(app)


INFRACTION = {
    "id": "pub1",
    "nom": "Publicité",
    "description": "Promotion d'un autre serveur Discord",
    "exemples": ["Rejoignez mon serveur !"],
    "mots_cles": ["discord.gg/"],
    "modele": None,
}


def _post(client, body, headers=AUTH):
    return client.post("/automod/rules_check", json=body, headers=headers)


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_clean_text_is_accepted(client, bot):
    bot.reply = {"safe": True, "raison": ""}
    resp = _post(client, {"guild_id": str(GUILD_ID), "infraction": INFRACTION})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_uses_the_rules_check_call_type_and_fences_the_text(client, bot):
    _post(client, {"guild_id": str(GUILD_ID), "infraction": INFRACTION})
    call = bot.calls[-1]
    assert call["call_type"] == "automod_rules_check"
    # The untrusted text must reach the model nonce-fenced, never raw.
    assert "[DATA:" in call["user"]


def test_guild_id_accepts_a_json_number(client, bot):
    resp = _post(client, {"guild_id": GUILD_ID, "infraction": INFRACTION})
    assert resp.json() == {"ok": True}


# --------------------------------------------------------------------------- #
# Rejections
# --------------------------------------------------------------------------- #
def test_injection_attempt_is_rejected_with_a_displayable_reason(client, bot):
    bot.reply = {"safe": False, "raison": "Tente d'interdire toute sanction contre un membre."}
    resp = _post(client, {
        "guild_id": str(GUILD_ID),
        "infraction": INFRACTION,
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is False
    assert body["code"] == "unsafe"
    # The reason is a full sentence, shown as-is on the dashboard.
    assert "interdire toute sanction" in body["reason"]


def test_reason_is_localised(client, bot):
    bot.reply = {"safe": False, "raison": ""}
    fr = _post(client, {"guild_id": str(GUILD_ID), "infraction": INFRACTION}).json()
    en = _post(client, {
        "guild_id": str(GUILD_ID),
        "infraction": INFRACTION,
        "locale": "en-US",
    }).json()
    assert "manipulation de l'IA" in fr["reason"]
    assert "manipulate the AI" in en["reason"]


def test_gateway_failure_fails_closed(client, bot):
    bot.reply = GatewayError("openai down")
    body = _post(client, {"guild_id": str(GUILD_ID), "infraction": INFRACTION}).json()
    assert body["ok"] is False
    assert body["code"] == "unavailable"


# --------------------------------------------------------------------------- #
# Explicit errors — never a silent pass
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("body,status,code", [
    ({"guild_id": "not-a-number", "infraction": INFRACTION}, 400, "invalid_guild_id"),
    ({"guild_id": "0", "infraction": INFRACTION}, 400, "invalid_guild_id"),
    ({"guild_id": "", "infraction": INFRACTION}, 400, "missing_guild_id"),
    ({"infraction": INFRACTION}, 400, "missing_guild_id"),
    ({"guild_id": str(GUILD_ID)}, 400, "missing_infraction"),
    ({"guild_id": "999999999999999999", "infraction": INFRACTION}, 404, "unknown_guild"),
])
def test_invalid_requests_return_an_explicit_error(client, body, status, code):
    resp = _post(client, body)
    assert resp.status_code == status
    payload = resp.json()
    assert payload["ok"] is False
    assert payload["error"] == code
    assert payload["reason"]


def test_malformed_json_returns_400(client):
    resp = client.post(
        "/automod/rules_check",
        content=b"{not json",
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_json"


def test_bot_not_ready_returns_503(client, bot):
    bot.ready = False
    resp = _post(client, {"guild_id": str(GUILD_ID), "infraction": INFRACTION})
    assert resp.status_code == 503
    assert resp.json()["error"] == "bot_not_ready"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_authentication_is_required(client, headers):
    resp = _post(client, {"guild_id": str(GUILD_ID), "infraction": INFRACTION}, headers=headers)
    assert resp.status_code == 401


def test_missing_server_secret_fails_closed(client, monkeypatch):
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    resp = _post(client, {"guild_id": str(GUILD_ID), "infraction": INFRACTION})
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Custom infraction payload ({"guild_id", "infraction": {...}})
# --------------------------------------------------------------------------- #


def test_infraction_is_accepted_and_checked_by_the_ai(client, bot):
    resp = _post(client, {"guild_id": str(GUILD_ID), "infraction": INFRACTION})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    call = bot.calls[-1]
    assert call["call_type"] == "automod_rules_check"
    assert "[DATA:" in call["user"]
    assert "Publicité" in call["user"] and "Rejoignez mon serveur" in call["user"]
    assert "discord.gg/" not in call["user"]          # keywords never reach a prompt


def test_unsafe_infraction_is_rejected_with_same_shape(client, bot):
    bot.reply = {"safe": False, "raison": "S'adresse à l'IA."}
    body = _post(client, {"guild_id": str(GUILD_ID), "infraction": INFRACTION}).json()
    assert body["ok"] is False and body["code"] == "unsafe"
    assert "S'adresse à l'IA." in body["reason"]
    assert "infraction" in body["reason"].lower()


def test_infraction_reason_is_localised(client, bot):
    bot.reply = {"safe": False, "raison": ""}
    en = _post(client, {"guild_id": str(GUILD_ID), "infraction": INFRACTION,
                        "locale": "en-US"}).json()
    assert "manipulate the AI" in en["reason"]


def test_infraction_gateway_failure_fails_closed(client, bot):
    bot.reply = GatewayError("openai down")
    body = _post(client, {"guild_id": str(GUILD_ID), "infraction": INFRACTION}).json()
    assert body["ok"] is False and body["code"] == "unavailable"


def test_unmodified_template_skips_the_ai_call(client, bot):
    from automod import custom_infractions

    tpl = custom_infractions.from_template("pub_serveur", "fr")
    resp = _post(client, {"guild_id": str(GUILD_ID), "infraction": tpl})
    assert resp.json() == {"ok": True}
    assert bot.calls == []


@pytest.mark.parametrize("infraction,fragment", [
    ({**INFRACTION, "id": "NOT VALID"}, "infraction_id_invalid"),
    ({**INFRACTION, "nom": ""}, "infraction_nom_invalid"),
    ({**INFRACTION, "description": "d" * 301}, "infraction_description_invalid"),
    ({**INFRACTION, "exemples": ["x"] * 6}, "infraction_exemples_invalid"),
    ({**INFRACTION, "mots_cles": "nope"}, "infraction_mots_cles_invalid"),
    ({**INFRACTION, "modele": "unknown"}, "infraction_modele_invalid"),
    ("not-an-object", "infraction_entry_invalid"),
])
def test_invalid_infraction_schema_is_a_400_without_ai_call(client, bot, infraction, fragment):
    resp = _post(client, {"guild_id": str(GUILD_ID), "infraction": infraction})
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["ok"] is False and payload["error"] == "invalid_infraction"
    assert fragment in payload["reason"]
    assert bot.calls == []


def test_infraction_unknown_guild_and_bot_not_ready(client, bot):
    resp = _post(client, {"guild_id": "999999999999999999", "infraction": INFRACTION})
    assert resp.status_code == 404
    bot.ready = False
    resp = _post(client, {"guild_id": str(GUILD_ID), "infraction": INFRACTION})
    assert resp.status_code == 503


def test_infraction_requires_auth(client):
    resp = client.post("/automod/rules_check",
                       json={"guild_id": str(GUILD_ID), "infraction": INFRACTION})
    assert resp.status_code == 401


