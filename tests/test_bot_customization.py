"""Pure-logic tests for the Bot Customization module.

Nothing here touches Discord: the point is the validation layer that decides
what we are allowed to send to ``PATCH /guilds/{id}/members/@me``.

    pytest tests/test_bot_customization.py -q
"""

import pytest

from modules.bot_customization import (
    BIO_ATTRIBUTION,
    EFFECT_GRADIENT,
    EFFECT_NEON,
    MAX_BIO_LENGTH,
    MAX_BIO_TOTAL_LENGTH,
    CustomizationError,
    build_bio,
    color_to_hex,
    normalize_style,
    parse_hex_color,
    style_is_empty,
    _style_payload,
)


# ------------------------------------------------------------------ bio

def test_bio_always_carries_the_attribution():
    assert build_bio("Hello") .endswith(BIO_ATTRIBUTION)
    # An empty bio is the attribution alone, never nothing.
    assert build_bio("") == BIO_ATTRIBUTION
    assert build_bio(None) == BIO_ATTRIBUTION


def test_bio_budget_fits_discord_limit():
    """A maximum-length user bio plus the suffix must still fit in 190."""
    assert len(build_bio("x" * MAX_BIO_LENGTH)) == MAX_BIO_TOTAL_LENGTH


# ---------------------------------------------------------------- colors

@pytest.mark.parametrize("raw,expected", [
    ("#5865F2", 0x5865F2),
    ("5865f2", 0x5865F2),
    ("  #000000 ", 0),
    ("", None),
    (None, None),
])
def test_parse_hex_color(raw, expected):
    assert parse_hex_color(raw) == expected


@pytest.mark.parametrize("raw", ["nope", "#12345678", "-1"])
def test_parse_hex_color_rejects_garbage(raw):
    with pytest.raises(CustomizationError) as exc:
        parse_hex_color(raw)
    assert exc.value.code == "invalid_color"


def test_color_to_hex_roundtrip():
    assert color_to_hex(parse_hex_color("#5865F2")) == "#5865F2"
    assert color_to_hex(None) == ""


# ----------------------------------------------------------------- style

def test_normalize_style_accepts_hex_strings_and_ints():
    style = normalize_style({"font_id": 7, "effect_id": EFFECT_GRADIENT,
                             "colors": ["#FF0000", 255]})
    assert style == {"font_id": 7, "effect_id": EFFECT_GRADIENT,
                     "colors": [0xFF0000, 255]}


def test_gradient_requires_two_colors():
    with pytest.raises(CustomizationError) as exc:
        normalize_style({"effect_id": EFFECT_GRADIENT, "colors": [1]})
    assert exc.value.code == "gradient_needs_two_colors"


def test_single_color_effect_requires_one_color():
    with pytest.raises(CustomizationError) as exc:
        normalize_style({"effect_id": EFFECT_NEON, "colors": []})
    assert exc.value.code == "effect_needs_color"


def test_extra_colors_are_dropped():
    style = normalize_style({"effect_id": EFFECT_NEON, "colors": [1, 2, 3]})
    assert style["colors"] == [1]


def test_colors_without_an_effect_are_dropped():
    """Nothing paints them, so keeping them would only mislead the UI."""
    style = normalize_style({"font_id": 3, "colors": [0xFF0000]})
    assert style == {"font_id": 3, "effect_id": None, "colors": []}


@pytest.mark.parametrize("style,code", [
    ({"font_id": 5}, "invalid_font"),        # in range 1..12 but not a real face
    ({"font_id": 99}, "invalid_font"),
    ({"effect_id": 9}, "invalid_effect"),
])
def test_invalid_ids_are_refused(style, code):
    with pytest.raises(CustomizationError) as exc:
        normalize_style(style)
    assert exc.value.code == code


def test_empty_style_detection():
    assert style_is_empty(normalize_style({}))
    assert style_is_empty(None)
    assert not style_is_empty(normalize_style({"font_id": 7}))


def test_reset_payload_sends_null_not_zero():
    """Discord refuses 0 / [] — a reset must be three explicit nulls."""
    payload = _style_payload(normalize_style({}))
    assert payload == {
        "display_name_font_id": None,
        "display_name_effect_id": None,
        "display_name_colors": None,
    }


def test_style_payload_maps_to_api_fields():
    payload = _style_payload(
        normalize_style({"font_id": 7, "effect_id": EFFECT_NEON, "colors": [255]})
    )
    assert payload == {
        "display_name_font_id": 7,
        "display_name_effect_id": EFFECT_NEON,
        "display_name_colors": [255],
    }
