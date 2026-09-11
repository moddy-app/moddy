# 2026-09-11 — Tickets: transcripts, closure detection, ratings

Three features on the Tickets module, plus two behaviours around members
leaving and coming back. All three of the first are optional and independently
switchable; the last two are not.

## What was asked

1. **Transcripts** — every ticket archivable on closing, the link given in the
   ticket log and in the closing DM, stored in the database for the backend to
   serve to the dashboard. As storage-cheap as possible.
2. **Closure detection** — spot a conversation that has run its course with
   embeddings and offer to close it, across all five supported languages,
   *without* letting the offer hand out a permission the clicker lacks.
3. **Ratings** — the member rates their handling out of five, with adjectives
   rather than numbers, optionally a comment, from three moments; plus a
   command showing per-staff volume and averages.

Mid-session, two more: when the author leaves the server, offer to close their
ticket (not optional); when they come back, put them back into it.

## Decisions worth recording

**DiscordChatExporter was asked for, and not used.** It is a .NET CLI. The repo
has no Dockerfile (Nixpacks, `python main.py`), and `docs/RAILWAY.md` is
explicit that resident memory is ~93% of the bill. Using it would have meant a
custom image with a second language runtime, a subprocess per closure, the bot
token in argv, and a full REST re-fetch of every history — to produce HTML we
would then have thrown away, since the dashboard renders the archive itself.
The user was asked and answered "whatever works, as long as it is autonomous",
so the exporter is native Python over `channel.history()`.

**A transcript is not joined to `tickets`.** That row is `DELETE`d the moment
its Discord channel disappears (`on_guild_channel_delete` → `forget_channel`),
so an archive with a foreign key on it would die with the thing it archives.
Everything the dashboard needs — guild, number, category *name*, owner,
participants, claimer, closer — is snapshotted at closing time instead. The
duplication is the feature. The same reasoning covers `ticket_ratings`, and it
is what lets the DM's "leave a review" button still work a week after the
channel was tidied away.

**One row per closure, not per channel.** Reopening changes nothing; the next
closure writes a second archive. Every link already handed out keeps showing
what it showed.

**Storage.** Short keys with empty fields omitted, authors stored once in their
own table, zstd level 19 over the result. A 30-message ticket lands around 300
bytes. Attachments are referenced by CDN url and never downloaded — an archiver
that copies files is a storage bill, not an archive. `codec` is stored per row,
so a deployment without the `zstandard` wheel falls back to stdlib `zlib` and
the two coexist with no migration.

**The Components V2 trap.** Moddy's own cards carry no `content` and no
`embeds`; all their text is in the component tree. Without
`extract_component_text` every card Moddy wrote would have archived blank.
Covered by a test.

**Detection is keyword-first, embedding-second.** This runs on every message of
every ticket of every server, so: an in-memory set of open ticket channel ids
answers "is this a ticket" with no query; ~90 multilingual roots rule out nearly
everything for free; structural checks (length, `?`, mentions, ticket age) are
free too; a 20 s debounce settles the conversation; only then is anything
embedded. The engine, its cosine maths and its LRU+TTL cache are reused from
`automod/` rather than rewritten — only the corpus and the threshold are new.
The threshold is 0.62 against automod's 0.45: a false positive there routes to a
second model, a false positive here puts a card in front of a whole channel.

**Suggesting is not granting.** The suggestion card has one action button whose
callback re-derives the *clicker's* permissions: `close` closes, `view` only
produces a close request, neither produces a refusal. Both branches go through
the service, the only thing in the codebase that checks a ticket permission.
Pinned by `TestClosureSuggestionAuthorisation`.

**Discord shaped the rating flow.** `send_modal` must be the *first* response to
an interaction, so one click cannot both close a ticket and open a form. The
two in-channel triggers therefore close the ticket and answer with an ephemeral
card carrying a "Leave a review" button; the modal opens on that second
interaction. One extra click, imposed by the platform — and closing never
depends on a member finishing a form.

**The DM button is a `DynamicItem`, on purpose.** A DM has no ticket channel to
derive identity from, and the channel may be gone by the time it is clicked. It
carries the transcript key and resolves the guild, the opener and the staff from
`ticket_transcripts`. Auth is "the clicker is the opener", re-read on every
click.

**Ratings are adjectives.** "3/5" means nothing consistent across people;
"Correct" does. Five adjectives in five languages, in a `RadioGroup` — which
exposes `.value`, not `.values`; there is a test for that specifically.

**Staff picker, not `UserSelect`.** A `UserSelect` cannot be pre-filled, and
nobody should be able to rate a member of the server who never touched their
ticket. A plain `Select` of the people who actually did, pre-selected on the
claimer else the closer, plus "nobody in particular" as a real answer.

**`/ticket stats` orders by volume, not by average.** A single 5/5 must not
outrank fifty tickets. Under three reviews the average is flagged rather than
ranked on. Read with SQL aggregates, not `bot.stats`, which is built for bounded
counters and rightly refuses a user id as a dimension. Volume is counted from
transcripts so that tidying a category away does not erase its staff's workload.

**Ticket logs are a module setting, not a `serverlogs/` category.** That
registry catalogues the 163 *Discord* events; a ticket closing is an application
event of Moddy's own making. The calque is AltGuard's `log_channel_id`.

**Settings are module-wide, not per-category.** `claim_enabled` genuinely
differs per workflow; a server archives its tickets or it does not. It also
gives one place to switch a feature off, which is what "optional" has to mean.
Closure detection defaults to **off** because it spends AI quota.

**Leaving offers, it does not close.** A departure often is the end of a ticket
— but a report still has to be acted on and a refund still has to be recorded.
So the card says what happened and its button goes through `close_ticket` like
everything else. Only the tickets the leaver *opened* are announced in: a ticket
does not lose its purpose because a bystander left. Coming back rebuilds access
through `sync_permissions` rather than patching an overwrite, so a claim lock or
an escalation that happened meanwhile is honoured.

## Files

**New** — `db/repositories/ticket_transcripts.py`,
`db/repositories/ticket_ratings.py`, `services/ticket_transcript_service.py`,
`services/ticket_closure_detector.py`,
`services/data/ticket_closure_references.json`, `utils/compression.py`,
`utils/ticket_rating_views.py`, `utils/ticket_stats_views.py`,
`tests/test_ticket_transcripts.py`, `tests/test_ticket_ratings.py`,
`docs/TICKETS_INTEGRATION.md`.

**Modified** — `db/base.py` (3 tables + indexes + mixins),
`db/repositories/tickets.py` (two queries), `modules/tickets.py` (settings,
`PERM_STATS`), `modules/configs/tickets_config.py` (settings screen),
`services/ticket_service.py` (capture, ticket log, open-channel cache,
membership), `utils/ticket_views.py` (transcript links, suggestion card,
owner-left card, the shared close tail), `cogs/tickets.py` (`/ticket stats`,
message + membership listeners, retention loop), `bot.py`,
`utils/persistent_views.py`, `stats/registry.py`, `requirements.txt`,
the 5 `locales/*.json`, the 32 `locales/commands/*.json`, `docs/TICKETS.md`,
`docs/PERSISTENT_VIEWS.md`, `docs/RAILWAY.md`, `CLAUDE.md`,
`tests/test_tickets.py`.

## Verification

`pytest tests/ -q` — 1948 passed. That includes 81 new rating tests and 68 new
transcript/detection/membership tests, and the pre-existing
`test_persistent_views.py` sweep now covering the three new views and the new
dynamic item.

Also checked by hand: every new card renders in all five locales, and every
touched module imports under a real `bot.py` load.

## Follow-ups

- The dashboard side of `GET /transcripts/<key>` does not exist yet; the
  contract it has to satisfy — including **withholding `staff_thread` from the
  ticket's own author** — is `docs/TICKETS_INTEGRATION.md`.
- Closure detection has no telemetry on its own accuracy beyond
  `ticket.closure_suggested`. If false positives show up, the honest fix is an
  annotation loop like automod's `eval/`, not a threshold nudged by feel.
- The ticket log only covers closure. Opening, claiming and escalation could
  join it later; they were deliberately left out of this pass.
