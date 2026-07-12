"""Characterization tests for ``automod.normalize``.

These pin the anti-circumvention behavior the blocklist and trivial allowlist
depend on: accent folding, leetspeak folding, repeat collapsing and the
repeat/concatenation de-spam used to defeat evasion by repetition.
"""

from automod.normalize import (
    collapse_repeats,
    fold_accents,
    normalize_compact,
    normalize_spaced,
    normalize_trivial,
)


class TestFoldAccents:
    def test_strips_french_diacritics(self):
        assert fold_accents("négrÉ çà") == "negrE ca"

    def test_leaves_plain_ascii_untouched(self):
        assert fold_accents("hello world") == "hello world"


class TestNormalizeSpaced:
    def test_lowercases_and_folds_accents(self):
        assert normalize_spaced("Éléphant") == "elephant"

    def test_leetspeak_folds_to_letters(self):
        # 1->i, 3->e, 4->a, 5->s, @->a, $->s, 0->o
        assert normalize_spaced("n1k3r") == "niker"
        assert normalize_spaced("sa10pe") == "saiope"  # 1->i, 0->o
        assert normalize_spaced("f@g") == "fag"

    def test_separators_become_single_space(self):
        assert normalize_spaced("f.d.p") == "f d p"
        assert normalize_spaced("a---b___c") == "a b c"

    def test_collapses_three_plus_repeats(self):
        # 3+ consecutive identical chars collapse to one
        assert normalize_spaced("saaalope") == "salope"

    def test_double_repeat_is_preserved(self):
        # exactly 2 repeats is NOT collapsed (only 3+)
        assert normalize_spaced("baal") == "baal"


class TestNormalizeCompact:
    def test_strips_all_separators(self):
        assert normalize_compact("f.d.p") == "fdp"
        assert normalize_compact("f d p") == "fdp"
        assert normalize_compact("f_d-p") == "fdp"


class TestNormalizeTrivial:
    def test_collapses_repeats_and_lowercases(self):
        assert normalize_trivial("MDRRRR") == "mdr"
        assert normalize_trivial("okkk") == "ok"

    def test_strips_surrounding_whitespace(self):
        assert normalize_trivial("  lol  ") == "lol"


class TestCollapseRepeats:
    def test_whole_string_periodic_unit(self):
        # "abcabcabc" is a period-3 repeat → the bare unit
        assert collapse_repeats("abcabcabc") == "abc"

    def test_repeated_phrase_collapses_to_single(self):
        # A whole-string periodic repeat collapses to its bare unit. Here the
        # separator-free period wins, yielding the compact single occurrence —
        # which is exactly what the ``compact`` blocklist patterns match on.
        text = "je vais te tuer " * 5
        assert collapse_repeats(text) == "jevaistetuer"

    def test_alternating_word_run_dedupes_consecutive(self):
        # No global period, but consecutive duplicate words are dropped.
        assert collapse_repeats("va va bene") == "va bene"

    def test_consecutive_word_run_collapses(self):
        assert collapse_repeats("tuer tuer tuer") == "tuer"

    def test_non_repeating_text_is_left_meaningful(self):
        # A normal sentence is returned as its spaced-normalized form.
        assert collapse_repeats("bonjour tout le monde") == "bonjour tout le monde"

    def test_empty_string(self):
        assert collapse_repeats("") == ""
        assert collapse_repeats("   ") == ""
