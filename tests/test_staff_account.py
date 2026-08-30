"""`/team account` — the staff user-account lookup.

What these guard:

* **Every section renders.** The panel aggregates nine independent DB reads;
  a staffer must still get the other eight when one of them fails.
* **The sensitive fields are actually shown.** The command exists for the
  email and the Stripe customer — a silent regression there makes it useless.
* **i18n completeness.** Every key the panel uses exists in the five locales
  (a missing key renders as a raw path in front of a staffer).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")

from staff.commands.team import account as account_cmd  # noqa: E402

NOW = datetime.now(timezone.utc)

LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")

#: Keys the panel reads, relative to ``staff.team.account``.
ACCOUNT_KEYS = (
    "title", "identity", "moddy_account", "email", "no_email", "updated",
    "timezone", "staff", "nodes", "denied", "staff_since", "moderation",
    "global_level", "global_expires", "cases", "cases_open", "cases_scopes",
    "notifications", "notifications_recent", "notifications_last", "privacy",
    "no_db",
)


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #

class _Flags:
    value = 0


class FakeUser:
    def __init__(self, user_id: int = 4242, bot: bool = False):
        self.id = user_id
        self.name = "jules"
        self.global_name = "Jules"
        self.bot = bot
        self.created_at = NOW - timedelta(days=900)
        self.public_flags = _Flags()


class FakeDB:
    """Only the reads the command performs; ``fail`` makes one of them raise."""

    def __init__(self, *, fail: set | None = None):
        self.fail = fail or set()

    def _guard(self, name):
        if name in self.fail:
            raise RuntimeError(f"{name} is down")

    async def get_user(self, user_id):
        self._guard("get_user")
        return {
            "user_id": user_id,
            "attributes": {"TEAM": True, "VERIFIED": True},
            "data": {"reminder_timezone": "Europe/Paris"},
            "email": "jules@example.com",
            "stripe_customer_id": "cus_ABC123",
            "created_at": NOW - timedelta(days=120),
            "updated_at": NOW,
        }

    async def get_staff_permissions(self, user_id):
        self._guard("get_staff_permissions")
        return {"roles": ["Support"], "denied_commands": ["t.flex"],
                "role_permissions": {"Support": ["user_lookup", "ticket_view"]},
                "created_at": NOW - timedelta(days=30)}

    async def get_subscription(self, user_id):
        self._guard("get_subscription")
        return {"tier": "Moddy Max", "expires_at": NOW + timedelta(days=10),
                "stripe_customer_id": "cus_ABC123", "is_active": True}

    async def get_subscription_servers(self, user_id):
        self._guard("get_subscription_servers")
        return [{"server_id": 1234, "added_at": NOW - timedelta(days=5)}]

    async def count_subject_cases(self, subject_type, subject_id, status=None):
        self._guard("count_subject_cases")
        return 2 if status == "open" else 7

    async def list_subject_scopes(self, subject_type, subject_id):
        self._guard("list_subject_scopes")
        return [{"scope_type": "discord_guild", "scope_id": "1234", "count": 7}]

    async def list_active_global_actions(self, subject_type, subject_id):
        self._guard("list_active_global_actions")
        return [{"action": "restrict", "expires_at": NOW + timedelta(days=2)}]

    async def list_notifications(self, *, recipient_id=None, limit=25, **kw):
        self._guard("list_notifications")
        return [{"id": uuid.uuid4(), "kind": "sanction", "created_at": NOW}]


class FakeBot:
    def __init__(self, db=None, user=None):
        self.db = db
        self.redis = None
        self.guilds = []
        self._user = user or FakeUser()

    async def fetch_user(self, user_id):
        return self._user


class FakeContext:
    """Captures the view a command sends instead of hitting Discord."""

    def __init__(self, bot, options, locale="fr"):
        self.bot = bot
        self.options = options
        self.locale = locale
        self.author = FakeUser(1)
        self.guild = None
        self.channel = None
        self.interaction = None
        self.sent = None
        self.deferred = False

    @property
    def is_slash(self):
        return False

    def opt(self, name, default=None):
        value = self.options.get(name, default)
        return default if value is None else value

    async def defer(self, thinking: bool = True):
        self.deferred = True

    async def send(self, view=None, content=None):
        self.sent = view
        return None


def _texts(view) -> str:
    """Flatten every TextDisplay of a rendered panel into one string."""
    chunks = []

    def walk(item):
        content = getattr(item, "content", None)
        if isinstance(content, str):
            chunks.append(content)
        for child in getattr(item, "children", []) or []:
            walk(child)

    for child in view.children:
        walk(child)
    return "\n".join(chunks)


async def _run(bot, options=None, locale="fr") -> str:
    command = account_cmd.AccountCommand(bot)
    ctx = FakeContext(bot, options if options is not None else {"user": bot._user}, locale)
    await command.execute(ctx)
    assert ctx.sent is not None
    return _texts(ctx.sent)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_command_is_gated_behind_the_user_lookup_node():
    # Personal data: the staff role check alone must never be enough.
    assert account_cmd.AccountCommand.permission == "user_lookup"

    from utils.staff_role_permissions import (
        MANAGER_PERMISSIONS, SUPPORT_PERMISSIONS, get_permission_label,
    )
    assert "user_lookup" in SUPPORT_PERMISSIONS
    assert "user_lookup" in MANAGER_PERMISSIONS
    assert get_permission_label("user_lookup") != "user_lookup"


async def test_panel_shows_the_account_data():
    text = await _run(FakeBot(db=FakeDB()))
    assert "jules@example.com" in text          # the whole point of the command
    assert "cus_ABC123" in text                 # Stripe customer
    assert "Europe/Paris" in text               # stored preference
    assert "`Support`" in text                  # staff role
    assert "user_lookup" in text                # granted nodes
    assert "`limited`" in text                  # global sanction level
    assert "`7`" in text and "`2`" in text      # cases: total / open
    assert "sanction" in text                   # last notification kind
    assert "**Jules**" in text                  # display name + badge (rule #7)


async def test_one_failing_read_does_not_sink_the_panel():
    text = await _run(FakeBot(db=FakeDB(fail={"list_notifications", "get_subscription"})))
    assert "jules@example.com" in text
    assert "`Support`" in text


async def test_missing_email_is_explicit():
    db = FakeDB()

    async def no_email(user_id):
        record = {"user_id": user_id, "attributes": {}, "data": {},
                  "email": None, "stripe_customer_id": None,
                  "created_at": NOW, "updated_at": NOW}
        return record

    db.get_user = no_email
    text = await _run(FakeBot(db=db))
    assert "Aucun email enregistré" in text


async def test_unknown_target_is_rejected_before_any_lookup():
    bot = FakeBot(db=FakeDB())
    command = account_cmd.AccountCommand(bot)
    ctx = FakeContext(bot, {"user_id": "not-an-id"})
    await command.execute(ctx)
    assert ctx.sent is not None
    assert "t.account" in _texts(ctx.sent)


async def test_without_a_database_the_command_says_so():
    text = await _run(FakeBot(db=None))
    assert "base de données" in text.lower()


@pytest.mark.parametrize("locale", LOCALES)
def test_i18n_keys_exist_in_every_locale(locale):
    with open(f"locales/{locale}.json", encoding="utf-8") as handle:
        block = json.load(handle)["staff"]["team"]["account"]
    missing = [key for key in ACCOUNT_KEYS if not block.get(key)]
    assert not missing, f"{locale}: missing {missing}"
