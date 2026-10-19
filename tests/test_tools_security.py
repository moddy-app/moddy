"""Security & dev tools: /pwned /password /uuid /hash /jwt /color.

Pure logic, card building (walking ``view.walk_children()``), error mapping,
modal wiring, DynamicItem round-trips and i18n parity. No network, no Discord.
"""

import asyncio
import base64
import hashlib
import io
import json
import re
import types
import uuid

import discord
import pytest

from cogs import tools_security as cog_mod
from services.public_api import PublicAPIError
from tools import color as colors
from tools import generators as gen
from tools import hashing as hs
from tools import jwt_decode as jwtd
from utils import tool_card as tc
from utils import tools_security_views as views
from utils.i18n import i18n, t

LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")
NAMESPACES = ("pwned", "password", "uuid", "hash", "jwt", "color")


def _texts(view):
    return [c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay)]


def _all_text(view):
    return "\n".join(_texts(view))


def _jwt(header, payload, sig="c2ln"):
    def enc(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{enc(header)}.{enc(payload)}.{sig}"


# --------------------------------------------------------------------------- #
# hashing
# --------------------------------------------------------------------------- #


def test_hash_text_matches_hashlib_and_is_utf8():
    digests = hs.hash_text("héllo")
    raw = "héllo".encode("utf-8")
    assert digests["md5"] == hashlib.md5(raw).hexdigest()
    assert digests["sha1"] == hashlib.sha1(raw).hexdigest()
    assert digests["sha256"] == hashlib.sha256(raw).hexdigest()
    assert digests["sha512"] == hashlib.sha512(raw).hexdigest()
    assert {k: len(v) for k, v in digests.items()} == hs.DIGEST_LENGTHS


def test_hash_empty_and_multi_chunk_bytes():
    assert hs.hash_bytes(b"")["sha256"] == hashlib.sha256(b"").hexdigest()
    big = b"x" * (hs.CHUNK_SIZE * 2 + 5)
    assert hs.hash_bytes(big)["sha1"] == hashlib.sha1(big).hexdigest()


def test_match_digest_normalises_and_finds_algorithm():
    digests = hs.hash_text("abc")
    sha = digests["sha256"]
    spaced = ":".join(sha[i:i + 2] for i in range(0, 64, 2)).upper()
    assert hs.match_digest(digests, spaced) == "sha256"
    assert hs.match_digest(digests, "0x" + digests["md5"]) == "md5"
    assert hs.match_digest(digests, "  ") is None
    assert hs.match_digest(digests, "not hex!") is None
    assert hs.match_digest(digests, "00" * 32) is None
    assert hs.algorithm_label("sha1") == "SHA-1"


def test_hibp_prefix_suffix_and_range_parsing():
    prefix, suffix = hs.sha1_parts("password")
    assert (prefix, suffix) == ("5BAA6", "1E4C9B93F3F0682250B6CF8331B7EE68FD8")
    body = f"AAAA:1\r\n{suffix}:9545824\r\nBBBB:0"
    assert hs.parse_hibp_range(body, suffix) == 9545824
    assert hs.parse_hibp_range(body.lower(), suffix.lower()) == 9545824
    # padding entries (count 0) and garbage never count as a hit
    assert hs.parse_hibp_range(f"{suffix}:0", suffix) == 0
    assert hs.parse_hibp_range(f"{suffix}:abc", suffix) == 0
    assert hs.parse_hibp_range("", suffix) == 0


# --------------------------------------------------------------------------- #
# generators
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", gen.KINDS[:2])
def test_password_has_every_class_and_length(kind):
    for _ in range(40):
        pw = gen.generate_password(kind, 16)
        assert len(pw) == 16
        assert any(c.isupper() for c in pw) and any(c.islower() for c in pw)
        assert any(c.isdigit() for c in pw) and any(c in gen.SYMBOLS for c in pw)
        if kind == gen.KIND_CLEAR:
            assert not set(pw) & set(gen.LOOK_ALIKES + gen.AMBIGUOUS_SYMBOLS)


def test_password_length_is_clamped():
    assert len(gen.generate_password(gen.KIND_STRONG, 1)) == gen.MIN_LENGTH
    assert len(gen.generate_password(gen.KIND_STRONG, 10_000)) == gen.MAX_LENGTH


def test_passphrase_shape_and_wordlist():
    assert len(gen.wordlist()) == 7776
    assert gen.word_count_for(8) == 4 and gen.word_count_for(128) == 10
    phrase = gen.generate_password(gen.KIND_PASSPHRASE, 24)
    parts = phrase.split("-")
    assert len(parts) == 6
    assert sum(any(c.isdigit() for c in p) for p in parts) == 1
    assert sum(p[0].isupper() for p in parts) == 1


def test_entropy_and_strength_labels():
    assert gen.strength(10) == "weak" and gen.strength(50) == "fair"
    assert gen.strength(70) == "good" and gen.strength(100) == "strong"
    assert gen.strength(130) == "excellent"
    assert gen.entropy_bits(gen.KIND_STRONG, 20) > gen.entropy_bits(gen.KIND_CLEAR, 20)
    assert gen.entropy_bits(gen.KIND_PASSPHRASE, 24) > 70
    assert 1 <= gen.strength_cells(1) <= gen.strength_cells(200) == 10


def test_uuid_versions_and_count_bounds():
    four = gen.generate_uuids("v4", 3)
    assert len(four) == 3 and all(uuid.UUID(u).version == 4 for u in four)
    seven = gen.uuid7(now_ms=1_700_000_000_000)
    assert seven.version == 7 and seven.variant == uuid.RFC_4122
    assert seven.int >> 80 == 1_700_000_000_000
    assert len(gen.generate_uuids("v7", 999)) == gen.MAX_COUNT
    assert len(gen.generate_uuids("v4", 0)) == gen.MIN_COUNT
    # v7 sorts by creation time
    early, late = gen.uuid7(now_ms=1000), gen.uuid7(now_ms=2000)
    assert str(early) < str(late)


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #


def test_jwt_decode_basic_and_prefixes():
    token = _jwt({"alg": "HS256", "typ": "JWT"}, {"sub": "42", "exp": 4_000_000_000})
    for raw in (token, f"Bearer {token}", f'"{token}"', f"Authorization: Bearer {token}",
                token[:20] + "\n" + token[20:]):
        decoded = jwtd.decode(raw, now=1_700_000_000)
        assert decoded.header["alg"] == "HS256" and decoded.payload["sub"] == "42"
        assert decoded.status == jwtd.STATUS_VALID and decoded.signature_present


def test_jwt_status_and_alg_none():
    now = 1_700_000_000
    expired = jwtd.decode(_jwt({"alg": "none"}, {"exp": now - 5}, sig=""), now=now)
    assert expired.status == jwtd.STATUS_EXPIRED and expired.alg_none
    assert not expired.signature_present
    early = jwtd.decode(_jwt({"alg": "HS256"}, {"nbf": now + 100}), now=now)
    assert early.status == jwtd.STATUS_NOT_YET
    plain = jwtd.decode(_jwt({"alg": "HS256"}, {"sub": "x"}), now=now)
    assert plain.status == jwtd.STATUS_NONE


def test_jwt_time_claim_edge_cases():
    decoded = jwtd.decode(_jwt({"alg": "HS256"}, {"exp": True, "iat": "soon", "nbf": 1e30}),
                          now=1_700_000_000)
    assert sorted(decoded.issues) == ["bad_time:exp", "bad_time:iat", "bad_time:nbf"]
    ms = jwtd.decode(_jwt({"alg": "HS256"}, {"iat": 1_700_000_000_000}))
    assert ms.times["iat"] == 1_700_000_000


@pytest.mark.parametrize("raw,code", [
    ("", "empty"), ("   ", "empty"), ("abc", "not_a_jwt"), ("a.b", "not_a_jwt"),
    ("!!!.???.###", "not_a_jwt"), ("a" * (jwtd.MAX_TOKEN_LENGTH + 1), "not_a_jwt"),
    (_jwt({"alg": "HS256"}, {}) + ".extra.parts", "not_a_jwt"),
])
def test_jwt_errors(raw, code):
    with pytest.raises(jwtd.JwtError) as info:
        jwtd.decode(raw)
    assert info.value.code == code


def test_jwt_encrypted_token_is_recognised():
    seg = base64.urlsafe_b64encode(b'{"alg":"RSA-OAEP","enc":"A256GCM"}').decode().rstrip("=")
    with pytest.raises(jwtd.JwtError) as info:
        jwtd.decode(f"{seg}.a.b.c.d")
    assert info.value.code == "encrypted"


# --------------------------------------------------------------------------- #
# colour
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text,rgb", [
    ("#5865F2", (88, 101, 242)), ("5865f2", (88, 101, 242)), ("#fff", (255, 255, 255)),
    ("0x000000", (0, 0, 0)), ("rgb(88, 101, 242)", (88, 101, 242)),
    ("RGB(100%, 0%, 0%)", (255, 0, 0)), ("hsl(0, 100%, 50%)", (255, 0, 0)),
    ("rebeccapurple", (102, 51, 153)), ("Rebecca Purple", (102, 51, 153)),
    ("5793266", (88, 101, 242)), ("`#FF0000`", (255, 0, 0)),
])
def test_color_parse(text, rgb):
    assert colors.parse(text).rgb == rgb


def test_color_alpha_and_random():
    assert colors.parse("#ff000080").alpha == pytest.approx(128 / 255, abs=0.01)
    assert colors.parse("rgba(1, 2, 3, 0.5)").alpha == 0.5
    parsed = colors.parse("random")
    assert parsed.random and len(parsed.rgb) == 3


@pytest.mark.parametrize("text,code", [
    ("", "empty"), ("not a colour", "invalid"), ("#12345", "invalid"),
    ("rgb(1,2)", "invalid"), ("16777216", "out_of_range"),
])
def test_color_errors(text, code):
    with pytest.raises(colors.ColorError) as info:
        colors.parse(text)
    assert info.value.code == code


def test_color_int_roundtrip_and_wcag():
    assert colors.int_to_rgb(colors.rgb_to_int((12, 34, 56))) == (12, 34, 56)
    best = colors.wcag(colors.BLACK, colors.WHITE)
    assert best.ratio == pytest.approx(21.0)
    assert best.aa and best.aaa and best.aa_large and best.aaa_large
    worst = colors.wcag(colors.WHITE, colors.WHITE)
    assert worst.ratio == pytest.approx(1.0) and not worst.aa and not worst.aa_large
    assert colors.closest_name((102, 51, 153)) == ("rebeccapurple", True)


def test_color_preview_is_a_png():
    png = colors.render_preview((88, 101, 242))
    assert png.startswith(b"\x89PNG")
    assert colors.FONT_PATH.exists()


# --------------------------------------------------------------------------- #
# cards
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("locale", LOCALES)
def test_pwned_card_verdicts(locale):
    bad = views.build_pwned_card(1234567, locale)
    good = views.build_pwned_card(0, locale)
    assert "/password" in _all_text(bad) and "/password" in _all_text(good)
    assert bad.children[0].accent_colour != good.children[0].accent_colour
    assert good.children[0].accent_colour.value == tc.ACCENT_SUCCESS
    assert bad.children[0].accent_colour.value == tc.ACCENT_ERROR
    # the password itself can never appear on the card: only a count
    assert "{" not in _all_text(bad)


def test_password_card_contains_password_and_persistent_button():
    view = views.build_password_card("Ab3$xyz!", gen.KIND_STRONG, 8, "en-US")
    assert "Ab3$xyz!" in _all_text(view)
    ids = [c.custom_id for c in view.walk_children() if hasattr(c, "custom_id")]
    assert ids == ["moddy:tools:password:regen:strong:8"]
    assert "computed" in _all_text(view).lower()


def test_uuid_card_lists_every_uuid():
    ids = gen.generate_uuids("v7", 4)
    text = _all_text(views.build_uuid_card(ids, "v7", "en-US"))
    assert all(u in text for u in ids)


def test_hash_card_match_and_mismatch():
    digests = hs.hash_text("abc")
    ok = views.build_hash_card(digests, subject="s", is_text=True, locale="en-US",
                               match="sha256", compared=True)
    no = views.build_hash_card(digests, subject="s", is_text=True, locale="en-US",
                               match=None, compared=True)
    plain = views.build_hash_card(digests, subject="s", is_text=False, locale="en-US",
                                  requester_id=7)
    assert ok.children[0].accent_colour.value == tc.ACCENT_SUCCESS
    assert no.children[0].accent_colour.value == tc.ACCENT_ERROR
    assert plain.children[0].accent_colour.value == tc.ACCENT_INFO
    assert all(d in _all_text(plain) for d in digests.values())
    assert "<@7>" in _all_text(plain)  # public answer names the requester
    assert "<@" not in _all_text(ok)


def test_jwt_card_never_claims_verification_and_attaches_when_long():
    token = _jwt({"alg": "HS256"}, {"iss": "me", "aud": ["a", "b"], "exp": 4_000_000_000,
                                    "blob": "x" * 2500})
    view, files = views.build_jwt_card(jwtd.decode(token), "en-US")
    text = _all_text(view)
    assert t("tools.jwt.unverified", locale="en-US") in text
    assert "a, b" in text
    assert len(files) == 1 and files[0].filename == views.JWT_FILENAME
    short, none = views.build_jwt_card(jwtd.decode(_jwt({"alg": "none"}, {"x": 1}, "")), "en-US")
    assert none == [] and t("tools.jwt.alg_none", locale="en-US") in _all_text(short)


def test_color_card_and_message():
    card = views.build_color_card((88, 101, 242), "en-US", alpha=0.5, requester_id=3)
    text = _all_text(card)
    assert "#5865F2" in text.upper()
    assert "WCAG" in text or "contrast" in text.lower()
    assert card.children[0].accent_colour.value == 0x5865F2
    view, files = asyncio.run(views.build_color_message((1, 2, 3), "fr"))
    assert files[0].filename == views.COLOR_FILENAME
    assert files[0].fp.read(4) == b"\x89PNG"


# --------------------------------------------------------------------------- #
# persistent items
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("cls,args", views.DYNAMIC_ITEM_CASES)
def test_dynamic_items_roundtrip(cls, args):
    item = cls(*args)
    custom_id = item.item.custom_id
    assert custom_id.startswith("moddy:tools:") and len(custom_id) <= 100
    match = re.fullmatch(cls.__discord_ui_compiled_template__, custom_id)
    assert match
    rebuilt = asyncio.run(cls.from_custom_id(None, item.item, match))
    assert rebuilt.item.custom_id == custom_id


def test_regen_ids_reject_foreign_parameters():
    tpl = views.PasswordRegenButton.__discord_ui_compiled_template__
    assert not re.fullmatch(tpl, "moddy:tools:password:regen:weird:20")
    assert re.fullmatch(views.UuidRegenButton.__discord_ui_compiled_template__,
                        "moddy:tools:uuid:regen:v4:3")


def test_marker_view_is_registered_and_persistent():
    from utils.tool_views_registry import dynamic_item_cases, persistent_view_classes
    assert views.ToolsSecurityPersistence in persistent_view_classes()
    assert set(views.DYNAMIC_ITEM_CASES) <= set(dynamic_item_cases())
    assert views.ToolsSecurityPersistence.__persistent__ is True
    added = []
    views.ToolsSecurityPersistence.register_persistent(
        types.SimpleNamespace(add_dynamic_items=lambda *items: added.extend(items)))
    assert set(added) == {views.PasswordRegenButton, views.UuidRegenButton,
                          views.ColorRandomButton}


class _Response:
    def __init__(self):
        self.done = False
        self.sent = None
        self.edited = None

    def is_done(self):
        return self.done

    async def send_message(self, **kw):
        self.done, self.sent = True, kw

    async def edit_message(self, **kw):
        self.done, self.edited = True, kw


def _interaction(ephemeral=True):
    response = _Response()
    return types.SimpleNamespace(
        response=response, locale="en-US", user=types.SimpleNamespace(id=1, locale="en-US"),
        message=types.SimpleNamespace(flags=types.SimpleNamespace(ephemeral=ephemeral)),
        followup=types.SimpleNamespace())


def test_regen_callbacks_edit_in_place():
    inter = _interaction()
    asyncio.run(views.PasswordRegenButton("clear", 12).callback(inter))
    assert inter.response.edited is not None
    inter = _interaction()
    asyncio.run(views.UuidRegenButton("v4", 2).callback(inter))
    assert inter.response.edited is not None


def test_random_color_edits_private_cards_and_answers_privately_otherwise():
    private = _interaction(ephemeral=True)
    asyncio.run(views.ColorRandomButton().callback(private))
    assert private.response.edited is not None and private.response.sent is None
    public = _interaction(ephemeral=False)
    asyncio.run(views.ColorRandomButton().callback(public))
    assert public.response.sent["ephemeral"] is True and public.response.edited is None


# --------------------------------------------------------------------------- #
# cog: /pwned lookup, rate limit, error mapping, secrets
# --------------------------------------------------------------------------- #


class _FakeAPI:
    def __init__(self, body="", error=None):
        self.body, self.error, self.calls = body, error, []

    async def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if self.error:
            raise self.error
        return types.SimpleNamespace(text=lambda: self.body)


def _cog(api):
    return cog_mod.ToolsSecurity(types.SimpleNamespace(public_api=api))


def test_pwned_lookup_sends_only_the_prefix():
    prefix, suffix = hs.sha1_parts("hunter2")
    api = _FakeAPI(f"{suffix}:77\n0000000000000000000000000000000000A:1")
    count = asyncio.run(_cog(api).lookup_pwned("hunter2"))
    assert count == 77
    method, url, kw = api.calls[0]
    assert url == hs.HIBP_RANGE_URL + prefix
    assert "hunter2" not in repr(api.calls) and suffix not in repr(api.calls)
    assert kw["headers"] == {"Add-Padding": "true"}


@pytest.mark.parametrize("code,expected", [
    ("timeout", "timeout"), ("network", "network"), ("rate_limited", "rate_limited"),
    ("http_error", "unavailable"), ("bad_payload", "unavailable"),
])
def test_pwned_api_errors_map_to_tool_errors(code, expected):
    api = _FakeAPI(error=PublicAPIError(code))
    with pytest.raises(tc.ToolError) as info:
        asyncio.run(_cog(api).lookup_pwned("x"))
    assert info.value.code == expected and info.value.tool == "pwned"


def test_pwned_without_public_api_is_unavailable():
    with pytest.raises(tc.ToolError) as info:
        asyncio.run(cog_mod.ToolsSecurity(types.SimpleNamespace()).lookup_pwned("x"))
    assert info.value.code == "unavailable"


def test_pwned_rate_limit():
    cog = _cog(_FakeAPI())
    for _ in range(cog_mod.PWNED_PER_MINUTE):
        cog.check_pwned_rate(5)
    with pytest.raises(tc.ToolError) as info:
        cog.check_pwned_rate(5)
    assert info.value.code == "cooldown"
    cog.check_pwned_rate(6)  # another member is unaffected


def test_secrets_are_never_slash_options():
    """/pwned and /jwt have no parameter at all; /hash takes none named like a secret."""
    cmds = {c.name: c for c in _cog(None).get_app_commands()}
    assert set(cmds) == {"pwned", "password", "uuid", "hash", "jwt", "color"}
    assert not cmds["pwned"].parameters and not cmds["jwt"].parameters
    for name in ("pwned", "password", "uuid", "jwt"):
        assert not any(p.name == "incognito" for p in cmds[name].parameters)
    for name in ("hash", "color"):
        assert any(p.name == "incognito" for p in cmds[name].parameters)


def test_modal_inputs_and_run_hash_errors():
    async def scenario():
        cog = _cog(None)
        pwned = cog_mod.PwnedModal(cog, "en-US")
        assert pwned.password.component.max_length == 256
        jwt_modal = cog_mod.JwtModal(cog, "fr")
        assert jwt_modal.token.component.max_length == jwtd.MAX_TOKEN_LENGTH
        cog_mod.HashModal(cog, "de", ephemeral=True, compare="abc")

        inter = _interaction()
        inter.response.done = False
        big = types.SimpleNamespace(size=cog_mod.MAX_HASH_FILE_BYTES + 1, filename="a")
        await cog.run_hash(inter, text=None, attachment=big, compare=None, ephemeral=True)
        assert inter.response.sent["ephemeral"] is True
        inter = _interaction()
        await cog.run_hash(inter, text=None, attachment=None, compare=None, ephemeral=False)
        assert inter.response.sent["ephemeral"] is True  # errors are always private
        inter = _interaction()
        await cog.run_hash(inter, text="abc", attachment=None, compare=hs.hash_text("abc")["md5"],
                           ephemeral=False)
        assert inter.response.sent["ephemeral"] is False
        assert hs.hash_text("abc")["sha256"] in _all_text(inter.response.sent["view"])
    asyncio.run(scenario())


def test_run_hash_file_path_uses_loading_then_edits():
    async def scenario():
        cog = _cog(None)
        edits = []

        class Att:
            size, filename = 5, "f.bin"

            async def read(self):
                return b"hello"

        inter = _interaction()

        async def edit_original_response(**kw):
            edits.append(kw)
        inter.edit_original_response = edit_original_response
        await cog.run_hash(inter, text=None, attachment=Att(), compare=None, ephemeral=True)
        assert inter.response.sent is not None and edits
        assert hashlib.sha256(b"hello").hexdigest() in _all_text(edits[0]["view"])
    asyncio.run(scenario())


def test_modules_never_log_or_store_secrets():
    """Static guard: no logging call, cache or save_state near secret handling."""
    root = cog_mod.__file__
    source = open(root, encoding="utf-8").read()
    assert "save_state" not in source
    for line in source.splitlines():
        if "logger." in line:
            assert "password" not in line.lower() and "token" not in line.lower()
    assert "save_state" not in open(views.__file__, encoding="utf-8").read()


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #


def _flat(data, prefix=""):
    out = {}
    for key, value in data.items():
        if isinstance(value, dict):
            out.update(_flat(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


def _locale_data(locale):
    path = __import__("pathlib").Path(__file__).parent.parent / "locales" / f"{locale}.json"
    return _flat(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("namespace", NAMESPACES)
def test_tool_keys_identical_in_the_five_locales(namespace):
    prefix = f"tools.{namespace}."
    keysets = {loc: {k for k in _locale_data(loc) if k.startswith(prefix)} for loc in LOCALES}
    reference = keysets["en-US"]
    assert reference, namespace
    for loc, keys in keysets.items():
        assert keys == reference, (namespace, loc, reference ^ keys)


@pytest.mark.parametrize("locale", LOCALES)
def test_placeholders_match_english_and_nothing_is_empty(locale):
    english, other = _locale_data("en-US"), _locale_data(locale)
    for key, value in english.items():
        if not re.match(r"tools\.(pwned|password|uuid|hash|jwt|color)\.", key):
            continue
        assert other[key].strip(), key
        assert set(re.findall(r"\{(\w+)\}", value)) == set(re.findall(r"\{(\w+)\}", other[key])), key


def test_every_literal_and_dynamic_key_resolves():
    keys = _locale_data("en-US")
    sources = "".join(open(p, encoding="utf-8").read()
                      for p in (cog_mod.__file__, views.__file__))
    for key in set(re.findall(r"""['"](tools\.[a-z_]+\.[a-z_.]+)['"]""", sources)):
        assert key in keys, key
    # dynamic families
    for status in (jwtd.STATUS_VALID, jwtd.STATUS_EXPIRED, jwtd.STATUS_NOT_YET):
        assert f"tools.jwt.status.{status}" in keys
    for claim in jwtd.TIME_CLAIMS + ("iss", "sub", "aud"):
        assert f"tools.jwt.claims.{claim}" in keys
    for kind in gen.KINDS:
        assert f"tools.password.kinds.{kind}" in keys
    for level in ("weak", "fair", "good", "strong", "excellent"):
        assert f"tools.password.strength.{level}" in keys
    for version in gen.VERSIONS:
        assert f"tools.uuid.version.{version}" in keys
    for name in ("dark", "light", "white", "black"):
        assert f"tools.color.contrast_{name}" in keys
    for code in ("both", "download_failed"):
        assert f"tools.hash.errors.{code}" in keys
    for code in ("empty", "encrypted", "not_a_jwt"):
        assert f"tools.jwt.errors.{code}" in keys
    for code in ("empty", "invalid", "out_of_range"):
        assert f"tools.color.errors.{code}" in keys
    for code in ("cooldown", "unavailable"):
        assert f"tools.pwned.errors.{code}" in keys or f"tools.common.errors.{code}" in keys


@pytest.mark.parametrize("locale", LOCALES)
def test_error_cards_render_in_every_locale(locale):
    for exc in (tc.ToolError("both", tool="hash"), tc.ToolError("empty", tool="jwt"),
                tc.ToolError("invalid", tool="color", examples=colors.EXAMPLES),
                tc.ToolError("cooldown", tool="pwned", retry="9 s")):
        text = _all_text(tc.tool_error_card(exc, locale))
        assert text and "tools." not in text and "{" not in text


def test_option_and_command_localizations_cover_the_six_commands():
    import pathlib
    folder = pathlib.Path(__file__).parent.parent / "locales" / "commands"
    files = sorted(folder.glob("*.json"))
    assert len(files) == 32
    cmds = {c.name: c for c in _cog(None).get_app_commands()}
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8")).get("commands", {})
        for name, command in cmds.items():
            entry = data.get(name)
            assert entry and entry.get("name") and entry.get("description"), (path.name, name)
            assert 1 <= len(entry["name"]) <= 32 and entry["name"] == entry["name"].lower()
            assert len(entry["description"]) <= 100
            declared = {p.name for p in command.parameters if p.name != "incognito"}
            assert declared <= set(entry.get("parameters", {})), (path.name, name)
