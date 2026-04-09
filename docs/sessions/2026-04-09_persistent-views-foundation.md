# Session: Persistent Views — Foundation + Group 1

**Date:** 2026-04-09
**Agent:** Claude Code (Opus 4.6)
**Branch:** `feature/persistent-views-foundation`
**Plan file:** `/home/codespace/.claude/plans/iterative-purring-quill.md`

---

## Context

User request (FR): *"tu vas rendre tous les boutons et autre trucs d'interaction persistant c'est à dire sans timeout et même si on redémarre le bot ça recharge les boutons et il redeviennent dispo"*.

Before this session, every View in Moddy had a timeout (180–300 s) and **no** views were registered with `bot.add_view()`. Result: buttons died after a few minutes and every bot restart killed every open interaction.

User decisions made during planning:
1. **Auth model**: per-view, judged by context — cannot generalize. Public informational commands get no check; personal data gets owner-only; guild config gets permission-based; staff tools use existing staff permission system.
2. **Rollout**: foundation-first PR, then migrate Groups 2–5 in follow-up PRs. This session ships only the foundation and Group 1.

---

## Summary

Shipped the persistence infrastructure plus the 4 trivial views (Group 1). Groups 2–5 (29 remaining view classes) are **deferred to follow-up PRs**, but every convention, helper, and doc needed to execute them is in place.

What now works:
- `BaseView` defaults to `timeout=None` — every existing view that didn't explicitly set a timeout will no longer expire in memory.
- `/moddy`, `/moddy → Attribution`, `/moddy → We Support`, and the back buttons on both sub-views **survive a bot restart**.
- Every custom_id follows the `moddy:<cog>:<view>:<action>` convention.
- A single audit point (`utils/persistent_views.py::_collect_persistent_view_classes`) lists every persistent view class.
- A full developer doc (`docs/PERSISTENT_VIEWS.md`) explains the pattern, custom_id convention, auth table, and a per-view migration cookbook.

---

## Changes Made

### Foundation

- **`cogs/error_handler.py`**
  - `BaseView.__init__` signature changed from `(*args, **kwargs)` to `(*, timeout: Optional[float] = None, **kwargs)`. Default `timeout=None`. Backward compatible: any subclass still passing `timeout=180`/`300` overrides the default.
  - Added `__persistent__: bool = False` class attribute.
  - Added `register_persistent(cls, bot)` classmethod that raises `NotImplementedError` by default; subclasses with `__persistent__ = True` must override.
  - Added docstring block explaining the persistence contract.
  - `BaseModal`: no signature change (discord.py `ui.Modal` already defaults `timeout=None`). Added docstring note explaining modals cannot be persistent.
  - `ErrorView` untouched (already `timeout=None`, contains only URL buttons).

- **`utils/persistent_views.py`** *(new file)*
  - `_collect_persistent_view_classes()` — explicit hand-maintained list of persistent view classes. No auto-discovery (intentional: diff-auditable).
  - `register_all_persistent_views(bot)` — iterates the list, calls `cls.register_persistent(bot)` on each. Catches exceptions per-class so a broken view never aborts bot startup. Logs one `INFO` line with `registered/total` at the end.

- **`bot.py`**
  - In `setup_hook()`, added a call to `register_all_persistent_views(self)` immediately after `await self.load_extensions()`. Ordering is critical: cogs must be loaded so view classes are importable.

### Group 1 — Views migrated

- **`cogs/moddy.py`** — full refactor. All 3 views (`ModdyMainView`, `AttributionView`, `WeSupportView`) are now persistent. Changes:
  - Module-level custom_id constants `_CID_MAIN_ATTRIBUTION`, `_CID_MAIN_WE_SUPPORT`, `_CID_ATTRIBUTION_BACK`, `_CID_WE_SUPPORT_BACK`.
  - Every constructor arg is optional with safe defaults (`bot=None, locale="en-US", user_id=None`) so `cls()` works for shell registration.
  - `ModdyMainView.build_view` guards `self.bot`-dependent stats with `if self.bot is not None:` — the shell builds a minimal content-less version without crashing.
  - All button callbacks re-derive `bot`, `locale`, `user_id` from `interaction.client` / `i18n.get_user_locale(interaction)` / `interaction.user.id`. **Removed** the `interaction.user.id != self.user_id` check per the user's decision for informational commands.
  - Each class has a `register_persistent(cls, bot)` that calls `bot.add_view(cls())` with a 1-line auth comment.
  - `__persistent__ = True` on all three.

- **`cogs/roll.py`** — `RollView` migrated from `ui.LayoutView` to `BaseView`. No buttons → not persistent. Explicit `timeout=180` dropped → inherits `None`. (This View was previously violating the CLAUDE.md rule that all Views must inherit `BaseView`.)

- **`cogs/banner.py`** — Same as `roll.py`: `BannerView` migrated from `ui.LayoutView` → `BaseView`, `timeout=180` dropped.

- **`cogs/avatar.py`** — `AvatarView` already inherited `BaseView`, just dropped the explicit `timeout=180`.

### Documentation

- **`CLAUDE.md`**
  - Added new mandatory rule **8. Persistent Views** with a 5-step quick checklist.
  - Bumped the old "Language" rule to #9.
  - Added `docs/PERSISTENT_VIEWS.md` to the Core References table.

- **`docs/PERSISTENT_VIEWS.md`** *(new file)* — ~230 lines covering:
  - Why persistence, and how discord.py dispatch works.
  - The Moddy contract (full code template).
  - Custom ID convention + what never to encode.
  - Authorization table (public / owner / guild permission / staff rank) with guidance per view type.
  - State reconstruction pattern and the "lost in-flight config edits" UX decision.
  - Registration flow diagram (setup_hook → load_extensions → register_all_persistent_views).
  - **Cookbook: migrating an existing view** — 10-step recipe for Groups 2–5.
  - How to verify a view is persistent (snippet).
  - Deliberate exclusions (Modals, ErrorView, WebhookView).

- **`docs/COMPONENTS_V2.md`** — added a callout below the LayoutView section warning never to inherit `ui.LayoutView` directly, and pointing to `PERSISTENT_VIEWS.md`.

---

## Decisions & Rationale

1. **Default `timeout=None` on `BaseView`** instead of leaving it explicit per-view.
   - Backward compatible (views passing `timeout=X` keep X).
   - Eliminates the "view dies after 5 min" UX regardless of whether a view is registered persistent.
   - No view in the repo needed a timeout > 0 for safety reasons — timeouts existed as discord.py defaults, not deliberate choices.

2. **Explicit hand-maintained registry** in `_collect_persistent_view_classes()` instead of auto-discovering all `BaseView` subclasses with `__persistent__ = True`.
   - One place to read/audit what survives a restart.
   - No surprise registrations from future cogs accidentally flipping the flag.

3. **Shell instances with `bot=None` + guarded `build_view`** instead of `DynamicItem` for Group 1.
   - Simpler for views with no state or fully public state.
   - `DynamicItem` is still the right answer for Groups 3–5 when `user_id` must be encoded in the custom_id. Documented in the cookbook.

4. **Remove `interaction.user.id != self.user_id` check in `/moddy`** rather than encoding user_id in the custom_id.
   - `/moddy` is an informational, mostly-ephemeral command. The current check was mostly cosmetic (ephemeral messages are only visible to invoker anyway).
   - Per user's guidance: auth is per-context, and informational public commands get no check.
   - If the user runs `/moddy incognito:False` (public message), other users can now click the buttons. This is a trivial behavior change, not a security issue — the only outcome is switching tabs in a public embed-like view.

5. **Registration runs after `load_extensions` but before `status_update.start`** in `setup_hook`.
   - Cogs must be loaded so `from cogs.moddy import ...` works.
   - Must run before `on_ready` so the bot is ready to dispatch clicks the instant it comes online.

6. **Failures are logged, not fatal.** A broken persistent view should never prevent startup — it just means clicks on that view's buttons will fall through until the next deploy.

---

## Verification

All Foundation + Group 1 verification steps from the plan were executed:

### Static
- `grep custom_id= cogs/moddy.py` → 4 unique namespaced custom_ids, all matching the module-level constants.
- `grep timeout= cogs/error_handler.py` → `BaseView.__init__` now `timeout: Optional[float] = None`.
- `grep "add_view\|register_persistent\|register_all_persistent_views" bot.py utils/persistent_views.py` → registration wired in both places.

### Runtime (offline smoke test, no live Discord)
- Instantiated `ModdyMainView()`, `AttributionView()`, `WeSupportView()` as shells inside an event loop.
- All three returned `is_persistent() == True` with `timeout=None`.
- `walk_children()` enumeration found all 4 custom_ids registered correctly.
- `register_all_persistent_views(FakeBot())` logged `Persistent views registered (3/3)` and the `ViewStore` contained all 4 `(type, custom_id)` keys under `None`.
- `BaseView(timeout=300)` → `300` (backward compat).
- `BaseView()` → `None` (new default).
- `RollView`, `BannerView`, `AvatarView` all inherit `timeout=None`.

### Not yet verified (requires live bot — do this after merge)
- Click `/moddy` buttons → restart bot → click the same buttons → should still work.
- Click `/moddy → Attribution → Back` flow end-to-end.
- No `CommandInvokeError: Unknown interaction` on restart.
- Expected log line: `INFO:moddy.persistent_views:Persistent views registered (3/3)`.

---

## ⚠️ Follow-up work — Groups 2–5

The foundation is ready. Each group below is **self-contained** and can be shipped as its own PR. Follow `docs/PERSISTENT_VIEWS.md` cookbook for each view. The list is ordered by effort (easiest first).

### Group 2 — External lookup (re-fetch by identifier)

Views whose state is a snapshot fetched from an external API. Encode the identifier in the custom_id and re-fetch on each click.

| View | File | State to encode | Auth | Notes |
|---|---|---|---|---|
| `InviteView` | `cogs/invite.py:20` | invite `code` (string) | Public | `custom_id`s to add: `moddy:invite:server_info:<code>`, `moddy:invite:raw:<code>`. Currently has **no custom_ids at all** (lines 65, 74, 87). Re-fetch invite via Discord API on click. |
| `ServerInfoView` | `cogs/invite.py:375` | invite `code` | Public | Same pattern. |
| `UserInfoView` | `cogs/user.py:24` | target `user_id` | Public | Encode target user id; re-fetch `discord.User` on click. |
| `EmojiView` | `cogs/emoji.py:20` | emoji id | Public | — |
| `EmojiNavigationView` | `cogs/emoji.py:87` | `(guild_id, page)` for pagination | Public | Re-query emoji list from guild on click. |
| `TranslateView` | `cogs/translate.py:22` | **SKIP persistence** | — | Translation results are transient; cost of re-calling DeepL on every click is not worth it. Just set `timeout=None` (inherited) and stop. |

**Estimated effort:** 1 PR, ~3–4 hours.

### Group 3 — User-scoped DB lookup

Views whose state is entirely reconstructable from `user_id` + DB. Use `DynamicItem` subclasses to encode `user_id` in the custom_id regex; verify `interaction.user.id == parsed_user_id` on click.

| View | File | Refactor notes |
|---|---|---|
| `PreferencesView` | `cogs/preferences.py:110` | Fetch prefs from `bot.db.users.get_preferences(user_id)` per click. Custom_id: `moddy:preferences:<action>:<user_id>`. |
| `RemindersManageView` | `cogs/reminder.py:478` | Biggest in this group. State: `user_id`, `show_history` flag, pagination. Buttons: `add`, `edit`, `delete`, `back`, `history`. Drop `self.reminders` / `self.past_reminders` — re-query via `bot.db.reminders.get_active_by_user(user_id)` / `get_history_by_user(user_id)` on every click. Existing non-namespaced custom_ids (`back_btn`, `add_btn`, `edit_btn`) **must be renamed** to `moddy:reminder:<action>:<user_id>` to avoid collisions. |
| `SavedMessagesLibraryView` | `cogs/saved_messages.py:222` | State: `user_id`, `page`, `show_detail`, `detail_msg_id`. Drop `self.messages` entirely — re-query `bot.db.saved_messages.list_for_user(user_id, offset, limit)` per click. Custom_id: `moddy:saved:<action>:<user_id>:<page>`. Note there are associated `BaseModal` subclasses (`AddNoteModal`, `EditNoteModal` at lines 92, 151) — leave them alone, modals can't be persistent. |
| `CaseDetailView` | `cogs/cases_user.py:24` | Encode `case_id`. Fetch case from DB. Owner check: load `case.user_id`, compare to `interaction.user.id`. |
| `SubscriptionView` | `cogs/subscription.py:20` | Subscription status is per-user. Encode `user_id`. Re-fetch Stripe status from DB per click. |

**Estimated effort:** 2 PRs (one for `reminder.py` alone, one for the rest), ~6–8 hours total.

**Gotcha:** `RemindersManageView` uses `AddReminderModal` / `EditReminderModal` which are `BaseModal` subclasses. Modals can still be opened from persistent callbacks — they just can't themselves be persistent. The modal submit handler runs while the bot has in-memory state, so no refactor needed there.

### Group 4 — Guild config panels (biggest refactor)

Views holding a `working_config` with pending unsaved edits. Accepted UX: on restart, the view rebuilds from the DB-saved config and pending edits are lost (documented in `docs/PERSISTENT_VIEWS.md`).

Pattern for all of them:
- Custom_id root: `moddy:config:<module>:<action>:<guild_id>`
- Auth: on click, verify `interaction.guild_id` matches the encoded `guild_id` **and** `interaction.user.guild_permissions.manage_guild` (or the module-specific permission).
- Rebuild: on every click, re-fetch module config from `bot.db.guilds.get_module_config(guild_id, module_name)` and rebuild a fresh view.
- **Drop** `self.working_config`, `self.has_changes` — save directly on each interaction (auto-save) OR accept that pending edits are lost on restart.

| View | File |
|---|---|
| `ConfigMainView` | `cogs/config.py:19` |
| `AutoRestoreRolesConfigView` | `modules/configs/auto_restore_roles_config.py:18` |
| `AutoRoleConfigView` | `modules/configs/auto_role_config.py:18` |
| `InterServerConfigView` | `modules/configs/interserver_config.py:18` |
| `StarboardConfigView` | `modules/configs/starboard_config.py:53` |
| `WelcomeChannelConfigView` | `modules/configs/welcome_channel_config.py:119` |
| `WelcomeDmConfigView` | `modules/configs/welcome_dm_config.py:119` |
| `EditSubscriptionView` | `modules/configs/youtube_notifications_config.py:124` |
| `YoutubeNotificationsConfigView` | `modules/configs/youtube_notifications_config.py:388` |

**Decision needed** before starting Group 4: do we want **auto-save on every interaction** (no working copy at all) or keep the current "edit → save" UX and accept lost edits on restart? I'd recommend **auto-save** — simpler code, matches web dashboards, and pending edits being lost on restart feels buggier than "whoops, I saved too early".

**Estimated effort:** 2–3 PRs (one for `config.py` + `auto_role` / `auto_restore_roles` as warm-up, one for the complex `interserver` + `starboard`, one for `welcome_*` + `youtube_*`), ~8–12 hours total.

### Group 5 — Staff / utility views

| View | File | Notes |
|---|---|---|
| `ServerListView` | `staff/dev_commands.py:28` | Pagination over the bot's guild list. Encode `(user_id, page)`. Staff-only. Fetch guild list from `bot.guilds` on click. |
| `RoleSelectView` | `staff/staff_manager.py:41` | Select menu for staff rank management. Currently `ui.View` — must migrate to `BaseView`. Owner-scoped. |
| `StaffPermissionsManagementView` | `staff/staff_manager.py:212` | Biggest staff view. Encode target staff member `user_id`. Re-check caller's rank on click via `utils/staff_permissions.py`. |
| `CaseSelectionView` | `utils/case_management_views.py:334` | Encode pagination + filter. Staff-only. |
| `StaffHelpView` | `utils/staff_help_view.py:103` | Pagination over staff command list. Staff-only. |

**Estimated effort:** 1 PR, ~4 hours. Use `utils/staff_permissions.py::check_permission(user, permission)` inside every callback to re-auth.

### Explicitly NOT migrating

- **All `BaseModal` subclasses** (~18 of them). Discord doesn't support persistent modals. They already default to `timeout=None` via `discord.ui.Modal`.
- **`cogs/webhook.py::WebhookView`** — shows webhook tokens/URLs which are secret. Not safe to re-render after restart. Keep as-is.
- **`cogs/error_handler.py::ErrorView`** — URL buttons only, already `timeout=None`.

---

## Known Issues

- **None observed during smoke test.** But the full verification plan still requires a live bot restart test (step 2 of the Verification section above), which was not executed in this session because it requires deploying to the dev Railway environment.

- **Potential: custom_id collisions in unmigrated views.** Some Group 2–5 views have existing non-namespaced custom_ids like `"back_btn"`, `"add_btn"`, `"edit_btn"` (see `cogs/reminder.py:536, 581, 590`). These are currently fine because they are not registered as persistent, but the moment their parent views get migrated, **they will collide with each other**. The Group 3 PR that migrates `RemindersManageView` must rename them to `moddy:reminder:<action>:<user_id>`.

- **`staff_manager.py::RoleSelectView` inherits `ui.View`** (not `ui.LayoutView` / `BaseView`). This is a pre-existing CLAUDE.md rule violation. Can be fixed as part of the Group 5 PR.

---

## Files Touched (final list)

Created:
- `utils/persistent_views.py`
- `docs/PERSISTENT_VIEWS.md`
- `docs/sessions/2026-04-09_persistent-views-foundation.md` (this file)

Modified:
- `cogs/error_handler.py` — BaseView signature + persistence hooks + BaseModal docstring
- `bot.py` — register_all_persistent_views call in setup_hook
- `cogs/moddy.py` — full persistence refactor (3 view classes)
- `cogs/roll.py` — migrated to BaseView, dropped explicit timeout
- `cogs/banner.py` — migrated to BaseView, dropped explicit timeout
- `cogs/avatar.py` — dropped explicit timeout (was already BaseView)
- `CLAUDE.md` — new "Persistent Views" mandatory rule + doc pointer
- `docs/COMPONENTS_V2.md` — callout pointing to PERSISTENT_VIEWS.md
