"""Icon theme: white icons are swapped for their ``_dark`` twins on the light theme.

Covers the emoji mapping, the pure payload transforms, the discord.py guard
(a library upgrade that moves a patched attribute must fail here), and the
patched payload builders with a real Components V2 view.
"""
from __future__ import annotations

import discord
import pytest
from discord import ui
from discord import http as discord_http

from utils import emojis, icon_theme

SETTINGS_ID = 1519800032499339354
SETTINGS_DARK_ID = emojis.DARK_VARIANTS["settings_dark"]
UNMAPPED = "<:green_status:1450929035428495505>"
UNMAPPED_ID = 1450929035428495505


@pytest.fixture(autouse=True)
def _installed():
    icon_theme.install()
    yield


# --------------------------------------------------------------------------- #
# Mapping
# --------------------------------------------------------------------------- #

def test_settings_maps_to_settings_dark():
    assert emojis.LIGHT_TO_DARK[SETTINGS_ID] == ("settings_dark", SETTINGS_DARK_ID)


def test_every_mapping_points_at_a_declared_dark_variant():
    for name, dark_id in emojis.LIGHT_TO_DARK.values():
        assert emojis.DARK_VARIANTS[name] == dark_id


def test_all_ids_sharing_a_name_are_mapped():
    # Two uploads of "warning" exist (old + new): both must map.
    assert emojis.LIGHT_TO_DARK[1519789903100121139][0] == "warning_dark"
    assert emojis.LIGHT_TO_DARK[1398729560895422505][0] == "warning_dark"


def test_unmapped_icons_are_not_in_the_table():
    assert UNMAPPED_ID not in emojis.LIGHT_TO_DARK


# --------------------------------------------------------------------------- #
# swap_text / swap_payload
# --------------------------------------------------------------------------- #

def test_swap_text_replaces_static_and_animated_tokens():
    out = icon_theme.swap_text(f"### {emojis.SETTINGS} Config {UNMAPPED}")
    assert f"<:settings_dark:{SETTINGS_DARK_ID}>" in out
    assert UNMAPPED in out
    assert "settings:1519800032499339354" not in out


def test_swap_text_keeps_animated_flag():
    emojis.LIGHT_TO_DARK[999000000000000001] = ("x_dark", 5)
    try:
        assert icon_theme.swap_text("<a:x:999000000000000001>") == "<a:x_dark:5>"
    finally:
        del emojis.LIGHT_TO_DARK[999000000000000001]


def test_swap_text_without_tokens_is_identity():
    assert icon_theme.swap_text("plain <b> text") == "plain <b> text"
    assert icon_theme.swap_text("") == ""


def _payload():
    return {
        "flags": 32768,
        "components": [
            {"type": 17, "components": [
                {"type": 10, "content": f"### {emojis.SETTINGS} Title"},
                {"type": 1, "components": [
                    {"type": 2, "custom_id": "a", "label": "Go",
                     "emoji": {"id": str(SETTINGS_ID), "name": "settings"}},
                    {"type": 2, "custom_id": "b", "label": "Other",
                     "emoji": {"id": str(UNMAPPED_ID), "name": "green_status"}},
                ]},
                {"type": 1, "components": [
                    {"type": 3, "custom_id": "s", "placeholder": f"{emojis.SETTINGS} pick",
                     "options": [{"label": "o", "value": "1",
                                  "emoji": {"id": SETTINGS_ID, "name": "settings"}}]},
                ]},
            ]},
        ],
    }


def test_swap_payload_on_components_v2_payload():
    original = _payload()
    out = icon_theme.swap_payload(original)
    container = out["components"][0]["components"]
    assert f"settings_dark:{SETTINGS_DARK_ID}" in container[0]["content"]
    btn_ok, btn_other = container[1]["components"]
    assert btn_ok["emoji"] == {"id": str(SETTINGS_DARK_ID), "name": "settings_dark"}
    assert btn_other["emoji"] == {"id": str(UNMAPPED_ID), "name": "green_status"}
    select = container[2]["components"][0]
    assert select["placeholder"].startswith(f"<:settings_dark:{SETTINGS_DARK_ID}>")
    assert select["options"][0]["emoji"]["id"] == SETTINGS_DARK_ID  # int stays int
    assert out["flags"] == 32768
    # The input is never mutated.
    assert original == _payload()


# --------------------------------------------------------------------------- #
# Theme state
# --------------------------------------------------------------------------- #

def test_default_theme_is_dark_and_context_resets():
    assert icon_theme.current_theme() == "dark"
    with icon_theme.use_icon_theme("light"):
        assert icon_theme.current_theme() == "light"
    assert icon_theme.current_theme() == "dark"


def test_use_icon_theme_for_reads_preferences(monkeypatch):
    from utils import user_preferences
    monkeypatch.setattr(user_preferences, "get_icon_theme",
                        lambda uid: "light" if uid == 7 else "dark")
    with icon_theme.use_icon_theme_for(7):
        assert icon_theme.current_theme() == "light"
    with icon_theme.use_icon_theme_for(8):
        assert icon_theme.current_theme() == "dark"


def test_use_icon_theme_for_falls_back_on_error(monkeypatch):
    from utils import user_preferences

    def boom(uid):
        raise RuntimeError("cache down")

    monkeypatch.setattr(user_preferences, "get_icon_theme", boom)
    with icon_theme.use_icon_theme_for(1):
        assert icon_theme.current_theme() == "dark"


# --------------------------------------------------------------------------- #
# Patched payload builders
# --------------------------------------------------------------------------- #

class _View(ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        row = ui.ActionRow()
        row.add_item(ui.Button(custom_id="t:a", label="Go",
                               emoji=discord.PartialEmoji.from_str(emojis.SETTINGS)))
        row.add_item(ui.Button(custom_id="t:b", label="Other",
                               emoji=discord.PartialEmoji.from_str(UNMAPPED)))
        self.add_item(ui.Container(ui.TextDisplay(f"### {emojis.SETTINGS} Title"), row))


async def test_handle_message_parameters_light_theme_swaps_icons():
    view = _View()
    with icon_theme.use_icon_theme("light"):
        with discord_http.handle_message_parameters(view=view) as params:
            payload = params.payload
    dumped = repr(payload)
    assert str(SETTINGS_ID) not in dumped
    assert str(SETTINGS_DARK_ID) in dumped
    assert str(UNMAPPED_ID) in dumped
    # The view itself is untouched (shared with other users' sends).
    assert str(SETTINGS_ID) in repr(view.to_components())


async def test_handle_message_parameters_dark_theme_is_noop():
    view = _View()
    with discord_http.handle_message_parameters(view=view) as params:
        dumped = repr(params.payload)
    assert str(SETTINGS_ID) in dumped
    assert str(SETTINGS_DARK_ID) not in dumped


async def test_light_theme_with_files_rewrites_payload_json():
    import io
    file = discord.File(io.BytesIO(b"x"), filename="a.txt")
    with icon_theme.use_icon_theme("light"):
        with discord_http.handle_message_parameters(content=f"hi {emojis.SETTINGS}", files=[file]) as params:
            part = params.multipart[0]
    assert part["name"] == "payload_json"
    assert str(SETTINGS_DARK_ID) in part["value"]
    assert str(SETTINGS_ID) not in part["value"]


def test_interaction_response_params_covers_modal_payload():
    from discord.webhook import async_ as wh
    with icon_theme.use_icon_theme("light"):
        params = wh.interaction_response_params(9, {"title": f"{emojis.SETTINGS} T", "components": []})
    assert str(SETTINGS_DARK_ID) in params.payload["data"]["title"]


def test_interaction_message_response_params_swaps():
    from discord.interactions import interaction_message_response_params
    with icon_theme.use_icon_theme("light"):
        params = interaction_message_response_params(type=4, content=f"{emojis.SETTINGS} x")
    assert str(SETTINGS_DARK_ID) in params.payload["data"]["content"]


def test_a_failing_transform_never_breaks_the_send(monkeypatch):
    monkeypatch.setattr(icon_theme, "swap_payload", lambda o: 1 / 0)
    with icon_theme.use_icon_theme("light"):
        with discord_http.handle_message_parameters(content=f"{emojis.SETTINGS}") as params:
            assert str(SETTINGS_ID) in params.payload["content"]


# --------------------------------------------------------------------------- #
# Theme selection from the interaction
# --------------------------------------------------------------------------- #

class _FakeUser:
    id = 42


class _FakeParent:
    user = _FakeUser()
    token = "tok"


async def test_interaction_response_wrapper_sets_theme(monkeypatch):
    from utils import user_preferences
    monkeypatch.setattr(user_preferences, "get_icon_theme", lambda uid: "light")
    seen = {}

    async def original(self, *a, **k):
        seen["theme"] = icon_theme.current_theme()

    wrapped = icon_theme._wrap_interaction_coro(original, lambda s: s._parent)
    holder = type("R", (), {"_parent": _FakeParent()})()
    await wrapped(holder)
    assert seen["theme"] == "light"
    assert icon_theme.current_theme() == "dark"


async def test_webhook_wrapper_uses_remembered_token(monkeypatch):
    from utils import user_preferences
    monkeypatch.setattr(user_preferences, "get_icon_theme", lambda uid: "light")
    icon_theme._remember_token(_FakeParent())
    seen = {}

    async def original(self, *a, **k):
        seen["theme"] = icon_theme.current_theme()

    wrapped = icon_theme._wrap_webhook_coro(original)
    await wrapped(type("W", (), {"token": "tok"})())
    assert seen["theme"] == "light"
    await wrapped(type("W", (), {"token": "unknown"})())
    assert seen["theme"] == "dark"


# --------------------------------------------------------------------------- #
# discord.py upgrade guard
# --------------------------------------------------------------------------- #

def test_every_patched_discord_attribute_exists_and_is_patched():
    import importlib
    for mod_name, attr, _ in icon_theme.PARAMS_BUILDER_TARGETS:
        fn = getattr(importlib.import_module(mod_name), attr)
        assert getattr(fn, "__icon_theme_patched__", False), f"{mod_name}.{attr}"
    for mod_name, cls_name, method in (icon_theme.INTERACTION_METHOD_TARGETS
                                       + icon_theme.WEBHOOK_METHOD_TARGETS
                                       + [icon_theme.FOLLOWUP_TARGET]):
        cls = getattr(importlib.import_module(mod_name), cls_name)
        assert getattr(cls.__dict__[method], "__icon_theme_patched__", False), f"{cls_name}.{method}"


def test_install_is_idempotent():
    from discord import http
    before = http.handle_message_parameters
    icon_theme.install()
    icon_theme.install()
    assert http.handle_message_parameters is before
