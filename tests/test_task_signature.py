"""Tests for the ``moddy:tasks`` HMAC signature contract.

Run with: python3 -m pytest tests/test_task_signature.py -q
"""

import hashlib
import hmac
import importlib.util
import json
import time
import uuid
from pathlib import Path

import pytest

# Loaded by path: `utils/__init__.py` pulls in discord.py, and this module is
# pure stdlib — the suite must stay runnable without a Discord install.
_SPEC = importlib.util.spec_from_file_location(
    "moddy_task_signature",
    Path(__file__).resolve().parent.parent / "utils" / "task_signature.py",
)
task_signature = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(task_signature)

CLOCK_SKEW = task_signature.CLOCK_SKEW
DEDUP_TTL = task_signature.DEDUP_TTL
REPLAY_WINDOW = task_signature.REPLAY_WINDOW
SEEN_KEY_PREFIX = task_signature.SEEN_KEY_PREFIX
TaskRejected = task_signature.TaskRejected
canonical_string = task_signature.canonical_string
check_freshness = task_signature.check_freshness
check_signature = task_signature.check_signature
compute_signature = task_signature.compute_signature
sign_fields = task_signature.sign_fields
verify_task = task_signature.verify_task

SECRET = "x" * 48


class FakeRedis:
    """Minimal ``SET key value NX EX`` stand-in."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.expiries[key] = ex
        return True


def make_fields(payload=None, task_type="send_announcement", guild_id=0, issued_at=None):
    return sign_fields(
        task_type,
        guild_id,
        {"message": "hello"} if payload is None else payload,
        str(uuid.uuid4()),
        int(time.time()) if issued_at is None else issued_at,
        SECRET,
    )


# ── Canonicalization ────────────────────────────────────────────────────────

def test_canonical_key_order_is_fixed():
    fields = make_fields()
    canonical = canonical_string(fields)
    assert canonical.startswith('{"guild_id":')
    assert list(json.loads(canonical).keys()) == [
        "guild_id", "issued_at", "payload", "task_id", "type"
    ]


def test_canonical_excludes_signature():
    fields = make_fields()
    assert "signature" not in json.loads(canonical_string(fields))


def test_canonical_has_no_whitespace():
    canonical = canonical_string(make_fields())
    assert ", " not in canonical and '": ' not in canonical


def test_payload_is_a_nested_json_string():
    """Double serialization: payload's quotes are escaped inside canonical."""
    fields = make_fields(payload={"b": 2, "a": 1})
    assert fields["payload"] == '{"a":1,"b":2}'
    assert '\\"a\\":1' in canonical_string(fields)


def test_payload_is_sorted_and_non_ascii_is_escaped():
    fields = make_fields(payload={"z": "é", "a": "ü"})
    assert fields["payload"] == '{"a":"\\u00fc","z":"\\u00e9"}'


def test_signature_matches_the_reference_implementation():
    """Recompute the way the backend does, straight from the spec."""
    payload = {"module_id": "starboard", "n": 3}
    task_id = str(uuid.uuid4())
    issued_at = str(int(time.time()))
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    reference_fields = {
        "type": "update_panel",
        "guild_id": "123456789",
        "payload": payload_json,
        "task_id": task_id,
        "issued_at": issued_at,
    }
    canonical = json.dumps(reference_fields, separators=(",", ":"), sort_keys=True)
    expected = hmac.new(
        SECRET.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()

    fields = sign_fields(
        "update_panel", 123456789, payload, task_id, issued_at, SECRET
    )
    assert fields["signature"] == expected
    assert expected == expected.lower()


def test_missing_signed_field_is_rejected():
    fields = make_fields()
    del fields["task_id"]
    with pytest.raises(TaskRejected) as exc:
        compute_signature(fields, SECRET)
    assert exc.value.code == "missing_field"


# ── Signature check ─────────────────────────────────────────────────────────

def test_valid_signature_passes():
    check_signature(make_fields(), SECRET)


def test_unsigned_entry_is_rejected():
    fields = make_fields()
    del fields["signature"]
    with pytest.raises(TaskRejected) as exc:
        check_signature(fields, SECRET)
    assert exc.value.code == "unsigned"


def test_empty_signature_is_rejected():
    fields = make_fields()
    fields["signature"] = ""
    with pytest.raises(TaskRejected) as exc:
        check_signature(fields, SECRET)
    assert exc.value.code == "unsigned"


@pytest.mark.parametrize("field", ["type", "guild_id", "payload", "task_id", "issued_at"])
def test_tampering_with_any_signed_field_breaks_the_signature(field):
    fields = make_fields(guild_id=42)
    fields[field] = fields[field] + "0"
    with pytest.raises(TaskRejected) as exc:
        check_signature(fields, SECRET)
    assert exc.value.code == "bad_signature"


def test_wrong_secret_is_rejected():
    with pytest.raises(TaskRejected) as exc:
        check_signature(make_fields(), "y" * 48)
    assert exc.value.code == "bad_signature"


def test_payload_reordering_breaks_the_signature():
    """A re-serialized payload with different key order must not verify."""
    fields = make_fields(payload={"a": 1, "b": 2})
    fields["payload"] = '{"b":2,"a":1}'
    with pytest.raises(TaskRejected) as exc:
        check_signature(fields, SECRET)
    assert exc.value.code == "bad_signature"


# ── Freshness ───────────────────────────────────────────────────────────────

def test_fresh_entry_passes():
    now = int(time.time())
    check_freshness(make_fields(issued_at=now), now=now)


def test_entry_at_the_edge_of_the_window_passes():
    now = int(time.time())
    check_freshness(make_fields(issued_at=now - REPLAY_WINDOW), now=now)
    check_freshness(make_fields(issued_at=now + CLOCK_SKEW), now=now)


def test_too_old_entry_is_rejected():
    now = int(time.time())
    with pytest.raises(TaskRejected) as exc:
        check_freshness(make_fields(issued_at=now - REPLAY_WINDOW - 1), now=now)
    assert exc.value.code == "expired"


def test_entry_from_the_future_is_rejected():
    now = int(time.time())
    with pytest.raises(TaskRejected) as exc:
        check_freshness(make_fields(issued_at=now + CLOCK_SKEW + 1), now=now)
    assert exc.value.code == "future"


def test_non_numeric_issued_at_is_rejected():
    fields = make_fields()
    fields["issued_at"] = "not-a-number"
    with pytest.raises(TaskRejected) as exc:
        check_freshness(fields)
    assert exc.value.code == "missing_field"


def test_dedup_ttl_outlives_the_replay_window():
    assert DEDUP_TTL > REPLAY_WINDOW + CLOCK_SKEW


# ── Full verification ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_valid_task_is_accepted_once():
    redis = FakeRedis()
    fields = make_fields()
    await verify_task(fields, SECRET, redis)
    assert redis.expiries[f"{SEEN_KEY_PREFIX}{fields['task_id']}"] == DEDUP_TTL


@pytest.mark.asyncio
async def test_replaying_the_same_task_id_is_rejected():
    redis = FakeRedis()
    fields = make_fields()
    await verify_task(fields, SECRET, redis)
    with pytest.raises(TaskRejected) as exc:
        await verify_task(fields, SECRET, redis)
    assert exc.value.code == "replay"


@pytest.mark.asyncio
async def test_two_identical_payloads_with_distinct_task_ids_both_pass():
    """Dedup is keyed on task_id, not content: two identical legitimate
    announcements must be able to go out in a row."""
    redis = FakeRedis()
    await verify_task(make_fields(payload={"message": "same"}), SECRET, redis)
    await verify_task(make_fields(payload={"message": "same"}), SECRET, redis)


@pytest.mark.asyncio
async def test_invalid_signature_does_not_burn_the_dedup_key():
    redis = FakeRedis()
    fields = make_fields()
    fields["signature"] = "0" * 64
    with pytest.raises(TaskRejected):
        await verify_task(fields, SECRET, redis)
    assert redis.store == {}


@pytest.mark.asyncio
async def test_missing_secret_fails_closed():
    redis = FakeRedis()
    with pytest.raises(TaskRejected) as exc:
        await verify_task(make_fields(), "", redis)
    assert exc.value.code == "no_secret"


@pytest.mark.asyncio
async def test_unsigned_task_is_rejected_by_default():
    redis = FakeRedis()
    fields = make_fields()
    del fields["signature"]
    with pytest.raises(TaskRejected) as exc:
        await verify_task(fields, SECRET, redis)
    assert exc.value.code == "unsigned"


@pytest.mark.asyncio
async def test_allow_unsigned_accepts_only_during_the_deployment_window():
    redis = FakeRedis()
    fields = make_fields()
    del fields["signature"]
    await verify_task(fields, SECRET, redis, allow_unsigned=True)


@pytest.mark.asyncio
async def test_allow_unsigned_still_rejects_a_forged_signature():
    """The escape hatch covers *absent* signatures, never wrong ones."""
    redis = FakeRedis()
    fields = make_fields()
    fields["signature"] = "0" * 64
    with pytest.raises(TaskRejected) as exc:
        await verify_task(fields, SECRET, redis, allow_unsigned=True)
    assert exc.value.code == "bad_signature"
