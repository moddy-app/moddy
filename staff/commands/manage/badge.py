"""`/manage badge` — manage a user's verification badges.

Slash: single-user assign/remove via options. Message: same, plus a bulk
`import` subcommand (attach a `.json` file or pass inline JSON). Badge dates and
org lists live in ``users.data.verification`` (not in attributes).
"""

import json
import logging
import re
from datetime import datetime, timezone

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_user_id
from utils import emojis
from utils.i18n import t

logger = logging.getLogger("moddy.staff.manage.badge")

BADGE_ALIASES = {
    "v": "VERIFIED", "verified": "VERIFIED",
    "org": "VERIFIED_ORG", "vo": "VERIFIED_ORG", "verified_org": "VERIFIED_ORG",
    "member": "VERIFIED_ORG_MEMBER", "m": "VERIFIED_ORG_MEMBER", "verified_org_member": "VERIFIED_ORG_MEMBER",
}
REMOVE_WORDS = {"rm", "remove", "del", "delete"}


async def _apply_set(db, uid, attr_key, modifier, orgs):
    now_ts = int(datetime.now(timezone.utc).timestamp())
    await db.set_attribute("user", uid, attr_key, True, modifier, f"Badge {attr_key} set via badge command")
    await db.update_user_data(uid, f"verification.{attr_key}.date", now_ts)
    if attr_key == "VERIFIED_ORG_MEMBER" and orgs:
        user_db = await db.get_user(uid)
        existing = (user_db.get("data") or {}).get("verification", {}).get("VERIFIED_ORG_MEMBER", {})
        existing_orgs = existing.get("orgs") if isinstance(existing, dict) else []
        merged = list(existing_orgs) if isinstance(existing_orgs, list) else []
        for org in orgs:
            if org not in merged:
                merged.append(org)
        await db.update_user_data(uid, "verification.VERIFIED_ORG_MEMBER.orgs", merged)
        return merged
    return None


async def _apply_remove(db, uid, attr_key, modifier):
    await db.set_attribute("user", uid, attr_key, False, modifier, f"Badge {attr_key} removed via badge command")
    await db.update_user_data(uid, f"verification.{attr_key}.date", None)
    if attr_key == "VERIFIED_ORG_MEMBER":
        await db.update_user_data(uid, "verification.VERIFIED_ORG_MEMBER.orgs", None)


@staff_command
class BadgeCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    name = "badge"
    permission = "badge_manage"
    description = "Manage a user's verification badges."
    options = [
        SlashOption("user", "user", "Target user.", required=True),
        SlashOption("action", "string", "Add or remove.", required=False, default="add", choices=["add", "remove"]),
        SlashOption("type", "string", "Badge type.", required=False, default="verified",
                    choices=["verified", "org", "member"]),
        SlashOption("orgs", "string", "Org names for the member badge (comma-separated).", required=False),
    ]

    async def execute(self, ctx):
        # Message bulk import: `m.badge import [json]` (or attach a .json file).
        if not ctx.is_slash:
            tokens = (ctx.raw_args or "").split()
            if tokens and tokens[0].lower() == "import":
                await self._import(ctx, tokens[1:])
                return
            await self._message_single(ctx, tokens)
            return

        await self._slash_single(ctx)

    # --- slash single user -------------------------------------------------

    async def _slash_single(self, ctx):
        db = ctx.bot.db
        locale = ctx.locale
        user = ctx.opt("user")
        action = ctx.opt("action") or "add"
        attr_key = BADGE_ALIASES.get((ctx.opt("type") or "verified").lower(), "VERIFIED")
        orgs = [o.strip() for o in (ctx.opt("orgs") or "").split(",") if o.strip()]

        if action == "remove":
            await _apply_remove(db, user.id, attr_key, ctx.author.id)
            await ctx.send(view=design.success(
                t("staff.manage.badge.removed_title", locale=locale),
                t("staff.manage.badge.removed", locale=locale, key=f"`{attr_key}`", user=user.mention),
            ))
            return

        merged = await _apply_set(db, user.id, attr_key, ctx.author.id, orgs)
        desc = t("staff.manage.badge.assigned", locale=locale, key=f"`{attr_key}`", user=user.mention)
        if merged:
            desc += "\n" + t("staff.manage.badge.orgs", locale=locale, orgs=", ".join(f"**{o}**" for o in merged))
        await ctx.send(view=design.success(t("staff.manage.badge.assigned_title", locale=locale), desc))

    # --- message single user ----------------------------------------------

    async def _message_single(self, ctx, tokens):
        db = ctx.bot.db
        locale = ctx.locale

        target_id = None
        for mention in ctx.message.mentions:
            if mention.id != ctx.bot.user.id:
                target_id = mention.id
                break
        if target_id is None and tokens:
            target_id = parse_user_id(tokens[0])
        if tokens and re.match(r"<@!?\d+>", tokens[0]) or (tokens and parse_user_id(tokens[0]) is not None):
            tokens = tokens[1:]

        if not target_id or not tokens:
            await ctx.send(view=design.invalid_usage(locale, "m.badge <@user> v|org|member [orgs]  ·  m.badge <@user> rm <type>"))
            return

        action = tokens[0].lower()
        try:
            user = await ctx.bot.fetch_user(target_id)
            mention = user.mention
        except Exception:
            mention = f"<@{target_id}>"

        if action in REMOVE_WORDS:
            if len(tokens) < 2 or tokens[1].lower() not in BADGE_ALIASES:
                await ctx.send(view=design.invalid_usage(locale, "m.badge <@user> rm <v|org|member>"))
                return
            attr_key = BADGE_ALIASES[tokens[1].lower()]
            await _apply_remove(db, target_id, attr_key, ctx.author.id)
            await ctx.send(view=design.success(
                t("staff.manage.badge.removed_title", locale=locale),
                t("staff.manage.badge.removed", locale=locale, key=f"`{attr_key}`", user=mention),
            ))
            return

        if action not in BADGE_ALIASES:
            await ctx.send(view=design.invalid_usage(locale, "m.badge <@user> v|org|member [orgs]"))
            return

        attr_key = BADGE_ALIASES[action]
        orgs = [o.strip() for o in " ".join(tokens[1:]).split(",") if o.strip()]
        merged = await _apply_set(db, target_id, attr_key, ctx.author.id, orgs)
        desc = t("staff.manage.badge.assigned", locale=locale, key=f"`{attr_key}`", user=mention)
        if merged:
            desc += "\n" + t("staff.manage.badge.orgs", locale=locale, orgs=", ".join(f"**{o}**" for o in merged))
        await ctx.send(view=design.success(t("staff.manage.badge.assigned_title", locale=locale), desc))

    # --- bulk import (message only) ---------------------------------------

    async def _import(self, ctx, extra_tokens):
        db = ctx.bot.db
        locale = ctx.locale

        raw_json = None
        for attachment in ctx.message.attachments:
            if attachment.filename.endswith(".json") or (attachment.content_type and "json" in attachment.content_type):
                try:
                    raw_json = (await attachment.read()).decode("utf-8")
                    break
                except Exception as exc:
                    await ctx.send(view=design.error(t("staff.manage.badge.import_fail_title", locale=locale), f"`{exc}`"))
                    return
        if raw_json is None and extra_tokens:
            raw_json = " ".join(extra_tokens)
        if not raw_json:
            await ctx.send(view=design.invalid_usage(locale, "m.badge import [json]  (or attach a .json file)"))
            return

        try:
            entries = json.loads(raw_json)
            assert isinstance(entries, list)
        except Exception:
            await ctx.send(view=design.error(
                t("staff.manage.badge.import_fail_title", locale=locale),
                t("staff.manage.badge.import_bad_json", locale=locale),
            ))
            return

        ok, errs = [], []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                errs.append(f"#{i + 1}: not an object")
                continue
            try:
                uid = int(str(entry.get("user_id", "")).strip())
            except ValueError:
                errs.append(f"#{i + 1}: invalid user_id")
                continue
            attr_key = BADGE_ALIASES.get(str(entry.get("badge", "")).lower())
            if not attr_key:
                errs.append(f"`{uid}`: unknown badge")
                continue
            try:
                if str(entry.get("action", "set")).lower() == "remove":
                    await _apply_remove(db, uid, attr_key, ctx.author.id)
                    ok.append(f"`{uid}` — removed `{attr_key}`")
                else:
                    orgs = entry.get("orgs", [])
                    orgs = orgs if isinstance(orgs, list) else ([str(orgs)] if orgs else [])
                    if entry.get("replace_orgs") and attr_key == "VERIFIED_ORG_MEMBER":
                        await db.set_attribute("user", uid, attr_key, True, ctx.author.id, "Badge import")
                        await db.update_user_data(uid, "verification.VERIFIED_ORG_MEMBER.orgs", orgs)
                        await db.update_user_data(uid, f"verification.{attr_key}.date", int(datetime.now(timezone.utc).timestamp()))
                    else:
                        await _apply_set(db, uid, attr_key, ctx.author.id, orgs)
                    ok.append(f"`{uid}` — set `{attr_key}`")
            except Exception as exc:
                errs.append(f"`{uid}`: {exc}")

        lines = [f"**{t('staff.manage.badge.import_summary', locale=locale, ok=len(ok), err=len(errs))}**"]
        if ok:
            lines.append("\n".join(f"{emojis.DONE} {r}" for r in ok[:25]))
        if errs:
            lines.append("\n".join(f"{emojis.ERROR} {r}" for r in errs[:15]))
        view = (design.warning if errs else design.success)(
            t("staff.manage.badge.import_title", locale=locale), "\n".join(lines))
        await ctx.send(view=view)
