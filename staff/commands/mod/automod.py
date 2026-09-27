"""`/mod automod` — the automod's learned data and its labeling queue.

* ``status`` (default) — labeling queue size and throughput, the Google Vision
  monthly budget (SafeSearch + /ocr), and the size of every learned set
  (image hashes, embedding references, blocklist terms).
* ``delete_hash`` / ``delete_reference`` / ``delete_term`` ``<id>`` — purge one
  wrongly learned entry (the id is shown on ``status`` listings and in the DB).

See docs/AUTOMOD_AI.md §9.
"""

import discord
from discord import ui

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from cogs.error_handler import BaseView
from utils import emojis
from utils.i18n import t

ACTIONS = ["status", "delete_hash", "delete_reference", "delete_term"]


@staff_command
class AutomodLearningCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    name = "automod"
    permission = "automod_label"
    description = "Automod labeling queue + learned data (status, purge an entry)."
    options = [
        SlashOption("action", "string", "What to do", required=False,
                    default="status", choices=ACTIONS),
        SlashOption("id", "integer", "Entry id (for the delete actions)", required=False),
    ]

    async def execute(self, ctx):
        action = (ctx.opt("action") or "status").strip()
        db = getattr(ctx.bot, "db", None)
        if db is None:
            await ctx.send(view=design.error(
                t("staff.automod.unavailable", locale=ctx.locale)))
            return
        if action == "status":
            await ctx.send(view=await _status_panel(ctx))
            return
        if action not in ACTIONS:
            await ctx.send(view=design.invalid_usage(ctx.locale, "mod.automod [status|delete_hash|delete_reference|delete_term] [id]"))
            return
        try:
            entry_id = int(ctx.opt("id"))
        except (TypeError, ValueError):
            await ctx.send(view=design.invalid_usage(ctx.locale, f"mod.automod {action} <id>"))
            return

        ok = False
        if action == "delete_hash":
            svc = getattr(ctx.bot, "automod_images", None)
            ok = bool(svc and await svc.remove_hash(entry_id))
        elif action == "delete_reference":
            ok = await db.delete_learned_reference(entry_id)
            labels = getattr(ctx.bot, "automod_labels", None)
            if ok and labels is not None:
                await labels.load_learned()
        elif action == "delete_term":
            ok = await db.delete_learned_term(entry_id)
            labels = getattr(ctx.bot, "automod_labels", None)
            if ok and labels is not None:
                await labels.reload_terms()
        if ok:
            await ctx.send(view=design.success(
                t("staff.automod.deleted", locale=ctx.locale, action=action, id=entry_id)))
        else:
            await ctx.send(view=design.error(
                t("staff.automod.not_found", locale=ctx.locale, id=entry_id)))


async def _status_panel(ctx) -> BaseView:
    db = ctx.bot.db
    loc = ctx.locale
    queue = await db.label_queue_stats()
    hashes = await db.count_image_hashes()
    refs = await db.count_learned_references()
    terms = len(await db.list_learned_terms())
    svc = getattr(ctx.bot, "automod_images", None)
    budget = await svc.budget_snapshot() if svc else {}

    view = BaseView()
    container = design.make_container("info")
    container.add_item(ui.TextDisplay(design.title_line(
        emojis.SHIELD, t("staff.automod.title", locale=loc))))
    container.add_item(ui.TextDisplay(
        f"**{t('staff.automod.queue', locale=loc)}**\n"
        f"- {t('staff.automod.pending', locale=loc)} `{queue['pending']}`\n"
        f"- {t('staff.automod.created_24h', locale=loc)} `{queue['created_24h']}`\n"
        f"- {t('staff.automod.labeled_24h', locale=loc)} `{queue['labeled_24h']}`"
        f" · {t('staff.automod.revoked_24h', locale=loc)} `{queue['revoked_24h']}`"))
    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    ss = budget.get("safe_search", {})
    ocr = budget.get("document_text", {})
    container.add_item(ui.TextDisplay(
        f"**{t('staff.automod.budget', locale=loc)}**\n"
        f"- SafeSearch `{ss.get('used', 0)}` / `{ss.get('limit', 0)}`\n"
        f"- /ocr `{ocr.get('used', 0)}` / `{ocr.get('limit', 0)}`"))
    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(
        f"**{t('staff.automod.learned', locale=loc)}**\n"
        f"- {t('staff.automod.hashes', locale=loc)} "
        f"scam `{hashes.get('scam_block', 0)}` / allow `{hashes.get('scam_allow', 0)}` · "
        f"nsfw `{hashes.get('nsfw_block', 0)}` / allow `{hashes.get('nsfw_allow', 0)}`\n"
        f"- {t('staff.automod.references', locale=loc)} `{refs}`\n"
        f"- {t('staff.automod.terms', locale=loc)} `{terms}`"))
    view.add_item(container)
    return view
