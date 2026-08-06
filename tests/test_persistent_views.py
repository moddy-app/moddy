"""Persistence contract tests.

Every class in ``utils/persistent_views.py::_collect_persistent_view_classes()``
must be constructible as a bare shell and must satisfy discord.py's definition
of a persistent view. Run with:

    pytest tests/test_persistent_views.py -q
    pytest tests/test_persistent_views.py -k <module> -q
"""

import re

import pytest

from utils.persistent_views import _collect_persistent_view_classes

# Parametrise by class so `-k <name>` filters to one module's views.
VIEW_CLASSES = _collect_persistent_view_classes()
IDS = [c.__name__ for c in VIEW_CLASSES]

CID_RE = re.compile(r"^moddy:[a-z0-9_]+:[a-z0-9_]+:[a-z0-9_]+(:.+)?$")


@pytest.fixture
def shell(request):
    """A default-constructed view — mirrors what register_persistent builds.

    No bot, no guild, no user: exactly the state discord.py falls back to
    after a restart. Constructing this is the single most valuable assertion
    in the suite; most migration bugs are an AttributeError right here.
    """
    return request.param()


@pytest.mark.parametrize("cls", VIEW_CLASSES, ids=IDS)
async def test_shell_constructs(cls):
    """Step 2 + step 3 of the cookbook: optional args, guarded self.bot."""
    cls()  # must not raise


@pytest.mark.parametrize("cls", VIEW_CLASSES, ids=IDS)
async def test_marked_persistent(cls):
    """Step 7: the registry skips anything without __persistent__."""
    assert cls.__persistent__ is True


@pytest.mark.parametrize("cls", VIEW_CLASSES, ids=IDS)
async def test_no_timeout(cls):
    view = cls()
    assert view.timeout is None, (
        f"{cls.__name__} passes a numeric timeout to super().__init__(); "
        "a persistent view must not expire"
    )


@pytest.mark.parametrize("cls", VIEW_CLASSES, ids=IDS)
async def test_is_persistent(cls):
    """The assertion cookbook step 10 is really asking for.

    Marker views (AppealPersistence, ShadowAnnotationPersistence) have zero
    children and are trivially persistent — they register dynamic items in
    register_persistent instead. Both cases must pass.
    """
    view = cls()
    assert view.is_persistent(), (
        f"{cls.__name__} has at least one child without a custom_id"
    )


@pytest.mark.parametrize("cls", VIEW_CLASSES, ids=IDS)
async def test_custom_ids_are_namespaced(cls):
    """Guards against the collisions catalogued in Appendix B.5."""
    view = cls()
    for item in view.walk_children():
        cid = getattr(item, "custom_id", None)
        if cid is None or getattr(item, "url", None):
            continue
        assert CID_RE.match(cid), (
            f"{cls.__name__}: custom_id {cid!r} is not "
            "moddy:<cog>:<view>:<action>[:<param>]"
        )


async def test_no_duplicate_custom_ids_across_registered_views():
    """Two registered views sharing a custom_id: one silently shadows the other.

    This is the test that would have caught welcome_channel_config and
    welcome_dm_config shipping identical ids.
    """
    seen: dict[str, str] = {}
    for cls in VIEW_CLASSES:
        for item in cls().walk_children():
            cid = getattr(item, "custom_id", None)
            if cid is None or getattr(item, "url", None):
                continue
            assert cid not in seen, (
                f"custom_id {cid!r} is used by both {seen[cid]} and "
                f"{cls.__name__} — clicks will dispatch to only one of them"
            )
            seen[cid] = cls.__name__


# --------------------------------------------------------------------------- #
# DynamicItem templates
#
# A template that does not match the custom_id the item emits fails SILENTLY:
# the click is never dispatched and the user sees "This interaction failed".
# Add one row per DynamicItem subclass as the migration introduces them.
# --------------------------------------------------------------------------- #

def _dynamic_item_cases():
    from utils.automod_shadow_views import ShadowAnnotateButton
    from utils.appeal_views import (
        AppealNewButton, AppealClaimButton, AppealInviteButton,
        AppealDecisionButton, AppealAcceptChoiceButton,
    )
    from cogs.preferences import PreferencesManageButton, TimezoneSelect
    from cogs.reminder import ReminderManageButton
    from cogs.saved_messages import SavedMessagesListButton, SavedMessagesDetailButton
    _U = "0f7d9c62-3b4e-4a1f-9c2d-5e6f70819a2b"
    _SNOWFLAKE = 123456789012345678
    return [
        (ShadowAnnotateButton, ("ok", _U)),
        (AppealNewButton, ("s", _U, _U)),
        (AppealClaimButton, (_U,)),
        (AppealInviteButton, (_U,)),
        (AppealDecisionButton, ("accept", _U)),
        (AppealAcceptChoiceButton, ("full", _U)),
        (PreferencesManageButton, ("timezone", _SNOWFLAKE)),
        (TimezoneSelect, (_SNOWFLAKE,)),
        (ReminderManageButton, ("add", _SNOWFLAKE)),
        (SavedMessagesListButton, ("view", _SNOWFLAKE, 0)),
        (SavedMessagesDetailButton, ("back", _SNOWFLAKE, 42, 0)),
    ]


@pytest.mark.parametrize(
    "cls,args", _dynamic_item_cases(),
    ids=lambda v: getattr(v, "__name__", ""),
)
async def test_dynamic_item_template_matches_emitted_id(cls, args):
    item = cls(*args)
    cid = item.item.custom_id
    assert cls.__discord_ui_compiled_template__.fullmatch(cid), (
        f"{cls.__name__}: template does not match its own custom_id {cid!r}"
    )
    assert len(cid) <= 100, f"{cls.__name__}: custom_id exceeds 100 chars"
