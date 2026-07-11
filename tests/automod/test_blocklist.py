"""Characterization tests for ``automod.blocklist`` (step 3).

A blocklist match only *routes* a message to nano — it never sanctions — so the
list is deliberately aggressive. These tests pin the routing behavior, the
anti-circumvention normalization, and the word-boundary safety that keeps the
worst Scunthorpe-style false positives out.
"""

from automod.blocklist import Blocklist, get_blocklist


def _bl() -> Blocklist:
    return get_blocklist()


class TestSingleton:
    def test_get_blocklist_is_cached(self):
        assert get_blocklist() is get_blocklist()


class TestPlainMatches:
    def test_insult_routes_with_category_and_gravity(self):
        entry = _bl().match("espèce de connard")
        assert entry is not None
        assert entry.categorie == "insultes"
        assert entry.gravite_indicative == "moyenne"

    def test_threat_is_high_gravity(self):
        entry = _bl().match("je vais te tuer sale type")
        assert entry is not None
        assert entry.categorie == "menaces"
        assert entry.gravite_indicative == "haute"

    def test_self_harm_incitement(self):
        entry = _bl().match("just kys already")
        assert entry is not None
        assert entry.categorie == "incitation_automutilation"

    def test_mild_vulgarity_low_gravity(self):
        entry = _bl().match("putain c'est nul")
        assert entry is not None
        assert entry.categorie == "vulgarite"
        assert entry.gravite_indicative == "basse"


class TestObfuscation:
    def test_leetspeak_is_folded(self):
        # c0nn4rd -> connard via 0->o, 4->a
        assert _bl().match("t'es qu'un c0nn4rd") is not None

    def test_separators_stripped_for_compact_terms(self):
        # f.d.p / f-d-p / f_d_p all collapse to the compact term "fdp"
        for variant in ("f.d.p", "f-d-p", "f_d_p", "f d p"):
            assert _bl().match(variant) is not None, variant

    def test_repeated_spam_still_matched(self):
        # Evasion by repetition must still hit the word-boundary threat pattern.
        assert _bl().match("je vais te tuer " * 40) is not None

    def test_tripled_letters_collapsed(self):
        assert _bl().match("espèce de coooonnard") is not None


class TestEmojiGestures:
    def test_middle_finger_emoji_flags(self):
        entry = _bl().match("\U0001F595 you")
        assert entry is not None
        assert entry.categorie == "insultes"

    def test_shortcode_middle_finger_flags(self):
        assert _bl().match("take this :middle_finger:") is not None


class TestNoMatch:
    def test_clean_message_returns_none(self):
        assert _bl().match("bonjour tout le monde, belle journée") is None

    def test_positive_english_message_returns_none(self):
        assert _bl().match("i really love this community") is None

    def test_empty_and_whitespace(self):
        assert _bl().match("") is None
        assert _bl().match("    ") is None

    def test_word_boundary_avoids_substring_false_positive(self):
        # "ass" is a boundary-matched term; it must NOT fire inside "class".
        assert _bl().match("this class was great") is None
