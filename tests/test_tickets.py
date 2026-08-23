"""Tickets module — configuration schema, permission model and rendering.

Everything here is pure Python: no gateway, no database. The Discord objects
the permission code touches (guild, member, role) are replaced by the small
stubs at the top of the file, which is exactly the surface the production code
uses.

    pytest tests/test_tickets.py -q
"""

from types import SimpleNamespace

import pytest

from modules.tickets import (
    DEFAULT_MAX_OPEN_PER_USER,
    FREE_MAX_CATEGORIES,
    FREE_MAX_PANELS,
    MAX_BUTTONS_PER_PANEL,
    MAX_SELECT_OPTIONS,
    PERM_ADMIN,
    PLACEHOLDERS,
    PERM_CLOSE,
    PERM_VIEW,
    PREMIUM_MAX_CATEGORIES,
    PREMIUM_MAX_PANELS,
    STYLE_BUTTONS,
    STYLE_SELECT,
    TICKET_PERMISSIONS,
    can_open,
    find_category,
    find_panel,
    get_limits,
    locate_category,
    max_categories_for_style,
    member_permissions,
    normalize_category,
    normalize_config,
    normalize_permissions,
    parse_emoji,
    render_channel_name,
    render_text,
    role_permissions,
    staff_role_ids,
)


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #
class FakeRole:
    def __init__(self, role_id: int, name: str = "role"):
        self.id = role_id
        self.name = name
        self.mention = f"<@&{role_id}>"


class FakeMember:
    def __init__(self, user_id: int, roles=(), administrator: bool = False,
                 name: str = "member", display_name: str = None):
        self.id = user_id
        self.roles = list(roles)
        self.name = name
        self.display_name = display_name or name
        self.mention = f"<@{user_id}>"
        self.guild_permissions = SimpleNamespace(administrator=administrator)


def make_category(**overrides):
    base = {
        'name': "Support",
        'discord_category_id': 100,
        'permissions': {},
        'allowed_role_ids': [],
        'denied_role_ids': [],
    }
    base.update(overrides)
    return normalize_category(base)


# =========================================================================== #
# Normalisation
# =========================================================================== #
class TestNormalisation:
    def test_empty_config_is_a_panel_list(self):
        assert normalize_config(None) == {'panels': []}
        assert normalize_config({}) == {'panels': []}
        assert normalize_config("nonsense") == {'panels': []}

    def test_panel_without_a_name_is_dropped(self):
        config = normalize_config({'panels': [{'id': 'p_a'}, {'name': "Kept"}]})
        assert [p['name'] for p in config['panels']] == ["Kept"]

    def test_ids_are_generated_when_missing(self):
        config = normalize_config({'panels': [
            {'name': "P", 'categories': [{'name': "C"}]},
        ]})
        panel = config['panels'][0]
        assert panel['id'].startswith('p_')
        assert panel['categories'][0]['id'].startswith('c_')

    def test_duplicate_ids_are_dropped(self):
        config = normalize_config({'panels': [
            {'id': 'p_a', 'name': "First"},
            {'id': 'p_a', 'name': "Shadow"},
        ]})
        assert [p['name'] for p in config['panels']] == ["First"]

    def test_snowflakes_survive_a_json_round_trip_as_strings(self):
        """The dashboard writes ids as strings; JSON has no 64-bit int."""
        category = make_category(discord_category_id="123456789012345678",
                                 allowed_role_ids=["1", 2, "1"])
        assert category['discord_category_id'] == 123456789012345678
        assert category['allowed_role_ids'] == [1, 2]

    def test_unknown_style_falls_back_to_buttons(self):
        config = normalize_config({'panels': [{'name': "P", 'style': "carousel"}]})
        assert config['panels'][0]['style'] == STYLE_BUTTONS

    def test_unknown_locale_falls_back_to_english(self):
        assert make_category(locale="klingon")['locale'] == "en-US"

    def test_max_open_per_user_is_clamped(self):
        assert make_category(max_open_per_user=0)['max_open_per_user'] == \
            DEFAULT_MAX_OPEN_PER_USER
        assert make_category(max_open_per_user=999)['max_open_per_user'] == 10

    def test_unknown_permissions_are_dropped(self):
        permissions = normalize_permissions({
            "42": ["view", "delete_the_server", "close"],
            "not-a-role": ["view"],
            "43": [],
        })
        assert permissions == {"42": ["view", "close"]}

    def test_accent_colour_is_clamped_to_rgb(self):
        config = normalize_config({'panels': [
            {'name': "P", 'accent_color': 0xFFFFFFFF},
        ]})
        assert config['panels'][0]['accent_color'] == 0xFFFFFF


# =========================================================================== #
# Lookups
# =========================================================================== #
class TestLookups:
    def setup_method(self):
        self.config = normalize_config({'panels': [
            {'id': 'p_a', 'name': "A", 'categories': [
                {'id': 'c_1', 'name': "One"}, {'id': 'c_2', 'name': "Two"},
            ]},
        ]})

    def test_find_panel_and_category(self):
        panel = find_panel(self.config, 'p_a')
        assert panel['name'] == "A"
        assert find_category(panel, 'c_2')['name'] == "Two"

    def test_missing_entries_are_none_not_an_error(self):
        assert find_panel(self.config, 'p_nope') is None
        assert find_category(None, 'c_1') is None
        assert locate_category(self.config, 'p_nope', 'c_1') == (None, None)


# =========================================================================== #
# Limits
# =========================================================================== #
class TestLimits:
    class _Bot:
        def __init__(self, premium):
            self._premium = premium

    @pytest.fixture
    def patched(self, monkeypatch):
        def install(premium=False, raises=False):
            async def fake(bot, guild_id):
                if raises:
                    raise RuntimeError("redis is down")
                return premium
            monkeypatch.setattr("utils.subscription.is_guild_premium", fake)
        return install

    async def test_free_limits(self, patched):
        patched(premium=False)
        limits = await get_limits(self._Bot(False), 1)
        assert limits == {'premium': False,
                          'max_panels': FREE_MAX_PANELS,
                          'max_categories': FREE_MAX_CATEGORIES}

    async def test_premium_limits(self, patched):
        patched(premium=True)
        limits = await get_limits(self._Bot(True), 1)
        assert limits == {'premium': True,
                          'max_panels': PREMIUM_MAX_PANELS,
                          'max_categories': PREMIUM_MAX_CATEGORIES}

    async def test_a_lookup_failure_never_grants_the_premium_quota(self, patched):
        patched(raises=True)
        limits = await get_limits(self._Bot(False), 1)
        assert limits['premium'] is False
        assert limits['max_panels'] == FREE_MAX_PANELS

    def test_the_rendering_caps_the_plan(self):
        # Premium allows 15 categories, but a dropdown could take 25 and a
        # button panel only 15 — the plan is what binds here.
        assert max_categories_for_style(STYLE_SELECT, PREMIUM_MAX_CATEGORIES) == \
            PREMIUM_MAX_CATEGORIES
        assert max_categories_for_style(STYLE_BUTTONS, PREMIUM_MAX_CATEGORIES) == \
            PREMIUM_MAX_CATEGORIES
        # …and a hypothetical plan above the rendering limit is capped by it.
        assert max_categories_for_style(STYLE_BUTTONS, 99) == MAX_BUTTONS_PER_PANEL
        assert max_categories_for_style(STYLE_SELECT, 99) == MAX_SELECT_OPTIONS


# =========================================================================== #
# Who may open a ticket
# =========================================================================== #
class TestCanOpen:
    def test_empty_allow_list_means_everyone(self):
        assert can_open(FakeMember(1), make_category()) is True

    def test_allow_list_restricts(self):
        category = make_category(allowed_role_ids=[10])
        assert can_open(FakeMember(1), category) is False
        assert can_open(FakeMember(1, [FakeRole(10)]), category) is True

    def test_deny_list_wins_over_allow_list(self):
        category = make_category(allowed_role_ids=[10], denied_role_ids=[11])
        member = FakeMember(1, [FakeRole(10), FakeRole(11)])
        assert can_open(member, category) is False

    def test_deny_list_wins_over_administrator(self):
        """A denied role that an admin could walk past would be a trap."""
        category = make_category(denied_role_ids=[11])
        member = FakeMember(1, [FakeRole(11)], administrator=True)
        assert can_open(member, category) is False

    def test_administrator_passes_an_allow_list(self):
        category = make_category(allowed_role_ids=[10])
        assert can_open(FakeMember(1, administrator=True), category) is True


# =========================================================================== #
# What a member may do inside a ticket
# =========================================================================== #
class TestMemberPermissions:
    def test_admin_permission_expands_to_everything(self):
        category = make_category(permissions={"10": [PERM_ADMIN]})
        assert set(role_permissions(category, 10)) == set(TICKET_PERMISSIONS)

    def test_roles_are_cumulative(self):
        category = make_category(permissions={
            "10": [PERM_VIEW], "11": [PERM_CLOSE],
        })
        member = FakeMember(1, [FakeRole(10), FakeRole(11)])
        assert member_permissions(member, category) == {PERM_VIEW, PERM_CLOSE}

    def test_guild_administrator_has_everything(self):
        member = FakeMember(1, administrator=True)
        assert member_permissions(member, make_category()) == set(TICKET_PERMISSIONS)

    def test_the_opener_can_see_their_ticket_but_gets_no_staff_power(self):
        ticket = {'owner_id': 7}
        granted = member_permissions(FakeMember(7), make_category(), ticket)
        assert granted == {PERM_VIEW}

    def test_a_stranger_has_nothing(self):
        assert member_permissions(FakeMember(9), make_category(), {'owner_id': 7}) == set()

    def test_someone_added_by_hand_can_see_the_ticket(self):
        """Must mirror the overwrites: they can read it, so they can act in it."""
        ticket = {'owner_id': 7, 'participants': [9], 'participant_roles': []}
        assert member_permissions(FakeMember(9), make_category(), ticket) == {PERM_VIEW}

    def test_a_role_added_by_hand_can_see_the_ticket(self):
        ticket = {'owner_id': 7, 'participants': [], 'participant_roles': [20]}
        member = FakeMember(9, [FakeRole(20)])
        assert member_permissions(member, make_category(), ticket) == {PERM_VIEW}

    def test_staff_role_ids_filters_by_permission(self):
        category = make_category(permissions={
            "10": [PERM_VIEW],
            "11": [PERM_VIEW, PERM_CLOSE],
            "12": [PERM_ADMIN],
        })
        assert sorted(staff_role_ids(category)) == [10, 11, 12]
        assert sorted(staff_role_ids(category, permission=PERM_CLOSE)) == [11, 12]
        assert staff_role_ids(category, permission=PERM_ADMIN) == [12]


# =========================================================================== #
# Rendering
# =========================================================================== #
class TestRendering:
    def test_channel_name_placeholders_and_sanitisation(self):
        category = make_category(name="Général", name_format="ticket-{number}-{username}")
        name = render_channel_name(category, member=FakeMember(1, name="Ada Lovelace"),
                                   number=42)
        assert name == "ticket-0042-ada-lovelace"

    def test_channel_name_never_ends_up_empty(self):
        category = make_category(name_format="!!!")
        assert render_channel_name(category, member=FakeMember(1), number=7) == \
            "ticket-0007"

    def test_message_placeholders(self):
        guild = SimpleNamespace(name="My Server")
        category = make_category(name="Billing")
        out = render_text("Hi {display_name}, ticket {number} on {server} — {category}",
                          member=FakeMember(1, name="ada", display_name="Ada"),
                          guild=guild, category=category, number=3)
        assert out == "Hi Ada, ticket 3 on My Server — Billing"

    def test_a_stray_brace_does_not_break_the_message(self):
        """str.format would raise here and silence the whole ticket."""
        guild = SimpleNamespace(name="S")
        out = render_text("50% off {not_a_placeholder} {",
                          member=FakeMember(1), guild=guild,
                          category=make_category(), number=1)
        assert out == "50% off {not_a_placeholder} {"

    @pytest.mark.parametrize("raw", [None, "", "support", "not an emoji at all"])
    def test_invalid_emoji_is_rejected(self, raw):
        assert parse_emoji(raw) is None

    def test_custom_emoji_is_accepted(self):
        emoji = parse_emoji("<:support:1519797524813185164>")
        assert emoji is not None and emoji.id == 1519797524813185164

    def test_unicode_emoji_is_accepted(self):
        assert parse_emoji("🎫") is not None


# =========================================================================== #
# Channel overwrites
# =========================================================================== #
class FakeGuild:
    def __init__(self, roles=(), members=()):
        self.default_role = FakeRole(0, "@everyone")
        self.me = FakeMember(999, name="Moddy")
        self._roles = {r.id: r for r in roles}
        self._members = {m.id: m for m in members}

    def get_role(self, role_id):
        return self._roles.get(role_id)

    def get_member(self, user_id):
        return self._members.get(user_id)


class TestOverwrites:
    def setup_method(self):
        from services.ticket_service import TicketService

        self.service = TicketService(bot=None)
        self.support = FakeRole(10, "Support")
        self.lead = FakeRole(11, "Lead")
        self.owner = FakeMember(7, name="opener")
        self.helper = FakeMember(8, name="helper")
        self.guild = FakeGuild(roles=[self.support, self.lead],
                               members=[self.owner, self.helper])
        self.category = make_category(permissions={
            "10": [PERM_VIEW, PERM_CLOSE],
            "11": [PERM_ADMIN],
        })

    def _ticket(self, **overrides):
        base = {'owner_id': 7, 'status': 'open', 'escalated': False,
                'participants': [], 'participant_roles': []}
        base.update(overrides)
        return base

    def test_an_open_ticket_hides_from_everyone_else(self):
        overwrites = self.service.build_overwrites(
            self.guild, self.category, self._ticket())
        assert overwrites[self.guild.default_role].view_channel is False
        assert overwrites[self.support].view_channel is True
        assert overwrites[self.lead].view_channel is True
        assert overwrites[self.owner].view_channel is True

    def test_participants_get_access(self):
        overwrites = self.service.build_overwrites(
            self.guild, self.category, self._ticket(participants=[8]))
        assert overwrites[self.helper].view_channel is True

    def test_escalation_keeps_only_the_responsibles_the_opener_and_manual_members(self):
        overwrites = self.service.build_overwrites(
            self.guild, self.category,
            self._ticket(escalated=True, participants=[8]))
        assert self.support not in overwrites      # plain staff role: out
        assert overwrites[self.lead].view_channel is True   # admin role: in
        assert overwrites[self.owner].view_channel is True  # opener: always in
        assert overwrites[self.helper].view_channel is True  # added by hand: in

    def test_escalation_after_dropping_participants(self):
        overwrites = self.service.build_overwrites(
            self.guild, self.category, self._ticket(escalated=True))
        assert self.helper not in overwrites

    def test_closing_hides_the_ticket_from_the_member_side_only(self):
        overwrites = self.service.build_overwrites(
            self.guild, self.category,
            self._ticket(status='closed', participants=[8]))
        assert self.owner not in overwrites
        assert self.helper not in overwrites
        assert overwrites[self.support].view_channel is True

    def test_a_missing_role_is_skipped_not_crashed_on(self):
        category = make_category(permissions={"404": [PERM_VIEW]})
        overwrites = self.service.build_overwrites(
            self.guild, category, self._ticket())
        assert all(getattr(target, 'id', None) != 404 for target in overwrites)


# =========================================================================== #
# Panel message
# =========================================================================== #
class TestPanelView:
    def _panel(self, style, count):
        return normalize_config({'panels': [{
            'id': 'p_a', 'name': "Support", 'style': style,
            'categories': [
                {'id': f'c_{i}', 'name': f"Cat {i}", 'discord_category_id': 100}
                for i in range(count)
            ],
        }]})['panels'][0]

    def _custom_ids(self, view):
        return [getattr(item, 'custom_id', None) for item in view.walk_children()]

    def test_buttons_are_rendered_one_per_category(self):
        from utils.ticket_views import build_panel_view

        view = build_panel_view(self._panel(STYLE_BUTTONS, 7))
        ids = [cid for cid in self._custom_ids(view)
               if cid and cid.startswith("moddy:tickets:open:")]
        assert len(ids) == 7
        assert ids[0] == "moddy:tickets:open:p_a:c_0"

    def test_a_dropdown_panel_renders_one_select(self):
        from utils.ticket_views import build_panel_view

        view = build_panel_view(self._panel(STYLE_SELECT, 7))
        ids = [cid for cid in self._custom_ids(view) if cid]
        assert ids == ["moddy:tickets:opensel:p_a"]

    def test_a_category_with_no_destination_is_not_offered(self):
        from utils.ticket_views import build_panel_view

        panel = self._panel(STYLE_BUTTONS, 3)
        panel['categories'][1]['discord_category_id'] = None
        panel['categories'][2]['enabled'] = False
        view = build_panel_view(panel)
        ids = [cid for cid in self._custom_ids(view)
               if cid and cid.startswith("moddy:tickets:open:")]
        assert ids == ["moddy:tickets:open:p_a:c_0"]

    def test_the_panel_view_never_expires(self):
        from utils.ticket_views import build_panel_view

        assert build_panel_view(self._panel(STYLE_BUTTONS, 1)).timeout is None


# =========================================================================== #
# /config screens
#
# The panel, category and permission screens are deliberately NOT in the
# persistent-view registry (their controls are dynamic items carrying the
# entity ids), so tests/test_persistent_views.py never builds them. These
# smoke tests are what stands in for that: they catch a screen that cannot be
# rendered at all, which is the failure mode that matters.
# =========================================================================== #
class FakeBot:
    """Just enough bot for a config screen: channel lookup and nothing else."""

    def __init__(self, channels=()):
        self._channels = {c.id: c for c in channels}

    def get_channel(self, channel_id):
        return self._channels.get(channel_id)


class TestConfigScreens:
    def setup_method(self):
        self.config = normalize_config({'panels': [{
            'id': 'p_a', 'name': "Support", 'channel_id': 500,
            'categories': [{
                'id': 'c_1', 'name': "General", 'discord_category_id': 100,
                'permissions': {"10": [PERM_VIEW, PERM_CLOSE], "11": [PERM_ADMIN]},
            }],
        }]})
        self.panel = self.config['panels'][0]
        self.category = self.panel['categories'][0]
        self.limits = {'premium': False, 'max_panels': FREE_MAX_PANELS,
                       'max_categories': FREE_MAX_CATEGORIES}
        self.bot = FakeBot()

    def _ids(self, view):
        return [cid for cid in
                (getattr(i, 'custom_id', None) for i in view.walk_children()) if cid]

    def test_panel_screen_renders(self):
        from modules.configs.tickets_panel_config import TicketPanelConfigView

        view = TicketPanelConfigView(self.bot, 1, "en-US", self.panel, self.limits)
        ids = self._ids(view)
        assert "moddy:tickets:pnlchan:p_a" in ids
        assert "moddy:tickets:pnlsel:style:p_a" in ids
        assert "moddy:tickets:pnlbtn:addcat:p_a" in ids
        assert view.timeout is None

    def test_panel_delete_confirmation_replaces_the_controls(self):
        from modules.configs.tickets_panel_config import TicketPanelConfigView

        view = TicketPanelConfigView(self.bot, 1, "en-US", self.panel, self.limits,
                                     confirm_delete=True)
        ids = self._ids(view)
        assert "moddy:tickets:pnlbtn:delconfirm:p_a" in ids
        # No way to keep configuring while the confirmation is up.
        assert "moddy:tickets:pnlchan:p_a" not in ids

    def test_category_screen_renders(self):
        from modules.configs.tickets_category_config import TicketCategoryConfigView

        view = TicketCategoryConfigView(self.bot, 1, "en-US", self.panel, self.category)
        ids = self._ids(view)
        assert "moddy:tickets:catdest:p_a:c_1" in ids
        assert "moddy:tickets:catroles:allowed:p_a:c_1" in ids
        assert "moddy:tickets:catroles:denied:p_a:c_1" in ids
        assert "moddy:tickets:catlang:p_a:c_1" in ids
        assert "moddy:tickets:catbtn:perms:p_a:c_1" in ids

    def test_permissions_screen_without_a_role_selected(self):
        from modules.configs.tickets_category_config import TicketPermissionsConfigView

        view = TicketPermissionsConfigView(self.bot, 1, "en-US", self.panel,
                                           self.category)
        ids = self._ids(view)
        assert "moddy:tickets:permrole:p_a:c_1" in ids
        assert not any(cid.startswith("moddy:tickets:permset:") for cid in ids)

    def test_permissions_screen_with_a_role_selected(self):
        from modules.configs.tickets_category_config import TicketPermissionsConfigView

        view = TicketPermissionsConfigView(self.bot, 1, "en-US", self.panel,
                                           self.category, role_id=10)
        ids = self._ids(view)
        assert "moddy:tickets:permset:p_a:c_1:10" in ids
        assert "moddy:tickets:permbtn:clear:p_a:c_1:10" in ids

    def test_every_config_custom_id_fits_discords_limit(self):
        from modules.configs.tickets_category_config import (
            TicketCategoryConfigView, TicketPermissionsConfigView,
        )
        from modules.configs.tickets_panel_config import TicketPanelConfigView

        views = [
            TicketPanelConfigView(self.bot, 1, "en-US", self.panel, self.limits),
            TicketCategoryConfigView(self.bot, 1, "en-US", self.panel, self.category),
            TicketPermissionsConfigView(self.bot, 1, "en-US", self.panel,
                                        self.category, role_id=123456789012345678),
        ]
        for view in views:
            for cid in self._ids(view):
                assert len(cid) <= 100, f"{cid} is {len(cid)} chars"


# =========================================================================== #
# i18n completeness
# =========================================================================== #
LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")


def _tickets_block(locale: str) -> dict:
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    with open(root / "locales" / f"{locale}.json", encoding="utf-8") as handle:
        return json.load(handle)["modules"]["tickets"]


def _flatten(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flatten(value, f"{prefix}.{key}" if prefix else key)
    else:
        yield prefix


@pytest.mark.parametrize("locale", [l for l in LOCALES if l != "en-US"])
def test_every_locale_has_the_same_ticket_keys(locale):
    reference = set(_flatten(_tickets_block("en-US")))
    translated = set(_flatten(_tickets_block(locale)))
    assert reference - translated == set(), \
        f"locales/{locale}.json is missing ticket keys"
    assert translated - reference == set(), \
        f"locales/{locale}.json has ticket keys English does not"


@pytest.mark.parametrize("locale", LOCALES)
def test_every_permission_is_named_and_described(locale):
    block = _tickets_block(locale)["permissions"]
    for permission in TICKET_PERMISSIONS:
        assert block[permission]["name"], f"{locale}: {permission} has no name"
        assert block[permission]["description"], \
            f"{locale}: {permission} has no description"


# =========================================================================== #
# Modals
#
# A modal over five top-level components is rejected by Discord at send time,
# with nothing in the logs to say which one — so the count is asserted here
# rather than discovered in production.
# =========================================================================== #
async def _noop(*args, **kwargs):
    pass


class TestModals:
    def setup_method(self):
        self.category = normalize_category({'name': "Support", 'ping_role_ids': [1, 2]})
        self.panel = normalize_config({'panels': [
            {'id': 'p_a', 'name': "Support", 'style': STYLE_SELECT},
        ]})['panels'][0]

    def _modals(self):
        from modules.configs.tickets_category_config import (
            CategoryIdentityModal, CategoryMessagesModal, CategoryOptionsModal,
        )
        from modules.configs.tickets_config import PanelAppearanceModal
        from utils.ticket_views import TicketReasonModal, TicketRenameModal

        return [
            PanelAppearanceModal("fr", None, _noop),
            PanelAppearanceModal("fr", self.panel, _noop),
            CategoryIdentityModal("fr", None, _noop),
            CategoryIdentityModal("fr", self.category, _noop),
            CategoryMessagesModal("fr", self.category, _noop),
            CategoryOptionsModal("fr", self.category, _noop),
            TicketReasonModal("fr", "close", _noop),
            TicketRenameModal("fr", "ticket-0001", _noop),
        ]

    def test_every_modal_builds_within_discords_limits(self):
        for modal in self._modals():
            assert len(modal.children) <= 5, type(modal).__name__
            assert len(modal.title) <= 45, type(modal).__name__

    def test_modal_titles_are_translated_not_key_paths(self):
        for modal in self._modals():
            assert not modal.title.startswith("["), \
                f"{type(modal).__name__}: missing translation {modal.title}"


# =========================================================================== #
# Ticket-channel views
# =========================================================================== #
class TestTicketChannelViews:
    def setup_method(self):
        self.category = normalize_category({'name': "Support", 'locale': "fr"})
        self.ticket = {'number': 42, 'owner_id': 7, 'status': 'open',
                       'escalated': False, 'participants': [8],
                       'participant_roles': [10]}
        self.actor = FakeMember(3, name="mod")

    def _ids(self, view):
        return [cid for cid in
                (getattr(i, 'custom_id', None) for i in view.walk_children()) if cid]

    def test_control_bar_offers_the_five_main_actions(self):
        from utils.ticket_views import build_ticket_message

        view = build_ticket_message(self.ticket, self.category, "Hello", "fr")
        assert self._ids(view) == [
            "moddy:tickets:ctrl:close",
            "moddy:tickets:ctrl:close_request",
            "moddy:tickets:ctrl:escalate",
            "moddy:tickets:ctrl:staff_thread",
            "moddy:tickets:ctrl:participants",
        ]

    def test_closing_card_offers_reopen_and_delete(self):
        from utils.ticket_views import build_closed_message

        view = build_closed_message(self.ticket, self.category, self.actor,
                                    "solved", "Bye", "fr")
        assert self._ids(view) == ["moddy:tickets:closed:reopen",
                                   "moddy:tickets:closed:delete"]

    def test_close_request_card(self):
        from utils.ticket_views import build_close_request_message

        view = build_close_request_message(self.ticket, self.actor, None, "fr")
        assert self._ids(view) == ["moddy:tickets:request:accept",
                                   "moddy:tickets:request:refuse"]

    def test_escalation_notice_can_be_undone(self):
        from utils.ticket_views import build_escalation_notice

        view = build_escalation_notice(self.ticket, self.actor, "abuse", "fr")
        assert self._ids(view) == ["moddy:tickets:escalated:cancel"]

    def test_participants_panel_preselects_the_current_ones(self):
        from utils.ticket_views import TicketParticipantsView

        view = TicketParticipantsView(None, self.ticket, "fr")
        selects = {getattr(i, 'custom_id', None): i for i in view.walk_children()}
        users = selects["moddy:tickets:members:users"]
        roles = selects["moddy:tickets:members:roles"]
        assert [d.id for d in users.default_values] == [8]
        assert [d.id for d in roles.default_values] == [10]

    def test_the_closing_dm_has_nothing_to_click(self):
        from types import SimpleNamespace

        from utils.ticket_views import build_close_dm

        view = build_close_dm(SimpleNamespace(name="S"), self.ticket, self.category,
                              self.actor, "solved", "fr")
        assert self._ids(view) == []


# =========================================================================== #
# Every translation key the code asks for actually exists
#
# A missing key does not raise — `t()` returns "[the.key.path]", which ships to
# a user as visible gibberish. Scanning the source is the only way to catch it
# before they do.
# =========================================================================== #
_SOURCE_FILES = (
    "modules/tickets.py",
    "services/ticket_service.py",
    "utils/ticket_views.py",
    "cogs/tickets.py",
    "modules/configs/tickets_config.py",
    "modules/configs/tickets_panel_config.py",
    "modules/configs/tickets_category_config.py",
)

# Keys built with an f-string cannot be read off the source, so every value the
# interpolation can take is listed here. A new permission or action added
# without a line here is a gap this test cannot see — see docs/TICKETS.md
# "Extending the module".
_INTERPOLATED_KEYS = (
    [f"modules.tickets.actions.{a}" for a in
     ("close", "close_request", "escalate", "staff_thread", "participants",
      "reopen", "delete", "deescalate", "rename")]
    + [f"modules.tickets.permissions.{p}.{k}" for p in TICKET_PERMISSIONS
       for k in ("name", "description")]
    + [f"modules.tickets.status.{s}" for s in ("open", "closed", "escalated")]
    + [f"modules.tickets.panel.style_{s}" for s in ("buttons", "select")]
    + [f"modules.tickets.category.color_{c}" for c in
       ("primary", "secondary", "success", "danger")]
    + [f"modules.tickets.messages.ph_{p.strip('{}')}" for p in PLACEHOLDERS]
    + [f"modules.tickets.participants.{k}_{s}" for k in ("added", "removed")
       for s in ("title", "description")]
)


def _referenced_keys():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    pattern = re.compile(
        r"""t\(\s*f?['"]((?:modules\.tickets|languages)[^'"{}]*)['"]""")
    keys = set(_INTERPOLATED_KEYS)
    for name in _SOURCE_FILES:
        keys |= set(pattern.findall((root / name).read_text(encoding="utf-8")))
    return sorted(keys)


@pytest.mark.parametrize("locale", LOCALES)
def test_every_referenced_key_resolves(locale):
    from utils.i18n import t

    keys = _referenced_keys()
    assert len(keys) > 150, "the source scan found suspiciously few keys"

    missing = [k for k in keys
               if (v := t(k, locale=locale)).startswith("[") and v.endswith("]")]
    assert not missing, f"locales/{locale}.json is missing: {missing}"


# =========================================================================== #
# Components V2 + content
#
# Discord rejects any message that carries both the IS_COMPONENTS_V2 flag and
# a `content` field, and discord.py sets that flag automatically for every
# LayoutView. `channel.send(content=..., view=SomeLayoutView())` is therefore
# a guaranteed 400 that only shows up at runtime — it shipped once already.
# =========================================================================== #
class TestNoContentWithLayoutView:
    def test_the_service_never_sends_content_alongside_a_view(self):
        """Static guard: the send calls in the service must have no content=."""
        import re
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent
                  / "services" / "ticket_service.py").read_text(encoding="utf-8")
        # Every `.send(` call and its argument list, up to the closing paren of
        # the call (good enough: no nested `send(` in this file).
        offenders = [
            call for call in re.findall(r"\.send\((.*?)\n\s*\)", source, re.S)
            if "view=" in call and re.search(r"\bcontent\s*=", call)
        ]
        assert not offenders, (
            "a send() passes both content= and view=; put the text inside the "
            f"view instead:\n{offenders}"
        )

    def test_mentions_are_rendered_inside_the_view(self):
        from utils.ticket_views import (
            build_close_request_message, build_escalation_notice,
            build_ticket_message,
        )

        category = normalize_category({'name': "Support", 'locale': "fr"})
        ticket = {'number': 1, 'owner_id': 7, 'status': 'open', 'escalated': False,
                  'participants': [], 'participant_roles': []}
        actor = FakeMember(3)
        mentions = "<@7> <@&10>"

        views = [
            build_ticket_message(ticket, category, "hello", "fr", mentions),
            build_close_request_message(ticket, actor, None, "fr", mentions),
            build_escalation_notice(ticket, actor, None, "fr", mentions),
        ]
        for view in views:
            rendered = "\n".join(
                getattr(item, "content", "") or "" for item in view.walk_children())
            assert mentions in rendered, type(view).__name__

    def test_the_views_still_build_without_mentions(self):
        """The persistent shells are constructed with no mentions at all."""
        from utils.ticket_views import (
            TicketCloseRequestView, TicketControlView, TicketEscalationView,
        )

        for cls in (TicketControlView, TicketCloseRequestView, TicketEscalationView):
            view = cls()
            assert view.is_persistent(), cls.__name__


# =========================================================================== #
# Default wording is offered, not hidden
# =========================================================================== #
class TestDefaultsArePrefilled:
    def test_messages_modal_prefills_both_messages_in_the_category_language(self):
        from modules.configs.tickets_category_config import CategoryMessagesModal
        from modules.tickets import default_close_message, default_open_message

        category = normalize_category({'name': "Support", 'locale': "fr"})
        modal = CategoryMessagesModal("de", category, _noop)   # admin reads German
        assert modal.open_input.default == default_open_message("fr")
        assert modal.close_input.default == default_close_message("fr")

    def test_an_existing_message_is_not_overwritten_by_the_default(self):
        from modules.configs.tickets_category_config import CategoryMessagesModal

        category = normalize_category({
            'name': "Support", 'locale': "fr",
            'open_message': "Bonjour !", 'close_message': "Au revoir !"})
        modal = CategoryMessagesModal("fr", category, _noop)
        assert modal.open_input.default == "Bonjour !"
        assert modal.close_input.default == "Au revoir !"

    def test_panel_modal_prefills_title_and_description(self):
        from modules.configs.tickets_config import PanelAppearanceModal
        from modules.tickets import default_panel_description, default_panel_title

        modal = PanelAppearanceModal("fr", None, _noop)
        assert modal.title_input.default == default_panel_title("fr")
        assert modal.description_input.default == default_panel_description("fr")

    @pytest.mark.parametrize("locale", LOCALES)
    def test_every_default_is_translated(self, locale):
        from modules.tickets import (
            default_close_message, default_open_message,
            default_panel_description, default_panel_title,
        )

        for fn in (default_open_message, default_close_message,
                   default_panel_title, default_panel_description):
            value = fn(locale)
            assert value and not value.startswith("["), f"{locale}: {fn.__name__}"
