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
