"""Characterization tests for ``automod.prefiltre`` (step 1)."""

from automod.prefiltre import pre_filter


class TestPreFilter:
    def test_passes_normal_human_message(self):
        assert pre_filter("hello there", is_bot=False, is_system=False) is True

    def test_drops_bot_messages(self):
        assert pre_filter("hello", is_bot=True, is_system=False) is False

    def test_drops_system_messages(self):
        assert pre_filter("hello", is_bot=False, is_system=True) is False

    def test_drops_empty_content(self):
        assert pre_filter("", is_bot=False, is_system=False) is False

    def test_drops_whitespace_only_content(self):
        assert pre_filter("   \n\t ", is_bot=False, is_system=False) is False

    def test_drops_none_content(self):
        assert pre_filter(None, is_bot=False, is_system=False) is False
