"""`/manage staff` — unified staff management panel.

Merges the old ``m.rank`` (add) and ``m.setstaff`` (roles + granular
permissions) into a single intuitive panel: assign roles, configure the
permissions for each role (and the shared "common" set), then save — or remove
the member from the team. Works from both message and slash transports.
"""

import json
import logging
from typing import Dict, List, Optional

import discord
from discord import ui

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_user_id
from staff.framework import badges
from utils import emojis
from utils.i18n import t
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


class StaffManagerPanel(BaseView):
    """Interactive role + permission editor for one staff member."""

    def __init__(self, *, bot, target: discord.User, modifier: discord.User, locale: str,
                 roles: List[StaffRole], role_permissions: Dict[str, List[str]],
                 common_permissions: List[str], is_staff: bool):
        super().__init__(timeout=600)
        self.bot = bot
        self.target = target
        self.modifier = modifier
        self.locale = locale
        self.roles = roles
        self.role_permissions = role_permissions
        self.common_permissions = common_permissions
        self.is_staff = is_staff
        self.scope: str = "common"
        self._build()

    # --- construction ------------------------------------------------------

    def _build(self):
        self.clear_items()
        loc = self.locale
        container = design.make_container("primary")
        container.add_item(ui.TextDisplay(
            f"{design.title_line(emojis.STAFF, t('staff.manage.staff.title', locale=loc))}\n"
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
        role_select = ui.Select(
            placeholder=t("staff.manage.staff.roles_placeholder", locale=loc),
            min_values=0, max_values=len(ASSIGNABLE_ROLES), custom_id="roles",
            options=[
                discord.SelectOption(
                    label=get_role_display_name(role.value), value=role.value,
                    emoji=discord.PartialEmoji.from_str(badges.role_badge(role.value)) if badges.role_badge(role.value) else None,
                    default=role in self.roles,
                ) for role in ASSIGNABLE_ROLES
            ],
        )
        role_select.callback = self._on_roles
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
            scope_select = ui.Select(placeholder=t("staff.manage.staff.scope_placeholder", locale=loc),
                                     min_values=1, max_values=1, custom_id="scope", options=scope_options)
            scope_select.callback = self._on_scope
            scope_row.add_item(scope_select)
            container.add_item(scope_row)

            available = COMMON_PERMISSIONS if self.scope == "common" else ROLE_PERMISSIONS_MAP.get(self.scope, [])
            current = self.common_permissions if self.scope == "common" else self.role_permissions.get(self.scope, [])
            if available:
                perm_row = ui.ActionRow()
                perm_select = ui.Select(
                    placeholder=t("staff.manage.staff.perms_placeholder", locale=loc),
                    min_values=0, max_values=len(available), custom_id="perms",
                    options=[discord.SelectOption(label=get_permission_label(p), value=p, default=p in current)
                             for p in available],
                )
                perm_select.callback = self._on_perms
                perm_row.add_item(perm_select)
                container.add_item(perm_row)

        self.add_item(container)

        # Action buttons.
        button_row = ui.ActionRow()
        save = ui.Button(label=t("staff.manage.staff.save", locale=loc), style=discord.ButtonStyle.success,
                         emoji=discord.PartialEmoji.from_str(emojis.SAVE))
        save.callback = self._on_save
        button_row.add_item(save)
        if self.is_staff:
            remove = ui.Button(label=t("staff.manage.staff.remove", locale=loc), style=discord.ButtonStyle.danger,
                               emoji=discord.PartialEmoji.from_str(emojis.LOGOUT))
            remove.callback = self._on_remove
            button_row.add_item(remove)
        self.add_item(button_row)

    # --- guards ------------------------------------------------------------

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.modifier.id:
            await interaction.response.send_message(t("staff.common.not_your_menu", locale=self.locale), ephemeral=True)
            return False
        return True

    # --- callbacks ---------------------------------------------------------

    async def _on_roles(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        values = interaction.data.get("values", [])
        new_roles = [StaffRole(v) for v in values]

        invalid = [r for r in new_roles if not await staff_permissions.can_assign_role(self.modifier.id, r)]
        if invalid:
            await interaction.response.send_message(
                t("staff.manage.staff.cannot_assign", locale=self.locale,
                  roles=", ".join(get_role_display_name(r.value) for r in invalid)),
                ephemeral=True,
            )
            return

        self.roles = new_roles
        kept = {r.value for r in new_roles}
        self.role_permissions = {k: v for k, v in self.role_permissions.items() if k in kept}
        for role in new_roles:
            self.role_permissions.setdefault(role.value, [])
        valid_scopes = ["common"] + [r.value for r in new_roles if ROLE_PERMISSIONS_MAP.get(r.value)]
        if self.scope not in valid_scopes:
            self.scope = "common"
        self._build()
        await interaction.response.edit_message(view=self)

    async def _on_scope(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        values = interaction.data.get("values", [])
        if values:
            self.scope = values[0]
            self._build()
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer()

    async def _on_perms(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        values = interaction.data.get("values", [])
        if self.scope == "common":
            self.common_permissions = values
        else:
            self.role_permissions[self.scope] = values
        self._build()
        await interaction.response.edit_message(view=self)

    async def _on_save(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        db = self.bot.db
        try:
            if not self.roles:
                await db.remove_staff_permissions(self.target.id)
                await db.set_attribute("user", self.target.id, "TEAM", False, self.modifier.id, "All roles removed via /manage staff")
                view = design.success(
                    t("staff.manage.staff.removed_title", locale=self.locale),
                    t("staff.manage.staff.removed", locale=self.locale, user=self.target.mention),
                )
            else:
                role_values = [r.value for r in self.roles]
                await db.set_staff_roles(self.target.id, role_values, self.modifier.id)
                all_perms = dict(self.role_permissions)
                all_perms["common"] = self.common_permissions
                async with db.pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE staff_permissions SET role_permissions = $1, updated_by = $2, updated_at = NOW() WHERE user_id = $3",
                        json.dumps(all_perms), self.modifier.id, self.target.id,
                    )
                view = design.success(
                    t("staff.manage.staff.saved_title", locale=self.locale),
                    t("staff.manage.staff.saved", locale=self.locale, user=self.target.mention),
                    fields=[{"name": t("staff.manage.staff.roles", locale=self.locale),
                             "value": " ".join(f"{badges.role_badge(r.value)} {get_role_display_name(r.value)}" for r in self.roles)}],
                )
            await interaction.response.edit_message(view=view)
        except Exception as exc:
            logger.error("Error saving staff permissions: %s", exc, exc_info=True)
            await interaction.response.send_message(
                t("staff.common.error.description", locale=self.locale), ephemeral=True)

    async def _on_remove(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        db = self.bot.db
        await db.remove_staff_permissions(self.target.id)
        await db.set_attribute("user", self.target.id, "TEAM", False, self.modifier.id, "Removed via /manage staff")
        await interaction.response.edit_message(view=design.success(
            t("staff.manage.staff.removed_title", locale=self.locale),
            t("staff.manage.staff.removed", locale=self.locale, user=self.target.mention),
        ))


@staff_command
class StaffPanelCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    name = "staff"
    aliases = ("rank", "setstaff")
    description = "Manage a member's staff roles and permissions."
    options = [
        SlashOption("user", "user", "Member to manage.", required=False),
        SlashOption("user_id", "string", "Member id (optional).", required=False),
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
