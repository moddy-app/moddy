"""Tests for ``automod.constants`` severity handling."""

import pytest

from automod import constants


class TestClampSeverity:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (1, 1), (3, 3), (5, 5),
            (0, 1), (-4, 1), (6, 5), (99, 5),
            ("4", 4), ("2", 2),
            (None, constants.SEVERITY_DEFAULT),
            ("abc", constants.SEVERITY_DEFAULT),
            (3.9, 3),  # int() truncates
        ],
    )
    def test_clamp(self, raw, expected):
        assert constants.clamp_severity(raw) == expected


class TestEmbeddingThreshold:
    def test_known_levels_match_table(self):
        for level, expected in constants.SEVERITY_EMBEDDING_THRESHOLDS.items():
            assert constants.embedding_threshold_for(level) == expected

    def test_higher_severity_lowers_threshold(self):
        # Stricter servers route more messages to nano → lower threshold.
        thresholds = [constants.embedding_threshold_for(s) for s in range(1, 6)]
        assert thresholds == sorted(thresholds, reverse=True)

    def test_out_of_range_severity_is_clamped(self):
        assert constants.embedding_threshold_for(0) == \
            constants.SEVERITY_EMBEDDING_THRESHOLDS[1]
        assert constants.embedding_threshold_for(9) == \
            constants.SEVERITY_EMBEDDING_THRESHOLDS[5]
