"""
Relationship graph & target-reaction tests (session 5.1 / 5.2).

Covers the two pure pieces (decayed familiarity scoring + the 4-way reaction
classifier) and the Redis-backed store (counters, mutual-reply detection,
positive reactions, TTL) driven by the in-memory FakeRedis.
"""

import time

import pytest

from automod import constants as ac
from automod import relations as ar

from _redis_stub import FakeRedis


NOW = 1_000_000_000.0
DAY = 86400.0


# --------------------------------------------------------------------------- #
# Familiarity scoring (pure, decayed)
# --------------------------------------------------------------------------- #

class TestFamiliarite:
    def test_no_history_is_aucune(self):
        assert ar.familiarite(ar.RelationCounters(), now=NOW) == ac.FAMILIARITE_AUCUNE

    def test_faible_threshold(self):
        # 3 interactions, fresh contact → score 3 → "faible".
        c = ar.RelationCounters(interactions=3, premier_contact_ts=NOW - 10 * DAY,
                                dernier_contact_ts=NOW)
        assert ar.familiarite(c, now=NOW) == ac.FAMILIARITE_FAIBLE

    def test_moyenne_threshold(self):
        # interactions 6 + 3 mutual (×2) = 12 → "moyenne".
        c = ar.RelationCounters(interactions=6, reponses_mutuelles=3,
                                premier_contact_ts=NOW - 10 * DAY,
                                dernier_contact_ts=NOW)
        assert ar.familiarite(c, now=NOW) == ac.FAMILIARITE_MOYENNE

    def test_haute_requires_score_and_age(self):
        # Big score but only 2 days old → not yet "haute" (falls to moyenne).
        young = ar.RelationCounters(interactions=50, premier_contact_ts=NOW - 2 * DAY,
                                    dernier_contact_ts=NOW)
        assert ar.familiarite(young, now=NOW) == ac.FAMILIARITE_MOYENNE
        # Same score, old enough → "haute".
        old = ar.RelationCounters(interactions=50, premier_contact_ts=NOW - 30 * DAY,
                                  dernier_contact_ts=NOW)
        assert ar.familiarite(old, now=NOW) == ac.FAMILIARITE_HAUTE

    def test_decay_lowers_the_level(self):
        # A once-close pair that went silent for two half-lives (~60 d) fades.
        c = ar.RelationCounters(interactions=50, premier_contact_ts=NOW - 200 * DAY,
                                dernier_contact_ts=NOW - 60 * DAY)
        # 50 * 0.5**(60/30) = 50 * 0.25 = 12.5 → "moyenne", not "haute".
        assert ar.familiarite(c, now=NOW) == ac.FAMILIARITE_MOYENNE

    def test_reactions_weighted_highest(self):
        # 1 positive reaction weighs 3 → still only "faible" alone.
        c = ar.RelationCounters(reactions_positives=1, premier_contact_ts=NOW - DAY,
                                dernier_contact_ts=NOW)
        assert ar.familiarite(c, now=NOW) == ac.FAMILIARITE_FAIBLE

    def test_payload_shape(self):
        c = ar.RelationCounters(interactions=214, reponses_mutuelles=5,
                                premier_contact_ts=NOW - 40 * DAY, dernier_contact_ts=NOW)
        p = ar.build_relation_payload(c, ac.REACTION_BANTER, now=NOW)
        assert p["familiarite"] == ac.FAMILIARITE_HAUTE
        assert p["interactions_30j"] == 214
        assert p["reciprocite"] is True
        assert p["reaction_cible"] == ac.REACTION_BANTER


# --------------------------------------------------------------------------- #
# Target-reaction classifier (pure) — the 4 signals
# --------------------------------------------------------------------------- #

class TestClassifyReaction:
    def test_banter_from_laughter_word(self):
        assert ar.classify_target_reaction(["mdrrr t'abuses"]) == ac.REACTION_BANTER

    def test_banter_from_laughter_emoji(self):
        assert ar.classify_target_reaction(["arrête 😂"]) == ac.REACTION_BANTER

    def test_conflict_when_aggressive_reply_no_laughter(self):
        assert ar.classify_target_reaction(
            ["ta gueule connard"], aggressive_hits=1) == ac.REACTION_CONFLIT

    def test_distress_wins_over_everything(self):
        assert ar.classify_target_reaction(
            ["mdr"], target_gone=True, aggressive_hits=1) == ac.REACTION_DETRESSE

    def test_nothing_observed(self):
        assert ar.classify_target_reaction([]) == ac.REACTION_AUCUNE
        assert ar.classify_target_reaction(["ok d'accord"]) == ac.REACTION_AUCUNE

    def test_laughter_beats_aggression(self):
        # "tg toi-même mdr" → the target laughed along despite a coarse word.
        assert ar.classify_target_reaction(
            ["tg toi-même mdr"], aggressive_hits=1) == ac.REACTION_BANTER

    def test_positive_emoji_detection(self):
        assert ar.is_positive_emoji("😂")
        assert ar.is_positive_emoji("👍")
        assert not ar.is_positive_emoji("😡")
        assert not ar.is_positive_emoji("")


# --------------------------------------------------------------------------- #
# Redis-backed store
# --------------------------------------------------------------------------- #

class TestRelationStore:
    async def test_inert_without_redis(self):
        store = ar.RelationStore(None)
        await store.record_message(1, 10, 20)          # no-op, no raise
        c = await store.get(1, 10, 20)
        assert c == ar.RelationCounters()

    async def test_records_interaction_and_ttl(self):
        r = FakeRedis()
        store = ar.RelationStore(r)
        await store.record_message(1, 10, 20, now=NOW)
        c = await store.get(1, 10, 20)
        assert c.interactions == 1
        assert c.premier_contact_ts == NOW
        assert c.dernier_contact_ts == NOW
        # Key carries the 60-day TTL and is symmetric (min:max ordering).
        key = ar.RelationStore._key(1, 20, 10)
        assert r.expires[key] == ac.RELATION_TTL_SECONDS

    async def test_mutual_reply_within_window(self):
        r = FakeRedis()
        store = ar.RelationStore(r)
        # A messages B, then B replies to A a minute later → one mutual.
        await store.record_message(1, 10, 20, now=NOW)
        await store.record_message(1, 20, 10, now=NOW + 60)
        c = await store.get(1, 10, 20)
        assert c.interactions == 2
        assert c.reponses_mutuelles == 1

    async def test_reply_outside_window_is_not_mutual(self):
        r = FakeRedis()
        store = ar.RelationStore(r)
        await store.record_message(1, 10, 20, now=NOW)
        # B replies 10 minutes later — past the 5-minute mutual window.
        await store.record_message(1, 20, 10, now=NOW + 600)
        c = await store.get(1, 10, 20)
        assert c.reponses_mutuelles == 0

    async def test_positive_reaction(self):
        r = FakeRedis()
        store = ar.RelationStore(r)
        await store.record_positive_reaction(1, 10, 20, now=NOW)
        c = await store.get(1, 10, 20)
        assert c.reactions_positives == 1

    async def test_self_pair_ignored(self):
        r = FakeRedis()
        store = ar.RelationStore(r)
        await store.record_message(1, 10, 10, now=NOW)
        assert await store.get(1, 10, 10) == ar.RelationCounters()

    async def test_end_to_end_reaches_haute(self):
        r = FakeRedis()
        store = ar.RelationStore(r)
        first = NOW - 30 * DAY
        # Build a long, reciprocal, well-reacted relationship.
        for i in range(45):
            await store.record_message(1, 10, 20, now=first + i * 60)
            await store.record_message(1, 20, 10, now=first + i * 60 + 30)
        for _ in range(5):
            await store.record_positive_reaction(1, 20, 10, now=NOW)
        c = await store.get(1, 10, 20)
        assert ar.familiarite(c, now=NOW) == ac.FAMILIARITE_HAUTE
