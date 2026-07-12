"""Characterization tests for ``automod.triviaux`` (step 2, allowlist)."""

import pytest

from automod.triviaux import TRIVIAUX, est_trivial


class TestEstTrivial:
    @pytest.mark.parametrize("msg", ["mdr", "lol", "ok", "gg", "merci", "salut"])
    def test_common_french_interjections(self, msg):
        assert est_trivial(msg) is True

    @pytest.mark.parametrize("msg", ["lmao", "thanks", "yeah", "gm", "based"])
    def test_common_english_interjections(self, msg):
        assert est_trivial(msg) is True

    def test_repeated_letters_still_trivial(self):
        # normalize_trivial collapses "mdrrrr" -> "mdr" before lookup
        assert est_trivial("MDRRRR") is True
        assert est_trivial("okkk") is True

    def test_surrounding_whitespace_ignored(self):
        assert est_trivial("  lol  ") is True

    @pytest.mark.parametrize(
        "msg",
        [
            "you are an idiot",
            "je vais te tuer",
            "connard",
            "this whole server is trash",
        ],
    )
    def test_non_trivial_messages_not_allowlisted(self, msg):
        assert est_trivial(msg) is False

    def test_allowlist_is_lowercase_and_stripped(self):
        # Safety invariant: every entry must be reachable by normalize_trivial,
        # which lowercases and strips — so no entry may carry stray case/space.
        for term in TRIVIAUX:
            assert term == term.lower().strip()
