# Session: Starboard Message as a Real Embed (documented exception)

**Date:** 2026-08-07
**Agent:** Claude Code

## Summary

Reworked the Starboard card (again) at the user's explicit request to match
a reference screenshot of a classic Discord embed layout (colored side bar,
author avatar + name header, message text, native timestamp). This is a
**deliberate, documented exception** to CLAUDE.md rule #1 ("Components V2
only, never `discord.Embed()`") — confirmed explicitly with the user before
implementing, since it contradicts the project's default rule.

The previous design (native `MessageReferenceType.forward` + two Components
V2 cards, see `docs/sessions/2026-08-06_starboard-rework.md`) is replaced
entirely by a single message: a content line (reaction count + origin
channel), a `discord.Embed` holding the starred message, and a
"jump to message" link button.

## Changes Made

- `modules/starboard.py`:
  - Removed the forward-message + two-card (title/buttons) design.
  - Added `_build_embed()`: builds a `discord.Embed` with `set_author()`
    (avatar + display name), the message content as description (with the
    hyperlinked verification badge prepended when present), an accent
    colour (`STARBOARD_EMBED_COLOR`), and `timestamp=message.created_at`
    (renders as Discord's native "Today at HH:MM").
  - Added `_find_image_url()`: picks the first image attachment
    (`content_type` or filename extension), falling back to the first bare
    image URL found in the message text, and sets it as `embed.image`.
  - Added `_build_content()`: the top content line (`{emoji} \`{count}\` |
    {channel.mention}`).
  - Added `_StarboardJumpView(discord.ui.View)`: a plain, non-`BaseView`
    view holding only the link button. It cannot be a `BaseView` because
    `BaseView` is `ui.LayoutView` (Components V2), and Discord rejects a
    message combining the `IS_COMPONENTS_V2` flag with a classic embed.
  - `starboard_messages` tracking simplified from a 3-key dict
    (`title`/`forward`/`buttons`) to a single `{original_id: message_id}`
    map, since there's now only one message per entry.
  - Anti-ping: `allowed_mentions=discord.AllowedMentions.none()` on every
    send/edit of the starboard message. Mentions inside the embed
    description were already inert (Discord never notifies from mentions
    inside embeds, only from plain message content), so this specifically
    guards the content line.
- `docs/COMPONENTS_V2.md` — added an "Exception documentée" section
  pointing at `StarboardModule` so the deviation isn't mistaken for drift.
- `docs/PERSISTENT_VIEWS.md` — added `_StarboardJumpView` to "Deliberate
  exclusions" (link-only button, no callback to persist; also explains why
  it isn't a `BaseView`).

## Decisions & Rationale

- Confirmed with the user (via `AskUserQuestion`) whether they wanted a real
  `discord.Embed()` or a Components V2 `ui.Container` with `accent_colour`
  that visually approximates an embed without breaking the project rule.
  The user explicitly chose the real embed ("le 1 un vrai embed
  exceptionnellement").
- Embed `author` fields don't render markdown/links, so the verification
  badge (rule #7) can't sit in `set_author()`'s name — it's prepended as the
  first line of the embed description instead, where markdown/links do
  render.
- Kept the reaction counter and origin-channel link in the message content
  (outside the embed) rather than in the embed, matching the reference
  screenshot's `✨ 214 | #announcements` line and keeping it live-editable
  independently of the embed.
- Only a single image is shown (first attachment or first URL match) —
  Discord embeds only render one inline image regardless.

## Known Issues / Follow-ups

- [ ] If an author's display name or avatar changes after their message was
      starred, the embed is not retroactively refreshed (matches the
      previous design's behaviour — only the reaction count is updated
      live).
- [ ] `tests/test_persistent_views.py` could not be run in this session
      (`discord.py` isn't installed in this environment); reviewed manually
      against `docs/PERSISTENT_VIEWS.md`'s exclusion criteria instead. Worth
      running in CI/a full environment before merge.

---

## Round 2 — badge fix, link previews, reactors button (same day)

Follow-up requested after review of the round-1 PR (#312): the verification
badge wasn't reliably visible, non-direct-image links (e.g. a GIF site's
share page) showed nothing, the top content line was unwanted, and a new
"who reacted" button was requested.

### Changes Made

- `modules/starboard.py`:
  - **Badge fix**: `_build_embed()` now puts `**{display_name}**{badge}` as
    the first line of the embed *description* (previously the badge sat
    alone on its own line, detached from the name). This matches CLAUDE.md
    rule #7's literal `**{name}**{badge}` format — `embed.set_author()`
    can't render markdown/links at all, so the badge could never live
    there; it must be in the description. `_get_author_badge()` also now
    fetches and passes `user_verification_data` (`users.data.verification`)
    to `get_user_verification_badge()`, matching the exact call shape
    already used in `cogs/user.py` (previously omitted — harmless for
    whether the badge shows, but needed for org-name completeness).
  - **Link previews**: `_find_image_url()` → `_find_image_url()` +
    `_fetch_link_preview_image()`. When a message contains a link that
    isn't itself a direct image URL, the bot now does a short (5s timeout,
    200KB cap) `aiohttp` GET and regex-extracts `og:image` /
    `og:image:secure_url` / `twitter:image` from the HTML, so pages like a
    GIF site's share link still show a preview image on the embed. No new
    dependency — plain regex over the fetched HTML (no `bs4`/`lxml` in
    `requirements.txt`).
  - **Content line removed**: `_build_content()` deleted. The starboard
    message is now embed-only (`channel.send(embed=..., view=...)`, no
    `content`). The reaction count moved onto the new reactors button's
    label instead of living in message text.
  - **Reactors button**: added `_StarboardReactorsButton`
    (`discord.ui.DynamicItem`, `template=r"moddy:starboard:reactors:..."`),
    styled like the emoji + count, placed before the jump button in the new
    `_StarboardCardView` (renamed from `_StarboardJumpView`, which now
    holds both buttons). Clicking it fetches the original message, finds
    the matching reaction, and replies ephemerally with the list of users
    who reacted (`AllowedMentions.none()` so listing them doesn't ping).
    Registered for persistence via `StarboardCardPersistence` (a
    `BaseView` marker class that's never instantiated, only used to call
    `bot.add_dynamic_items(_StarboardReactorsButton)` — same pattern as
    `utils/appeal_views.py::AppealPersistence`) and added to
    `utils/persistent_views.py`. Its callback is guarded with
    `report_component_error` (fire-and-forget via `asyncio.create_task`)
    rather than a live view's `on_error`, since persistent `DynamicItem`s
    dispatched via `add_dynamic_items` have no live view instance — same
    reasoning as the existing appeal buttons.
  - `_update_starboard()` now edits the message's `view` (rebuilding the
    reactors button with the new count) instead of editing `content`, since
    there's no content line left to hold the count.
- `locales/{fr,en-US,es-ES,pt-BR,de}.json` — added
  `modules.starboard.message.reactors.{title,empty,not_found,more}`.
- `docs/PERSISTENT_VIEWS.md`, `docs/COMPONENTS_V2.md` — updated the
  `_StarboardJumpView` references to `_StarboardCardView` and documented
  the reactors `DynamicItem`'s persistence/error-handling approach.

### Decisions & Rationale

- Considered adding a new `BaseClassicView(ui.View)` base class (mirroring
  `BaseView`'s `on_error`) for views that must coexist with an embed, but
  found the codebase already solves this exact problem for `DynamicItem`s
  dispatched without a live view (`report_component_error` +
  `utils/appeal_views.py`'s `_guarded` pattern) — used that instead of
  introducing an unused abstraction.
- The link-preview fetch is a plain `aiohttp` GET + regex, not a full HTML
  parser: adding `bs4`/`lxml` for one `<meta>` tag felt disproportionate,
  and the project doesn't fetch/parse arbitrary third-party HTML anywhere
  else, so a minimal, defensively-bounded (timeout + byte cap,
  `Content-Type` check) implementation was preferred over a new dependency.

### Known Issues / Follow-ups

- [ ] The link-preview fetch has no rate limiting beyond the existing
      `reaction_count` threshold gate (a message needs enough reactions to
      reach the starboard before we ever fetch its link). Acceptable given
      the threshold, but worth revisiting if abused.
- [ ] `tests/test_persistent_views.py` still could not be run in this
      environment (no `discord.py` installed) — please run in CI before
      merging, especially to confirm the new `DynamicItem` template doesn't
      collide with existing custom_ids.
