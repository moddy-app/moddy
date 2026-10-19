"""Text utility tools: /define /tldr /regex /diff /textstats (docs/features/TOOLS_TEXT.md).

Pure logic, card building (walking ``view.walk_children()``), modal parsing,
persistent items and i18n parity. Nothing touches the network or Discord.
"""

from __future__ import annotations

import json
import re
import types
from pathlib import Path

import discord
import pytest
from discord import ui

from cogs import tools_text as cog
from tools import wiktionary as wk
from utils import tool_card as tc
from utils import tools_text_views as v
from utils.i18n import i18n

LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")
ROOT = Path(__file__).parent.parent
TOOLS = ("define", "tldr", "regex", "diff", "textstats")


def texts(view):
    return [c.content for c in view.walk_children() if isinstance(c, ui.TextDisplay)]


def joined(view):
    return "\n".join(texts(view))


def children(view, kind):
    """Components of ``kind``; a DynamicItem is seen through the component it wraps."""
    found = []
    for c in view.walk_children():
        c = getattr(c, "item", c) if isinstance(c, ui.DynamicItem) else c
        if isinstance(c, kind):
            found.append(c)
    return found


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #


def _flatten(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flatten(value, f"{prefix}{key}.")
    else:
        yield prefix[:-1]


def test_i18n_keys_identical_in_the_five_locales():
    keys = {}
    for locale in LOCALES:
        data = json.load(open(ROOT / "locales" / f"{locale}.json", encoding="utf-8"))
        keys[locale] = {tool: set(_flatten(data["tools"][tool])) for tool in TOOLS}
    for tool in TOOLS:
        reference = keys["en-US"][tool]
        assert reference, tool
        for locale in LOCALES:
            assert keys[locale][tool] == reference, (tool, locale)


def test_every_key_used_in_code_exists():
    source = "".join((ROOT / p).read_text(encoding="utf-8")
                     for p in ("utils/tools_text_views.py", "cogs/tools_text.py"))
    data = json.load(open(ROOT / "locales" / "en-US.json", encoding="utf-8"))
    flat = {f"tools.{tool}.{k}" for tool in TOOLS for k in _flatten(data["tools"][tool])}
    for key in set(re.findall(r't\(\s*"(tools\.(?:%s)\.[a-z_.]+)"' % "|".join(TOOLS), source)):
        assert key in flat, key
    # f-string keys: every value of the dynamic part must exist too
    for key in ("pos.%s" % k for k in ("noun", "verb", "other", "proper_noun")):
        assert f"tools.define.{key}" in flat
    for lang in wk.WIKI_LANGUAGES:
        assert f"tools.define.lang.{lang}" in flat and f"tools.textstats.languages.{lang}" in flat
        assert f"tools.textstats.formulas.{lang}" in flat
    for key in ("i", "m", "s", "x"):
        assert f"tools.regex.flags.{key}" in flat
    for level in ("very_easy", "easy", "average", "difficult", "very_difficult"):
        assert f"tools.textstats.levels.{level}" in flat


def test_every_pos_key_has_a_label():
    keys = {k for _, k in wk._POS_TABLE}
    data = json.load(open(ROOT / "locales" / "en-US.json", encoding="utf-8"))
    assert keys <= set(data["tools"]["define"]["pos"])


# --------------------------------------------------------------------------- #
# /define
# --------------------------------------------------------------------------- #


def _entry(meanings=2, lang="fr", source="parse"):
    items = [
        wk.Meaning(pos="noun", label="Nom", definitions=["(zool.) Petit *félin* domestique.", "Un deuxième sens.", "Un troisième.", "Un quatrième."],
                   synonyms=["minou", "matou"]),
        wk.Meaning(pos="verb", label="Verbe", definitions=["Faire quelque chose."]),
        wk.Meaning(pos="noun", label="Nom", definitions=["Autre nom."]),
    ][:meanings]
    return wk.Entry(word="chat", lang=lang, meanings=items, source=source)


def test_wiki_language_follows_the_locale():
    assert v.wiki_language("fr") == "fr"
    assert v.wiki_language("pt-BR") == "pt"
    assert v.wiki_language("es-ES") == "es"
    assert v.wiki_language("ja") == "en"


def test_define_card_shows_three_definitions_synonyms_and_select():
    view = v.build_define_card(_entry(meanings=3), 0, "en-US", word="chat")
    body = joined(view)
    assert "### " in body and "Dictionary" in body
    assert "1. " in body and "3. " in body and "4. " not in body
    assert "minou" in body and "Synonyms" in body
    assert "1** more definition" not in body and "more definition" in body
    assert "Provided by" in body and "**Wiktionary**" in body and "CC BY-SA" in body
    selects = children(view, ui.Select)
    assert len(selects) == 1
    assert [o.label for o in selects[0].options] == ["Noun (1)", "Verb", "Noun (2)"]
    assert [o.default for o in selects[0].options] == [True, False, False]
    assert selects[0].custom_id == "moddy:tools:define:sense:fr:chat"
    links = [b for b in children(view, ui.Button) if b.url]
    assert links and links[0].url == "https://fr.wiktionary.org/wiki/chat"


def test_define_card_without_other_senses_has_no_select():
    view = v.build_define_card(_entry(meanings=1), 0, "fr", word="chat")
    assert not children(view, ui.Select)
    assert "Dictionnaire" in joined(view)


def test_define_card_selected_sense_and_bad_index():
    view = v.build_define_card(_entry(), 1, "en-US", word="chat")
    assert "Faire quelque chose" in joined(view)
    assert "Faire quelque chose" in joined(v.build_define_card(_entry(), 1, "en-US", word="chat"))
    assert "Petit" in joined(v.build_define_card(_entry(), 99, "en-US", word="chat"))


def test_define_card_escapes_wiki_markup_and_requester():
    entry = _entry()
    entry.meanings[0].definitions = ["@everyone *bold* <@1>"]
    view = v.build_define_card(entry, 0, "en-US", word="chat", requester_id=42)
    body = joined(view)
    assert "@everyone" not in body.replace("@​", "")
    assert "requested by <@42>" in body


def test_define_fallback_note_when_rest_source():
    view = v.build_define_card(_entry(lang="fr", source="rest"), 0, "en-US", word="chat")
    assert "French" in joined(view) and "English" in joined(view)
    assert children(view, ui.Button)[0].url.startswith("https://en.wiktionary.org")


def test_define_select_round_trip_and_length():
    word = "é" * 50
    item = v.DefineSenseSelect("de", word)
    custom_id = item.item.custom_id
    assert len(custom_id) <= 100
    match = v.DefineSenseSelect.__discord_ui_compiled_template__.fullmatch(custom_id)
    assert match and match["lang"] == "de" and match["word"] == word
    assert v.DefineSenseSelect.__discord_ui_compiled_template__.fullmatch("moddy:tools:define:sense:xx:a") is None


# --------------------------------------------------------------------------- #
# /regex
# --------------------------------------------------------------------------- #


async def test_regex_compute_errors_map_to_tool_errors():
    cases = [("(", "abc", "invalid_pattern"), ("a" * 600, "a", "pattern_too_long"),
             ("a{5000}", "a", "invalid_pattern"), ("", "x", "empty_pattern"), ("a", "", "empty_text")]
    for pattern, text, code in cases:
        with pytest.raises(tc.ToolError) as info:
            await v.compute_regex(pattern, text, ())
        assert info.value.code == code and info.value.tool == "regex"


async def test_regex_catastrophic_pattern_times_out():
    with pytest.raises(tc.ToolError) as info:
        await v.compute_regex(r"(a|aa)+$", "a" * 60 + "!", ())
    assert info.value.code == "timeout"


async def test_regex_card_highlights_and_tables():
    result = await v.compute_regex(r"(?P<user>\w+)@(\w+)\.com", "mail bob@site.com and amy@corp.com now", ["i"])
    assert result.total == 2
    view, files = v.build_regex_card(r"(?P<user>\w+)@(\w+)\.com", "mail bob@site.com and amy@corp.com now",
                                     ["i"], result, "en-US", token="abcDEF1234")
    body = joined(view)
    assert "`2` matches" in body
    assert "**__bob" in body.replace("\\", "") or "**__" in body
    assert "user=bob" in body and "Groups" in body
    assert "Flags" in body and "`i`" in body
    assert not files
    button = [b for b in children(view, ui.Button)][0]
    assert button.custom_id == "moddy:tools:regex:edit:abcDEF1234"
    assert view.children[0].accent_colour == discord.Colour(tc.ACCENT_SUCCESS)


async def test_regex_card_no_match_and_attachment_when_many_matches():
    result = await v.compute_regex(r"zzz", "abc", ())
    view, _ = v.build_regex_card("zzz", "abc", (), result, "fr", token="abcDEF1234")
    assert "Aucune correspondance" in joined(view)
    assert view.children[0].accent_colour == discord.Colour(tc.ACCENT_WARNING)

    text = "a " * 40
    result = await v.compute_regex(r"a", text, ())
    view, files = v.build_regex_card("a", text, (), result, "en-US", token="abcDEF1234")
    assert result.total == 40 and len(files) == 1 and files[0].filename == "regex.txt"
    assert "`40` matches" in joined(view)


def test_regex_state_normalises_flags():
    assert v.regex_state("a", "b", ["x", "i", "zz"]) == {"p": "a", "t": "b", "f": ["i", "x"]}


# --------------------------------------------------------------------------- #
# Edit buttons + state
# --------------------------------------------------------------------------- #


class FakeResponse:
    def __init__(self):
        self.calls = []
        self._done = False

    def is_done(self):
        return self._done

    async def send_modal(self, modal):
        self._done = True
        self.calls.append(("modal", modal))

    async def send_message(self, **kw):
        self._done = True
        self.calls.append(("send", kw))

    async def edit_message(self, **kw):
        self._done = True
        self.calls.append(("edit", kw))


def fake_interaction(*, ephemeral=True, owner_id=7, user_id=7, locale="en-US"):
    flags = types.SimpleNamespace(ephemeral=ephemeral)
    message = types.SimpleNamespace(flags=flags,
                                    interaction_metadata=types.SimpleNamespace(user=types.SimpleNamespace(id=owner_id)))
    bot = types.SimpleNamespace(redis=None)
    return types.SimpleNamespace(
        response=FakeResponse(), client=bot, message=message, user=types.SimpleNamespace(id=user_id),
        locale=discord.Locale.american_english, data={}, followup=types.SimpleNamespace())


async def test_regex_edit_button_reopens_prefilled_modal():
    interaction = fake_interaction()
    token = await tc.save_state(interaction.client, v.STATE_REGEX, v.regex_state(r"\d+", "a1 b22", ["m"]))
    assert re.fullmatch(r"[A-Za-z0-9]{10}", token)
    item = v.RegexEditButton(token)
    await item.callback(interaction)
    kind, modal = interaction.response.calls[0]
    assert kind == "modal" and isinstance(modal, v.RegexModal)
    assert modal.pattern.component.default == r"\d+"
    assert modal.text.component.default == "a1 b22"
    assert [o.value for o in modal.flags.component.options if o.default] == ["m"]
    assert modal.edit_in_place is True


async def test_diff_edit_button_and_expired_state():
    interaction = fake_interaction(ephemeral=False)
    token = await tc.save_state(interaction.client, v.STATE_DIFF, v.diff_state("a", "b"))
    await v.DiffEditButton(token).callback(interaction)
    kind, modal = interaction.response.calls[0]
    assert isinstance(modal, v.DiffModal) and modal.edit_in_place is False
    assert modal.before.component.default == "a" and modal.after.component.default == "b"

    expired = fake_interaction()
    await v.DiffEditButton("zzzzzzzzzz").callback(expired)
    kind, kw = expired.response.calls[0]
    assert kind == "send" and "expired" in joined(kw["view"]).lower()


def test_ownership_rules():
    assert v.owns_message(fake_interaction(ephemeral=True, owner_id=1, user_id=2))
    assert v.owns_message(fake_interaction(ephemeral=False, owner_id=2, user_id=2))
    assert not v.owns_message(fake_interaction(ephemeral=False, owner_id=1, user_id=2))


async def test_deliver_edits_in_place_or_sends():
    interaction = fake_interaction()
    view = ui.LayoutView(timeout=None)
    await v.deliver(interaction, view, edit_in_place=True)
    assert interaction.response.calls[0][0] == "edit"
    assert interaction.response.calls[0][1]["attachments"] == []
    other = fake_interaction()
    await v.deliver(other, view, ephemeral=False)
    assert other.response.calls[0][0] == "send" and other.response.calls[0][1]["ephemeral"] is False


async def test_regex_submit_error_card_carries_the_edit_button():
    interaction = fake_interaction()
    await v.submit_regex(interaction, "(", "text", [], ephemeral=True, edit_in_place=False)
    kind, kw = interaction.response.calls[0]
    assert kind == "send" and kw["ephemeral"] is True
    buttons = children(kw["view"], ui.Button)
    assert buttons and buttons[0].custom_id.startswith("moddy:tools:regex:edit:")
    assert "not valid" in joined(kw["view"])


# --------------------------------------------------------------------------- #
# Modals
# --------------------------------------------------------------------------- #


def test_modals_are_valid_v2_modals():
    for modal in (v.RegexModal("fr", ephemeral=True), v.DiffModal("de", ephemeral=False),
                  v.TextstatsModal("es-ES", ephemeral=True)):
        payload = modal.to_components()
        assert len(payload) <= 5
        assert len(modal.title) <= 45
        assert any(c["type"] == 10 for c in payload)  # help TextDisplay first
    modal = v.RegexModal("en-US", ephemeral=True)
    assert isinstance(modal.flags.component, ui.CheckboxGroup)
    assert len(modal.flags.component.options) == 4
    assert not hasattr(v.DiffModal("en-US", ephemeral=True), "mode")


async def test_diff_submit_with_nothing_is_an_error():
    interaction = fake_interaction()
    await v.submit_diff(interaction, "", "", ephemeral=True, edit_in_place=False)
    assert "at least one" in joined(interaction.response.calls[0][1]["view"])


# --------------------------------------------------------------------------- #
# /diff
# --------------------------------------------------------------------------- #


async def test_diff_lines_card():
    result = await v.compute_diff("a\nb\nc", "a\nB\nc\nd")
    view, files = v.build_diff_card(result, "en-US", token="abcDEF1234")
    body = joined(view)
    assert "```diff" in body and "- b" in body and "+ B" in body
    assert "`+2 -1`" in body
    assert not files
    assert children(view, ui.Button)[0].custom_id == "moddy:tools:diff:edit:abcDEF1234"


async def test_diff_is_a_coloured_code_block_only():
    result = await v.compute_diff("the quick fox", "the slow fox")
    view, _ = v.build_diff_card(result, "en-US", token="abcDEF1234")
    body = joined(view)
    assert "```diff" in body and "- the quick fox" in body and "+ the slow fox" in body
    assert "~~" not in body
    assert "red" in body and "green" in body


async def test_diff_identical_and_long_output_attaches():
    result = await v.compute_diff("same", "same")
    view, _ = v.build_diff_card(result, "fr", token="abcDEF1234")
    assert "identiques" in joined(view)

    before = "\n".join(f"line {i}" for i in range(400))
    after = "\n".join(f"LINE {i}" for i in range(400))
    result = await v.compute_diff(before, after)
    view, files = v.build_diff_card(result, "en-US", token="abcDEF1234")
    assert files and files[0].filename == "diff.diff"
    assert len(joined(view)) < 4000


# --------------------------------------------------------------------------- #
# /textstats
# --------------------------------------------------------------------------- #

LONG_FR = ("Le chat dort sur le canapé. Il fait beau aujourd'hui et les enfants jouent dans le jardin. "
           "Nous avons mangé des pommes avec du fromage, puis nous sommes allés à la plage ensemble. "
           "Le soir, la famille se retrouve autour de la table pour parler de la journée. "
           "Les oiseaux chantent dans les arbres pendant que le soleil se couche doucement.")


async def test_textstats_card_with_readability():
    stats = await v.compute_textstats(LONG_FR)
    assert stats.language == "fr" and stats.readability is not None
    body = joined(v.build_textstats_card(stats, "fr"))
    assert "Statistiques du texte" in body and "Lisibilité" in body
    assert "Kandel-Moles" in body and "/100" in body
    assert "Temps de lecture" in body


async def test_textstats_short_text_explains_missing_score():
    stats = await v.compute_textstats("Hello world.")
    body = joined(v.build_textstats_card(stats, "en-US", requester_id=5))
    assert "at least" in body and "requested by <@5>" in body


async def test_textstats_empty_is_an_error():
    with pytest.raises(tc.ToolError):
        await v.compute_textstats("   ")


# --------------------------------------------------------------------------- #
# /tldr helpers
# --------------------------------------------------------------------------- #


def _message(i, content, *, bot=False, mtype=discord.MessageType.default, attachments=()):
    return types.SimpleNamespace(
        id=i, author=types.SimpleNamespace(bot=bot, display_name=f"User{i}"), type=mtype,
        clean_content=content, attachments=list(attachments), jump_url=f"https://discord.com/channels/1/2/{i}")


def test_format_history_filters_numbers_and_neutralises_mentions():
    messages = [
        _message(1, "hello @everyone and @bob"),
        _message(2, "from a bot", bot=True),
        _message(3, "", attachments=[object()]),
        _message(4, "joined", mtype=discord.MessageType.new_member),
        _message(5, ""),
        _message(6, "last one"),
    ]
    transcript, urls = cog.format_history(messages)
    lines = transcript.splitlines()
    assert len(lines) == 3 and urls == {1: messages[0].jump_url, 2: messages[2].jump_url, 3: messages[5].jump_url}
    assert "@" not in transcript and "everyone" in lines[0]
    assert lines[1].endswith("[attachment]")
    assert lines[2].startswith("#3 User6: last one")


def test_format_history_keeps_the_newest_when_too_long():
    messages = [_message(i, "x" * 300) for i in range(1, 80)]
    transcript, urls = cog.format_history(messages, limit=3000)
    assert len(transcript) <= 3000 + 200
    assert max(urls) == len(urls) and urls[len(urls)].endswith("/79")


def test_link_references():
    urls = {1: "https://x/1", 2: "https://x/2"}
    out = cog.link_references("- point [#2]\n- other [#9]\n- plain", urls, "jump")
    assert "[jump](https://x/2)" in out and "#9" not in out and "- plain" in out


def test_tldr_prompt_is_fenced_and_localised():
    prompt = cog.tldr_system_prompt("NONCE", "French", True)
    assert "[DATA:NONCE]" in prompt and "French" in prompt and "'@'" in prompt
    assert "'@'" not in cog.tldr_system_prompt("N", "French", False)


def test_tldr_card():
    view = v.build_tldr_card("- one\n- two", "en-US", channel_id=99, count=50, requester_id=3)
    body = joined(view)
    assert "<#99>" in body and "Summarized by" in body and "requested by <@3>" in body


# --------------------------------------------------------------------------- #
# The cog
# --------------------------------------------------------------------------- #


class FakeBot:
    def __init__(self):
        self.public_api = None
        self.gateway = None
        self.redis = None


def _command(name):
    instance = cog.ToolsText(FakeBot())
    return {c.name: c for c in instance.get_app_commands()}[name]


def test_commands_are_declared_with_the_right_scopes():
    instance = cog.ToolsText(FakeBot())
    commands = {c.name: c for c in instance.get_app_commands()}
    assert set(commands) == {"define", "tldr", "regex", "diff", "textstats"}
    assert commands["tldr"].guild_only is True
    for name in ("define", "regex", "diff", "textstats"):
        assert commands[name].guild_only is False
        assert commands[name].allowed_contexts.dm_channel is True
        assert commands[name].allowed_installs.user is True
    assert commands["tldr"].allowed_installs.user is False
    for name in ("define", "tldr", "regex", "diff", "textstats"):
        assert "incognito" in {p.name for p in commands[name].parameters}
    assert {c.value for c in commands["tldr"]._params["messages"].choices} == {25, 50, 100}
    assert {c.value for c in commands["define"]._params["language"].choices} == set(wk.WIKI_LANGUAGES)


def test_tldr_cooldown():
    instance = cog.ToolsText(FakeBot())
    instance._check_cooldown(1)
    with pytest.raises(tc.ToolError) as info:
        instance._check_cooldown(1)
    assert info.value.code == "cooldown"
    instance._check_cooldown(2)


def test_persistence_registration():
    from utils import tool_views_registry as reg
    assert v.ToolsTextPersistence in reg.persistent_view_classes()
    names = {cls.__name__ for cls, _ in reg.dynamic_item_cases()}
    assert {"DefineSenseSelect", "RegexEditButton", "DiffEditButton"} <= names
    for item_cls, args in v.DYNAMIC_ITEM_CASES:
        custom_id = item_cls(*args).item.custom_id
        assert len(custom_id) <= 100 and item_cls.__discord_ui_compiled_template__.fullmatch(custom_id)


# --------------------------------------------------------------------------- #
# /tldr end to end (fake gateway)
# --------------------------------------------------------------------------- #


class FakeAI:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    async def chat(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.result


def _tldr_cog(ai):
    bot = FakeBot()
    bot.gateway = types.SimpleNamespace(ai=ai, openai_available=lambda: True)
    return cog.ToolsText(bot)


def _tldr_interaction():
    return types.SimpleNamespace(user=types.SimpleNamespace(id=5), guild=types.SimpleNamespace(id=9))


async def test_tldr_summarise_links_references_and_uses_the_tldr_quota():
    ai = FakeAI("- Decision made [#2]\n- Nothing else [#42]")
    instance = _tldr_cog(ai)
    history = [_message(i, f"message number {i}") for i in range(1, 6)]
    summary = await instance._summarise(_tldr_interaction(), history, "en-US", strip=True)
    assert "(https://discord.com/channels/1/2/2)" in summary and "#42" not in summary
    call = ai.calls[0]
    assert call["call_type"] == "tldr" and call["model"] == cog.TLDR_MODEL
    assert {(q.scope, q.type) for q in call["quota"]} == {("user", "tldr"), ("guild", "tldr")}
    assert call["user"].startswith("[DATA:") and "#1 User1: message number 1" in call["user"]


async def test_tldr_errors_map_to_tool_errors():
    from gateway.errors import GatewayError, QuotaExceededError
    from gateway.spec import QuotaTarget

    history = [_message(i, f"message number {i}") for i in range(1, 6)]
    for error, code in ((QuotaExceededError(QuotaTarget.user(5, "tldr")), "quota"),
                        (GatewayError("boom"), "unavailable")):
        with pytest.raises(tc.ToolError) as info:
            await _tldr_cog(FakeAI(error=error))._summarise(_tldr_interaction(), history, "en-US", strip=False)
        assert info.value.code == code
    with pytest.raises(tc.ToolError) as info:
        await _tldr_cog(FakeAI("x"))._summarise(_tldr_interaction(), history[:2], "en-US", strip=False)
    assert info.value.code == "not_enough"
