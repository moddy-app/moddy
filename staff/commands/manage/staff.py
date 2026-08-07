"""`/manage staff` — unified staff management panel.

Merges the old ``m.rank`` (add) and ``m.setstaff`` (roles + granular
permissions) into a single intuitive panel: assign roles, configure the
permissions for each role (and the shared "common" set), then save — or remove
the member from the team. Works from both message and slash transports.
"""

import json
import logging
import re
from typing import Dict, List, Optional

import discord
from discord import ui

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_user_id
from staff.framework import badges
from utils import emojis
from utils.i18n import i18n, t
from utils.components_v2 import create_error_message
from utils.staff_permissions import staff_permissions, StaffRole
from utils.staff_role_permissions import (
    COMMON_PERMISSIONS, ROLE_PERMISSIONS_MAP, get_permission_label, get_role_display_name,
)
from cogs.error_handler import BaseView

logger = logging.getLogger("moddy.staff.manage.staff")

# Roles that can be assigned through the panel (Dev is never assignable here).
ASSIGNABLE_ROLES = [
    StaffRole.MANAGER, StaffRole.SUPERVISOR_MOD, StaffRole.SUPERVISOR_COM,
    StaffRole.SUPERVISOR_SUP, StaffRole.MODERATOR, StaffRole.COMMUNICATION, StaffRole.SUPPORT,
]

# --------------------------------------------------------------------------- #
# custom_id templates
#
#   moddy:staffpanel:<action>:<target_id>:<modifier_id>
#
# HIGH-PRIVILEGE VIEW — grants/revokes staff roles and permissions. Both
# target_id (whose record is being edited) and modifier_id (who is allowed
# to edit it, "not your menu" semantics) must be encoded: neither is
# otherwise on the interaction, and unlike every other DynamicItem in this
# codebase a click here has real privilege-escalation consequences if the
# wrong id were ever trusted. See docs/PERSISTENT_VIEWS.md Migration log,
# Step 15 — THIS VIEW REQUIRES HUMAN REVIEW BEFORE MERGE.
# --------------------------------------------------------------------------- #
_CID_TEMPLATE = r"moddy:staffpanel:(?P<action>roles|scope|save|remove):(?P<target>\d{1,20}):(?P<modifier>\d{1,20})"

# The perms select needs a 4th field: which scope ("common" or a role value)
# its options belong to, since that isn't reconstructible from the
# interaction otherwise and guessing wrong would misfile permissions under
# the wrong role.
_CID_PERMS_TEMPLATE = r"moddy:staffpanel:permscope:(?P<scope>[a-z_]+):(?P<target>\d{1,20}):(?P<modifier>\d{1,20})"

# StaffRole values are mixed-case ("Manager", "Supervisor_Mod"); custom_ids
# must stay lowercase (moddy:<cog>:<view>:<action> convention), so the perms
# select's scope is lowercased on the way in and restored on the way out.
_SCOPE_BY_LOWER = {"common": "common", **{r.value.lower(): r.value for r in ASSIGNABLE_ROLES}}


def _guarded(callback):
    """Route DynamicItem callback errors to the central error handler.

    A DynamicItem dispatched via ``bot.add_dynamic_items`` has no live
    ``BaseView``, so ``BaseView.on_error`` never fires. Copied from
    ``utils/appeal_views.py``.
    """
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001 — funnel everything to the handler
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


async def _reject_if_not_modifier(interaction: discord.Interaction, modifier_id: int) -> bool:
    """Return True (and answer ephemerally) if the clicker is not the staff
    member who opened this panel — same "not your menu" semantics the
    non-persistent view enforced via self.modifier.id."""
    if interaction.user.id == modifier_id:
        return False
    locale = i18n.get_user_locale(interaction)
    await interaction.response.send_message(
        view=create_error_message(
            t("errors.not_your_message.title", locale=locale),
            t("errors.not_your_message.description", locale=locale),
        ),
        ephemeral=True,
    )
    return True


async def _load_panel_state(bot, target_id: int):
    """Re-fetch everything a StaffManagerPanel needs to render, keyed only
    on target_id. Used both for the initial command and to rebuild after a
    restarted shell's click, so a stale in-progress edit is discarded
    rather than silently mixed with whichever staff member's session last
    used the shared shell instance (see Step 8's mutate-and-resend-self
    writeup for why that matters)."""
    target = await bot.fetch_user(target_id)
    user_data = await bot.db.get_user(target_id)
    is_staff = bool(user_data["attributes"].get("TEAM"))
    perms = await bot.db.get_staff_permissions(target_id)
    roles = [StaffRole(r) for r in perms["roles"] if r != StaffRole.DEV.value]
    role_perms = {k: list(v) for k, v in (perms.get("role_permissions", {}) or {}).items() if k != "common"}
    common = list((perms.get("role_permissions", {}) or {}).get("common", []))
    return target, is_staff, roles, role_perms, common


class StaffManagerPanel(BaseView):
    """Interactive role + permission editor for one staff member.

    Persistent: yes. Auth: the staff member who opened the panel
    ("modifier"), re-checked per click via the encoded modifier_id — NOT
    staff rank re-verification (the original non-persistent view only ever
    checked ownership too; this migration preserves that exact model rather
    than introducing a stricter one). Role assignment still re-checks
    ``can_assign_role`` per role on every Roles-select change, unchanged.

    BEHAVIOUR CHANGE from the pre-migration version: roles and permissions
    now apply immediately when a select changes, instead of staging an
    in-memory pending edit for a later Save click. This was forced by
    DynamicItem's reconstruction model (every click is rebuilt fresh via
    ``from_custom_id``, live or restarted, with no access to a previous
    click's Python state — see docs/PERSISTENT_VIEWS.md Migration log,
    Step 15). Save is kept only as a confirmation of the already-applied
    state; it performs no additional write.

    HIGH PRIVILEGE — grants/revokes staff roles and permissions. A dispatch
    bug here is a privilege-escalation bug. Reviewed as part of the
    persistent-views migration (docs/PERSISTENT_VIEWS.md Step 15) but
    flagged there for mandatory human review before merge.
    """

    __persistent__ = True

    def __init__(self, *, bot=None, target: Optional[discord.User] = None,
                 modifier: Optional[discord.User] = None, locale: str = "en-US",
                 roles: Optional[List[StaffRole]] = None,
                 role_permissions: Optional[Dict[str, List[str]]] = None,
                 common_permissions: Optional[List[str]] = None, is_staff: bool = False,
                 scope: str = "common"):
        super().__init__()  # timeout=None
        self.bot = bot
        self.target = target
        self.modifier = modifier
        self.locale = locale
        self.roles = roles or []
        self.role_permissions = role_permissions or {}
        self.common_permissions = common_permissions or []
        self.is_staff = is_staff
        self.scope: str = scope
        self._build()

    # --- construction ------------------------------------------------------

    def _build(self):
        self.clear_items()
        loc = self.locale

        if self.target is None or self.modifier is None:
            # Registration shell — never actually sent to a user. Every
            # child is a DynamicItem registered by class (bot.add_dynamic_items),
            # not by the shell's own rendered contents, so this only needs
            # to construct without crashing (test_shell_constructs).
            container = design.make_container("primary")
            container.add_item(ui.TextDisplay("—"))
            self.add_item(container)
            return

        container = design.make_container("primary")
        container.add_item(ui.TextDisplay(
            f"{design.title_line(emojis.MODDYTEAM_BADGE, t('staff.manage.staff.title', locale=loc))}\n"
            f"{self.target.mention} (`{self.target.id}`)\n"
            f"-# {t('staff.manage.staff.subtitle', locale=loc)}"
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Role assignment.
        container.add_item(ui.TextDisplay(
            f"**{t('staff.manage.staff.roles', locale=loc)}**\n"
            f"-# {t('staff.manage.staff.roles_hint', locale=loc)}"
        ))
        role_row = ui.ActionRow()
        role_select = StaffPanelRolesSelect(self.target.id, self.modifier.id, locale=loc, roles=self.roles)
        role_row.add_item(role_select)
        container.add_item(role_row)

        # Permission configuration (only when roles are assigned).
        if self.roles:
            scope_options = [discord.SelectOption(
                label=t("staff.manage.staff.common", locale=loc), value="common",
                emoji=discord.PartialEmoji.from_str(emojis.SETTINGS), default=self.scope == "common",
            )]
            for role in self.roles:
                if ROLE_PERMISSIONS_MAP.get(role.value):
                    scope_options.append(discord.SelectOption(
                        label=get_role_display_name(role.value), value=role.value,
                        emoji=discord.PartialEmoji.from_str(badges.role_badge(role.value)) if badges.role_badge(role.value) else None,
                        default=self.scope == role.value,
                    ))

            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(
                f"**{t('staff.manage.staff.perms', locale=loc)}**\n"
                f"-# {t('staff.manage.staff.perms_hint', locale=loc)}"
            ))
            scope_row = ui.ActionRow()
            scope_select = StaffPanelScopeSelect(
                self.target.id, self.modifier.id, locale=loc, options=scope_options,
            )
            scope_row.add_item(scope_select)
            container.add_item(scope_row)

            available = COMMON_PERMISSIONS if self.scope == "common" else ROLE_PERMISSIONS_MAP.get(self.scope, [])
            current = self.common_permissions if self.scope == "common" else self.role_permissions.get(self.scope, [])
            if available:
                perm_row = ui.ActionRow()
                perm_select = StaffPanelPermsSelect(
                    self.target.id, self.modifier.id, self.scope, locale=loc,
                    available=available, current=current,
                )
                perm_row.add_item(perm_select)
                container.add_item(perm_row)

        self.add_item(container)

        # Action buttons.
        button_row = ui.ActionRow()
        button_row.add_item(StaffPanelActionButton("save", self.target.id, self.modifier.id, locale=loc))
        if self.is_staff:
            button_row.add_item(StaffPanelActionButton("remove", self.target.id, self.modifier.id, locale=loc))
        self.add_item(button_row)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: the staff member who opened the panel (modifier_id,
        encoded and re-checked on every click)."""
        bot.add_dynamic_items(
            StaffPanelRolesSelect, StaffPanelScopeSelect, StaffPanelPermsSelect, StaffPanelActionButton,
        )

    # --- shared rebuild helper ---------------------------------------------

    @staticmethod
    async def _rebuild(interaction: discord.Interaction, target_id: int, modifier_id: int, *,
                        roles: List[StaffRole], role_permissions: Dict[str, List[str]],
                        common_permissions: List[str], scope: str = "common") -> "StaffManagerPanel":
        """Construct a fresh panel — never mutate/resend a possibly-shared
        shell instance in place (see Step 8's writeup on why that leaks
        state across sessions on a view this high-privilege)."""
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        target, is_staff, _, _, _ = await _load_panel_state(bot, target_id)
        modifier = await bot.fetch_user(modifier_id)
        return StaffManagerPanel(
            bot=bot, target=target, modifier=modifier, locale=locale,
            roles=roles, role_permissions=role_permissions,
            common_permissions=common_permissions, is_staff=is_staff, scope=scope,
        )


# --------------------------------------------------------------------------- #
# Dynamic items (persistent). All four encode target_id + modifier_id — see
# the module-level docstring on _CID_TEMPLATE for why both are required.
# --------------------------------------------------------------------------- #

class StaffPanelRolesSelect(ui.DynamicItem[ui.Select], template=_CID_TEMPLATE):
    """Role-assignment select. Re-checks can_assign_role per role, unchanged
    from the pre-migration behaviour.

    Applies immediately (writes to the DB on change) rather than staging an
    in-memory pending edit for a later Save click — see the module-level
    note on why this view cannot support a deferred multi-step "edit
    several things, then Save" flow once every control has to be a
    DynamicItem (Migration log, Step 15). This is a deliberate,
    human-review-flagged behaviour change, not an oversight.
    """

    def __init__(self, target_id: int, modifier_id: int, *, locale: str = "en-US",
                 roles: Optional[List[StaffRole]] = None):
        roles = roles or []
        options = [
            discord.SelectOption(
                label=get_role_display_name(role.value), value=role.value,
                emoji=discord.PartialEmoji.from_str(badges.role_badge(role.value)) if badges.role_badge(role.value) else None,
                default=role in roles,
            ) for role in ASSIGNABLE_ROLES
        ]
        super().__init__(
            ui.Select(
                placeholder=t("staff.manage.staff.roles_placeholder", locale=locale),
                min_values=0, max_values=len(ASSIGNABLE_ROLES), options=options,
                custom_id=f"moddy:staffpanel:roles:{target_id}:{modifier_id}",
            )
        )
        self.target_id = target_id
        self.modifier_id = modifier_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Select, match: re.Match):
        return cls(int(match["target"]), int(match["modifier"]), locale=i18n.get_user_locale(interaction))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_modifier(interaction, self.modifier_id):
            return

        locale = i18n.get_user_locale(interaction)
        bot = interaction.client
        values = self.item.values
        new_roles = [StaffRole(v) for v in values]

        invalid = [r for r in new_roles if not await staff_permissions.can_assign_role(self.modifier_id, r)]
        if invalid:
            await interaction.response.send_message(
                t("staff.manage.staff.cannot_assign", locale=locale,
                  roles=", ".join(get_role_display_name(r.value) for r in invalid)),
                ephemeral=True,
            )
            return

        db = bot.db
        if not new_roles:
            await db.remove_staff_permissions(self.target_id)
            await db.set_attribute("user", self.target_id, "TEAM", False, self.modifier_id,
                                    "All roles removed via /manage staff")
            role_permissions, common = {}, []
        else:
            _, _, _, saved_role_perms, saved_common = await _load_panel_state(bot, self.target_id)
            kept = {r.value for r in new_roles}
            role_permissions = {k: v for k, v in saved_role_perms.items() if k in kept}
            for role in new_roles:
                role_permissions.setdefault(role.value, [])
            role_values = [r.value for r in new_roles]
            await db.set_staff_roles(self.target_id, role_values, self.modifier_id)
            all_perms = dict(role_permissions)
            all_perms["common"] = saved_common
            async with db.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE staff_permissions SET role_permissions = $1, updated_by = $2, updated_at = NOW() WHERE user_id = $3",
                    json.dumps(all_perms), self.modifier_id, self.target_id,
                )
            common = saved_common

        valid_scopes = ["common"] + [r.value for r in new_roles if ROLE_PERMISSIONS_MAP.get(r.value)]
        scope = "common" if "common" in valid_scopes else valid_scopes[0]

        view = await StaffManagerPanel._rebuild(
            interaction, self.target_id, self.modifier_id,
            roles=new_roles, role_permissions=role_permissions,
            common_permissions=common, scope=scope,
        )
        await interaction.response.edit_message(view=view)


class StaffPanelScopeSelect(ui.DynamicItem[ui.Select], template=_CID_TEMPLATE):
    """Picks which role's (or "common") permission set the perms select edits."""

    def __init__(self, target_id: int, modifier_id: int, *, locale: str = "en-US",
                 options: Optional[List[discord.SelectOption]] = None):
        options = options or [discord.SelectOption(label="—", value="common")]
        super().__init__(
            ui.Select(
                placeholder=t("staff.manage.staff.scope_placeholder", locale=locale),
                min_values=1, max_values=1, options=options,
                custom_id=f"moddy:staffpanel:scope:{target_id}:{modifier_id}",
            )
        )
        self.target_id = target_id
        self.modifier_id = modifier_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Select, match: re.Match):
        return cls(int(match["target"]), int(match["modifier"]), locale=i18n.get_user_locale(interaction))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_modifier(interaction, self.modifier_id):
            return
        values = self.item.values
        if not values:
            await interaction.response.defer()
            return

        bot = interaction.client
        _, _, roles, role_permissions, common = await _load_panel_state(bot, self.target_id)
        view = await StaffManagerPanel._rebuild(
            interaction, self.target_id, self.modifier_id,
            roles=roles, role_permissions=role_permissions,
            common_permissions=common, scope=values[0],
        )
        await interaction.response.edit_message(view=view)


class StaffPanelPermsSelect(ui.DynamicItem[ui.Select], template=_CID_PERMS_TEMPLATE):
    """Edits the permission set for whichever scope is currently selected.

    The scope is encoded in the custom_id (lowercased) rather than inferred,
    so a restarted shell's callback always knows which role's (or
    "common"'s) permissions the submitted values belong to — guessing wrong
    here would misfile a permission grant under the wrong role.
    """

    def __init__(self, target_id: int, modifier_id: int, scope: str = "common", *, locale: str = "en-US",
                 available: Optional[List[str]] = None, current: Optional[List[str]] = None):
        available = available or []
        current = current or []
        options = [
            discord.SelectOption(label=get_permission_label(p), value=p, default=p in current)
            for p in available
        ] or [discord.SelectOption(label="—", value="none")]
        super().__init__(
            ui.Select(
                placeholder=t("staff.manage.staff.perms_placeholder", locale=locale),
                min_values=0, max_values=max(len(options), 1), options=options,
                custom_id=f"moddy:staffpanel:permscope:{scope.lower()}:{target_id}:{modifier_id}",
            )
        )
        self.target_id = target_id
        self.modifier_id = modifier_id
        self.scope = scope

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Select, match: re.Match):
        scope = _SCOPE_BY_LOWER.get(match["scope"], "common")
        return cls(int(match["target"]), int(match["modifier"]), scope, locale=i18n.get_user_locale(interaction))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_modifier(interaction, self.modifier_id):
            return

        bot = interaction.client
        db = bot.db
        _, _, roles, role_permissions, common = await _load_panel_state(bot, self.target_id)
        values = self.item.values

        if self.scope == "common":
            common = values
        else:
            role_permissions[self.scope] = values

        # Applies immediately — see StaffPanelRolesSelect's docstring for
        # why this view cannot defer to a later Save click.
        all_perms = dict(role_permissions)
        all_perms["common"] = common
        async with db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE staff_permissions SET role_permissions = $1, updated_by = $2, updated_at = NOW() WHERE user_id = $3",
                json.dumps(all_perms), self.modifier_id, self.target_id,
            )

        view = await StaffManagerPanel._rebuild(
            interaction, self.target_id, self.modifier_id,
            roles=roles, role_permissions=role_permissions,
            common_permissions=common, scope=self.scope,
        )
        await interaction.response.edit_message(view=view)


class StaffPanelActionButton(ui.DynamicItem[ui.Button], template=_CID_TEMPLATE):
    """Save / Remove button."""

    _STYLE = {"save": discord.ButtonStyle.success, "remove": discord.ButtonStyle.danger}
    _EMOJI = {"save": emojis.SAVE, "remove": emojis.LOGOUT}
    _LABEL_KEY = {"save": "staff.manage.staff.save", "remove": "staff.manage.staff.remove"}

    def __init__(self, action: str, target_id: int, modifier_id: int, *, locale: str = "en-US"):
        super().__init__(
            ui.Button(
                label=t(self._LABEL_KEY[action], locale=locale)[:80],
                style=self._STYLE[action],
                emoji=discord.PartialEmoji.from_str(self._EMOJI[action]),
                custom_id=f"moddy:staffpanel:{action}:{target_id}:{modifier_id}",
            )
        )
        self.action = action
        self.target_id = target_id
        self.modifier_id = modifier_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match):
        return cls(match["action"], int(match["target"]), int(match["modifier"]),
                   locale=i18n.get_user_locale(interaction))

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if await _reject_if_not_modifier(interaction, self.modifier_id):
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        db = bot.db
        target = await bot.fetch_user(self.target_id)

        if self.action == "remove":
            await db.remove_staff_permissions(self.target_id)
            await db.set_attribute("user", self.target_id, "TEAM", False, self.modifier_id,
                                    "Removed via /manage staff")
            await interaction.response.edit_message(view=design.success(
                t("staff.manage.staff.removed_title", locale=locale),
                t("staff.manage.staff.removed", locale=locale, user=target.mention),
            ))
            return

        # save — every role/permission select already writes to the DB the
        # moment it changes (StaffPanelRolesSelect/StaffPanelPermsSelect),
        # because a DynamicItem is reconstructed fresh via from_custom_id()
        # on EVERY click — live or restarted — and never gets access to a
        # previous click's in-memory state (discord.py registers
        # DynamicItems by class, not by live instance; see
        # discord/ui/view.py View.add_view). A deferred "edit several
        # things, then Save" flow is therefore not implementable here
        # without external scratch storage, which is out of scope for this
        # migration. Save is kept only as a confirmation of the
        # already-applied state — it performs no additional write.
        _, _, roles, role_permissions, common = await _load_panel_state(bot, self.target_id)
        if not roles:
            view = design.success(
                t("staff.manage.staff.removed_title", locale=locale),
                t("staff.manage.staff.removed", locale=locale, user=target.mention),
            )
        else:
            view = design.success(
                t("staff.manage.staff.saved_title", locale=locale),
                t("staff.manage.staff.saved", locale=locale, user=target.mention),
                fields=[{"name": t("staff.manage.staff.roles", locale=locale),
                         "value": " ".join(f"{badges.role_badge(r.value)} {get_role_display_name(r.value)}" for r in roles)}],
            )
        await interaction.response.edit_message(view=view)


@staff_command
class StaffPanelCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    name = "staff"
    aliases = ("rank", "setstaff")
    description = "Manage a member's staff roles and permissions."
    options = [
        SlashOption("user", "user", "Member to manage.", required=True),
    ]

    def parse_message(self, raw: str) -> dict:
        return {"user_id": (raw or "").strip()}

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale
        target = ctx.opt("user")
        uid = target.id if target else parse_user_id(ctx.opt("user_id") or "")
        if not uid:
            await ctx.send(view=design.invalid_usage(locale, "m.staff <@user|user_id>"))
            return
        if target and target.bot:
            await ctx.send(view=design.error(
                t("staff.manage.staff.bot_title", locale=locale),
                t("staff.manage.staff.bot", locale=locale),
            ))
            return

        try:
            user = await bot.fetch_user(uid)
        except discord.NotFound:
            await ctx.send(view=design.error(
                t("staff.team.user_notfound_title", locale=locale),
                t("staff.team.user_notfound", locale=locale, id=f"`{uid}`"),
            ))
            return

        user_data = await bot.db.get_user(uid)
        is_staff = bool(user_data["attributes"].get("TEAM"))

        if (is_staff or bot.is_developer(uid)) and not await staff_permissions.can_modify_user(ctx.author.id, uid):
            await ctx.send(view=design.permission_denied(locale, t("staff.manage.hierarchy", locale=locale)))
            return

        perms = await bot.db.get_staff_permissions(uid)
        roles = [StaffRole(r) for r in perms["roles"] if r != StaffRole.DEV.value]
        role_perms = {k: list(v) for k, v in (perms.get("role_permissions", {}) or {}).items() if k != "common"}
        common = list((perms.get("role_permissions", {}) or {}).get("common", []))

        author = ctx.author if isinstance(ctx.author, discord.abc.User) else await bot.fetch_user(ctx.author.id)
        panel = StaffManagerPanel(
            bot=bot, target=user, modifier=author, locale=locale,
            roles=roles, role_permissions=role_perms, common_permissions=common, is_staff=is_staff,
        )
        await ctx.send(view=panel)
