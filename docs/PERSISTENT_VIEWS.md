# Persistent Views

> All interactive Discord views in Moddy should survive a bot restart. This
> document explains how the persistence layer works, the conventions every
> view must follow, and a cookbook for adding a new one.

**Persistence is mandatory, not opt-in.** See the rule in
[`CLAUDE.md`](../CLAUDE.md) ("Persistent Views — MANDATORY"). Any new
interactive component (button, select, `DynamicItem`) must ship persistent
from the start; a review that finds a non-persistent view with no
documented exception (see "Deliberate exclusions" below) should block the PR.

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

You register persistent views **once** at startup with `bot.add_view(view)`
(regular views) or `bot.add_dynamic_items(ItemClass)` (`DynamicItem`-based
views — see below). Discord dispatches incoming button clicks by looking up
`(component_type, custom_id)`:
- **Regular views**: the live in-memory view that was sent with the message
  receives the click if discord.py still has it cached; otherwise it falls
  back to the registered shell instance (`self` has no per-message state).
- **`DynamicItem`s**: discord.py registers the **class** and its
  `template=` regex, not any instance. `dispatch_dynamic_items()` runs
  first on every click and always reconstructs a fresh instance via
  `from_custom_id()` — **live session or restart makes no difference.** A
  `DynamicItem` callback can never rely on state from a *previous* click; it
  only ever has what the current click's custom_id encodes, plus a fresh DB
  read. Design the item around that from the start — see "Two gotchas" below
  for what goes wrong when a view is written as if the shared shell were the
  only thing to worry about.

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
| User-scoped | `moddy:reminder:manage:add:<user_id>` | Only the owner can click |
| Guild-scoped | `moddy:config:welcome:<guild_id>` | Re-check permission on click |
| Entity-scoped | `moddy:cases:view:<case_id>` | ID identifies a DB row |
| Paginated | `moddy:saved:page:<user_id>:<page>` | Page is small int |

**Namespace collisions are a real, silent failure mode**, not a style nit:
discord.py dispatches on `(component_type, custom_id)` globally, so two
unrelated *registered* views sharing a bare id like `"back_btn"` will have
one silently shadow the other. `tests/test_persistent_views.py` has a
dedicated test for this
(`test_no_duplicate_custom_ids_across_registered_views`) — it must pass
before a new view is added to the registry.

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
| Public informational (`/moddy`, `/roll`) | **Public** | No check. Anyone who can see the message can click. |
| Personal data (reminders, preferences, saved messages, user cases) | **Owner only** | Encode `user_id` in the custom_id (via a `DynamicItem`), compare to `interaction.user.id` on click. Reject mismatches with an ephemeral error. |
| Guild config panels (`/config`, `modules/configs/*`) | **Guild permission** | `interaction.guild_id` is already on the interaction — a static namespaced custom_id plus a re-checked `manage_guild` permission is enough. No `DynamicItem` needed for this class of view. Use the shared `modules/configs/_common.py::check_guild_perms(interaction)` helper rather than re-implementing the check per panel. |
| Staff tools (`staff/*`) | **Staff rank** | On click, re-run `utils/staff_permissions.py` check — no user_id in custom_id, unless the view is also owner-scoped (e.g. "only the staffer who opened this may use it"), in which case encode `owner_id` too. |

When the auth check fails, respond with an ephemeral
`utils/components_v2.create_error_message(...)` — never silently swallow.
Owner-only rejections share one i18n key pair,
`errors.not_your_message` (title + description, in
[`locales/fr.json`](../locales/fr.json) and
[`locales/en-US.json`](../locales/en-US.json)) — reuse it instead of adding
a per-view key.

---

## State reconstruction

Persistent views cannot remember anything between clicks. That is actually
*the* feature — it forces a clean flow:

1. **Click arrives** → `on_foo(interaction)` runs on either the live view or
   the registered shell (or, for a `DynamicItem`, on a freshly reconstructed
   instance — see "How discord.py persistence works" above).
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
Several guild config panels (e.g. `AutoRoleConfigView`,
`WelcomeChannelConfigView`) hold a `working_config` with unsaved changes in
memory. After a restart — or when a click falls back to the shared
registered shell (see the mutate-and-resend gotcha below) — those are lost.
**Accepted UX**: the view rebuilds from the DB-saved config on the next
click; the user re-applies their unsaved edits. No drafts table.

---

## Two gotchas found migrating the guild config panels

These are not obvious from the contract above and bit every `working_config`
panel during the migration. Apply both as a matter of course to any new view
that keeps unsaved edits on `self`.

1. **Mutate-and-resend-`self` is unsafe for a registered persistent view.**
   `self.working_config[...] = x; self._build_view(); await
   interaction.response.edit_message(view=self)` is fine for a live,
   per-message view instance — but `register_persistent` registers exactly
   **one** shared instance via `bot.add_view(cls())`, and discord.py falls
   back to dispatching clicks on *any* message whose specific view isn't in
   its in-memory cache to that **same** shared object. If the callback
   mutates `self` in place, two different guilds whose panels both fall back
   to the shell in the same window would be editing the same Python object's
   `working_config` — one guild's in-progress edit becoming visible on
   another guild's message. **Fix**: never resend `view=self`. Every
   callback derives a **new** `FooConfigView(...)` instance from
   `interaction` (fresh `bot`, `guild_id`, `user_id`, `locale`) and edits
   with that. Decide whether `self`'s in-memory state is trustworthy (it is,
   if e.g. `self.guild_id` still matches `interaction.guild_id` — this
   really is the live per-message instance) or must be reloaded from the DB
   (it's the shared shell, or a stale instance for a different guild).
   `DynamicItem`-based views (reminders, saved messages, preferences) don't
   have this problem at all — `bot.add_dynamic_items()` registers the item
   *class*, not a shared instance, so there is no single shared object to
   leak state through.

2. **A conditionally-rendered button/select's `custom_id` is only known to
   discord.py if the registered shell instance happens to include it.** If
   `back`/`save`/`cancel`/`delete` only appear depending on
   `has_changes`/`has_existing_config`, a bare shell built with the class's
   defaults would never register those custom_ids — a live message showing
   "Save" + "Cancel" would dispatch nowhere after a restart, exactly the
   failure persistence exists to prevent. **Fix**: give the shell an
   `is_shell = self.bot is None` escape hatch and, on that branch only,
   render **every** variant of every conditional item (all buttons
   together, every mode's selects together) purely so their custom_ids get
   registered — this instance is never actually sent to a user.

---

## Registration flow

1. `bot.setup_hook()` runs once on startup
2. Cogs are loaded via `await self.load_extensions()`
3. Immediately after, `register_all_persistent_views(self)` is called
4. That function walks `_collect_persistent_view_classes()` and calls
   `cls.register_persistent(bot)` on each class
5. Each class typically calls `bot.add_view(cls())` (regular view) or
   `bot.add_dynamic_items(ItemClass, ...)` (`DynamicItem`-based view)

If a single view fails to register, the error is logged and the bot
continues — persistence is best-effort, it should never prevent startup.

---

## Cookbook: adding or migrating a view

Given a `BaseView` subclass like `OldView(bot, guild_id, user_id, locale)`:

1. **Pick an auth model** (see table above) and write it in a 1-line
   docstring/comment.
2. **Make every constructor arg optional** with safe defaults so
   `OldView()` works, including any `parent_view` — a live view object
   cannot be reconstructed from a custom_id. Drop the reference; have
   "Back" build a fresh parent from the DB instead.
3. **Guard any `self.bot.something` access** inside `_build_view` with
   `if self.bot is not None:` so the shell can build without a live bot.
4. **Add `custom_id` to every button / select** using module-level
   constants. Namespaced: `moddy:<cog>:<view>:<action>`.
5. **Rewrite callbacks** to re-derive `bot`, `locale`, `user_id`,
   `guild_id` from `interaction` instead of `self`. Never resend
   `view=self` on a registered view — see gotcha 1 above.
6. **For user-scoped or entity-scoped views**: use a `DynamicItem`
   subclass with a `template=` regex encoding the id (`user_id`,
   `case_id`, a page number, …) — see the reference implementations below.
   Guild config panels don't need this (`interaction.guild_id` is already
   available); most owner-scoped and entity-scoped views do.
7. **If any button/select only renders conditionally**, add the
   `is_shell` escape hatch (gotcha 2 above) so every variant's custom_id
   still gets registered.
8. **Set `__persistent__ = True`**.
9. **Implement `register_persistent`**: `bot.add_view(cls())` for a regular
   view, or `bot.add_dynamic_items(ItemClass)` for a `DynamicItem`-based one.
10. **Add the class** to
    `utils/persistent_views.py::_collect_persistent_view_classes()`.
11. **Verify**: `python3 -m pytest tests/test_persistent_views.py -k <name> -q`
    (see "Verifying a view is persistent" below for what it checks).

### Reference implementations to copy from

- **Regular persistent view, public auth**: `ModdyMainView`
  ([cogs/moddy.py](../cogs/moddy.py)) — the canonical `if self.bot is not
  None:` guard.
- **Regular persistent view, guild permission auth**:
  `SocialNotificationsConfigView`
  ([modules/configs/social_notifications_config.py](../modules/configs/social_notifications_config.py)).
- **`DynamicItem`, owner-scoped**: `ReminderManageButton`
  ([cogs/reminder.py](../cogs/reminder.py)) — encodes `owner_id` in the
  custom_id, rejects mismatches ephemerally.
- **`DynamicItem`, entity-scoped, no live view at all** (marker-view
  pattern — use when several unrelated cards share one button type):
  `ShadowAnnotateButton` ([utils/automod_shadow_views.py](../utils/automod_shadow_views.py),
  simplest) and the five buttons in
  [`utils/appeal_views.py`](../utils/appeal_views.py). Both wrap every
  callback in a local `_guarded` decorator — **copy that too**: a
  `DynamicItem` dispatched via `bot.add_dynamic_items` has no live
  `BaseView`, so `BaseView.on_error` never fires, and an unwrapped
  exception simply vanishes instead of reaching the user or the error
  handler.

---

## Verifying a view is persistent

```bash
# once per environment — requirements-dev.txt alone does not pull in discord.py
python3 -m pip install -r requirements.txt -r requirements-dev.txt

# after changing one view/module:
python3 -m pytest tests/test_persistent_views.py -k <name> -q

# the whole persistence contract suite:
python3 -m pytest tests/test_persistent_views.py -q

# full test suite before opening a PR:
python3 -m pytest -q
```

`tests/test_persistent_views.py` parametrizes over every class in
`_collect_persistent_view_classes()` (plus the known `DynamicItem`
subclasses) and asserts, per class: it constructs as a bare shell with no
arguments; `__persistent__` is `True`; `timeout is None`; `is_persistent()`
is `True`; every non-URL custom_id matches the
`moddy:<cog>:<view>:<action>[:<param>]` shape; and no two registered views
share a custom_id. Add a new view to the file's parametrization (or to
`_collect_persistent_view_classes()`, which it reads from automatically) as
part of the same commit that migrates it — a view is not done until this
suite covers it.

`bot.add_view(v)` will silently accept a non-persistent view; clicks will
just never dispatch. The test suite is what actually catches that, not a
manual assertion.

---

## Deliberate exclusions

Views that are intentionally **not** persistent, and why. Adding a new
exclusion requires the same kind of concrete justification as these — "it
was easier not to" is not one.

- **Modals (`BaseModal`)** — Discord treats modal submission as a one-shot
  interaction tied to the owning message's in-memory component store.
  `discord.ui.Modal` already defaults to `timeout=None`, so modals will
  not expire mid-edit as long as the bot stays up. On restart, any open
  modal is effectively lost — the user re-opens it.
- **`ErrorView`, `FallbackErrorView`, `PermissionErrorView`,
  `CooldownErrorView`, `NotFoundView`** ([cogs/error_handler.py](../cogs/error_handler.py),
  [bot.py](../bot.py)) — error-recovery UI, URL-only buttons or none at all.
  Nothing to register, and `FallbackErrorView` in particular must not depend
  on the registry it may be reporting a failure in.
- **`cogs/webhook.py::WebhookView`** — displays webhook tokens / URLs which
  are secret and should not be re-rendered after a restart. Keep as-is;
  users re-run the command.
- **`staff/framework/views.py::ConfirmView`,
  `staff/framework/context.py::_ModalButtonView`** — generic confirm/cancel
  and "click to open a modal" shims whose behaviour is an in-memory Python
  callable with no stable identity. Not serialisable into a custom_id. Their
  short timeouts (60s/300s) are a deliberate safety property, not an
  oversight.
- **`staff/commands/dev/sql.py::SqlConfirmView`** — confirming an arbitrary
  SQL statement after a restart would execute a query typed in a different
  process lifetime, with no visible context. `timeout=60` is intentional.
- **`utils/case_management_views.py::CaseCreationView`, `AddSanctionView`,
  `RevokeSanctionView`** — each is constructed with an `on_done`/`on_created`
  Python callable supplied by the staff command that opened it; same
  no-stable-identity shape as `ConfirmView` above. A partially-built
  moderation case must not be silently resurrected on restart, either.
- **`staff/commands/dev/emoji_preview.py::EmojiPreviewView`** — "Temporary
  by design" per its own docstring: every control is a no-op preview
  handler with nothing to reconstruct.
- **`cogs/translate.py::TranslateView`, `cogs/invite.py::InviteView` /
  `ServerInfoView`, `cogs/user.py::UserInfoView`,
  `cogs/emoji.py::EmojiNavigationView`** — each renders from a payload
  fetched once from an external API (DeepL, Discord's invite/user APIs) and
  held only in memory; persisting them means re-fetching from that API on
  every click after a restart, which is a real cost/behaviour tradeoff that
  hasn't been made yet. `NEEDS DECISION` if picked up again.
- **`utils/staff_help_view.py::StaffHelpView`** — overlaps with
  `staff/commands/team/help.py::HelpView`, which is already persistent and
  covers the same catalogue. Confirm `StaffHelpView` is still reachable
  before deciding whether it needs migrating or removing.
- **Every view with zero interactive children** (`AvatarView`, `BannerView`,
  `RollView`, `TextResultView`, static info/log cards, …) or whose only
  children are `ButtonStyle.link` buttons (e.g. `SubscriptionView`) —
  `bot.add_view()` on these is a no-op. Do not add `__persistent__`; there
  is nothing to register.

---

## Current coverage

Registered persistent views live in
[`utils/persistent_views.py`](../utils/persistent_views.py) —
`_collect_persistent_view_classes()` is the single source of truth for
"what survives a restart today." As of the last migration pass it includes
the `/moddy` views, `/config` and every `modules/configs/*` panel, social
notifications, `/cases` & `/mycases`, automod appeals and shadow-mode
annotations, reminders, preferences, saved messages, and the staff
help/server-list/manager panels.

**`staff/commands/manage/staff.py::StaffManagerPanel`** grants and revokes
staff roles/permissions. Making it persistent required changing its
edit-then-Save flow to apply each change immediately (see the class's own
comments) because `DynamicItem`s reconstruct from scratch on every click —
there is no `self` to stage edits on. Treat any further change to that file
as touching a privilege-escalation surface: get a second reviewer, not just
green tests.
