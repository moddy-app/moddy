"""Shared helpers for the ``/manage stripe`` sub-group."""

from __future__ import annotations

from typing import Any, Dict, Optional

from staff.framework import design, parse_user_id
from utils.i18n import t

GROUP = "stripe"
GROUP_DESCRIPTION = "Stripe subscription admin actions (cancel/resume/refund/trial)"


def resolve_target(ctx) -> Optional[int]:
    """Resolve the target user id from either transport (slash user option or raw id)."""
    target = ctx.opt("user")
    return target.id if target else parse_user_id(ctx.opt("user_id") or "")


async def send_result_panel(ctx, action_key: str, uid: int, result: Dict[str, Any]) -> None:
    """Render the signed backend reply as a success or error panel."""
    locale = ctx.locale
    title = t(f"staff.manage.stripe.{action_key}.title", locale=locale)

    if result.get("ok"):
        fields = [
            {"name": key, "value": f"`{value}`"}
            for key, value in result.items() if key not in ("ok", "request_id")
        ]
        await ctx.send(view=design.success(
            title,
            t(f"staff.manage.stripe.{action_key}.done", locale=locale, id=f"`{uid}`"),
            fields=fields,
        ))
        return

    error = result.get("error") or "unknown_error"
    await ctx.send(view=design.error(
        title,
        t("staff.manage.stripe.action_failed", locale=locale, error=f"`{error}`"),
    ))
