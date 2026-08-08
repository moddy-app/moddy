"""The Groq/Whisper limits shipped in gateway/config.py.

They mirror the provider console and are the only thing standing between a
busy day and a 429 storm, so their defaults are pinned here on purpose: this
test failing means somebody changed a production limit, which should be a
deliberate, reviewed act.
"""

from __future__ import annotations

import importlib

from gateway.config import GatewayConfig
from gateway.ratelimit import DAY, HOUR, MINUTE, UNIT_AUDIO_SECONDS, UNIT_REQUESTS

WHISPER = ("groq", "whisper-large-v3-turbo")


def rules():
    return {r.name: r for r in GatewayConfig().model_rate_limits[WHISPER]}


def test_whisper_defaults_match_the_provider_console():
    by_name = rules()
    assert by_name["rpm"].limit == 20
    assert by_name["rpd"].limit == 2_000
    assert by_name["ash"].limit == 7_200
    assert by_name["asd"].limit == 28_800


def test_whisper_rules_use_the_expected_units_and_windows():
    by_name = rules()
    assert (by_name["rpm"].unit, by_name["rpm"].window) == (UNIT_REQUESTS, MINUTE)
    assert (by_name["rpd"].unit, by_name["rpd"].window) == (UNIT_REQUESTS, DAY)
    assert (by_name["ash"].unit, by_name["ash"].window) == (UNIT_AUDIO_SECONDS, HOUR)
    assert (by_name["asd"].unit, by_name["asd"].window) == (UNIT_AUDIO_SECONDS, DAY)


def test_limits_can_be_raised_from_the_environment(monkeypatch):
    monkeypatch.setenv("GROQ_WHISPER_RPM", "60")
    import gateway.config as config_module
    importlib.reload(config_module)
    try:
        by_name = {r.name: r for r in config_module.GatewayConfig().model_rate_limits[WHISPER]}
        assert by_name["rpm"].limit == 60
    finally:
        monkeypatch.delenv("GROQ_WHISPER_RPM")
        importlib.reload(config_module)


def test_a_broken_env_value_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("GROQ_WHISPER_RPD", "not-a-number")
    import gateway.config as config_module
    importlib.reload(config_module)
    try:
        by_name = {r.name: r for r in config_module.GatewayConfig().model_rate_limits[WHISPER]}
        assert by_name["rpd"].limit == 2_000
    finally:
        monkeypatch.delenv("GROQ_WHISPER_RPD")
        importlib.reload(config_module)
