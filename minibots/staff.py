"""
Staff commands on a mini-bot: ``@BumpReminder d.jsk …`` runs on that bot.

The same router as Moddy (``staff/framework/cog.py``), restricted to the
commands that make sense *on* one bot — acting on it or on the servers it is
in. Only those modules are imported in the mini-bots process (RAM). Anything
else answers "run it on Moddy", and Moddy's own info commands already include
every mini-bot (minibots/queries.py).

Permissions, denied commands and the staff audit log are exactly Moddy's:
same database, same rules; the log names the bot.
"""

from __future__ import annotations

import importlib
import logging
from typing import Set

from config import DEVELOPER_IDS

logger = logging.getLogger("moddy.minibots.staff")

#: Staff command modules available on a mini-bot.
MINIBOT_STAFF_COMMANDS = (
    "staff.commands.dev.jsk",         # evaluate code on that bot
    "staff.commands.dev.sql",         # same database, from that bot
    "staff.commands.dev.presence",    # that bot's status
    "staff.commands.dev.stats",       # that bot's numbers
    "staff.commands.dev.serverlist",  # that bot's servers
    "staff.commands.team.server",     # a server, as that bot sees it
    "staff.commands.team.invite",     # an invite through that bot
)

#: Persistent views the allowed commands need on each client.
MINIBOT_STAFF_VIEWS = ("staff.commands.dev.serverlist:ServerListView",)


class StaffFacade:
    """The ``bot`` the process-wide permission manager reads."""

    def __init__(self, db):
        self.db = db
        self.developer_ids: Set[int] = set(DEVELOPER_IDS or ())

    def is_developer(self, user_id: int) -> bool:
        return user_id in self.developer_ids


_facade: "StaffFacade | None" = None


def setup_process(db) -> StaffFacade:
    """Create the process-wide permission manager once (all mini-bots)."""
    global _facade
    from utils.staff_permissions import setup_staff_permissions
    if _facade is None:
        _facade = StaffFacade(db)
        setup_staff_permissions(_facade)
    return _facade


def make_router_class():
    from staff.framework import design, registry
    from staff.framework.cog import StaffCommandsRouter
    from utils.i18n import t
    from utils.staff_permissions import CommandType

    class MiniBotStaffRouter(StaffCommandsRouter):
        async def setup(self):
            for path in MINIBOT_STAFF_COMMANDS:
                importlib.import_module(path)
            allowed = set(MINIBOT_STAFF_COMMANDS)
            message_index, subgroup_index, _groups = registry.build(self.bot, self._run_slash)
            self.message_index = {k: c for k, c in message_index.items()
                                  if type(c).__module__ in allowed}
            self.subgroup_index = {}
            for key, bucket in subgroup_index.items():
                kept = {n: c for n, c in bucket.items() if type(c).__module__ in allowed}
                if kept:
                    self.subgroup_index[key] = kept
            # Every type is answered here (there is no legacy cog on a mini-bot)
            # and no slash group is published: message transport only.
            self.groups = []
            self.owned_types = {ct.value for ct in CommandType}

        async def _unknown_command(self, message, command_type, command_name: str) -> None:
            await self.reply_with_tracking(message, design.error(
                t("minibots.staff.moddy_only.title", locale="en-US"),
                t("minibots.staff.moddy_only.description", locale="en-US",
                  command=f"{command_type.value}.{command_name}", name=self.bot.spec.name),
            ))

    return MiniBotStaffRouter


async def setup(client) -> None:
    """Load the staff router on one mini-bot client."""
    from utils.staff_logger import StaffLogger
    facade = setup_process(client.db)
    client.staff_logger = StaffLogger(client)
    client.is_developer = facade.is_developer
    router = make_router_class()(client)
    await router.setup()
    await client.add_cog(router)
    for path in MINIBOT_STAFF_VIEWS:
        module, _, attr = path.partition(":")
        getattr(importlib.import_module(module), attr).register_persistent(client)


async def learn_developers(client) -> None:
    """Add the app's owner / team members to the developer set (like Moddy)."""
    if _facade is None:
        return
    try:
        info = await client.application_info()
        if info.team:
            _facade.developer_ids |= {m.id for m in info.team.members}
        elif info.owner:
            _facade.developer_ids.add(info.owner.id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Mini-bot %s: application info unavailable: %s", client.app_id, exc)
