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

---

## Appendix A — View inventory & auth mapping

Every `discord.ui.View` / `ui.LayoutView` / `BaseView` subclass in the
repository, as of the recon pass. `tests/` is excluded.

**How to read the columns**

- *# children* counts interactive component **construction sites**
  (`ui.Button` / `ui.Select` / `ui.ChannelSelect` / `ui.RoleSelect` /
  `ui.UserSelect`) found statically in the class body. Most `_build_view`
  methods are branchy, so the number of children on a given rendered message
  is usually **lower**. Treat it as "how much surface has to get a
  `custom_id`", not as an exact runtime count.
- *Persistent?* = `__persistent__ = True` **and** present in
  `utils/persistent_views.py::_collect_persistent_view_classes()`.
- *Timeout* is what the class passes to `super().__init__`; `—` means it
  inherits `BaseView`'s `timeout=None`.

### A.1 — `cogs/`

| Class | File | `__init__` signature | # children | Timeout | Persistent? | Proposed auth model | Why |
|---|---|---|---|---|---|---|---|
| `AvatarView` | [cogs/avatar.py:18](../cogs/avatar.py) | `(user_data, moddy_attributes, locale, user_verification_data=None)` | 0 | — | No | **Public** | Renders a public avatar card; no interactive child exists, so persistence is a no-op (see B.4). |
| `BannerView` | [cogs/banner.py:18](../cogs/banner.py) | `(user_data, moddy_attributes, locale, user_verification_data=None)` | 0 | — | No | **Public** | Same as `AvatarView` — static banner card, nothing dispatchable. |
| `ConfigMainView` | [cogs/config.py:19](../cogs/config.py) | `(bot, guild_id, user_id, locale)` | 1 | 300 s | No | **Guild permission** | The module select routes into every guild config panel; a click mutates guild scope, so `manage_guild` must be re-checked. Has an `interaction_check`, but it compares against the in-memory `user_id`. |
| `EmojiView` | [cogs/emoji.py:20](../cogs/emoji.py) | `(emoji_data, locale, bot=None)` | 0 | 180 s | No | **Public** | Static emoji info card. Only the 180 s timeout needs removing. |
| `EmojiNavigationView` | [cogs/emoji.py:87](../cogs/emoji.py) | `(emoji_list, locale, bot, author)` | 3 | 180 s | No | **Owner only** | Paginates the invoker's scanned emoji list and already gates on `author` via `interaction_check`; the emoji list itself is unsaved in-memory state (see B.1). |
| `BaseView` | [cogs/error_handler.py:166](../cogs/error_handler.py) | `(*, timeout=None, **kwargs)` | 0 | None | n/a | n/a | Abstract base class, never instantiated directly. |
| `ErrorView` | [cogs/error_handler.py:419](../cogs/error_handler.py) | see file | 2 | None | No — **excluded** | n/a | URL-only buttons (see "Deliberate exclusions"). |
| `PermissionErrorView` | [cogs/error_handler.py:861](../cogs/error_handler.py) | `()` (nested in a function) | 1 | None | No — **excluded** | n/a | URL-only button, function-local class (see B.5). |
| `CooldownErrorView` | [cogs/error_handler.py:892](../cogs/error_handler.py) | `()` (nested) | 0 | None | No — **excluded** | n/a | No interactive child. |
| `NotFoundView` | [cogs/error_handler.py:917](../cogs/error_handler.py) | `()` (nested) | 0 | None | No — **excluded** | n/a | No interactive child. |
| `ReportView` | [cogs/interserver_commands.py:126](../cogs/interserver_commands.py) | `(bot, moddy_id, reporter_id, author_mention, guild_name, guild_id, reporter_mention, content)` | 3 | — | No | **Staff rank** | Claim / Processed / Skip buttons on an inter-server abuse report. `on_claim` already re-runs `staff_permissions.get_user_roles` against `MODERATOR`+ ([cogs/interserver_commands.py:191-199](../cogs/interserver_commands.py)), so the auth model is already correct — but the class is function-local (B.5) and its `claimed_by` lives only in memory (B.1). |
| `InfoView` | [cogs/interserver_commands.py:329](../cogs/interserver_commands.py) | nested | 0 | — | No | **Public** | Static info card. |
| `ProcessedView` | [cogs/interserver_commands.py:365](../cogs/interserver_commands.py) | nested | 0 | — | No | **Public** | Static confirmation card. |
| `InviteView` | [cogs/invite.py:20](../cogs/invite.py) | `(invite_data, locale)` | 3 | 180 s | No | **Public** | Shows a public invite's metadata; the buttons only toggle between rendered panes of data already fetched. Note the "Raw Data" button is marked `# TEMP` in the source — confirm it ships (B.5). |
| `ServerInfoView` | [cogs/invite.py:375](../cogs/invite.py) | `(invite_data, locale)` | 1 | 180 s | No | **Public** | Back button into `InviteView`; same public invite payload. Its `invite_data` is unsaved in-memory state (B.1). |
| `AttributionView` | [cogs/moddy.py:25](../cogs/moddy.py) | `(bot=None, locale="en-US", user_id=None)` | 1 | — | **Yes** | **Public** | Already migrated. Reference implementation for the stateless/public case. |
| `WeSupportView` | [cogs/moddy.py:109](../cogs/moddy.py) | `(bot=None, locale="en-US", user_id=None)` | 1 | — | **Yes** | **Public** | Already migrated. |
| `ModdyMainView` | [cogs/moddy.py:174](../cogs/moddy.py) | `(bot=None, locale="en-US", user_id=None)` | 2 | — | **Yes** | **Public** | Already migrated; the canonical `if self.bot is not None:` guard example. |
| `PreferencesView` | [cogs/preferences.py:110](../cogs/preferences.py) | `(bot, user_id, locale, user_data)` | 2 | 300 s | No | **Owner only** | Reads and writes the clicker's own user row (timezone, incognito). Subclasses `LayoutView` directly, not `BaseView` (B.5). custom_ids `"timezone_btn"` / `"back_btn"` are un-namespaced and collide (B.5). |
| `RemindersManageView` | [cogs/reminder.py:478](../cogs/reminder.py) | `(bot, user_id, reminders, locale, user_tz, show_history=False, past_reminders=None, original_interaction=None)` | 5 | 300 s | No | **Owner only** | Lists, edits and deletes the clicker's personal reminders; already has an owner `interaction_check`. Subclasses `LayoutView` directly (B.5); holds `reminders`, `past_reminders`, `show_history` and an `original_interaction` in memory (B.1). |
| `RollView` | [cogs/roll.py:17](../cogs/roll.py) | `(result, max_value, locale)` | 0 | — | No | **Public** | Static dice result. Nothing to register. |
| `SavedMessagesLibraryView` | [cogs/saved_messages.py:222](../cogs/saved_messages.py) | `(bot, user_id, messages, locale, …)` | 8 | 300 s | No | **Owner only** | Browses and deletes the clicker's bookmarked messages. Subclasses `LayoutView` directly (B.5); paginated over an in-memory `messages` slice (B.1); un-namespaced custom_ids collide with `reminder.py` (B.5). |
| `SubscriptionView` | [cogs/subscription.py:27](../cogs/subscription.py) | `(bot, user_id, sub, servers, locale)` | 3 | 300 s | No | **Public** (URL-only) | All three children are `ButtonStyle.link` buttons ([cogs/subscription.py:99-115](../cogs/subscription.py)) — nothing dispatches. The **only** required change is dropping `timeout=300`; do not register it. |
| `TextResultView` | [cogs/text_tools.py:109](../cogs/text_tools.py) | `(*, title, body, footer, accent, meta=None, code_block=True)` | 0 | — | No | **Public** | Docstring states it is purely informational; no interactive child. |
| `TranslateView` | [cogs/translate.py:21](../cogs/translate.py) | `(bot, original_text, translated_text, from_lang, current_to_lang, locale, author)` | 1 | 120 s | No | **Owner only** | The target-language select re-translates; already author-gated. Holds `original_text` in memory, which is not recoverable from the DB (B.1 — the hard case). |
| `UserInfoView` | [cogs/user.py:42](../cogs/user.py) | `(user_data, bot_data, moddy_attributes, locale, author_id, bot, user_verification_data=None)` | 5 | 180 s | No | **Public** | Shows public Discord profile data (avatar / banner / description panes). It carries an `author_id` but no `interaction_check` was found, so it is effectively public today — **keep it public rather than tightening behaviour during a mechanical migration.** |
| `WebhookView` | [cogs/webhook.py:19](../cogs/webhook.py) | `(webhook_data, author, locale)` | 4 | 300 s | No — **excluded** | n/a | Renders webhook tokens/URLs. Already an agreed exclusion. |

### A.2 — `modules/`

| Class | File | `__init__` signature | # children | Timeout | Persistent? | Proposed auth model | Why |
|---|---|---|---|---|---|---|---|
| `AdaptiveSlowmodeChannelConfigView` | [modules/configs/adaptive_slowmode_config.py:66](../modules/configs/adaptive_slowmode_config.py) | `(bot, guild_id, user_id, locale, parent_view, channel_id=None, channel_config=None)` | 6 | 300 s | No | **Guild permission** | Edits per-channel slowmode bounds for a guild. `parent_view` is a required positional with no default and cannot be reconstructed (B.1 — the hardest case in the repo). |
| `AdaptiveSlowmodeConfigView` | [modules/configs/adaptive_slowmode_config.py:364](../modules/configs/adaptive_slowmode_config.py) | `(bot, guild_id, user_id, locale, current_config=None)` | 7 | 300 s | No | **Guild permission** | Guild slowmode panel with Save/Cancel; holds a `working_config` deep copy (B.1). |
| `AutoRestoreRolesConfigView` | [modules/configs/auto_restore_roles_config.py:18](../modules/configs/auto_restore_roles_config.py) | `(bot, guild_id, user_id, locale, current_config=None)` | 8 | 300 s | No | **Guild permission** | Chooses which guild roles get restored on rejoin; `working_config` (B.1). Zero `custom_id`s today. |
| `AutoRoleConfigView` | [modules/configs/auto_role_config.py:18](../modules/configs/auto_role_config.py) | `(bot, guild_id, user_id, locale, current_config=None)` | 6 | 300 s | No | **Guild permission** | Assigns guild roles to joiners; `working_config` (B.1). Zero `custom_id`s today. |
| `AutomodAIConfigView` | [modules/configs/automod_ai_config.py:145](../modules/configs/automod_ai_config.py) | `(bot, guild_id, user_id, locale="en-US", current_config=None)` | 14 | 300 s | No | **Guild permission** | The largest config panel — toggles automod enforcement for the guild. `working_config` plus lazily-loaded `_precedent_count` / `_precedent_last` (B.1). Zero `custom_id`s today. |
| `AutomodAIPrecedentsView` | [modules/configs/automod_ai_precedents_view.py:31](../modules/configs/automod_ai_precedents_view.py) | `(bot, guild_id, user_id, locale="en-US", parent=None)` | 4 | 300 s | No | **Guild permission** | Paginated browse/delete over `automod_precedents` rows for the guild. `rows` are re-fetchable via `load()`, so state loss is cheap; `parent` is not (B.1). |
| `InterServerConfigView` | [modules/configs/interserver_config.py:18](../modules/configs/interserver_config.py) | `(bot, guild_id, user_id, locale, current_config=None)` | 6 | 300 s | No | **Guild permission** | Named in the doc body as the `working_config` exemplar. |
| `SocialNotificationsConfigView` | [modules/configs/social_notifications_config.py:286](../modules/configs/social_notifications_config.py) | `(bot=None, guild_id=None, user_id=None, …)` | 4 | — | **Yes** | **Guild permission** | Already migrated. Reference implementation for the guild-permission case. |
| `AddSubscriptionView` | [modules/configs/social_notifications_config.py:479](../modules/configs/social_notifications_config.py) | `(bot=None, guild_id=None, locale="en-US", …)` | 7 | — | **Partly** | **Guild permission** | Has `__persistent__`-style shape and full `custom_id`s, but is **not** in `_collect_persistent_view_classes()`. Either add it or document why the parent registration is sufficient (B.5). |
| `ManageSubscriptionView` | [modules/configs/social_notifications_config.py:774](../modules/configs/social_notifications_config.py) | `(bot=None, guild_id=None, locale="en-US", …)` | 6 | — | **Partly** | **Guild permission** | Same as above — migrated in shape, unregistered in fact. |
| `StarboardConfigView` | [modules/configs/starboard_config.py:92](../modules/configs/starboard_config.py) | `(bot, guild_id, user_id, locale, current_config=None)` | 7 | 300 s | No | **Guild permission** | Guild starboard channel/threshold/emoji; `working_config` (B.1). 6 of 7 children already have (un-namespaced) custom_ids. |
| `WelcomeChannelConfigView` | [modules/configs/welcome_channel_config.py:119](../modules/configs/welcome_channel_config.py) | `(bot, guild_id, user_id, locale, current_config=None)` | 13 | 300 s | No | **Guild permission** | Guild welcome message + embed builder; `working_config` (B.1). Un-namespaced custom_ids collide 1:1 with `welcome_dm_config.py` (B.5 — the worst collision in the repo). |
| `WelcomeDmConfigView` | [modules/configs/welcome_dm_config.py:119](../modules/configs/welcome_dm_config.py) | `(bot, guild_id, user_id, locale, current_config=None)` | 11 | 300 s | No | **Guild permission** | Same panel shape for the DM variant; identical custom_ids to the channel variant (B.5). |
| `WelcomeView` | [modules/interserver.py:444](../modules/interserver.py) | `()` (function-local) | 0 | — | No | **Public** | Static DM card. Nothing to register. |
| `StaffLogView` | [modules/interserver.py:507](../modules/interserver.py) | `(moddy_id, author_info, server_info, content_preview, success_count, total_count, is_moddy_team)` (function-local) | 0 | — | No | **Public** | Static log card. Nothing to register. |

### A.3 — `staff/`

| Class | File | `__init__` signature | # children | Timeout | Persistent? | Proposed auth model | Why |
|---|---|---|---|---|---|---|---|
| `EmojiPreviewView` | [staff/commands/dev/emoji_preview.py:49](../staff/commands/dev/emoji_preview.py) | `(partial_emoji, emoji_str)` | 3 | — | No | **Staff rank** | Dev-only emoji rendering preview; the select and sample buttons only re-render the same emoji. One child is a URL button. |
| `ServerListView` | [staff/commands/dev/serverlist.py:13](../staff/commands/dev/serverlist.py) | `(bot, author_id, guilds, locale, per_page=10)` | 2 | 300 s | No | **Staff rank** | Paginates the bot's full guild list — dev-visible data. Already author-gated; the `guilds` snapshot is in memory but trivially re-derivable from `bot.guilds` (B.1). |
| `SqlConfirmView` | [staff/commands/dev/sql.py:48](../staff/commands/dev/sql.py) | `(bot, author_id, query, locale)` | 2 | 60 s | No — **recommend exclusion** | n/a | Confirm/cancel gate on an arbitrary SQL statement. The pending `query` string is the entire payload and must not be re-runnable after a restart (B.4). |
| `BannerTypeSelectView` | [staff/commands/manage/banner/_modals.py:108](../staff/commands/manage/banner/_modals.py) | `(bot, author_id, locale, banner_id=None, prefill=None)` | 2 | 180 s | No | **Staff rank** | Two buttons that each open a modal. Carries a `prefill` dict of unsaved banner content (B.1). |
| `StaffManagerPanel` | [staff/commands/manage/staff.py:35](../staff/commands/manage/staff.py) | `(*, bot, target, modifier, locale, …)` | 5 | 600 s | No | **Staff rank** | Grants and revokes staff roles/permissions — the highest-privilege panel in the bot. `target` and `modifier` are `discord.User` objects; a persistent version must encode `target_id` and re-fetch (B.1, B.2). |
| `HelpView` | [staff/commands/team/help.py:34](../staff/commands/team/help.py) | `(*, bot, author_id, locale, data)` | 1 | 300 s | No | **Staff rank** | Department select over the staff command catalogue; `data` is a rebuildable registry snapshot. Lowest-risk staff view to migrate first. |
| `_ModalButtonView` | [staff/framework/context.py:168](../staff/framework/context.py) | `(*, bot, author_id, modal_factory, label, …)` | 1 | 300 s | No — **recommend exclusion** | n/a | Generic "click to open a modal" shim whose behaviour is a `modal_factory` **callable** held in memory. Not serialisable into a custom_id (B.1, B.4). |
| `ConfirmView` | [staff/framework/views.py:16](../staff/framework/views.py) | `(*, bot, author_id, locale, title, description, …)` | 2 | 60 s | No — **recommend exclusion** | n/a | Generic confirm/cancel whose "yes" branch is an in-memory callback. Same problem as `_ModalButtonView` (B.4). |

### A.4 — `utils/`

| Class | File | `__init__` signature | # children | Timeout | Persistent? | Proposed auth model | Why |
|---|---|---|---|---|---|---|---|
| `AppealPersistence` | [utils/appeal_views.py:850](../utils/appeal_views.py) | inherited | 0 | — | **Yes** | **Staff rank** (per-item) | Marker view; `register_persistent` calls `bot.add_dynamic_items(...)` for the five appeal buttons. Reference implementation for the `DynamicItem` case. |
| `ShadowAnnotationPersistence` | [utils/automod_shadow_views.py:248](../utils/automod_shadow_views.py) | inherited | 0 | — | **Yes** | **Guild permission** (per-item) | Same marker pattern for `ShadowAnnotateButton`, which re-checks `manage_messages` on click. |
| `CaseCreationView` | [utils/case_management_views.py:63](../utils/case_management_views.py) | `(*, bot, staff_id, subject_type, subject_id, subject_name, …)` | 4 | 300 s | No | **Staff rank** | Multi-step case creation wizard. The half-built case exists only in memory before Confirm (B.1). |
| `AddSanctionView` | [utils/case_management_views.py:273](../utils/case_management_views.py) | `(*, bot, staff_id, case_id, reference, …)` | 1 | 300 s | No | **Staff rank** | Action select bound to an existing `case_id` — a good `DynamicItem` candidate (B.2). |
| `RevokeSanctionView` | [utils/case_management_views.py:379](../utils/case_management_views.py) | `(*, bot, staff_id, reference, …)` | 1 | 300 s | No | **Staff rank** | Select over a case's active sanctions; keyed by `reference` (B.2). |
| `CasesBrowserView` | [utils/cases_views.py:167](../utils/cases_views.py) | `(bot=None, *, mode="server", viewer_id=None, locale="en-US", …)` | 19 | None | **Yes** | **Owner only** (user mode) / **Guild permission** (server mode) | Already migrated. The reference for a mode-parameterised custom_id (`…:{mode}`) and for a `_build_shell()` fallback. |
| `StaffHelpView` | [utils/staff_help_view.py:128](../utils/staff_help_view.py) | `(bot, user_id, user_roles)` | 1 | 180 s | No | **Staff rank** | Legacy staff help category select. `user_roles` is a `List[StaffRole]` that must be re-derived per click. Overlaps with `staff/commands/team/help.py::HelpView` — confirm both are still reachable before migrating either. |

### A.5 — Out of scope

| Class | File | Why |
|---|---|---|
| `FallbackErrorView` | [bot.py:950](../bot.py) | Last-resort error card used when the error handler itself is unavailable. URL button only, `timeout=None`. Must not depend on any registry. |


---

## Appendix B — Edge cases

Views that do **not** drop cleanly through the 10-step cookbook, grouped by
failure mode. A view can appear in more than one group.

Unless a row says otherwise, the accepted UX is the one already stated in
"State reconstruction → Working-copy / pending edits": **rebuild from the DB
on the next click and let the user re-apply unsaved edits.** The rows below
only spell out what is actually lost, so the migration agent can write the
right i18n string instead of inventing one.

### B.1 — Non-reconstructible in-memory state

#### B.1.a — `working_config` panels (the standard case)

Eight guild config panels follow the identical `current_config` /
`working_config` / `has_changes` shape:

| View | File | What is lost on restart |
|---|---|---|
| `AdaptiveSlowmodeConfigView` | [modules/configs/adaptive_slowmode_config.py:364](../modules/configs/adaptive_slowmode_config.py) | Added/removed/edited channel entries not yet saved |
| `AutoRestoreRolesConfigView` | [modules/configs/auto_restore_roles_config.py:18](../modules/configs/auto_restore_roles_config.py) | Mode, excluded/included role selections, log channel |
| `AutoRoleConfigView` | [modules/configs/auto_role_config.py:18](../modules/configs/auto_role_config.py) | Member-role and bot-role selections |
| `AutomodAIConfigView` | [modules/configs/automod_ai_config.py:145](../modules/configs/automod_ai_config.py) | Every toggle, severity, max-action, language, indications text, immune roles/channels |
| `InterServerConfigView` | [modules/configs/interserver_config.py:18](../modules/configs/interserver_config.py) | Channel + relay type (already named in the doc body) |
| `StarboardConfigView` | [modules/configs/starboard_config.py:92](../modules/configs/starboard_config.py) | Channel, reaction count, emoji |
| `WelcomeChannelConfigView` | [modules/configs/welcome_channel_config.py:119](../modules/configs/welcome_channel_config.py) | Channel, message text, embed title/description/colour, all toggles |
| `WelcomeDmConfigView` | [modules/configs/welcome_dm_config.py:119](../modules/configs/welcome_dm_config.py) | Message text, embed title/description/colour, all toggles |

**Proposed UX (all eight):** on a click that lands on the shell, re-read the
saved config via `bot.module_manager.get_module_config(guild_id, module_id)`,
rebuild with `has_changes = False`, and surface a one-line notice above the
panel ("your unsaved changes were lost, the panel was reloaded"). One shared
i18n key, not eight.

> The `IndicationsModal` on `AutomodAIConfigView`
> ([modules/configs/automod_ai_config.py:89](../modules/configs/automod_ai_config.py))
> writes into `parent.working_config`, so it is a second, indirect path into
> the same loss. Same treatment.

#### B.1.b — Views holding a parent view reference

These take another **live view object** as a constructor argument. It cannot
be encoded in a custom_id and cannot be rebuilt from `interaction`.

| View | Argument | Notes |
|---|---|---|
| `AdaptiveSlowmodeChannelConfigView` | `parent_view` — **required positional, no default** | Step 2 of the cookbook (make every arg optional) is not enough: the Back/Save callbacks call into `parent_view`. Proposed fix: drop the reference and have Back **construct a fresh `AdaptiveSlowmodeConfigView` from the DB**, which is what the user perceives anyway. |
| `AutomodAIPrecedentsView` | `parent=None` | Already optional. Back should build a fresh `AutomodAIConfigView` from the DB. |
| `BannerTypeSelectView` | `prefill=None` | An unsaved banner draft; the two buttons prefill a modal from it. Lost on restart → user re-enters. |
| `ReminderSelectForEdit` / `ReminderSelectForDelete` / `ReminderAddModal` / `ReminderEditModal` | `parent_view=None` | Used to call `parent_view.refresh()` after a mutation. Replace with a rebuild-from-DB helper keyed on `interaction.user.id`. |
| `AddNoteModal` / `EditNoteModal` / `ViewMessageModal` | `parent_view` | Same pattern in [cogs/saved_messages.py](../cogs/saved_messages.py). `ViewMessageModal` takes `parent_view` as a **required positional**. |

#### B.1.c — State that is genuinely not in the database

This is the group where "rebuild from DB" is **not** an available answer,
because there is no row to read back.

| View | State | Consequence | Proposal |
|---|---|---|---|
| `TranslateView` ([cogs/translate.py:21](../cogs/translate.py)) | `original_text`, `translated_text`, `from_lang` | Translations are not persisted. After a restart the language select has nothing to re-translate. | `NEEDS DECISION` — either (a) leave non-persistent and accept that the select dies on restart, or (b) re-read the source text from `interaction.message` and re-call the gateway (costs a DeepL call per click). Recommend (a): a translation card is short-lived and re-running `/translate` is cheap. |
| `EmojiNavigationView` ([cogs/emoji.py:87](../cogs/emoji.py)) | `emoji_list` — the scanned result set | Pagination has no pages to page through. | Re-scan the guild's emojis on click (cheap, `guild.emojis` is cached), clamp the page from the custom_id. |
| `InviteView` / `ServerInfoView` ([cogs/invite.py](../cogs/invite.py)) | `invite_data` — the fetched invite payload | Nothing to render on the other pane. | Encode the invite code in the custom_id and re-fetch. If that is judged too heavy, exclude both (they are throwaway lookup cards). |
| `CaseCreationView` ([utils/case_management_views.py:63](../utils/case_management_views.py)) | The half-built case before Confirm | The wizard cannot complete. | Accept the loss and restart the wizard — a partially-built moderation case must **not** be silently resurrected. |
| `SqlConfirmView` ([staff/commands/dev/sql.py:48](../staff/commands/dev/sql.py)) | `query` | See B.4 — deliberate exclusion. |
| `ConfirmView` / `_ModalButtonView` (staff framework) | A Python callable | See B.4 — deliberate exclusion. |
| `StaffManagerPanel` ([staff/commands/manage/staff.py:35](../staff/commands/manage/staff.py)) | `target` and `modifier` as `discord.User` objects | The panel does not know whose permissions it is editing. | Encode `target_id` in the custom_id (B.2) and re-fetch; derive `modifier` from `interaction.user`. |
| `ReportView` ([cogs/interserver_commands.py:126](../cogs/interserver_commands.py)) | `claimed_by`, `content` | A claimed report reverts to unclaimed. | `NEEDS DECISION` — whether claim state is worth a DB column is a product call, not a migration call. |

#### B.1.d — Snapshots that are cheap to re-derive (low risk)

`RemindersManageView.reminders`, `SavedMessagesLibraryView.messages`,
`AutomodAIPrecedentsView.rows`, `ServerListView.guilds`,
`PreferencesView.user_data`, `HelpView.data`, `StaffHelpView.user_roles`,
`CasesBrowserView.rows`. Each is a straight re-read (DB, `bot.guilds`, or the
staff registry). No UX decision needed — just re-fetch in the callback.

### B.2 — Requires `DynamicItem`

A `custom_id` must encode a variable id, parsed back out by regex. See
Appendix C for the full worked example.

| View / item | Id to encode | Why a static constant will not do |
|---|---|---|
| `RemindersManageView` ([cogs/reminder.py:478](../cogs/reminder.py)) | `user_id` | Owner-only, and the same button constant would otherwise be shared by every user's card. |
| `SavedMessagesLibraryView` ([cogs/saved_messages.py:222](../cogs/saved_messages.py)) | `user_id` + `page` (+ `detail_id` on the detail screen) | Owner-only **and** paginated; the page index has to survive. |
| `PreferencesView` ([cogs/preferences.py:110](../cogs/preferences.py)) | `user_id` | Owner-only. |
| `EmojiNavigationView` ([cogs/emoji.py:87](../cogs/emoji.py)) | `author_id` + `page` | Owner-only and paginated. |
| `TranslateView` ([cogs/translate.py:21](../cogs/translate.py)) | `author_id` | Owner-only — only if B.1.c is resolved as "keep it". |
| `AddSanctionView` ([utils/case_management_views.py:273](../utils/case_management_views.py)) | `case_id` (UUID) | The select acts on one specific case row. |
| `RevokeSanctionView` ([utils/case_management_views.py:379](../utils/case_management_views.py)) | case `reference` | Same. |
| `StaffManagerPanel` ([staff/commands/manage/staff.py:35](../staff/commands/manage/staff.py)) | `target_id` | The panel must know whose staff record it edits. |
| `ServerListView` ([staff/commands/dev/serverlist.py:13](../staff/commands/dev/serverlist.py)) | `author_id` + `page` | Owner-only and paginated. |
| `ReportView` ([cogs/interserver_commands.py:126](../cogs/interserver_commands.py)) | `moddy_id` | Identifies the relayed message being reported. |
| `AutomodAIPrecedentsView` ([modules/configs/automod_ai_precedents_view.py:31](../modules/configs/automod_ai_precedents_view.py)) | `guild_id` + `page` | Guild-scoped and paginated. |

Guild config panels **do not** need `DynamicItem`: `interaction.guild_id` is
already on the interaction, so a static constant plus a re-checked
`manage_guild` is sufficient. That is exactly what
`SocialNotificationsConfigView` does today — copy it, don't over-engineer.

Existing in-repo `DynamicItem` implementations to copy from:
[`ShadowAnnotateButton`](../utils/automod_shadow_views.py) (simplest) and the
five buttons in [`utils/appeal_views.py`](../utils/appeal_views.py). Note the
`_guarded` decorator in both files — dynamic items dispatched via
`bot.add_dynamic_items` have **no live `BaseView`**, so `BaseView.on_error`
never fires and errors would otherwise vanish. Every new `DynamicItem`
callback must be wrapped the same way.

### B.3 — Unguarded `self.bot` access in the build method

These will raise `AttributeError` on `None` the moment
`register_all_persistent_views` constructs the shell with `bot=None`. Verified
by walking each class's `_build_view` / `build_view` AST.

| View | Build-method access |
|---|---|
| `ConfigMainView` ([cogs/config.py:62](../cogs/config.py)) | `self.bot.module_manager.get_available_modules()` |
| `AdaptiveSlowmodeChannelConfigView` | `self.bot.get_channel(...)`, `self.bot.get_guild(...)` |
| `AdaptiveSlowmodeConfigView` | `self.bot.get_guild(self.guild_id)` |
| `AutoRestoreRolesConfigView` | `self.bot.get_guild(...)` ×4 |
| `AutoRoleConfigView` | `self.bot.get_guild(...)` ×2 |
| `InterServerConfigView` | `self.bot.get_channel(self.working_config['channel_id'])` |
| `StarboardConfigView` | `self.bot.get_channel(self.working_config['channel_id'])` |
| `WelcomeChannelConfigView` | `self.bot.get_channel(self.working_config['channel_id'])` |
| `SubscriptionView` | `self.bot.get_guild(int(sid))` — moot once it is left unregistered (A.1) |
| `TranslateView` | `self.bot.get_cog('Translate')` |
| `AddSubscriptionView` | `self.bot.get_channel(...)`, `self.bot.get_guild(...)` — **already migrated in shape but these two lines are unguarded**, so registering it today (B.5) would fail at startup. Fix before adding it to the registry. |

Already correct — use as the pattern:
`ModdyMainView` (`if self.bot is not None:`),
`ManageSubscriptionView` (`... if self.bot else None`),
`CasesBrowserView._build_list` (`... if self.bot else None`).

### B.4 — Should be excluded entirely

Beyond the three exclusions already in the doc body (`BaseModal`, `ErrorView`,
`WebhookView`):

| View | Rationale |
|---|---|
| `SqlConfirmView` ([staff/commands/dev/sql.py:48](../staff/commands/dev/sql.py)) | Confirming an arbitrary SQL statement after a restart would execute a query the operator typed in a different process lifetime, with no visible context. Its short `timeout=60` is a **safety feature**, not an oversight — keep it. |
| `ConfirmView` ([staff/framework/views.py:16](../staff/framework/views.py)) | Generic confirm/cancel; the "yes" branch is an in-memory Python callback with no stable identity. Persisting it would dispatch a click to a shell that does not know what it is confirming. Its `timeout=60` is likewise deliberate. |
| `_ModalButtonView` ([staff/framework/context.py:168](../staff/framework/context.py)) | Same: behaviour is a `modal_factory` callable. Not serialisable. |
| `FallbackErrorView` ([bot.py:950](../bot.py)) | Runs when the error handler itself is unavailable. It must not depend on the registry it may be reporting a failure in. URL button only. |
| `PermissionErrorView`, `CooldownErrorView`, `NotFoundView` ([cogs/error_handler.py](../cogs/error_handler.py)) | URL-only or zero interactive children, and function-local. Nothing to dispatch. |
| Every view with **0 interactive children** — `AvatarView`, `BannerView`, `EmojiView`, `RollView`, `TextResultView`, `InfoView`, `ProcessedView`, `WelcomeView`, `StaffLogView` | `bot.add_view` on a childless view is a no-op. **Do not add `__persistent__` to these.** The only change they may need is dropping a stale `timeout=` (`EmojiView` has `timeout=180`), which is cosmetic since there is nothing to expire. |

### B.5 — Other things worth knowing before starting

1. **Un-namespaced `custom_id` collisions — the biggest correctness hazard.**
   Discord dispatches on `(component_type, custom_id)` globally. Several
   already-set custom_ids are bare strings that collide across unrelated
   views:
   - `"back_btn"` — `cogs/reminder.py`, `cogs/saved_messages.py`,
     `cogs/preferences.py`, `modules/configs/interserver_config.py`,
     `modules/configs/starboard_config.py`,
     `modules/configs/welcome_channel_config.py`,
     `modules/configs/welcome_dm_config.py`
   - `"save_btn"` / `"cancel_btn"` / `"delete_btn"` — the same five config panels
   - `"edit_message"`, `"toggle_embed"`, `"toggle_thumbnail"`,
     `"toggle_author"`, `"edit_embed_title"`, `"edit_embed_color"`,
     `"edit_embed_description"` — **identical in
     `welcome_channel_config.py` and `welcome_dm_config.py`**
   - `"prev_btn"` / `"next_btn"` / `"page_info"` — `cogs/saved_messages.py`
     vs `"prev_emoji_btn"`/`"next_emoji_btn"`/`"page_info"` in `cogs/emoji.py`
     (`"page_info"` collides exactly)

   Today this is harmless because none of these views are registered — the
   live in-memory view always wins. **The moment two colliding views are both
   registered, one silently shadows the other.** Rule for the migration:
   never register a view until every one of its custom_ids has been renamed
   to `moddy:<cog>:<view>:<action>`.

2. **Three views bypass `BaseView` entirely** and subclass `LayoutView`
   directly: `PreferencesView` ([cogs/preferences.py:110](../cogs/preferences.py)),
   `RemindersManageView` ([cogs/reminder.py:478](../cogs/reminder.py)),
   `SavedMessagesLibraryView` ([cogs/saved_messages.py:222](../cogs/saved_messages.py)).
   They get no centralized error handling and no `__persistent__` hook. They
   must be re-parented to `BaseView` **before** migration — that is a
   behaviour change (errors start routing to the handler) and deserves its own
   commit.

3. **Three views are already migrated in shape but never registered.**
   `AddSubscriptionView` and `ManageSubscriptionView`
   ([modules/configs/social_notifications_config.py](../modules/configs/social_notifications_config.py))
   have optional-arg constructors and full namespaced custom_ids, but are
   absent from `_collect_persistent_view_classes()`. Their buttons therefore
   still die on restart. Either register them (after fixing the unguarded
   `self.bot` in `AddSubscriptionView._build_view`, B.3) or add a comment
   explaining why not. This is the cheapest real win in the whole migration.

4. **Function-local view classes cannot be registered at all.** `ReportView`,
   `InfoView`, `ProcessedView` ([cogs/interserver_commands.py](../cogs/interserver_commands.py)),
   `WelcomeView`, `StaffLogView` ([modules/interserver.py](../modules/interserver.py)),
   `PermissionErrorView`, `CooldownErrorView`, `NotFoundView`
   ([cogs/error_handler.py](../cogs/error_handler.py)) are defined **inside
   functions**. `_collect_persistent_view_classes()` cannot import them.
   `ReportView` is the only one of these with dispatchable buttons, so it is
   the only one that needs hoisting to module level — a refactor, not a
   mechanical edit. Flag it, don't attempt it in the same commit.

5. **`StaffHelpView` and `HelpView` overlap.**
   [utils/staff_help_view.py:128](../utils/staff_help_view.py) and
   [staff/commands/team/help.py:34](../staff/commands/team/help.py) both render
   a staff command catalogue with a category select.
   `UNKNOWN — needs human input`: whether the `utils/` one is still reachable.
   Do not migrate both; confirm first.

6. **`InviteView` ships a button labelled `Raw Data` marked `# TEMP: … for
   debugging`** ([cogs/invite.py:73](../cogs/invite.py)), duplicated in the
   non-guild branch. `UNKNOWN — needs human input`: whether it should be
   removed rather than given a permanent custom_id.

7. **`discord.py` is not installed in the current dev/test environment.**
   `requirements-dev.txt` pins only `pytest` and `pytest-asyncio`; the existing
   `tests/automod/` suite is deliberately Discord-free. Any persistence smoke
   test needs `pip install -r requirements.txt` first — see Appendix D.

---

## Appendix C — `DynamicItem` reference implementation

Step 6 of the cookbook says "use a `DynamicItem` subclass when the id is
encoded with a regex". This appendix is the worked example.

**When you need this**: only when the callback needs an id that is **not
already on the interaction**. `interaction.user.id`, `interaction.guild_id`
and `interaction.channel_id` are always available, so a guild config panel
never needs a `DynamicItem` — a static `custom_id` constant plus a re-checked
permission is enough (that is what `SocialNotificationsConfigView` does).

You need a `DynamicItem` when the id is **the owner of the message rather
than the clicker** (owner-only views: the point is to reject a *different*
user), or when it is a page index, a case UUID, or any other per-message
value.

**Subject chosen**: `RemindersManageView`
([cogs/reminder.py:478](../cogs/reminder.py)) — owner-only, five buttons, a
`show_history` sub-screen, and custom_ids that currently collide with three
other views. It is the most representative user-scoped view in the codebase.

### C.1 — Before

```python
# cogs/reminder.py (abridged, as it exists today)

class RemindersManageView(LayoutView):          # (1) not a BaseView
    """Main view for managing reminders"""

    def __init__(self, bot, user_id: int, reminders: List[Dict], locale: str,
                 user_tz: ZoneInfo, show_history: bool = False,
                 past_reminders: List[Dict] = None,
                 original_interaction: discord.Interaction = None):
        super().__init__(timeout=300)           # (2) dies after 5 minutes
        self.bot = bot
        self.user_id = user_id                  # (3) owner kept in memory only
        self.reminders = reminders              # (4) unsaved snapshot
        self.show_history = show_history
        self._build_view()

    def _build_view(self):
        self.clear_items()
        container = Container()
        ...
        add_btn = discord.ui.Button(
            label=t("commands.reminder.buttons.add", locale=self.locale),
            style=discord.ButtonStyle.success,
            custom_id="add_btn",                # (5) collides globally
        )
        add_btn.callback = self.add_callback    # (6) bound to THIS instance
        btn_row1.add_item(add_btn)
        ...

    async def interaction_check(self, interaction) -> bool:
        # (7) compares against self.user_id — empty on a restarted shell
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t("commands.reminder.errors.author_only", interaction),
                ephemeral=True,
            )
            return False
        return True

    async def add_callback(self, interaction: discord.Interaction):
        modal = ReminderAddModal(self.locale, self.bot, parent_view=self)
        await interaction.response.send_modal(modal)
```

Seven problems, numbered above. After a restart `self.user_id` is `None`, so
`interaction_check` rejects **everyone** — and before that, `timeout=300`
means the buttons are already dead.

### C.2 — After

```python
# cogs/reminder.py
from __future__ import annotations

import re
import discord
from discord import ui

from cogs.error_handler import BaseView
from utils.components_v2 import create_error_message
from utils.i18n import i18n, t

# --------------------------------------------------------------------------- #
# custom_id template
#
#   moddy:rem:manage:<action>:<owner_id>[:<page>]
#
# `owner_id` is the user the card was rendered FOR. It is compared against
# interaction.user.id on every click. A Discord snowflake is public
# information already visible in the message, so encoding it leaks nothing.
# --------------------------------------------------------------------------- #

_ACTIONS = "add|edit|delete|history|back"
_CID_TEMPLATE = rf"moddy:rem:manage:(?P<action>{_ACTIONS}):(?P<owner>\d{{17,20}})"


def _guarded(callback):
    """Route DynamicItem callback errors to the central error handler.

    A DynamicItem dispatched via ``bot.add_dynamic_items`` has no live
    ``BaseView``, so ``BaseView.on_error`` never fires. Without this wrapper
    an exception in the callback is swallowed and the user just sees
    "This interaction failed". Copied from ``utils/appeal_views.py``.
    """
    async def wrapper(self, interaction: discord.Interaction):
        try:
            await callback(self, interaction)
        except Exception as e:  # noqa: BLE001 — funnel everything to the handler
            from cogs.error_handler import report_component_error
            await report_component_error(interaction, e, self.__class__.__name__)
    return wrapper


class ReminderManageButton(ui.DynamicItem[ui.Button], template=_CID_TEMPLATE):
    """One button on the /reminder manage card. Auth: owner only.

    The owner id is encoded in the custom_id so a restarted bot can still
    tell the card's owner from a passer-by clicking someone else's buttons.
    """

    _STYLE = {
        "add":     discord.ButtonStyle.success,
        "edit":    discord.ButtonStyle.primary,
        "delete":  discord.ButtonStyle.danger,
        "history": discord.ButtonStyle.secondary,
        "back":    discord.ButtonStyle.secondary,
    }
    _EMOJI = {
        "add":     "<:add:1520000000000000001>",
        "edit":    "<:edit:1520000000000000002>",
        "delete":  "<:delete:1520000000000000003>",
        "history": "<:history:1520000000000000004>",
        "back":    "<:back:1519795556665397431>",
    }

    def __init__(self, action: str, owner_id: int, *,
                 locale: str = "en-US", disabled: bool = False):
        super().__init__(
            ui.Button(
                label=t(f"commands.reminder.buttons.{action}", locale=locale)[:80],
                style=self._STYLE[action],
                emoji=discord.PartialEmoji.from_str(self._EMOJI[action]),
                custom_id=f"moddy:rem:manage:{action}:{owner_id}",
                disabled=disabled,
            )
        )
        self.action = action
        self.owner_id = owner_id

    # -- reconstruction after a restart ---------------------------------- #
    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction,
                             item: ui.Button, match: re.Match):
        """Rebuild the item from the clicked custom_id.

        Runs on EVERY click once the item is registered — the live instance is
        not reused. Keep it cheap and side-effect free: no DB, no API calls.
        Locale is deliberately re-derived here, never encoded in the id.
        """
        return cls(
            match["action"],
            int(match["owner"]),
            locale=i18n.get_user_locale(interaction),
        )

    # -- ownership check -------------------------------------------------- #
    async def _reject_if_not_owner(self, interaction: discord.Interaction) -> bool:
        """Return True (and answer ephemerally) if the clicker is not the owner."""
        if interaction.user.id == self.owner_id:
            return False
        locale = i18n.get_user_locale(interaction)
        await interaction.response.send_message(
            view=create_error_message(
                t("commands.reminder.errors.author_only_title", locale=locale),
                t("commands.reminder.errors.author_only", locale=locale),
            ),
            ephemeral=True,
        )
        return True

    @_guarded
    async def callback(self, interaction: discord.Interaction):
        if await self._reject_if_not_owner(interaction):
            return

        # Re-derive EVERYTHING from the interaction — there is no live view.
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        user_id = interaction.user.id

        if self.action == "add":
            await interaction.response.send_modal(
                ReminderAddModal(locale, bot, owner_id=user_id)
            )
            return

        # Every other action re-reads from the DB and re-renders in place.
        view = await RemindersManageView.build_for(
            bot, user_id, locale, show_history=(self.action == "history")
        )
        await interaction.response.edit_message(view=view)
```

The view itself becomes a thin renderer — it holds no authorization state at
all, because the buttons carry it:

```python
class RemindersManageView(BaseView):
    """Reminder management card. Persistent: yes (via ReminderManageButton).

    Auth: owner only — enforced per-button by the encoded owner_id, NOT by
    interaction_check (which cannot work on a restarted shell).
    """

    __persistent__ = True

    def __init__(self, bot=None, owner_id: int | None = None,
                 locale: str = "en-US", reminders: list | None = None,
                 past_reminders: list | None = None,
                 user_tz=None, show_history: bool = False):
        super().__init__()                      # timeout=None
        self.bot = bot
        self.owner_id = owner_id
        self.locale = locale
        self.reminders = reminders or []
        self.past_reminders = past_reminders or []
        self.user_tz = user_tz
        self.show_history = show_history
        self._build_view()

    @classmethod
    async def build_for(cls, bot, owner_id: int, locale: str, *,
                        show_history: bool = False) -> "RemindersManageView":
        """Fetch fresh state and return a rendered view. The ONLY constructor
        callers should use — it guarantees the data is not a stale snapshot."""
        reminders = await bot.db.get_user_reminders(owner_id)
        past = await bot.db.get_user_past_reminders(owner_id) if show_history else []
        tz = await get_user_timezone(bot, owner_id, locale)
        return cls(bot, owner_id, locale, reminders, past, tz, show_history)

    def _build_view(self):
        self.clear_items()
        container = ui.Container()
        # ... TextDisplays, guarded with `if self.bot is not None:` where they
        #     touch live bot state ...

        row = ui.ActionRow()
        if self.show_history:
            row.add_item(ReminderManageButton(
                "back", self.owner_id or 0, locale=self.locale))
        else:
            row.add_item(ReminderManageButton(
                "add", self.owner_id or 0, locale=self.locale))
            row.add_item(ReminderManageButton(
                "edit", self.owner_id or 0, locale=self.locale,
                disabled=not self.reminders))
            row.add_item(ReminderManageButton(
                "delete", self.owner_id or 0, locale=self.locale,
                disabled=not self.reminders))
            row.add_item(ReminderManageButton(
                "history", self.owner_id or 0, locale=self.locale))
        container.add_item(row)
        self.add_item(container)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: owner only — owner_id is encoded in each button's
        custom_id and compared to interaction.user.id on click."""
        bot.add_dynamic_items(ReminderManageButton)
```

Then in [`utils/persistent_views.py`](../utils/persistent_views.py):

```python
from cogs.reminder import RemindersManageView
...
    # Group 6 — /reminder manage (owner-only dynamic items)
    RemindersManageView,
```

### C.3 — Things that bite

- **`bot.add_dynamic_items(Cls)`, not `bot.add_view(cls())`.** A view whose
  children are `DynamicItem`s does not need `add_view` at all; registering the
  item class is what makes dispatch work. `AppealPersistence` and
  `ShadowAnnotationPersistence` are marker views that do exactly this and add
  no children of their own — that pattern is fine when the buttons live on
  several different cards.
- **Wrap the callback in `_guarded`.** Non-negotiable: without a live
  `BaseView`, `BaseView.on_error` is never invoked and exceptions disappear.
- **`from_custom_id` runs on every click**, not only after a restart. It must
  be cheap and free of side effects. Put DB reads in `callback`.
- **Regex escaping in f-strings.** `\d{17,20}` inside an f-string needs
  doubled braces: `rf"...\d{{17,20}}"`. Both existing implementations sidestep
  this by defining `_UUID` as a plain module constant and interpolating it —
  copy that if the template gets long.
- **The template must match the emitted `custom_id` exactly.** A mismatch
  fails silently: the click is simply never dispatched. Assert it in the smoke
  test (Appendix D).
- **100-character limit** on `custom_id`. `moddy:rem:manage:history:` +
  a 20-digit snowflake is 45 — fine. Watch it when encoding two UUIDs.
- **Do not encode `locale`.** Re-derive with `i18n.get_user_locale(interaction)`
  so a card rendered in French answers an English clicker in English.
- **`create_error_message(title, description)` needs two strings.** Today's
  owner checks pass a single bare string to `send_message`. `author_only`
  exists in the locale files but there is no matching **title** key — the
  migration must add one (e.g. `commands.reminder.errors.author_only_title`)
  to both `locales/fr.json` and `locales/en-US.json`, or reuse a shared
  `errors.not_your_message.title`. Decide once and apply it to every
  owner-only view rather than inventing a key per cog.

---

## Appendix D — Verification command

### D.1 — Current state of the test setup

| Fact | Value |
|---|---|
| Runner | `pytest` (config in [`pytest.ini`](../pytest.ini)) |
| `testpaths` | `tests` |
| Collected files | `test_*.py` only — the other files in `tests/` (`Test V2.py`, `404 disboard test.py`, …) are manual scratch scripts and are **not** collected |
| Event loop | `pytest-asyncio` with `asyncio_mode = auto` — an `async def test_…` gets a loop with no decorator and no fixture |
| `sys.path` | [`conftest.py`](../conftest.py) at the repo root inserts the root, so `import utils` / `import cogs` work from anywhere |
| Existing suites | [`tests/automod/`](../tests/automod) — deliberately Discord-free pure-Python |
| Dev deps | [`requirements-dev.txt`](../requirements-dev.txt) — `pytest`, `pytest-asyncio` only. **`discord.py` is NOT installed by it.** |
| CI | No `.github/workflows` in the repo; `make test` runs `pytest -q` |

**There is no persistent-views test file today.** The spec for one is in D.3.

### D.2 — The command to run after each module

```bash
# once per environment — the suite imports discord.py, which
# requirements-dev.txt does not pull in
pip install -r requirements.txt -r requirements-dev.txt

# after migrating a module, run only its slice:
pytest tests/test_persistent_views.py -k reminder -q

# before committing, run the whole persistence suite:
pytest tests/test_persistent_views.py -q

# before opening the PR, the full suite (must stay green):
make test
```

The `-k <module>` filter works because the parametrised test ids are the
class names — `-k reminder` matches `RemindersManageView`, `-k welcome`
matches both welcome panels, `-k config` matches every `*ConfigView`.

> **`asyncio_mode = auto` is what makes this work.** `discord.ui.View.__init__`
> wants a running event loop; an `async def` test body provides one. A plain
> `def` test would raise `RuntimeError: no running event loop` on the first
> instantiation. Every test below is therefore `async def`.

### D.3 — Spec for `tests/test_persistent_views.py`

Create this file (path exactly as below — `test_*` so `pytest.ini` collects
it). **Do not create it during the recon pass**; the migration agent creates
it in its first commit, before touching any view.

```python
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
    _U = "0f7d9c62-3b4e-4a1f-9c2d-5e6f70819a2b"
    return [
        (ShadowAnnotateButton, ("ok", _U)),
        (AppealNewButton, ("s", _U, _U)),
        (AppealClaimButton, (_U,)),
        (AppealInviteButton, (_U,)),
        (AppealDecisionButton, ("accept", _U)),
        (AppealAcceptChoiceButton, ("full", _U)),
        # (ReminderManageButton, ("add", 123456789012345678)),  # Appendix C
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
```

**Notes for whoever writes it**

- `__discord_ui_compiled_template__` is the attribute discord.py 2.7 stores
  the compiled `template=` regex on. Confirm the name against the installed
  version before relying on it; if it moved, re-compile the template string
  in the test instead.
- The `shell` fixture above is only needed if the migration adds tests that
  want a constructed instance by name; the parametrised tests construct
  inline. Drop it if unused.
- These tests intentionally do **not** touch the DB, the gateway, or a live
  bot. If a view cannot be constructed without one of those, that is the bug
  the test is reporting — fix the view, do not mock the bot.

---

## Appendix E — Suggested migration order

Ordered easiest/lowest-risk first. Each numbered step is one commit, so a
regression is bisectable and revertable without unpicking anything else.

The ordering follows three rules:
1. **Infrastructure before views** — the test harness and the naming scheme
   must exist before anything is registered, or early commits cannot be
   verified.
2. **Blast radius ascending** — cosmetic → public read-only → personal data →
   guild config → moderation → staff privilege.
3. **Never register a colliding pair separately.** `welcome_channel_config`
   and `welcome_dm_config` share every custom_id and must move in one commit.

| # | Step | Views | Risk | Rationale |
|---|---|---|---|---|
| 0 | Test harness | — | None | Create `tests/test_persistent_views.py` per Appendix D against the 7 already-registered classes. If it does not pass on today's code, the spec is wrong before a single view moves. |
| 1 | Register the three ready-made views | `AddSubscriptionView`, `ManageSubscriptionView` (+ fix the unguarded `self.bot` in `AddSubscriptionView._build_view`) | Very low | Already migrated in shape (B.5.3). A one-line registry change plus a two-line guard. Proves the whole pipeline end to end with almost no new code. |
| 2 | Timeout-only cleanups | `AvatarView`, `BannerView`, `EmojiView`, `RollView`, `TextResultView`, `SubscriptionView`, `InfoView`, `ProcessedView`, `WelcomeView`, `StaffLogView` | Very low | Zero interactive children (or URL-only). Drop the stale `timeout=180/300`, add **no** `__persistent__`, register nothing. Purely subtractive. |
| 3 | Shared i18n keys | — | Very low | Add the owner-rejection title/description keys to `fr.json` + `en-US.json` once (see C.3), so steps 5-8 do not each invent their own. |
| 4 | Re-parent the three `LayoutView` orphans | `PreferencesView`, `RemindersManageView`, `SavedMessagesLibraryView` | Low | `LayoutView` → `BaseView` only (B.5.2). Behaviour change: errors start reaching the central handler. Its own commit so that change is not tangled with persistence. |
| 5 | `/moddy`-adjacent public views | `InviteView`, `ServerInfoView`, `UserInfoView` | Low | Public auth, no DB writes, and `ModdyMainView` is already the worked example for exactly this shape. Resolve the `# TEMP` Raw Data button (B.5.6) here. |
| 6 | Owner-only, no `DynamicItem` needed | `PreferencesView` | Low | The single simplest owner-only view — 2 children, state is one DB row. Use it to validate the owner-rejection path before the harder ones. |
| 7 | Owner-only with `DynamicItem` | `RemindersManageView`, then `SavedMessagesLibraryView`, then `EmojiNavigationView` | Medium | Appendix C is written against `RemindersManageView` — do it first and literally. `SavedMessagesLibraryView` adds pagination + a detail screen; `EmojiNavigationView` adds re-scanning (B.1.c). |
| 8 | Small guild config panels | `AutoRoleConfigView`, `AutoRestoreRolesConfigView`, `StarboardConfigView`, `InterServerConfigView` | Medium | The standard `working_config` shape (B.1.a) at its smallest — 6-8 children each. Establishes the shared "unsaved changes were lost" notice that steps 9-10 reuse. |
| 9 | The colliding welcome pair | `WelcomeChannelConfigView` **and** `WelcomeDmConfigView` — one commit | Medium-high | 24 children between them and a 1:1 custom_id collision (B.5.1). Splitting this commit would register two views that shadow each other. |
| 10 | `ConfigMainView` | `ConfigMainView` | Medium-high | The router every panel returns to, and its `_build_view` calls `self.bot.module_manager` unguarded (B.3). Deliberately after the panels it routes to, so a break here is obviously this commit. |
| 11 | Adaptive slowmode | `AdaptiveSlowmodeConfigView`, `AdaptiveSlowmodeChannelConfigView` | High | The `parent_view` required-positional problem (B.1.b) forces a real refactor of the Back/Save flow, not a mechanical edit. |
| 12 | Automod config | `AutomodAIConfigView`, `AutomodAIPrecedentsView` | High | 14 children, the largest `working_config`, a lazily-loaded precedent count, a modal that writes into the parent, and a `parent` link. Everything hard in one panel. |
| 13 | Case management | `AddSanctionView`, `RevokeSanctionView`, `CaseCreationView` | High | Mutates moderation records. `AddSanctionView`/`RevokeSanctionView` are clean `DynamicItem` candidates keyed on `case_id`; `CaseCreationView` may be better left non-persistent (B.1.c) — decide before starting. |
| 14 | Staff, read-only first | `HelpView`, then `ServerListView`, then `EmojiPreviewView` | Medium | Staff rank auth, but nothing they do is destructive. `HelpView` has one select and a rebuildable registry snapshot — the gentlest introduction to re-running `staff_permissions` on click. |
| 15 | `StaffManagerPanel` | `StaffManagerPanel` | Highest | Grants and revokes staff permissions. A dispatch bug here is a privilege-escalation bug. Needs `target_id` encoded (B.2) and the staff check re-run on every click. **Last, and reviewed by a human.** |
| — | Deferred / not in this migration | `ReportView` (hoist out of a function first, B.5.4), `TranslateView` (B.1.c decision), `StaffHelpView` (dedupe against `HelpView` first, B.5.5) | — | Each needs a product or refactor decision that is not the migration agent's to make. |
| — | Never | `SqlConfirmView`, `ConfirmView`, `_ModalButtonView`, `WebhookView`, `ErrorView`, `FallbackErrorView`, `PermissionErrorView`, `CooldownErrorView`, `NotFoundView`, all `BaseModal` subclasses | — | See B.4 and "Deliberate exclusions". |

After every numbered step: run
`pytest tests/test_persistent_views.py -q` and commit. Do not batch two steps
into one commit, even when both are one-liners — the duplicate-custom_id test
is the one most likely to fail, and a single-step commit says immediately
which view introduced the clash.

---

## Migration log

Running log of the overnight migration pass (2026-08-06,
branch `claude/persistent-views-migration-nph7ok`). Each entry is a decision
that either wasn't fully specified by this doc or was explicitly marked
NEEDS DECISION / UNKNOWN and therefore intentionally not resolved here.

### Step 0 — Test harness
Created `tests/test_persistent_views.py` exactly per Appendix D.3. It failed
to even collect on unmodified `main`: `config.py` calls `sys.exit(1)` at
import time when `DISCORD_TOKEN` is unset, which aborts pytest collection
before a single test runs. Fixed by setting a placeholder
`DISCORD_TOKEN` in `conftest.py` (`os.environ.setdefault`, never overrides a
real value). This was not covered by Appendix D.1's "facts" table — the
table says discord.py isn't installed by `requirements-dev.txt`, but doesn't
mention the config import crash. Judged safe/necessary since it blocks
every subsequent step, not a scope change to the views themselves.
All 42 tests passed against the 7 already-registered classes before any
view was touched, confirming the spec matches today's code.

### Step 1 — `AddSubscriptionView` / `ManageSubscriptionView`
Appendix B.3 says both have unguarded `self.bot.get_channel(...)` /
`self.bot.get_guild(...)` calls in `AddSubscriptionView._build_view`. On
inspection ([modules/configs/social_notifications_config.py:565](../modules/configs/social_notifications_config.py),
line 583) both call sites are already wrapped in
`if self.channel_id and self.bot:` / `if self.role_ids and self.bot:` — a
bare shell (`channel_id=None`, `role_ids=[]`) never reaches them. Appendix
B.3 appears stale (fixed since the recon pass); no code change was needed
there, only adding `__persistent__` + `register_persistent` to both classes
and the registry entry.

### Step 4 — Re-parenting the three `LayoutView` orphans
Mechanical: `LayoutView` → `BaseView`, dropped the now-unused `LayoutView`
import where nothing else in the file used it (`preferences.py`,
`saved_messages.py`; `reminder.py` still constructs bare `LayoutView()`
instances elsewhere for DM messages, so that import stayed). No behaviour
decision beyond what the doc already specifies.

### Step 5 — `/moddy`-adjacent public views: NOT migrated, deferred
Appendix E lists this step as "Low" risk with `ModdyMainView` as the worked
example. On inspection none of the three views actually fit that shape:

- **`InviteView` / `ServerInfoView`** ([cogs/invite.py](../cogs/invite.py)) —
  `invite_data` is fetched once from Discord's public invite API at command
  time and held only in memory (Appendix B.1.c). Making the view persistent
  means either (a) encoding the invite code in every button's custom_id and
  re-calling the Discord API on every click after a restart, which is a real
  behavioural/cost change the doc explicitly declines to pre-decide
  ("if judged too heavy, exclude both"), or (b) the `# TEMP` Raw Data button
  ([cogs/invite.py:73](../cogs/invite.py), duplicated at
  [cogs/invite.py:84-94](../cogs/invite.py)) — explicitly listed as
  `UNKNOWN — needs human input` in Appendix B.5.6, which this migration was
  told not to resolve. Since the Raw Data button has no `custom_id` today,
  giving every *other* button on the same view a `custom_id` while leaving
  it alone would still leave the view non-persistent
  (`discord.ui.View.is_persistent()` requires every dispatchable child to
  have one) — so there is no partial migration available here that doesn't
  first resolve the flagged decision. **Left as-is** (still `timeout=180`,
  not registered). This is the "preserve current behaviour" default per the
  operating instructions for this pass, not a judgment that the doc's "Low"
  risk rating was wrong for the general case.
- **`UserInfoView`** ([cogs/user.py:42](../cogs/user.py)) — not flagged
  anywhere in Appendix B, but on inspection its four buttons
  (`bot_info`/`avatar`/`banner`/`description`, un-namespaced custom_ids
  already, [cogs/user.py:381-425](../cogs/user.py)) all render from
  `self.user_data` / `self.bot_data` / `self.moddy_attributes` /
  `self.user_verification_data` — a snapshot fetched once via Discord's
  public user API plus `bot.db.get_user()` at command invocation, not
  reconstructible from `interaction` alone. Persisting it correctly would
  require encoding the target `user_id` in a `DynamicItem` and
  re-running the full data-gathering pipeline that today lives in the
  `/user` command handler, inside the callback — a genuine refactor, not a
  mechanical one, and out of scope for a "Low risk" step. **NEEDS DECISION**
  (new, not previously flagged): whether that re-fetch cost is acceptable
  per click. Left as-is (`timeout=180`, not registered) pending that call.

No files changed in this step beyond this log entry — there was no safe
subset of the three views to migrate without deciding one of the open
questions above. Continuing to Step 6 per the "commit what works, log the
rest, move on" instruction.

### Step 6 — `PreferencesView`
Appendix E's own step title says "Owner-only, no `DynamicItem` needed", but
Appendix B.2 explicitly lists `PreferencesView` as requiring one ("the same
button constant would otherwise be shared by every user's card"). Went with
B.2 plus cookbook step 6, which is more specific — a static custom_id would
make every user's Manage Timezone button collide on the same
`(component_type, custom_id)` key, and `interaction_check` comparing against
`self.user_id` breaks entirely on a restarted shell (`self.user_id is None`).
Implemented `PreferencesManageButton` and `TimezoneSelect` as
`DynamicItem`s encoding `owner_id`, following Appendix C's shape (including
the `_guarded` wrapper and `_reject_if_not_owner` ephemeral rejection using
the new `errors.not_your_message` key from Step 3).

**Deviation from Appendix C's literal template**: C's worked example uses
`\d{17,20}` (a real Discord snowflake) in the regex, and the reference
`RemindersManageView` code builds its shell buttons with
`self.owner_id or 0`. Those two don't compose: `"0"` is a 1-digit string and
does not match `\d{17,20}}`, so instantiating a bare shell with the literal
template raises `ValueError` inside `discord.ui.DynamicItem.__init__`
(`item custom_id 'moddy:pref:manage:timezone:0' must match the template`) —
`test_shell_constructs` fails immediately. Relaxed both templates in
`cogs/preferences.py` to `\d{1,20}` so the `0` placeholder used for a
bare/default-constructed shell still matches, while every real click still
carries a real snowflake (1-20 digits already covers the full snowflake
range plus the placeholder). This is a correction, not a reinterpretation —
worth flagging in case Appendix C's template is copied literally into a
later step (7, 13) and hits the same failure.

### Step 7 — `RemindersManageView` (Appendix C, literal)
Implemented `ReminderManageButton` following Appendix C's worked example
almost verbatim (`_guarded`, `from_custom_id`, per-button owner check), with
the same `\d{1,20}` correction from Step 6 applied to `_CID_REM_TEMPLATE`.

One structural difference from the C.2 sample code, required by Appendix
B.1.b for this exact view: `ReminderAddModal`, `ReminderEditModal`,
`ReminderSelectForEdit` and `ReminderSelectForDelete` previously took a
`parent_view` — a live `RemindersManageView` Python object — and called
`parent_view.refresh(interaction)` on completion, which in turn used a
stashed `self.original_interaction` to `edit_original_response`. That
`original_interaction` is exactly the kind of state Appendix B.1.b says
cannot survive a restart, and the whole `parent_view` chain only exists to
carry it. Replaced all four with a `_refresh_manage_card(bot, owner_id,
locale, channel_id, message_id)` helper keyed on the plain `channel_id` /
`message_id` of the card message (captured off the button-click
`interaction` at modal/select creation time, not off a view instance), which
re-fetches reminders from the DB and edits the message via
`channel.get_partial_message(message_id).edit(...)`. This works identically
whether the manage card's original view object is still alive or the
process restarted in between, which the old `parent_view` chain did not.

`RemindersManageView.build_for(bot, user_id, locale, show_history=...)` is
the fetch-fresh-then-render entry point the History/Back actions and the
`/reminders` command now share, mirroring `build_for` in Appendix C.2. The
`/reminders` command call site was also fixed to pass constructor args as
keywords — the old positional order was `(bot, user_id, reminders, locale,
user_tz, …)`, which does not match the new `(bot, user_id, locale,
reminders, user_tz, …)` shell-first signature (`user_id` before `locale`
before `reminders`, so the persistent-view contract's
`(bot=None, user_id=None, locale="en-US", …)` shape holds); a positional
call would have silently swapped `reminders` and `locale`.

### Step 7 (continued) — `SavedMessagesLibraryView` done, `EmojiNavigationView` deferred

**`SavedMessagesLibraryView`**: fully DB-backed (list + detail screens), so
migrated per Appendix C/B.2 like `RemindersManageView`. Two `DynamicItem`
classes: `SavedMessagesListButton` (`view`/`prev`/`next`/`pageinfo`, template
`moddy:svm:manage:<action>:<owner>:<page>`) and `SavedMessagesDetailButton`
(`back`/`edit_note`/`export`/`delete`, template
`moddy:svm:manage:<action>:<owner>:<saved_id>:<page>`). Notable choice: the
target page is baked directly into each nav button at render time
(`prev` encodes `page-1`, `next` encodes `page+1`) instead of encoding the
*current* page and subtracting/adding in the callback — removes the need to
track "current page" as separate mutable state anywhere, live view or shell
alike. `ViewMessageModal`/`EditNoteModal` lost their `parent_view` the same
way `ReminderAddModal` did in Step 7's first half — replaced with
`_refresh_library_card(bot, owner_id, locale, channel_id, message_id, …)`
keyed on plain IDs captured off the interaction that opened the modal.

**`EmojiNavigationView`**: NOT migrated, deferred. Appendix B.1.c's proposed
fix — "re-scan the guild's emojis on click (cheap, `guild.emojis` is
cached)" — does not match what this view actually shows: `emoji_list` here
is not `guild.emojis`, it's the result of regex-extracting `<:name:id>`
mentions out of one specific *message's content*
([cogs/emoji.py:321-364](../cogs/emoji.py), the "Get Emojis" context menu),
each entry additionally requiring a per-emoji HTTP call
(`check_if_animated`, [cogs/emoji.py:294](../cogs/emoji.py)) to determine if
it's a GIF. Reconstructing this on a restarted shell would mean encoding the
source message's `channel_id`/`message_id` in every Prev/Next click,
re-fetching that message, re-running the regex, and re-issuing one HTTP
request per emoji found — real per-click cost for a view whose current
`timeout=180` already caps its lifetime to three minutes and whose command
is ephemeral/single-use. Judged not worth the reconstruction cost for a
short-lived lookup card, same call as Step 5's `InviteView`. Left as-is
(`timeout=180`, not registered); the row in Appendix B.1.c should read "not
`guild.emojis`, see Migration log" for whoever revisits this.

`tests/test_persistent_views.py` now covers `SavedMessagesLibraryView` and
both its `DynamicItem`s (72 tests, still green).

### Step 8 — Small guild config panels (`AutoRoleConfigView`, `AutoRestoreRolesConfigView`, `StarboardConfigView`, `InterServerConfigView`)

All four follow the identical `current_config`/`working_config`/`has_changes`
shape (Appendix B.1.a). Per Appendix B.2/B.5, none need a `DynamicItem` —
`interaction.guild_id` is already on the interaction, so a static namespaced
`custom_id` plus a re-checked `manage_guild` permission is enough. Added a
shared `modules/configs/_common.py::check_guild_perms(interaction)` (reused
by all four, and by every later config-panel step) instead of duplicating
the same guild-permission check four more times — a deliberate deviation
from the "copy the tiny helper per file" convention used for
`_guarded`/`_reject_if_not_owner`, because this one is truly identical logic
with no per-file variation, not merely similarly-shaped.

**Two correctness bugs found while doing this, not called out anywhere in
Appendix A/B/C, and both are structural — every guild config panel migrated
in this and later steps needed the same two fixes:**

1. **Mutate-and-resend-self is unsafe for a registered persistent view.**
   The pre-migration pattern was `self.working_config[...] = x;
   self._build_view(); await interaction.response.edit_message(view=self)`.
   That's fine for a live, per-message view instance — but
   `register_persistent` registers exactly **one** shared instance via
   `bot.add_view(cls())`, and discord.py falls back to dispatching clicks on
   *any* message whose specific view isn't in its in-memory cache (always
   true right after a restart, for every guild) to that **same** shared
   object. If the callback mutates `self` in place, two different guilds
   whose config panels both fall back to the shell in the same window would
   be editing the same Python object's `working_config` — one guild's
   in-progress edit becoming visible on another guild's message. Fixed by
   never resending `view=self`: every callback now derives a **new**
   `FooConfigView(...)` instance from `interaction` (fresh `bot`,
   `guild_id`, `user_id`, `locale`) and edits with that. A `_is_live_for()` /
   `_fresh_working_config()` pair on each view decides whether `self`'s
   in-memory `working_config` is trustworthy (it is, if `self.guild_id`
   still matches `interaction.guild_id` — i.e. this really is the live
   per-message instance) or must be reloaded from the DB (it's the shared
   shell, or a stale instance for a different guild). This is the same
   discipline `SocialNotificationsConfigView` already used (`_render_main`
   always builds fresh via `.create()`) — it just wasn't written down as a
   rule anywhere, so it would have been easy to silently reintroduce per
   step. Confirmed retroactively that Steps 6-7's `DynamicItem`-based views
   (`PreferencesView`, `RemindersManageView`, `SavedMessagesLibraryView`)
   don't have this problem at all: `bot.add_dynamic_items()` registers the
   *item class* and its `from_custom_id` template, not a shared instance, so
   there is no single shared object to leak state through.

2. **A conditionally-rendered button/select's custom_id is only known to
   discord.py if the registered shell instance happens to include it.**
   `back`/`save`/`cancel`/`delete` only appear in `_add_action_buttons()`
   depending on `has_changes`/`has_existing_config`; the mode-dependent role
   selectors in `AutoRestoreRolesConfigView` only appear for one `mode` at a
   time. A bare shell built with the class's defaults
   (`has_changes=False`, `has_existing_config=False`, `mode='all'`) would
   therefore never register `_CID_SAVE`/`_CID_CANCEL`/`_CID_DELETE` or the
   excluded/included role selects at all — a live message showing "Save" +
   "Cancel" (because a real user has unsaved edits) would dispatch nowhere
   after a restart, failing exactly the case persistence exists to fix.
   Fixed with an `is_shell = self.bot is None` escape hatch at each
   conditional: the shell renders **every** variant of every conditional
   item (all four buttons together, both role selectors together) purely so
   their custom_ids get registered — this instance is never actually sent
   to a user, only used for `bot.add_view()`. `SocialNotificationsConfigView`
   already had one instance of this exact fix (the placeholder `manage_select`
   when `self.bot is None`); it just wasn't generalized into a named pattern
   anywhere in this doc.

Given both bugs are structural rather than per-view, whoever does Steps
9-15 should apply `_is_live_for`/`_fresh_working_config`/`_rebuild` plus the
`is_shell` conditional-registration escape hatch as a matter of course, not
re-derive them from scratch each time.

### Step 9 — `WelcomeChannelConfigView` + `WelcomeDmConfigView` (colliding pair, one commit)

Same `_is_live_for`/`_fresh_working_config`/`_rebuild` + `is_shell`
conditional-registration pattern as Step 8, applied to both views in a
single commit per Appendix E's rule 3 ("never register a colliding pair
separately"). Verified after the rename that the two namespaces
(`moddy:welcomechan:config:*` vs `moddy:welcomedm:config:*`) produce zero
overlapping custom_ids — this is exactly the collision
`test_no_duplicate_custom_ids_across_registered_views` exists to catch
(previously both used bare `"edit_embed_title"`, `"toggle_thumbnail"`, etc.,
per Appendix B.5.1).

Modal-driven fields (message, embed title/description/color) use the same
closure-over-locally-fetched-`working_config` pattern introduced for
`StarboardConfigView` in Step 8, not a `self`-bound callback — each
`on_edit_*` re-derives `working_config` via `_fresh_working_config`
immediately before opening the modal, so the modal's `_on_submit` closure
never touches `self`.

### Step 10 — `ConfigMainView`

The router every panel's Back button returns to. Its `_build_view` calls
`self.bot.module_manager.get_available_modules()` unguarded (Appendix B.3) —
fixed with the `if self.bot is None:` shell branch, matching
`SocialNotificationsConfigView`'s existing precedent for a select with no
live data source: a single disabled placeholder option, custom_id still
registered. `on_module_select`'s long `if/elif` chain constructing each
module's config view previously read `self.bot`/`self.guild_id`/
`self.user_id`/`self.locale` — replaced with `bot`/`guild_id`/`user_id`/
`locale` local variables re-derived from `interaction` at the top of the
callback, so the router doesn't propagate a potentially-stale `self` into
every child panel it opens. No `working_config` here (the router holds no
editable state), so no `_rebuild`/`_fresh_working_config` pair was needed —
just the auth model swap and the is-shell guard.

### Step 11 — Adaptive slowmode (`AdaptiveSlowmodeConfigView`, `AdaptiveSlowmodeChannelConfigView`)

**`AdaptiveSlowmodeConfigView`** (the channel list): same `check_guild_perms`
+ fresh-instance-per-callback + `is_shell` pattern as Steps 8-10, plus a new
`SlowmodeListButton` `DynamicItem` for the per-row Edit/Remove buttons —
these needed a `DynamicItem` (unlike every other button on this and prior
panels) because the target `channel_id` is per-row state, not something
already on the interaction (Appendix B.2). `DynamicItem` registration is
class-level (`bot.add_dynamic_items(SlowmodeListButton)`), so unlike the
static-custom_id buttons it does *not* need the `is_shell`-renders-everything
trick — the item class's template governs dispatch regardless of how many
channel rows the shell happens to render (zero, on a bare shell with no
guild context).

**`AdaptiveSlowmodeChannelConfigView`** (add/edit one channel): this is the
view Appendix B.1.b calls "the hardest case in the repo" — a required
`parent_view: AdaptiveSlowmodeConfigView` positional with no default,
because Back/Save call `self.parent_view._build_view()` /
`self.parent_view.working_config[...] = ...` directly on that live object.
Followed B.1.b's own proposed fix exactly: dropped `parent_view` entirely;
the constructor now takes the parent's `working_config` as a **plain dict**
(not a view reference) plus `has_existing_config`, and Back/Save each
construct a **fresh** `AdaptiveSlowmodeConfigView` to return to, rather than
reaching back into a parent instance that might not even be the one that
opened this wizard (see the Step 8 writeup on why mutating a possibly-shared
instance is unsafe).

Given that fix, `AdaptiveSlowmodeChannelConfigView` no longer *needs* a
parent_view reference — but it is still **not** made persistent. Rationale:
everything it edits (`min_delay`/`max_delay`/`sensitivity` for one channel,
and whether a channel has even been picked yet in add mode) is an unsaved
draft that isn't written to the DB until the *parent* list view's own Save
button is clicked — the same "nothing to recover, restart the wizard"
situation as `CaseCreationView` (Appendix B.1.c). Its buttons/selects
therefore keep their auto-generated (non-namespaced) custom_ids and the
view keeps `timeout=300`; it is not added to `_collect_persistent_view_classes()`.
This is a considered exclusion, not an oversight — flagging explicitly in
case a future pass assumes every view touched by this migration ended up
persistent.

### Step 12 — `AutomodAIConfigView` (`AutomodAIPrecedentsView` deliberately excluded)

The largest panel (14 children). Same `check_guild_perms` +
fresh-instance-per-callback + `is_shell` pattern as every prior guild config
panel, applied across the module toggle, the 3-way options select, notify
channel, severity, max action, language, exempt roles/channels, and the
conditionally-shown "view precedents" button (only rendered when a
precedent count is known and non-zero — needed the same `is_shell` escape
hatch as Step 8's save/cancel/delete buttons).

`IndicationsModal` previously took the whole `parent: AutomodAIConfigView`
and wrote into `parent.working_config` / called
`parent._build_view()` / resent `view=parent` directly — the same
mutate-and-resend-self hazard as Step 8's `StarboardConfigView` modals, just
on a bigger panel. Fixed the same way: the modal now takes the working_config
draft as a plain dict plus the view's `_rebuild` bound method (which,
notably, doesn't read any `self` state itself — it only closes over
`interaction` — so handing it out from a possibly-shared `self` is safe).

`AutomodAIPrecedentsView` (opened from the "view precedents" button):
**not** made persistent, matching `AdaptiveSlowmodeChannelConfigView` from
Step 11. Its rows are cheap to re-fetch (Appendix B.1.d already says so),
but it is only ever reached via a live click from an already-open
`AutomodAIConfigView` — never independently, never registered — so there is
no restart-survival requirement for it specifically; a dead restart just
means the user re-clicks "view precedents" from the (now-refreshed) parent
panel. Its `parent=None` default (Appendix B.1.b: "already optional") and
the existing DB-rebuild fallback in `on_back` were left as-is — they already
implement B.1.b's proposed fix and didn't need touching. `on_view_precedents`
was updated to stop passing `parent=self` at all, since the callback that
opens it already only has `interaction`-derived state to hand over (there is
no live `self` worth keeping a reference to once the auth model no longer
depends on it).

### Step 13 — Case management: NOT migrated, deferred

`utils/case_management_views.py`'s own module docstring already states the
design intent: "These are short-lived, author-scoped, ephemeral staff flows
(timeout-based, not persistent — they wrap in-memory callbacks, mirroring
the existing staff `_ModalButtonView` pattern)." On inspection this is
accurate and load-bearing, not just stale documentation: `CaseCreationView`,
`AddSanctionView`, and `RevokeSanctionView` are all constructed with an
`on_created`/`on_done` **Python callable** parameter
([utils/case_management_views.py:73](../utils/case_management_views.py),
:285, :390) supplied by whichever staff command opened them — the same
"behaviour is a callable with no stable identity, not serialisable into a
custom_id" shape Appendix B.4 already uses to permanently exclude
`ConfirmView` and `_ModalButtonView`. There is no `custom_id` that could
encode "call this specific staff command's continuation after a restart";
the callback simply would not exist anymore in a fresh process.

`AddSanctionView`/`RevokeSanctionView` are otherwise clean `DynamicItem`
candidates keyed on `case_id`/sanction `reference` per Appendix B.2 and
Appendix E's own note — but that only addresses *authorization*, not the
`on_done` callable each one is built with, which is the actual blocker.
Migrating just the auth model while leaving the callback parameter would
still crash a restarted shell's dispatch the moment it tried to invoke a
callable that no longer exists.

Given this is a correctness blocker the doc itself half-flags ("may be
better left non-persistent... decide before starting") and this migration's
mandate not to make product/architecture calls beyond what's written down,
**left all three views untouched**: no `custom_id`s, no `__persistent__`,
not in the registry, `timeout=300`/`600` unchanged. Same accepted-loss
rationale as `CaseCreationView` already gets in Appendix B.1.c ("a
partially-built moderation case must not be silently resurrected") — now
applied to `AddSanctionView`/`RevokeSanctionView` too, since they share the
identical callable-parameter shape. A real fix (e.g. replacing the callable
with a small enum of "what to do when done" that a `DynamicItem` callback
could re-derive and act on) is a refactor of the staff mod-case command
flow, not a mechanical migration step — out of scope here.

### Step 14 — Staff read-only views (`HelpView`, `ServerListView`; `EmojiPreviewView` excluded)

**`HelpView`**: the department listing is the caller's own
permission-filtered command set (`router._has_permission` re-run per
command against `ctx.author.id`), not something read from a fixed table —
so "cheap to re-derive" (Appendix B.1.d) meant extracting the data-gathering
loop out of `HelpCommand.execute` into a shared `_build_help_data(bot,
author_id)` function, callable identically from the command and from a
restarted shell's click. `HelpDeptSelect` is a `DynamicItem` encoding
`owner_id` (Appendix B.2 — this is owner-only, not staff-rank re-checked;
the code already only ever compared `interaction.user.id` to `author_id`,
so this migration preserves that, not Appendix A.1's suggested "Staff rank"
label for this view, which doesn't match what the code actually does).

**`ServerListView`**: `guilds` is trivially re-derivable from `bot.guilds`
(Appendix B.1.d, already flagged). `ServerListNavButton` is a `DynamicItem`
encoding `owner_id` + the **target** page (not the current page), same
"bake the destination into the button" trick as `SavedMessagesListButton`
in Step 7 — no page-tracking state needs to survive anywhere, live or
restarted.

**`EmojiPreviewView`**: **not** migrated. Its own class docstring already
states "Non-persistent preview view. Temporary by design." — confirmed
accurate: every button/select on it calls the same `_noop` handler that
just says "this is a preview," and its only state (`partial_emoji`,
`emoji_str`) is a one-off developer lookup with nothing to reconstruct from
DB or interaction. This is the file-level equivalent of an Appendix B.4
exclusion; left completely untouched.

### Step 15 — `StaffManagerPanel` — **HIGH PRIVILEGE, NEEDS HUMAN REVIEW BEFORE MERGE**

Grants and revokes staff roles and permissions. Migrated per the operating
instructions for this pass ("do it like the others, but flag it"), not
skipped — but this is the one step in the whole migration where a
mechanical port was not possible, and the resulting behaviour change should
be read carefully before this lands anywhere real.

**What changed and why, in order of discovery:**

1. **`target`/`modifier` are `discord.User` objects** (Appendix B.1.c) —
   fixed the expected way: encode `target_id` + `modifier_id` in every
   item's `custom_id` (`moddy:staffpanel:<action>:<target>:<modifier>`,
   `permscope` variant additionally carries the lowercased scope) and
   re-fetch both via `bot.fetch_user()` on every click. `modifier_id` is
   encoded too, not just inferred from the clicker, to preserve the
   original "not your menu" semantics for a stale/shared shell.

2. **A structural discovery, not specific to this view but most consequential
   here**: `discord.py` registers `DynamicItem`s by **class**, not by living
   instance (`discord/ui/view.py::View.add_view`: `if isinstance(item,
   DynamicItem): self._dynamic_items[pattern] = item.__class__` — the
   specific instance is discarded). `dispatch_view` unconditionally runs
   `dispatch_dynamic_items()` first, which always reconstructs via
   `from_custom_id()`. **This means every DynamicItem callback in this
   entire migration reconstructs from scratch on every single click — live
   session or restarted shell, no difference.** Every other `DynamicItem`
   written in this migration (Steps 6, 7, 11, 14) already happened to be
   correct under this constraint because each one's callback only ever
   needed the clicked value plus a fresh DB read — never a *previous
   click's* in-memory state. `StaffManagerPanel` is the one view whose
   pre-migration design actively relied on exactly that: pick roles, switch
   scope, edit permissions for one role, switch scope again, edit another
   role's permissions, *then* click Save — accumulating unsaved edits
   across several clicks in `self` before a single commit. That flow is not
   implementable with `DynamicItem`s at all, restart or not, without
   external scratch storage (e.g. a drafts table) to carry state between
   clicks — out of scope for this migration.

3. **Resolution**: changed the panel to apply every change immediately.
   `StaffPanelRolesSelect` writes the new role list (and prunes/seeds
   `role_permissions` for kept/added roles) to the DB the moment it's
   changed, re-running `can_assign_role` per role exactly as before.
   `StaffPanelPermsSelect` writes its scope's permission list to the DB the
   moment it's changed (the scope is in its own custom_id, so it never
   guesses which role a submitted permission list belongs to).
   `StaffPanelScopeSelect` stays read-only (just changes which permission
   set is displayed). **Save no longer performs a write** — it re-reads the
   current DB state and shows the same confirmation card as before,
   because there is nothing left to commit. Remove is unchanged (was always
   immediate). This is a real, user-visible behaviour change: permissions
   now take effect per-click instead of only after Save, which is arguably
   safer (no way to "forget" to save a role grant) but is a genuine product
   decision, not a mechanical port, and should be confirmed rather than
   assumed correct.

4. Everything else follows the established pattern: `check`-equivalent is
   `_reject_if_not_modifier` (owner/modifier-only, matching the
   pre-migration `_guard`'s exact semantics, not tightened to re-verify
   staff rank on every click since the original never did either);
   `_guarded` wraps every callback for central error-handler routing; the
   registration shell (`StaffManagerPanel()` with `target=None`) renders a
   single placeholder line since every real control is a `DynamicItem`
   registered by class, not by the shell's rendered contents.

**Flagging per the operating instructions for this migration pass: this
view grants and revokes staff permissions. A dispatch bug here is a
privilege-escalation bug, and the immediate-apply behaviour change in point
3 above is a product decision made under migration constraints, not a
foregone conclusion. Do not merge this step without a human reviewing
`staff/commands/manage/staff.py` specifically.**

---

## Final status (branch `claude/persistent-views-migration-nph7ok`)

**All 16 numbered steps of Appendix E (0-15) are complete**, one commit per
step, `tests/test_persistent_views.py` green after every commit (139 tests
at the end of Step 15, up from the 42 that covered the 7 already-registered
classes before this pass started).

**Persistent (registered) as of Step 15**: `ModdyMainView`, `AttributionView`,
`WeSupportView`, `SocialNotificationsConfigView`, `AddSubscriptionView`,
`ManageSubscriptionView`, `CasesBrowserView`, `AppealPersistence`,
`ShadowAnnotationPersistence`, `PreferencesView`, `RemindersManageView`,
`SavedMessagesLibraryView`, `InterServerConfigView`, `AutoRoleConfigView`,
`AutoRestoreRolesConfigView`, `StarboardConfigView`,
`WelcomeChannelConfigView`, `WelcomeDmConfigView`, `ConfigMainView`,
`AdaptiveSlowmodeConfigView`, `AutomodAIConfigView`, `HelpView`,
`ServerListView`, `StaffManagerPanel` (24 view classes, several with
multiple `DynamicItem` subclasses alongside them).

**Deliberately NOT migrated**, each with reasoning in the corresponding
step's log entry above — not omissions:
- `InviteView` / `ServerInfoView` / `UserInfoView` (Step 5) — blocked on the
  explicitly-flagged Raw Data NEEDS DECISION plus a real re-fetch cost
  tradeoff neither this migration nor the doc pre-decided.
- `EmojiNavigationView` (Step 7) — B.1.c's proposed fix doesn't match what
  the view actually holds (message-scoped emoji mentions + per-emoji HTTP
  calls, not `guild.emojis`); not worth the reconstruction cost for an
  ephemeral lookup card.
- `AdaptiveSlowmodeChannelConfigView` (Step 11) — pure unsaved-draft wizard,
  same accepted loss as `CaseCreationView`; the `parent_view` blocker itself
  *was* fixed (dropped in favor of a plain dict).
- `AutomodAIPrecedentsView` (Step 12) — only ever reached via a live click
  from an open `AutomodAIConfigView`, rows cheap to re-fetch, `parent=None`
  fallback already correct.
- `CaseCreationView` / `AddSanctionView` / `RevokeSanctionView` (Step 13) —
  all three take an `on_done`/`on_created` Python callable, the same
  no-stable-identity shape Appendix B.4 already excludes `ConfirmView` and
  `_ModalButtonView` for. Confirmed by the module's own docstring.
- `EmojiPreviewView` (Step 14) — its own docstring already says
  "Non-persistent... Temporary by design"; confirmed accurate.
- Every view already listed under Appendix E's own "Deferred / not in this
  migration" and "Never" rows (`ReportView`, `TranslateView`,
  `StaffHelpView`, `SqlConfirmView`, `ConfirmView`, `_ModalButtonView`,
  `WebhookView`, `ErrorView`, `FallbackErrorView`, `PermissionErrorView`,
  `CooldownErrorView`, `NotFoundView`, all `BaseModal` subclasses) — never
  touched, per the explicit instruction not to resolve NEEDS DECISION/UNKNOWN
  items in this pass.

**Corrections made to this document along the way** (all cross-referenced
from their step's log entry, not repeated here): Appendix D.1's test-setup
facts were missing the `DISCORD_TOKEN`-at-import crash (Step 0); Appendix
C's literal `\d{17,20}` template breaks `test_shell_constructs` for a
default-constructed shell and needed relaxing to `\d{1,20}` everywhere it
was reused (first hit: Step 6); Appendix E's own Step 6 heading
("no DynamicItem needed") contradicted Appendix B.2's row for the same view
(Step 6); two structural bugs not mentioned anywhere in Appendix A/B/C — the
shared-shell mutate-and-resend-`self` hazard, and conditionally-rendered
items never getting registered on a default-state shell — affect every
`working_config`-style guild panel and are written up once in Step 8 rather
than repeated in Steps 9-12; and Step 15 surfaces the biggest one:
`discord.py` registers `DynamicItem`s by class, not by live instance, so
*every* `DynamicItem` callback in this migration reconstructs from scratch
on every click, restart or not — harmless everywhere else, but incompatible
with `StaffManagerPanel`'s original stage-then-Save editing flow, which is
why that panel's permissions now apply immediately instead (see Step 15,
flagged for mandatory human review).

**Nothing here should be treated as a substitute for the human review Step
15 explicitly asks for.** Everything else is a normal, mechanical
persistent-view migration and should be reviewable the same way as any
other PR in this repo.
