import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discord import ui

from utils.log_cards import TEXT_BUDGET, build_log_card


def _texts(view):
    out = []

    def walk(item):
        if isinstance(item, ui.TextDisplay):
            out.append(item.content)
        for child in getattr(item, "children", []):
            walk(child)

    for item in view.children:
        walk(item)
    return out


def test_card_has_accent_title_and_timestamp_footer():
    view = build_log_card("Title", accent=0xED4245, fields=[("Name", "value")], footer="foot")
    container = view.children[0]
    assert container.accent_colour.value == 0xED4245
    texts = _texts(view)
    assert texts[0] == "### Title"
    assert "**Name**\nvalue" in texts
    assert texts[-1].startswith("-# foot") and "<t:" in texts[-1]


def test_card_stays_within_v2_text_budget():
    big = "x" * 5000
    view = build_log_card("T", accent=1, description=big, fields=[("a", big), ("b", big), ("c", big)])
    assert sum(len(t) for t in _texts(view)) <= 4000
    assert TEXT_BUDGET < 4000
