# Persistent Views

> All interactive Discord views in Moddy should survive a bot restart. This
> document explains how the persistence layer works, the conventions every
> view must follow, and a cookbook for migrating existing views.

---

## Why

Without persistence:
- A view's buttons stop working after `timeout` seconds (default 180 s in
  raw discord.py).
- **Every** view in the bot dies on restart (Railway deploys, crashes,
  manual `d.restart`). Users see "This interaction failed" on every click.

With persistence:
- `BaseView` defaults to `timeout=None` — views never expire in memory.
- Registered views survive restarts: discord.py dispatches clicks back to a
  registered "shell" instance that rebuilds fresh state from `interaction`.

---

## How discord.py persistence works (short version)

A view is *persistent* when:
1. `timeout=None`, AND
2. every child item has a `custom_id` (URL buttons and non-interactive
   components count as persistent automatically).

You register persistent views **once** at startup with `bot.add_view(view)`.
Discord dispatches incoming button clicks by looking up
`(component_type, custom_id)`:
- **Running bot**: the live in-memory view that was sent with the message
  receives the click (full state available).
- **After restart**: falls back to the registered persistent view (the
  "shell"). `self` on the shell has no per-message state.

> **Rule**: callbacks must never rely on `self.locale`, `self.user_id`, etc.
> They must re-derive everything from `interaction`.

---

## The Moddy contract

Every persistent view in Moddy follows the same shape:

```python
from cogs.error_handler import BaseView
from utils.i18n import i18n, t
import discord
from discord import ui


_CID_DO_THING = "moddy:<cog>:<view>:do_thing"  # custom_id constant


class MyView(BaseView):
    """One-line description. Persistent: yes. Auth: <who can click>."""

    __persistent__ = True

    def __init__(self, bot=None, locale: str = "en-US", <other state>=None):
        super().__init__()  # timeout=None by default
        self.bot = bot
        self.locale = locale
        # ...
        self.build_view()

    def build_view(self):
        self.clear_items()
        container = ui.Container()
        # Any TextDisplay that needs live bot state goes inside
        #   `if self.bot is not None:` so the shell can build without crashing.
        if self.bot is not None:
            container.add_item(ui.TextDisplay(f"Servers: {len(self.bot.guilds)}"))
        self.add_item(container)

        # Interactive children — ALWAYS present, with stable custom_ids.
        row = ui.ActionRow()
        btn = ui.Button(
            label=t("...", locale=self.locale),
            style=discord.ButtonStyle.primary,
            custom_id=_CID_DO_THING,
        )
        btn.callback = self.on_do_thing
        row.add_item(btn)
        self.add_item(row)

    async def on_do_thing(self, interaction: discord.Interaction):
        # Re-derive EVERYTHING from interaction — self state may be empty.
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        # ... fetch data, rebuild view, edit message ...

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: <describe>."""
        bot.add_view(cls())  # shell instance: bot=None, defaults everywhere
```

Then add the class to
[`utils/persistent_views.py`](../utils/persistent_views.py) in
`_collect_persistent_view_classes()`. That's it.

---

## Custom ID convention

Format: `moddy:<cog>:<view>:<action>[:<param>...]`

Define them as **module-level constants** (e.g. `_CID_MAIN_BACK`) so they
show up in a single grep and typos can't silently break dispatch.

| Scope | Example | Comment |
|---|---|---|
| Stateless | `moddy:moddy:main:attribution` | No state encoded — callback derives from interaction |
| User-scoped | `moddy:reminder:delete:<user_id>` | Only the owner can click |
| Guild-scoped | `moddy:config:welcome:<guild_id>` | Re-check permission on click |
| Entity-scoped | `moddy:cases:view:<case_id>` | ID identifies a DB row |
| Paginated | `moddy:saved:page:<user_id>:<page>` | Page is small int |

### Never put in a `custom_id`
- `locale` → fetch from interaction via `i18n.get_user_locale(interaction)`
- Secrets, tokens, webhook URLs
- Anything the user should not be able to see (custom_ids are exposed to the
  Discord client)

---

## Authorization

**No single rule fits every view.** Pick the simplest model that preserves
today's UX, and document it in a comment above `register_persistent`.

| View type | Auth model | How |
|---|---|---|
| Public informational (`/moddy`, `/invite`, `/user`, `/avatar`, `/banner`, `/roll`) | **Public** | No check. Anyone who can see the message can click. |
| Personal data (reminders, preferences, saved messages, user cases) | **Owner only** | Encode `user_id` in custom_id, compare to `interaction.user.id` on click. Reject mismatches with an ephemeral error. |
| Guild config panels (`/config`, `modules/configs/*`) | **Guild permission** | Encode `guild_id` in custom_id. On click, verify `interaction.guild_id` matches and the user has the required permission (usually `manage_guild`). |
| Staff tools (`staff/*`) | **Staff rank** | On click, re-run `utils/staff_permissions.py` check — no user_id in custom_id. |

When the auth check fails, respond with an ephemeral
`utils/components_v2.create_error_message(...)` — never silently swallow.

---

## State reconstruction

Persistent views cannot remember anything between clicks. That is actually
*the* feature — it forces a clean flow:

1. **Click arrives** → `on_foo(interaction)` runs on either the live view or
   the registered shell.
2. **Re-derive context** from `interaction`:
   ```python
   bot = interaction.client
   locale = i18n.get_user_locale(interaction)
   user_id = interaction.user.id
   guild_id = interaction.guild_id
   ```
3. **Fetch fresh data** from the database / Discord API — don't trust any
   cached list stored on `self`.
4. **Build a new view** with the fresh state and `edit_message(view=...)`.

### Working-copy / pending edits
Some current views (e.g. `InterServerConfigView`) hold a
`working_config` with unsaved changes in memory. After a restart those are
lost. **Accepted UX**: the view rebuilds from the DB-saved config on the
next click; the user re-applies their unsaved edits. No drafts table.

---

## Registration flow

1. `bot.setup_hook()` runs once on startup
2. Cogs are loaded via `await self.load_extensions()`
3. Immediately after, `register_all_persistent_views(self)` is called
4. That function walks `_collect_persistent_view_classes()` and calls
   `cls.register_persistent(bot)` on each class
5. Each class typically calls `bot.add_view(cls())` with a shell instance

If a single view fails to register, the error is logged and the bot
continues — persistence is best-effort, it should never prevent startup.

---

## Cookbook: migrating an existing view

Given an existing `BaseView` subclass like `OldView(bot, guild_id, user_id, locale)`:

1. **Pick an auth model** (see table above) and write it in a 1-line comment.
2. **Make every constructor arg optional** with safe defaults so
   `OldView()` works.
3. **Guard any `self.bot.something` access** inside `_build_view` with
   `if self.bot is not None:` so the shell can build without a live bot.
4. **Add `custom_id` to every button / select** using module-level
   constants. Namespaced: `moddy:<cog>:<view>:<action>`.
5. **Rewrite callbacks** to re-derive `bot`, `locale`, `user_id`,
   `guild_id` from `interaction` instead of `self`.
6. **For user-scoped views**: parse the `user_id` out of the custom_id and
   reject mismatches. (Use a `DynamicItem` subclass when the id is encoded
   with a regex.)
7. **Set `__persistent__ = True`**.
8. **Implement `register_persistent`**: `bot.add_view(cls())`.
9. **Add the class** to `utils/persistent_views.py::_collect_persistent_view_classes()`.
10. **Smoke test**: instantiate a shell in an asyncio context and assert
    `view.is_persistent() is True`.

---

## Verifying a view is persistent

```python
# In an async context (event loop required):
v = MyView()  # shell, default args
assert v.timeout is None
assert v.is_persistent()
for item in v.walk_children():
    cid = getattr(item, "custom_id", None)
    if cid:
        print(cid)  # should match the namespaced constants
```

`bot.add_view(v)` will silently accept a non-persistent view on the None
key, but clicks will never dispatch. Always assert `is_persistent()` in
tests or at the top of `register_persistent`.

---

## Deliberate exclusions

- **Modals (`BaseModal`)** — Discord treats modal submission as a one-shot
  interaction tied to the owning message's in-memory component store.
  `discord.ui.Modal` already defaults to `timeout=None`, so modals will
  not expire mid-edit as long as the bot stays up. On restart, any open
  modal is effectively lost — the user re-opens it.
- **`ErrorView`** ([cogs/error_handler.py](../cogs/error_handler.py)) —
  error-recovery UI with only URL buttons. Already `timeout=None`. No
  dispatchable items to register.
- **`cogs/webhook.py::WebhookView`** — displays webhook tokens / URLs which
  are secret and should not be re-rendered after a restart. Keep as-is;
  users re-run the command.
