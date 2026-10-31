# CLAUDE.md — Moddy Bot AI Agent Guide

> This file is the primary entry point for AI agents (Claude Code, Copilot, etc.) working on the Moddy project.
> It provides a complete overview of the project architecture, conventions, and pointers to detailed documentation.

--- 

## Project Overview 
 
**Moddy** is a production-grade **Discord bot** for community moderation and utilities, built in Python and hosted on **Railway**

| Key Info | Value |
|---|---|
| Language | Python 3.11+ |
| Framework | discord.py 2.6+ (with Components V2) |
| Database | PostgreSQL (asyncpg) |
| HTTP Server | FastAPI + Uvicorn (health checks + internal API) |
| Hosting | Railway (containerized) |
| i18n | JSON-based (French, English, Spanish, Portuguese, German) |
| Error Tracking | Centralized handler + database logging |
| License | CC BY-NC-SA 4.0 |

---

## Project Structure

```
moddy/
├── main.py                    # Entry point — launches bot + FastAPI server
├── bot.py                     # ModdyBot class — core bot logic and events
├── config.py                  # Configuration from Railway env vars
├── database.py                # Database backward-compat shim
├── moddy/                      # Internal discord.py-compatible framework
│
├── cogs/                      # Discord slash commands and event handlers
│   ├── error_handler.py       #   Centralized error handler (38KB)
│   ├── config.py              #   /config command (module configuration)
│   ├── module_events.py       #   Event handlers for modules
│   ├── team_link_events.py    #   Gateway wiring + boot recovery for the linking window
│   ├── server_tag_events.py   #   Gateway hooks: server tag text + Auto Role tag roles sync
│   ├── invite.py              #   /invite command
│   ├── bug_report.py          #   /bug-report command (Modal V2 → team channel)
│   ├── brocoli_chat.py        #   Brocoli in a channel (dev-guild gated, SSE → Components V2)
│   ├── emoji.py               #   /emoji (one emoji, or several pasted from a message)
│   ├── reminder.py            #   /reminder command
│   ├── bump_reminder.py       #   Bump Reminder listener + 30s sweeper loop + directory-join offer
│   ├── bump_prospects.py      #   Bump prospects: scan triggers + follow-up loop (internal, team-side)
│   ├── member_applications.py #   Discord join requests: gateway parser hook + reconciliation poll
│   ├── stats_events.py        #   Statistics emitters (commands, members, guild joins)
│   ├── saved_messages.py      #   Message bookmarking
│   ├── translate.py           #   /translate (DeepL)
│   ├── announcement_translation.py #  Support-server announcements translated once → flag buttons
│   ├── announcement_verification.py # Manager/Dev authenticate announcements → check_circle reaction + Translate button under relayed copies
│   ├── text_tools.py          #   /fix, /rephrase, /summarize (OpenAI, Modal V2 + context menus)
│   ├── voice_transcription.py #   "Transcribe" context menu (Groq Whisper; images → OCR)
│   ├── ocr.py                 #   /ocr (Google Vision document OCR)
│   ├── ai_detector.py         #   /ai-check (C2PA + generation metadata, image or message link)
│   ├── tools_text.py          #   /define, /tldr, /regex, /diff, /textstats (docs/features/TOOLS_TEXT.md)
│   ├── tools_security.py      #   /pwned, /password, /uuid, /hash, /jwt, /color
│   ├── tools_web.py           #   /whois, /dns, /ssl, /subdomains, /link-check
│   ├── tools_lookup.py        #   /status, /company, /username
│   ├── tools_media.py         #   /image (resize, compress, convert, remove background), /convert
│   ├── tools_inspect.py       #   /sauce (SauceNAO + trace.moe; no menu: the 5 message menus are taken)
│   ├── music_links.py         #   /music (Spotify → Deezer / Apple Music / YouTube Music)
│   ├── webhook.py             #   Webhook management
│   ├── logs.py                #   Advanced server logs — Discord wiring only
│   ├── social_notifications.py #  Social notifications dispatch + feeds service wiring
│   ├── altguard.py            #   AltGuard verdicts, membership events, /altguard verify|unverify
│   ├── automod_profiles.py    #   Automod member profiles: periodic hold sweep (renew / lift timeouts)
│   ├── tickets.py             #   /ticket group (module-gated) + channel cleanup + staff-thread guard
│   ├── interserver_commands.py #  Inter-server commands
│   ├── ping.py, user.py, avatar.py, banner.py, roll.py, moddy.py
│   ├── subscription.py        #   Premium features
│   ├── preferences.py         #   /preferences (timezone, Discord theme → icon colour, forced language)
│   ├── blacklist_check.py     #   Global suspension gate for prefix commands (cases system)
│   ├── cases_user.py          #   Personal cases browser (/mycases — all servers, filters, read-only)
│   ├── cases_server.py        #   Server cases browser (/cases — guild scope, mod actions, perms)
│   ├── case_sync.py           #   Auto-records guild sanctions as cases (audit log)
│   ├── auto_restore_roles_commands.py
│   ├── cog_manager.py         #   Hot-reload / disable cogs
│   ├── console_logger.py      #   Console logging
│   ├── command_logger.py      #   Non-staff command usage → technical webhook logs
│   ├── dev_logger.py          #   Dev logging
│   ├── dev_tools.py           #   Developer tools
│   └── subscription.py        #   /subscription command (user subscription status)
│
├── modules/                   # Server-level configurable features
│   ├── module_manager.py      #   ModuleManager + ModuleBase class
│   ├── welcome_channel.py     #   Welcome messages in channels (up to 5, Components V2)
│   ├── welcome_dm.py          #   Welcome DMs to new members (up to 3, Components V2)
│   ├── auto_role.py           #   Auto role assignment (join roles + server tag roles)
│   ├── auto_restore_roles.py  #   Role restoration on rejoin
│   ├── starboard.py           #   Reaction-based starboard
│   ├── adaptive_slowmode.py   #   Adaptive slowmode (EWMA + hysteresis)
│   ├── interserver.py         #   Inter-server message relay
│   ├── social_notifications.py #  Social notifications (via moddy-feeds service)
│   ├── bump_reminder.py       #   Bump reminders (Disboard, DiscordL, French.gg…)
│   ├── altguard.py            #   AltGuard anti multi-account verification gate
│   ├── member_applications.py #   Member Applications (review Discord "Apply to Join" in a channel)
│   ├── tickets.py             #   Tickets (panels, categories, permissions, claim)
│   ├── automod_ai.py          #   Automod (applies decisions, cases+evidence, scalable features,
│   │                          #   member profiles: avatar / banner / names via apply_profile_decision)
│   ├── bot_customization.py   #   Bot identity per guild (nick/avatar/banner/bio + name style)
│   ├── voice_transcription.py #   Voice message transcription (button or automatic)
│   ├── ai_detector.py         #   AI Detector: replies under C2PA-attested images (DEFAULT_ENABLED)
│   ├── logs.py                #   Advanced server logs (config + routing, 163 events)
│   ├── music_links.py         #   Music links: alternatives under Spotify links (DEFAULT_ENABLED)
│   ├── translate_button.py    #   Translate button under foreign-language messages (opt-in)
│   └── configs/               #   Components V2 config UIs per module
│       ├── server_settings_config.py      # Server-wide settings (language)
│       ├── adaptive_slowmode_config.py
│       ├── altguard_config.py             # AltGuard gate (channel, roles, logs)
│       ├── member_applications_config.py  # Member Applications (channel, roles, preset reasons Modal V2)
│       ├── social_notifications_config.py
│       ├── bump_reminder_config.py        # Bump reminders list + Modal V2
│       ├── automod_ai_config.py           # Automod: first-run wizard (+ Skip), home (instant save, generic state, delete)
│       ├── automod_ai_sanctions_config.py # Automod: sanctions list, one infraction (multi-actions, timeout Modal V2, approval), custom infractions
│       ├── automod_ai_advanced_config.py  # Automod: monitoring, exemptions, advanced (draft + Save/Cancel)
│       ├── automod_ai_common.py           # Automod panel: normalize, drafts (pending edits), effective wording, navigation
│       ├── automod_ai_precedents_view.py  # Learned-precedents browser (persistent dynamic items)
│       ├── welcome_channel_config.py      # Welcome messages list + add/manage (Modal V2)
│       ├── welcome_dm_config.py           # Welcome DMs list + add/manage (Modal V2)
│       ├── tickets_config.py              # Tickets: panel list (root screen)
│       ├── tickets_panel_config.py        # Tickets: one panel (channel, style, categories)
│       ├── tickets_category_config.py     # Tickets: one category + its per-role permissions
│       │                                  # (tickets_config.py also holds the module-wide
│       │                                  #  settings screen: transcripts, detection, ratings, logs)
│       ├── voice_transcription_config.py  # Voice transcription (status, mode, channels)
│       ├── ai_detector_config.py          # AI Detector (announced verdicts, ignored channels)
│       ├── bot_customization_config.py    # Bot customization (identity Modal V2 + name style)
│       ├── logs_config.py                 # Server logs (categories, events, options)
│
├── serverlogs/                # Advanced server logs (the log messages themselves)
│   ├── registry.py            #   Single catalogue of categories + events (source of truth)
│   ├── renderer.py            #   The only place deciding what a log looks like + formatters
│   ├── dispatcher.py          #   Per-channel webhooks, 10-embed batching, bounded queues
│   ├── audit.py               #   Gateway-fed audit-log cache ("who did this", no REST call)
│   ├── service.py             #   Listener API: open() / submit() / executor()
│   └── listeners/             #   One builder module per category + _diff.py (before/after)
│                              #   members, messages, channels, roles, guild, threads, voice,
│                              #   invites, assets, scheduled_events, stage, polls,
│                              #   integrations, automod, moderation
│
├── tools/                     # Utility tools core (pure; no Discord, no DB) — docs/features/TOOLS.md
│   ├── wiktionary.py, regex_test.py, diffing.py, textstats.py, text_escape.py   # text
│   ├── hashing.py, generators.py, jwt_decode.py, color.py, data/                  # security & dev
│   ├── domain/ (rdap, doh, tls, crtsh, names), linkcheck.py                       # domains & links
│   ├── status_registry.py, company.py, whatsmyname.py                            # lookups
│   ├── image_ops.py, bg_removal.py (u2netp via onnxruntime), convert/, media_errors.py
│   ├── sauce.py                                                                  # inspection
│   └── music.py, langdetect.py (py3langid)                                       # modules
│
├── stats/                     # Statistics (aggregate before writing)
│   ├── registry.py            #   Every metric declared once (no migration to add one)
│   ├── service.py             #   bot.stats — incr()/observe_unique(), 60s flush to PG
│   └── rollup.py              #   Daily snapshots, hourly→daily ageing, partition purge
│
├── notifications/             # Centralized notifications (EVERY DM goes through it)
│   ├── models.py              #   Uniform payload + source + service registry + hashing
│   ├── render.py              #   Payload → Components V2 + `sent by` attribution line
│   └── service.py             #   NotificationService (bot.notifications)
│
├── bumpreminder/              # Bump detection core (pure; no Discord, no DB)
│   ├── registry.py            #   The 7 supported directories + their markers
│   ├── detect.py              #   Success/failure funnel, next-bump time, intervals
│   └── prospects.py           #   Bump prospects: directory present, no reminder bot
│
├── aidetector/                # AI image detector core (pure; no Discord, no DB)
│   ├── formats.py             #   Magic-byte sniffing + cheap C2PA marker test
│   ├── png_text.py            #   Unsigned SD / ComfyUI parameters in PNG text chunks
│   ├── manifest.py            #   Trusted C2PA store -> verdict + privacy whitelist
│   ├── verify.py              #   c2pa-python read (trust list, EKUs, network off)
│   └── analysis.py            #   One image -> Analysis (verdict or reason)
│
├── automod/                   # Automod DETECTION pipeline (decides only; no side effects)
│   ├── infractions.py         #   Infraction catalogue (single source of truth) + per-server `sanctions` schema/validation
│   ├── custom_infractions.py  #   Server-defined infractions: schema, templates, keywords, routing refs
│   ├── image_hash.py          #   Pillow-only pHash/dHash, safe decode, known-hash index
│   ├── scam_anchors.py        #   OCR-noise-tolerant scam anchor scorer (routes to nano)
│   ├── image_policy.py        #   SafeSearch → verdict, monthly pacing, scam pre-rules
│   ├── engine.py              #   Shared per-bot orchestrator (funnel entry)
│   ├── presets.py             #   Wizard protection levels (Light/Balanced/Strict) -> full config
│   ├── prefiltre.py / triviaux.py / blocklist.py / embeddings.py / nano.py
│   ├── relations.py           #   Relationship graph + target-reaction signals (familiarity)
│   ├── routing.py             #   Difficulty router (nano→mini) + heavy-sanction confirm (S6)
│   ├── precedents.py          #   Server precedents matcher (jurisprudence RAG, pure) (S7)
│   ├── normalize.py / injection.py / rules_check.py / schemas.py / constants.py
│   ├── bareme.py              #   Deterministic sanction scale (cran ladder + recidivism, per-server overrides)
│   ├── actions.py             #   Multi-action vocabulary (delete/warn/timeout/kick/ban), exact timeouts parse/format, cran bridge
│   ├── approvals.py           #   Moderator-approval snapshots, permissions, expiry (pure)
│   ├── cache.py               #   LRU+TTL score cache (embedding de-duplication)
│   ├── eval/                  #   Regression harness (golden set, offline runner, shadow annotations)
│   │   ├── golden.jsonl / fixtures.json / golden_baseline.json
│   │   ├── run.py             #     Offline runner (--replay/--live, CI gate on faux_positif_reel)
│   │   └── import_candidates.py #   annotated candidates → golden JSONL (make eval-import)
│   └── data/references.json   #   Embedding reference phrases (FR + EN)
│
├── staff/                     # Staff/dev command system (message + slash)
│   ├── base.py                #   StaffCommandsCog (auto-delete tracking)
│   ├── staff_commands.py      #   Entrypoint extension for the new framework
│   ├── framework/             #   Scalable dual message+slash engine (see docs)
│   │   ├── cog.py             #     Dispatcher: routing, perms, logging, incognito
│   │   ├── command.py         #     StaffCommand base + SlashOption + registry
│   │   ├── context.py         #     StaffContext (unifies message + slash)
│   │   ├── registry.py        #     Discovery + dynamic /dev,/team… group build
│   │   ├── design.py          #     Standardized Components V2 panels (accents)
│   │   └── parsing.py         #     Arg helpers (user/guild id)
│   ├── commands/dev/          #   /dev commands (one file each)
│   ├── commands/team/         #   /team commands (incl. help, /team user — full
│   │                          #   user card: account, billing, cases, notifications;
│   │                          #   /team role, /team access, /team ticket — the
│   │                          #   two Moddy Team linked roles (team + manager),
│   │                          #   /team config — a server's /config panel, staff-only,
│   │                          #   permission requests and staff tickets,
│   │                          #   see docs/features/LINKED_ROLES.md)
│   ├── commands/mod/          #   /mod commands + case/, global/ and altguard/ sub-groups
│   │                          #   (incl. /mod notif — notification lookup by uuid)
│   ├── commands/com/          #   /com commands (/com send — notification to a user,
│   │                          #   a server, or thousands of them; /com beta — the
│   │                          #   one-off beta-launch campaign to server owners)
│   ├── commands/manage/       #   /manage commands (staff panel, badge, redirect/, banner/…)
│   ├── support_commands.py    #   sup. commands — legacy (not yet migrated)
│   └── communication_commands.py  # com. commands — legacy (not yet migrated)
│
├── db/                        # Database layer (repository pattern)
│   ├── base.py                #   ModdyDatabase core class
│   └── repositories/          #   Specialized repos
│       ├── users.py, guilds.py, staff.py, errors.py
│       ├── reminders.py, saved_messages.py, saved_roles.py
│       ├── moderation.py, interserver.py, attributes.py
│       ├── altguard.py          #   AltGuard verifications + gate state (altguard_*)
│       ├── tickets.py           #   Live ticket state incl. claim (tickets table)
│       ├── ticket_transcripts.py #  Archived ticket conversations, compressed (ticket_transcripts)
│       ├── ticket_ratings.py    #   Ticket handling ratings + staff aggregates (ticket_ratings)
│       ├── appeals.py           #   Automod sanction appeals (case_appeals)
│       ├── enforcements.py      #   Global sanction appeal countdowns (case_enforcements)
│       ├── eval_candidates.py   #   Automod eval/annotation corpus (automod_eval_candidates)
│       ├── precedents.py        #   Automod server precedents (automod_precedents, RAG)
│       ├── automod_learning.py  #   Automod labeling queue + learned hashes/references/terms
│       ├── automod_profiles.py  #   Automod profile holds (automod_profile_holds)
│       ├── automod_approvals.py #   Automod pending moderator approvals (automod_pending_approvals)
│       ├── token_alerts.py, token_secrets.py
│       ├── subscription.py    #   Subscription read-only queries (incl. is_guild_premium)
│       ├── notifications.py   #   Notifications, deliveries, abuse reports
│       ├── support_requests.py #  Bug reports / config-help requests + their exchange
│       ├── social.py          #   Social notifications subscriptions
│       ├── bump.py            #   Pending bump reminders (bump_reminders)
│       ├── bump_prospects.py  #   Servers bumping without a reminder (bump_prospects)
│       ├── member_applications.py # Review cards of Discord join requests (member_applications)
│       ├── announcement_translations.py # Stored announcement translations
│       ├── announcement_verifications.py # Authenticated announcements + their relayed copies
│       ├── stats.py           #   Statistics: counters, snapshots, guild lifecycle,
│       │                      #   acquisition (guild_installs), partitions
│       └── _utils.py
│
├── utils/                     # Utility modules
│   ├── bot_permissions.py     #   Bot permission checks (baseline, guild + channel level, all gaps at once)
│   ├── i18n.py                #   Internationalization system (honours the /preferences language override)
│   ├── loop_monitor.py        #   Event-loop lag monitor + watchdog stack dump + late-interaction logging (10062 diagnosis), notifies stalls > 2.5 s
│   ├── errors.py              #   Expected errors: ModdyError family + build_error_card (one look for every non-bug error)
│   ├── interaction_guard.py   #   Auto-acknowledges interactions unanswered after ~2 s (defer + response shims), see docs/guides/ERROR_HANDLING.md
│   ├── user_preferences.py    #   Per-user prefs cache (icon theme + language), loaded at startup
│   ├── icon_theme.py          #   Light-theme icon swap: rewrites outgoing payloads (white → *_dark twins)
│   ├── guild_language.py      #   Server language (one setting, every module)
│   ├── guild_tags.py          #   Server tags: does a server have one, is a member wearing it
│   ├── command_translator.py  #   Slash command name/description localization (32 locales)
│   ├── emojis.py              #   Emoji constants
│   ├── components_v2.py       #   V2 helper functions (create_error_message, etc.)
│   ├── staff_permissions.py   #   Permission system
│   ├── subscription.py        #   Subscription helper (is_subscribed, is_guild_premium…)
│   ├── staff_logger.py        #   Staff action logging (also feeds technical webhook logs)
│   ├── tech_logger.py         #   Technical staff logs via webhooks (Components V2, per-event channels)
│   ├── staff_role_permissions.py
│   ├── staff_help_view.py
│   ├── case_management_views.py #  Cases Views/Modals (create, sanction, comment…) — staff
│   ├── cases_views.py         #   /cases & /mycases browser (CasesBrowserView, persistent)
│   ├── altguard_views.py      #   AltGuard panel (persistent), consent Modal V2, link + log cards
│   ├── moddy_team_role.py     #   The two Moddy Team roles: kinds, find/create/remember, linked state
│   ├── team_access_views.py   #   /team access: permission picker + admin request card (persistent)
│   ├── staff_config_views.py  #   t.config: launcher card + staff key into /config (private panels only)
│   ├── staff_flex.py          #   Staff flex card (t.flex) + /user Flex button in DMs (persistent, public answer)
│   ├── automod_shadow_views.py #  Automod shadow-mode (dry_run) SIMULATION card + annotation buttons (persistent)
│   ├── automod_approval_views.py # Automod alert card (applied vs failed actions) + moderator Approve/Reject buttons (persistent)
│   ├── automod_label_views.py #   Automod team labeling card, labels/category/terms (persistent) + Modal V2
│   ├── ocr_views.py           #   /ocr cards (loading, result, error)
│   ├── ai_detector_views.py   #   AI detector verdict card, details panel, persistent button
│   ├── sanction_reversal.py   #   Undo the Discord side of a sanction (appeals + labeling queue)
│   ├── automod_render.py      #   Shared automod card helpers (barème breakdown, sanction name/accent)
│   ├── notification_views.py  #   Notification abuse-report review panels (staff side)
│   ├── support_request_views.py # Support requests: staff card, reply DM, modals, buttons
│   ├── install_welcome.py     #   DM sent to whoever adds Moddy to a server
│   ├── beta_announcement.py   #   Beta-launch campaign card (temporary — see docs)
│   ├── ticket_views.py        #   Ticket panel, ticket message, cards, claim, participants modal
│   ├── ticket_rating_views.py #   Ticket rating Modal V2 + persistent DM "leave a review" button
│   ├── ticket_stats_views.py  #   /ticket stats cards (team leaderboard, one staffer)
│   ├── compression.py         #   zstd (fallback zlib) for stored blobs — one codec decision
│   ├── transcription_views.py #   Voice transcription cards + persistent Transcribe button
│   ├── bump_views.py          #   Bump thank-you + reminder cards, opt-in button
│   ├── bump_prospect_views.py #   Bump prospect team card (Invite/Claim/Close, persistent) + scan summary
│   ├── member_application_views.py # Application review card, Approve/Reject (persistent), reject Modal V2
│   ├── appeal_views.py        #   Automod appeal UI (DM buttons + reviewer panels, persistent)
│   ├── expiration_views.py    #   Sanction-expiration DM (unban/unmute/unwarn + invite)
│   ├── invites.py             #   Shared guild invite creation (appeals, expirations)
│   ├── members.py             #   Member lookup without the startup chunk
│   │                          #   (get_or_fetch_member / fetch_all_members)
│   ├── moderation_cases.py    #   Cases domain model + enums + reference gen
│   ├── global_sanctions.py    #   Global (Moddy-team) sanction levels: warn/limited/suspended
│   ├── global_sanction_views.py #  Global sanction UI (notice DMs, staff panels, Modals V2)
│   ├── embeds.py
│   ├── announcement_setup.py
│   ├── task_signature.py      #   moddy:tasks HMAC verification (signature, freshness, anti-replay)
│   ├── brocoli_signature.py   #   HMAC identity assertions sent to the backend's /ai surface
│   ├── brocoli_views.py       #   Brocoli channel cards + persistent confirmation buttons
│   ├── incognito.py
│   ├── tool_card.py           #   Shared tool card (ToolCard, source_line, loading/error/expired, save_state)
│   ├── tool_views_registry.py #   Feeds every tools_*_views PERSISTENT_VIEWS / DYNAMIC_ITEM_CASES
│   ├── tools_{text,security,web,lookup,media,inspect}_views.py # Tool cards + persistent items
│   └── music_links_views.py, translate_button_views.py         # Module replies + persistent items
│
├── gateway/                   # Centralized API gateway (ALL external API calls go here)
│   ├── __init__.py            #   Gateway class (bot.gateway) — public surface
│   ├── config.py              #   GatewayConfig (from env vars)
│   ├── errors.py              #   Typed error hierarchy
│   ├── spec.py                #   CallSpec, QuotaTarget, QuotaScope
│   ├── quota.py               #   QuotaManager (Redis daily counters + PG limits)
│   ├── ratelimit.py           #   RateLimiter (provider-account windows: rpm, audio sec/h…)
│   ├── resilience.py          #   CircuitBreaker + retry/backoff
│   ├── logger.py              #   Buffered PG logging + staff webhook per call
│   ├── executor.py            #   GatewayExecutor (single execution path)
│   ├── adapters/              #   Provider adapters
│   │   ├── openai.py          #     embed + chat
│   │   ├── deepl.py           #     translate
│   │   ├── groq.py            #     transcribe (whisper-large-v3-turbo)
│   │   ├── google_vision.py   #     SafeSearch + document OCR (monthly free tier)
│   │   ├── saucenao.py, tracemoe.py #   /sauce
│   │   ├── virustotal.py, urlscan.py #  /link-check
│   │   └── cloudconvert.py    #     /convert (office documents, ebooks)
│   └── clients/               #   High-level clients
│       ├── ai.py              #     bot.gateway.ai
│       ├── translation.py     #     bot.gateway.translation
│       ├── transcription.py   #     bot.gateway.transcription
│       ├── vision.py          #     bot.gateway.vision
│       ├── image_search.py    #     bot.gateway.saucenao / tracemoe
│       ├── web_security.py    #     bot.gateway.virustotal / urlscan
│       └── conversion.py      #     bot.gateway.cloudconvert
│
├── services/                  # External service clients
│   ├── altguard_client.py     #   AltGuard service client (HTTP + altguard:* Pub/Sub)
│   ├── altguard_invites.py    #   Invite-use tracker → inviter_id sent with the AltGuard token call
│   ├── brocoli_client.py      #   Brocoli HTTP + SSE client (backend /ai surface)
│   ├── staff_events.py        #   Staff rank changes -> backend (moddy:staff, linked roles)
│   ├── backend_client.py      #   Backend HTTP client
│   ├── feeds_client.py        #   moddy-feeds Redis client (social notifications)
│   ├── case_service.py        #   Scalable sanction→case entry point (source registry)
│   ├── expiration_notifier.py #   Expired sanctions: lift the ban + DM the subject (invite)
│   ├── invoice_notifier.py    #   Stripe invoices (notify_invoice): one DM per invoice, trials incl.
│   ├── global_sanction_service.py # Global sanctions: grouped cases, notice DM, 48h countdown, Redis
│   ├── appeal_service.py      #   Automod sanction appeals (server / Moddy team, binding)
│   ├── precedent_service.py   #   Automod server precedents (record + serve, RAG)
│   ├── automod_image_service.py # Automod images: downloads, hash index, caches, OCR/SafeSearch
│   ├── automod_profile_service.py # Automod member profiles: avatar/banner/name verdict caches, profile SafeSearch budget
│   ├── automod_label_service.py # Moddy team labeling queue (learn + revoke bot sanctions)
│   ├── ocr_service.py         #   Image → text for /ocr and the Transcribe menu
│   ├── ai_detector_service.py #   AI detector: C2PA trust list (Redis 24 h), downloads, to_thread
│   ├── ticket_service.py      #   Ticket lifecycle (open/close/escalate/move/participants)
│   ├── member_application_service.py # Join request API calls + review card lifecycle
│   ├── ticket_transcript_service.py # Archives a closing ticket's conversation (compressed JSON)
│   ├── ticket_closure_detector.py   # Spots a finished conversation, offers the closure (embeddings)
│   ├── support_request_service.py # Bug reports + config-help requests (team side)
│   ├── bump_prospect_service.py # Bump prospects: scan queue, one team card per server, follow-up
│   ├── bump_suggestion_service.py # DMs a one-pick Bump Reminder activation when a directory joins
│   ├── transcription_service.py #  Voice/audio speech-to-text (shared by cog + module)
│   ├── team_link_session.py   #   /team role: the window in which a staffer links the roles
│   ├── heartbeat.py           #   Moddy Health Monitor heartbeat (dead man's switch)
│   ├── betterstack_heartbeat.py #  Better Stack cron/heartbeat monitor ping (3 min)
│   ├── railway_diagnostic.py  #   Railway diagnostics
│   ├── public_api.py          #   bot.public_api: keyless public APIs (shared session, cache, public-only resolver)
│   ├── web_tools_service.py   #   /whois /dns /ssl /subdomains /link-check orchestration
│   ├── media_tools_service.py #   /image + /convert engines (local + CloudConvert)
│   ├── sauce_service.py       #   /sauce (SauceNAO + trace.moe)
│   ├── music_links_service.py #   Spotify page → Deezer / iTunes matching + cache
│   └── translate_button_service.py # Language gating, learned languages, DeepL click flow
│
├── internal_api/              # FastAPI internal API
│   ├── server.py              #   FastAPI app + /health, /ping, /status + router wiring
│   ├── routes/                #   API route handlers
│   │   └── automod.py         #     POST /automod/rules_check (custom-infraction safety check)
│   └── middleware/             #   Auth middleware
│
├── schemas/                   # Data schemas
├── locales/                   # i18n translation files
│   ├── fr.json                #   French (primary)
│   ├── en-US.json             #   English
│   └── commands/              #   Slash command names + descriptions, one file per
│                              #   Discord locale (fr.json, de.json, ja.json… 32 total)
│
├── docs/                      # Documentation: guides/ features/ integration/ infra/ archive/ sessions/ public/ (see docs/README.md)
└── tests/                     # Test files
    ├── automod/               #   pytest suite for the pure-Python detection core
    │                          #   (`pip install -r requirements-dev.txt && pytest`)
    ├── internal_api/          #   pytest suite for the internal API routes
    │                          #   (FastAPI TestClient, bot + gateway stubbed)
    ├── gateway/               #   Provider rate limits + executor reservation lifecycle
    ├── test_guild_language.py #   Server language: auto rule, cache, invalidation
    ├── test_altguard.py       #   AltGuard verdicts, gate roles, auto_role hold-back, consent notice, reason labels
    ├── test_altguard_invites.py # AltGuard inviter attribution + token payload (inviter_id, consent version)
    ├── test_auto_role_tags.py #   Auto Role server tag roles: detection, sync, panel, gateway hook
    ├── test_global_sanctions.py   # Global sanction levels, cache TTL, user/guild context
    ├── test_global_sanction_flow.py # Groups, notices, countdown, Redis events, allowlists
    ├── test_bot_customization.py  # Bot customization validation (bio budget, styles)
    ├── test_expiration_notifications.py # Expired sanctions: unban, invite, DM card
    ├── test_invoice_notifications.py # Stripe invoices: variant wording, amounts, dedup
    ├── test_task_signature.py #   moddy:tasks HMAC contract (canonicalization, replay, dedup)
    ├── test_brocoli.py        #   Brocoli: assertion signing vs backend algorithm, SSE parsing
    ├── test_tickets.py        #   Tickets: schema, permissions, claim, overwrites, screens, i18n
    ├── test_ticket_transcripts.py # Tickets: compression, export, closure detection,
    │                          #   settings, membership changes
    ├── test_ticket_ratings.py #   Tickets: rating modal, DM button, stats cards
    ├── test_linked_roles.py   #   moddy:staff publication, /team access catalogue, staff tickets
    ├── test_staff_config.py   #   t.config: staff key into /config only on private panels, launcher card
    ├── test_staff_flex.py     #   Flex card, /user Flex button (owner-only, public, DM-only)
    ├── test_transcription.py  #   Voice transcription helpers, guard rails, cards
    ├── test_notifications.py  #   Notifications: hashing, attribution, report rules, i18n
    ├── test_support_requests.py #  Support requests: cards, buttons, beta card, welcome DM
    ├── test_bump_reminder.py  #   Bump detection (real payloads + refusals), config, cards, i18n
    ├── test_bump_prospects.py #   Bump prospects: detection, one card per server, follow-up, card, i18n
    ├── test_member_applications.py # Member applications: card, dedupe, decisions, poll, gateway, i18n
    ├── test_logs.py           #   Server logs: registry, routing, rendering, delivery
    ├── test_logs_i18n.py      #   Server logs: i18n completeness on the 5 locales
    ├── test_automod_ai_panel.py #  Automod /config panel: presets, state migration, drafts + Save, screens, persistence, i18n
    ├── test_stats.py          #   Statistics: registry guards, aggregation, flush, rollup
    ├── test_heartbeat.py      #   Health Monitor heartbeat: payload, lifecycle, status decisions
    ├── test_automod_images.py #   Automod image features, image service, labeling queue + card
    ├── test_automod_profiles.py # Automod member profiles: holds, rename+warn, caches, budget, events, i18n
    ├── test_ocr.py            #   /ocr service: errors, re-encoding, card
    ├── test_ai_detector.py    #   AI detector: signed C2PA fixtures, classification, cards,
    │                          #   privacy, module, default-enabled manager, service
    ├── test_icon_theme.py     #   Icon theme: light→dark mapping, payload swap, discord.py patch guard
    ├── test_user_preferences.py # Preferences cache, language override, /preferences selects
    ├── test_staff_user_command.py # /team user: sections, personal-data gate, i18n (5 locales)
    ├── test_tools_{text,security,web,lookup,media,inspect}.py # Utility tools per theme
    ├── test_announcement_verification.py # Relay detection, Manager/Dev gate, copies answered once, edits
    └── test_music_links.py, test_translate_button.py          # Music links (default-enabled) + translate button (opt-in)
```

---

## Startup Flow

1. `main.py` → `asyncio.run(main())`
2. Sets up logging, starts `ServiceManager`
3. Creates `ModdyBot()` instance
4. Launches **FastAPI server** and **Discord bot** concurrently via `asyncio.gather()`
5. `bot.setup_hook()` → connects DB, loads cogs, syncs global commands, loads modules
6. `bot.on_ready()` → syncs guild-only commands per server, starts scheduled tasks

---

## Mandatory Rules for Writing Code

### 1. Components V2 Only
- **ALWAYS** use `ui.Container()` + `ui.TextDisplay()` — **NEVER** use `discord.Embed()`
- Use `ui.LayoutView` or `BaseView` (which extends it)
- See → [docs/guides/COMPONENTS_V2.md](docs/guides/COMPONENTS_V2.md)

### 2. BaseView / BaseModal Required
- **ALL** Views must inherit from `BaseView`
- **ALL** Modals must inherit from `BaseModal`
- These ensure errors are caught and routed to the centralized error handler
- See → [docs/guides/ERROR_HANDLING.md](docs/guides/ERROR_HANDLING.md)

### 3. Custom Emojis Only
- **NEVER** use default Unicode emojis (except country flags)
- Use custom emojis from `/utils/emojis.py`
- Full list → [docs/guides/EMOJIS.md](docs/guides/EMOJIS.md)
- **No "close enough" icons.** If no existing icon clearly fits what you are
  showing, do **not** pick a vaguely related one or reuse an icon that already
  means something else. Do **not** stop working: carry on with the task, use
  no icon there for now, and tell the user which icon is missing (describe what
  it should depict, and remind them it needs a white version plus a `_dark`
  twin) so they can upload it. When they give you the IDs, add them to
  `utils/emojis.py` and `docs/guides/EMOJIS.md` and wire them in.
- **Light-theme twins**: most icons are white, invisible on Discord's light theme.
  A user who picks *Light* in `/preferences` gets every icon that has a
  `<name>_dark` twin swapped automatically at send time (`utils/icon_theme.py`
  patches discord.py's payload builders). **Never** pick the `_dark` variant by
  hand in code. A new white icon needs its `<name>_dark` uploaded and added to
  `DARK_VARIANTS` in `utils/emojis.py`. Public messages the bot sends on its
  own (logs, welcome, panels) stay white. See → [docs/guides/ICON_THEME.md](docs/guides/ICON_THEME.md)

### 4. Internationalization (i18n)
- **ALL** user-facing text must use the i18n system
- `from utils.i18n import t` → `t('key.path', locale=locale)`
- Translation files: `/locales/fr.json`, `/locales/en-US.json`, `/locales/es-ES.json`,
  `/locales/pt-BR.json`, `/locales/de.json`
- **Command names and descriptions** are localized separately (Discord shows them
  in the user's own language): declare the command in English in the cog, then add
  its key to every `/locales/commands/<locale>.json`
- **Server-facing text uses the SERVER language, never `guild.preferred_locale`
  and never a per-module language setting.** Anything a whole server reads
  (welcome message, panel, log, sanction DM) takes its locale from
  `await guild_locale(bot, guild)` (`utils/guild_language.py`), which resolves
  the single setting stored in `guilds.data.settings.language` (`/config` →
  *Server settings*). Anything one person reads privately (ephemeral reply,
  `/config` screen, error) keeps `i18n.get_user_locale(interaction)`.
  That call also honours the language a user forced in `/preferences`
  (`utils/user_preferences.py`) — always go through it, never read
  `interaction.locale` directly.
  **Adding a language dropdown to a module's config is a bug.**
- See → [docs/guides/COMMAND_LOCALIZATION.md](docs/guides/COMMAND_LOCALIZATION.md),
  [docs/guides/SERVER_LANGUAGE.md](docs/guides/SERVER_LANGUAGE.md)

### 5. Title Format
- Titles in Components V2 must use: `### <:emoji:id> Title Text`
- Example: `### <:settings:1519800032499339354> Configuration`

### 6. Dynamic Values in Backticks
- Wrap all dynamic/user-specific data in backticks: `` f"**User:** `{user.id}`" ``

### 7. Verification Badge on Usernames
- **Whenever a command displays a username or display name (outside of mentions), it must show the verification badge** using `get_user_verification_badge()` from `utils/emojis.py`.
- Three tiers (priority order):
  1. `VERIFIED_ORG` attribute → `<:verified_org:...>` badge
  2. Discord staff flag / `TEAM` attribute / `VERIFIED_ORG_MEMBER` attribute → `<:verified:...>` badge + `-# affiliation notice`
  3. `VERIFIED` attribute → `<:verified:...>` badge
- The badge is wrapped as a **hyperlink** using `format_verification_badge(badge)` from `utils/emojis.py`, which produces `[<:verified:...>](https://docs.moddy.app/articles/verified-badges)`.
- The formatted badge is appended **after the bold name** — no space between name and badge: `**{name}**{badge}`.
- Pass `name=` and `badge=` as **separate** i18n parameters (not combined into one).
- Use `global_name` (display name) instead of `username` wherever possible.
- Fetch `moddy_attributes` from `bot.db.get_user()` before building the view.
- Do **not** show any badge if the user has none — empty string.
- `get_user_verification_badge()` now returns a 3-tuple `(badge_emoji, org_names, tier)`. Unpack all three.
- See `utils/emojis.py::get_user_verification_badge()` and `format_verification_badge()` for the implementation.

### 8. Error Handling
- For "unexpected" errors in cogs/modules: let the global error handler manage them
- For expected errors: `raise` a `utils.errors.ModdyError` subclass (`NotFound`, `InvalidInput`, `MissingPermissions`, `Cooldown`...) and let it propagate, or build the card with `build_error_card()` / `create_error_message()`. See docs/guides/ERROR_HANDLING.md → Expected errors

### 8. Persistent Views — **MANDATORY, NO EXCEPTIONS**
- **You MUST use persistent views for EVERY interactive Discord component,
  in every cog, module, and staff command — always, without exception.**
  Every button, select, and `DynamicItem` MUST have a stable, namespaced
  `custom_id` and live on a `timeout=None` view so it never dies — neither
  after a timeout nor after a bot restart. This is not a per-feature
  judgment call: **writing a new interactive view that is not persistent is
  a bug**, exactly like using `discord.Embed()` instead of Components V2 or
  hardcoding a user-facing string instead of using i18n. Shipping a view
  whose buttons stop working after a restart (or after a few minutes of
  inactivity) is **not acceptable** and will be rejected in review.
- The **only** acceptable exceptions are the ones explicitly listed and
  justified in [docs/guides/PERSISTENT_VIEWS.md → "Deliberate exclusions"](docs/guides/PERSISTENT_VIEWS.md)
  (modals, error-recovery views, secret-displaying views, in-memory-callback
  confirm dialogs, and similar). If you believe a new view needs to be
  excluded, add it to that section with the same level of concrete
  justification as the existing entries — do not silently skip persistence.
- `BaseView` defaults to `timeout=None` — views never expire in memory.
- To make a view survive a **bot restart**:
  1. Set `__persistent__ = True` on the class
  2. Give every interactive child a stable, namespaced `custom_id` (`moddy:<cog>:<view>:<action>`)
  3. Make `__init__` safely accept `bot=None` / default args so a "shell" can be instantiated
  4. For owner-scoped, guild-mismatched, or entity-scoped state (a `user_id`,
     a `case_id`, a page number, …), use a `DynamicItem` subclass with a
     `template=` regex — a static custom_id alone is only enough when
     `interaction.guild_id`/`interaction.user.id` already provide the auth
     context (typical guild config panels)
  5. Implement `register_persistent(cls, bot)` (`bot.add_view(cls())` for a
     regular view, `bot.add_dynamic_items(ItemClass)` for a `DynamicItem`)
  6. Add the class to `utils/persistent_views.py::_collect_persistent_view_classes()`
- Callbacks on persistent views must re-derive state from `interaction` (not
  `self`) — after a restart, `self` is the shell, and a `DynamicItem`
  reconstructs from scratch on **every single click**, not just after a
  restart.
- Before opening a PR that adds or touches a view, run
  `python3 -m pytest tests/test_persistent_views.py -q` — it asserts every
  registered view is actually persistent and that no two views collide on a
  `custom_id`. A view is not done until this suite covers it.
- See → [docs/guides/PERSISTENT_VIEWS.md](docs/guides/PERSISTENT_VIEWS.md) for the full
  contract, the custom_id convention, auth models, and worked examples.

### 9. "Current value" only when nothing else shows it
- Do **not** print a `Valeur actuelle : …` / `Current value: …` line under a
  setting whose control already displays its own state. A `ChannelSelect`,
  `RoleSelect`, `UserSelect`, `MentionableSelect` or a `Select` with
  `default=True` on the chosen option all render the current selection
  themselves — repeating it as text is duplicated, and the two can drift.
- **Do** show the current value when the setting is edited through something
  that displays nothing: a Modal (custom message, colour, thresholds…), a
  toggle button, or a value stored but not represented by any component on
  the panel.
- Rule of thumb: if the user can read the answer off the control, the text
  line is noise; if the control is a button that opens a Modal, the text line
  is the only way to know what is stored.

### 10. Language
- Code comments, commits, PRs: **English only**
- User-facing strings: via i18n (French + English)

### 11. Every message to a human goes through the notification system
- **NEVER call `user.send(...)` / `member.send(...)` directly.** Every DM — and
  every server-wide notice Moddy posts on its own behalf — goes through
  `bot.notifications` so that it is stored, attributable, and reportable when
  someone other than Moddy wrote it.
- Pass a uniform `NotificationContent` **and** a `NotificationSource` that tells
  the truth about who wrote the words. A feature that already has its own card
  passes it as `view=` — the uniform content is still required, because it is
  what the dashboard, the mail pipeline and the staff preview render.
- Keep `{placeholders}` in the content and pass their values as `variables`:
  that is what lets thousands of identical DMs share one stored body while each
  one stays reproducible to the character.
- The only exception is a sender with a genuinely exotic delivery path (the
  token detector opens the DM channel with the user's own token). It still calls
  `record()` and then `mark_delivered()` / `mark_failed()`.
- See → [docs/guides/NOTIFICATIONS.md](docs/guides/NOTIFICATIONS.md)

### 12. Never require Administrator — declare per-module bot permissions instead
- **Moddy must never require the `Administrator` permission**, neither as the
  bot's default invite scope nor as a runtime gate (this matters for listing
  Moddy on bot directories like top.gg, which frown on `Administrator`-only
  bots). The bot only ever needs the specific Discord permissions each
  feature actually uses.
- Every module (`modules/*.py`, `ModuleBase` subclass) **must** declare
  `REQUIRED_BOT_PERMISSIONS: List[str]` — the exact `discord.Permissions`
  flag names (e.g. `manage_roles`, `manage_channels`, `manage_webhooks`,
  `ban_members`, `kick_members`, `moderate_members`, `manage_messages`) the
  bot itself needs to perform that module's Discord-side actions. Leave it
  as `[]` when the module needs nothing beyond the baseline (view/send in
  the channels it already has access to).
- `/config` checks the bot's permissions **per module**, not globally: when
  a server selects a module to configure, the panel checks
  `guild.me.guild_permissions` against that module's
  `REQUIRED_BOT_PERMISSIONS` and shows a "missing permissions" message
  (naming exactly what is missing, with a re-invite link scoped to that
  need) instead of opening the config screen — see
  `modules/configs/_common.py::check_bot_perms` and `cogs/config.py`.
- Adding a new module means auditing every Discord API call it (or the
  service it delegates to) makes and setting `REQUIRED_BOT_PERMISSIONS`
  accordingly — do not guess, and do not default to `administrator` as a
  shortcut.
- `REQUIRED_BOT_PERMISSIONS` lists only the module-specific permissions; the
  baseline (view_channel, send_messages, read_message_history,
  use_external_emojis) is merged by `ModuleBase.required_permissions()` unless
  `SENDS_MESSAGES = False`. Channel-level needs go in
  `channel_requirements(config)`. /config reports ALL gaps in one message
  (`utils/bot_permissions.py`); `validate_config` must use
  `channel_validation_error`, never a first-failure `if not perms.x` chain.
- See → [docs/guides/COMMANDS.md](docs/guides/COMMANDS.md), [docs/guides/MODULE_SYSTEM.md](docs/guides/MODULE_SYSTEM.md)

---

## Documentation Index

All documentation is in [docs/](docs/) (structure and conventions: [docs/README.md](docs/README.md)). Read the relevant file **before** working on a feature.

### Guides — cross-cutting rules (read first when relevant)
`docs/guides/` — Rules and how-tos that apply to any feature: UI, i18n, errors, persistence, commands, modules, notifications, staff.
| Document | When to Read |
|---|---|
| [docs/guides/AGENTS.md](docs/guides/AGENTS.md) | Agent system documentation |
| [docs/guides/COMMANDS.md](docs/guides/COMMANDS.md) | Creating or modifying slash commands |
| [docs/guides/COMMAND_LOCALIZATION.md](docs/guides/COMMAND_LOCALIZATION.md) | Translating slash command names/descriptions (32 Discord locales) |
| [docs/guides/COMPONENTS_V2.md](docs/guides/COMPONENTS_V2.md) | Any code that creates Discord UI elements |
| [docs/guides/DESIGN.md](docs/guides/DESIGN.md) | Any UI/UX work, configuration panels, interactive components |
| [docs/guides/EMOJIS.md](docs/guides/EMOJIS.md) | When you need to use an emoji/icon |
| [docs/guides/ERROR_HANDLING.md](docs/guides/ERROR_HANDLING.md) | Any code with Views, Modals, or error handling |
| [docs/guides/ICON_THEME.md](docs/guides/ICON_THEME.md) | **Icon theme** — light-theme users get the black `_dark` icons; how the send-time swap works, adding an icon, discord.py upgrade guard |
| [docs/guides/MODALS_V2.md](docs/guides/MODALS_V2.md) | Any code that builds a Modal (Label / TextDisplay / Select in modals) |
| [docs/guides/MODDY_FRAMEWORK.md](docs/guides/MODDY_FRAMEWORK.md) | Internal framework public API and migration |
| [docs/guides/MODULE_SYSTEM.md](docs/guides/MODULE_SYSTEM.md) | Creating or modifying server modules |
| [docs/guides/NOTIFICATIONS.md](docs/guides/NOTIFICATIONS.md) | **Centralized notifications** — every DM/mail/dashboard message, attribution buttons, abuse reports, `/com send`, `/mod notif` |
| [docs/guides/PERSISTENT_VIEWS.md](docs/guides/PERSISTENT_VIEWS.md) | Any view that should survive a bot restart (most views) |
| [docs/guides/PREMIUM.md](docs/guides/PREMIUM.md) | **Premium gating** — how to check whether a server (or a user) is premium |
| [docs/guides/SERVER_LANGUAGE.md](docs/guides/SERVER_LANGUAGE.md) | **Server language** — the single setting every module reads; what follows the server vs. the user |
| [docs/guides/STAFF_COMMANDS_FRAMEWORK.md](docs/guides/STAFF_COMMANDS_FRAMEWORK.md) | Staff command engine internals (message + slash from one definition) |
| [docs/guides/STAFF_SYSTEM.md](docs/guides/STAFF_SYSTEM.md) | Staff/dev commands, permissions, roles |

### Features — one doc per feature or module
`docs/features/` — How a feature works on the bot side.
| Document | When to Read |
|---|---|
| [docs/features/AI_DETECTOR.md](docs/features/AI_DETECTOR.md) | **AI Detector** — C2PA Content Credentials (official trust list, EKUs), verdict templates, privacy whitelist, details button, `/ai-check`, default-enabled module |
| [docs/features/ALTGUARD.md](docs/features/ALTGUARD.md) | **AltGuard** — anti multi-account verification gate, consent, service contract, staff commands |
| [docs/features/ANNOUNCEMENT_TRANSLATION.md](docs/features/ANNOUNCEMENT_TRANSLATION.md) | **Announcement translation** — support-server announcements translated once into every language, one flag button per language |
| [docs/features/ANNOUNCEMENT_VERIFICATION.md](docs/features/ANNOUNCEMENT_VERIFICATION.md) | **Announcement verification** — Managers/Devs authenticate support-server announcements; every relayed copy in a following server gets a check_circle reaction + Translate button |
| [docs/features/AUTOMOD.md](docs/features/AUTOMOD.md) | Automod — detection pipeline (+ `/config` panel, §0), nano decider, scalable features, **images (NSFW + scam screenshots)**, **Moddy team labeling queue**, rules + custom-infraction safety check, **infraction catalogue & per-type sanctions**, **custom infractions**, **member profiles** |
| [docs/features/AUTO_ROLE.md](docs/features/AUTO_ROLE.md) | **Auto Role** — join roles for members/bots + roles mirrored on the server tag (gateway hook, tag detection) |
| [docs/features/BOT_CUSTOMIZATION.md](docs/features/BOT_CUSTOMIZATION.md) | Bot Customization — per-guild nickname/avatar/banner/bio + name styles, Redis dashboard contract |
| [docs/features/BROCOLI_CHANNEL.md](docs/features/BROCOLI_CHANNEL.md) | **Brocoli in a channel** — the AI assistant as a Discord channel, signed identity assertions, confirmation cards, dev-guild gating |
| [docs/features/BUMP_PROSPECTS.md](docs/features/BUMP_PROSPECTS.md) | **Bump prospects** (internal) — servers bumping on a directory with no reminder → team card (Invite / Claim / Close), `/dev bumpscan` |
| [docs/features/BUMP_REMINDER.md](docs/features/BUMP_REMINDER.md) | **Bump Reminder** — detecting a *successful* bump on 7 directories, the reminder, ping modes, quotas |
| [docs/features/GLOBAL_SANCTIONS.md](docs/features/GLOBAL_SANCTIONS.md) | **Global sanctions** — Moddy-team warn / limited / suspended, on users *and* servers |
| [docs/features/LINKED_ROLES.md](docs/features/LINKED_ROLES.md) | **Linked roles** — the `moddy:staff` publication the backend needs, the two Moddy Team roles (`team` / `manager`), and `/team role` / `/team access` / `/team ticket` |
| [docs/features/LOGS.md](docs/features/LOGS.md) | **Advanced server logs** — 163 events, registry, rendering, webhook delivery, stored config & dashboard contract |
| [docs/features/MEMBER_APPLICATIONS.md](docs/features/MEMBER_APPLICATIONS.md) | **Member Applications** — Discord "Apply to Join" reviewed from a channel: what the API allows bots, gateway hook + poll, the card, Approve/Reject, the `member_applications` table |
| [docs/features/MODERATION_CASES.md](docs/features/MODERATION_CASES.md) | Moderation cases/sanctions, the case service & sources, auto-sync |
| [docs/features/OCR.md](docs/features/OCR.md) | **OCR** — `/ocr` and the Transcribe menu on images (Google Vision, monthly free tier) |
| [docs/features/SOCIAL_NOTIFICATIONS.md](docs/features/SOCIAL_NOTIFICATIONS.md) | Social Notifications module + `moddy-feeds` Redis contract (what the backend must mirror) |
| [docs/features/SUPPORT_REQUESTS.md](docs/features/SUPPORT_REQUESTS.md) | **Support requests** — `/bug-report`, the "configure it for me" button, the staff card and its reply flow, the beta-launch campaign |
| [docs/features/TEXT_TOOLS.md](docs/features/TEXT_TOOLS.md) | AI text tools — `/fix`, `/rephrase`, `/summarize` (models, presets, mention stripping) |
| [docs/features/TOOLS.md](docs/features/TOOLS.md) | **Utility tools** — shared design contract (card anatomy, source phrase, Modals V2, secrets, persistence, network); per theme: [TOOLS_TEXT](docs/features/TOOLS_TEXT.md), [TOOLS_SECURITY](docs/features/TOOLS_SECURITY.md), [TOOLS_WEB](docs/features/TOOLS_WEB.md), [TOOLS_LOOKUP](docs/features/TOOLS_LOOKUP.md), [TOOLS_MEDIA](docs/features/TOOLS_MEDIA.md), [TOOLS_INSPECT](docs/features/TOOLS_INSPECT.md) |
| [docs/features/MUSIC_LINKS.md](docs/features/MUSIC_LINKS.md) | **Music links** — module + `/music`: Spotify page → Deezer / Apple Music / YouTube Music |
| [docs/features/TRANSLATE_BUTTON.md](docs/features/TRANSLATE_BUTTON.md) | **Translate button** — local language detection, server's usual languages, DeepL on click |
| [docs/features/TICKETS.md](docs/features/TICKETS.md) | **Tickets** — panels, categories, per-role permissions, the claim system, escalation, transcripts, closure detection, ratings, module-gated `/ticket` commands |
| [docs/features/VOICE_TRANSCRIPTION.md](docs/features/VOICE_TRANSCRIPTION.md) | Voice transcription — context menu, module, Groq Whisper, cost control |
| [docs/features/WELCOME_DM.md](docs/features/WELCOME_DM.md) | Welcome DM module (`welcome_dm`) — config schema, placeholders, backend/dashboard contract |
| [docs/features/WELCOME_MESSAGES.md](docs/features/WELCOME_MESSAGES.md) | Welcome messages module (`welcome_channel`) — config schema, placeholders, backend/dashboard contract |

### Integration — contracts with the backend / dashboard
`docs/integration/` — Schemas, wire formats and Redis/DB contracts the other services depend on.
| Document | When to Read |
|---|---|
| [docs/integration/ALTGUARD_INTEGRATION.md](docs/integration/ALTGUARD_INTEGRATION.md) | AltGuard ↔ bot exact wire contract — payload types, error codes, debugging |
| [docs/integration/AUTOMOD_CONFIG.md](docs/integration/AUTOMOD_CONFIG.md) | Automod configuration schema in DB (generic `state` replaces `enabled`/`dry_run`) (backend / dashboard integration): `sanctions`, `infractions_perso`, `features.profile`, validation errors, `rules_check` route, tables, Redis keys, deployment order |
| [docs/integration/BACKEND-INTEGRATION.md](docs/integration/BACKEND-INTEGRATION.md) | Bot ↔ Backend integration (Redis, Pub/Sub, Streams, `/status`) |
| [docs/integration/NOTIFICATIONS_INTEGRATION.md](docs/integration/NOTIFICATIONS_INTEGRATION.md) | Notifications ↔ backend contract — tables the backend reads/owns, exact payload rendering + placeholder algorithm, mail/dashboard delivery loop |
| [docs/integration/REDIS_COMMUNICATION.md](docs/integration/REDIS_COMMUNICATION.md) | **Redis inter-service communication** — Pub/Sub vs Streams vs plain keys, current channel/stream inventory, checklist for wiring up a new Redis-based service |
| [docs/integration/SUBSCRIPTION_SCHEMA.md](docs/integration/SUBSCRIPTION_SCHEMA.md) | Subscription DB schema, Redis cache contract, Pub/Sub events (incl. `notify_invoice` — Stripe invoice DMs) |
| [docs/integration/TASK_SIGNATURE.md](docs/integration/TASK_SIGNATURE.md) | **`moddy:tasks` HMAC signature** — canonicalization, anti-replay, `TASK_STREAM_SECRET`, deployment order |
| [docs/integration/TICKETS_INTEGRATION.md](docs/integration/TICKETS_INTEGRATION.md) | Tickets ↔ backend contract — the transcript/rating tables, the compressed body schema, `/transcripts/<key>` and its authorisation, the rating aggregates |

### Infrastructure — hosting, gateway, data, monitoring
`docs/infra/` — Railway, API gateway, database, stats, health and technical logs.
| Document | When to Read |
|---|---|
| [docs/infra/API_GATEWAY.md](docs/infra/API_GATEWAY.md) | API Gateway — all external API calls (OpenAI, DeepL, Groq, Google Vision), quotas, provider rate limits (incl. calendar-month fail-closed), resilience, logging |
| [docs/infra/DATABASE.md](docs/infra/DATABASE.md) | Database schema, queries, repository pattern |
| [docs/infra/HEALTH_MONITOR.md](docs/infra/HEALTH_MONITOR.md) | **Health Monitor heartbeats** — dead man's switch push to `moddy-health-monitor` (`HM_URL`/`HM_INGEST_TOKEN`) and the Better Stack cron ping (`BETTERSTACK_HEARTBEAT_URL`) |
| [docs/infra/RAILWAY.md](docs/infra/RAILWAY.md) | Environment variables, deployment, troubleshooting, **memory footprint & Railway cost** |
| [docs/infra/STATS.md](docs/infra/STATS.md) | **Statistics** — recording anything (`bot.stats`), the metric registry, what is stored and for how long, the growth/retention/acquisition queries, the backend UTM contract |
| [docs/infra/TECHNICAL_LOGS.md](docs/infra/TECHNICAL_LOGS.md) | Internal technical staff logs (webhook-based, per-event channels) |

### Archive
Point-in-time documents kept for reference but superseded or closed —
audits, dated change specs, one-off schema notes for another service, a
finished implementation plan. Not part of the day-to-day reading list; check
here before writing a new one-off doc so it doesn't end up back at the top
level.

| Document | What it is |
|---|---|
| [docs/archive/SECURITY_AUDIT.md](docs/archive/SECURITY_AUDIT.md) | Security audit report (2026-08-20) — findings fixed and residual risks at that date |
| [docs/archive/SOCIAL_NOTIFICATIONS_CHANGES_2026-06-14.md](docs/archive/SOCIAL_NOTIFICATIONS_CHANGES_2026-06-14.md) | Backend/dashboard change spec (2026-06-14), now folded into [docs/features/SOCIAL_NOTIFICATIONS.md](docs/features/SOCIAL_NOTIFICATIONS.md) |
| [docs/archive/REDIRECTS_BANNERS_SCHEMA.md](docs/archive/REDIRECTS_BANNERS_SCHEMA.md) | `redirect_links`/banners DB schema note written for the backend team |
| [docs/archive/UTILITY_TOOLS_PLAN.md](docs/archive/UTILITY_TOOLS_PLAN.md) | Utility tools plan (validated) + the agent brief [UTILITY_TOOLS_AGENT_BRIEF.md](docs/archive/UTILITY_TOOLS_AGENT_BRIEF.md) |
| [docs/archive/AUTOMOD_V2_PLAN.md](docs/archive/AUTOMOD_V2_PLAN.md) | Automod v2 implementation plan/progress log — all sessions closed; design rationale still cited from `automod/` code comments |

### Session Logs
| Directory | Purpose |
|---|---|
| [docs/sessions/](docs/sessions/) | AI agent session summaries — after each work session, write a summary here |

---

## Key Design Patterns

### Repository Pattern (Database)
Database access goes through specialized repositories in `db/repositories/`:
```python
# Access via bot.db.<repository>.<method>()
await bot.db.users.get_user(user_id)
await bot.db.guilds.get_guild(guild_id)
await bot.db.staff.get_permissions(user_id)
```

### Module System
Server features are implemented as modules in `modules/`:
- Each module extends `ModuleBase`
- Config is stored as JSONB in `guilds.data.modules.<module_id>`
- Opt-in by default; `DEFAULT_ENABLED = True` makes a module run everywhere with
  its defaults until a server pauses it (absent / `{}` config = defaults, active)
- Config UI lives in `modules/configs/`
- See → [docs/guides/MODULE_SYSTEM.md](docs/guides/MODULE_SYSTEM.md)

### Staff Command System
Prefix-based commands triggered by mentioning the bot:
- `@Moddy t.help` → team commands
- `@Moddy d.reload cog_name` → dev commands
- `@Moddy m.rank @user Manager` → management commands
- `@Moddy mod.blacklist @user reason` → moderation commands

### Command Sync (Global vs Guild-Only)
- **Global commands** (e.g., `/ping`, `/user`): available everywhere including DMs
  - Must have `@allowed_installs(guilds=True, users=True)` and `@allowed_contexts(guilds=True, dms=True, private_channels=True)`
- **Guild-only commands** (e.g., `/config`): only in servers where Moddy is installed
  - Must have `@app_commands.guild_only()`
- Sync happens in 2 phases: `setup_hook()` (global) → `on_ready()` (per-guild)
- **Message context menus: Discord allows 5 per app, and all 5 are taken**
  (`Save Message`, `Get Emojis`, `Translate`, `AI text tools`, `Transcribe`). A sixth
  makes the extension that registers it fail at startup (`CommandLimitReached`).
  A new tool gets a slash command, or is merged into an existing menu.
  `tests/test_context_menu_limit.py` guards it.

### Colors
```python
from config import COLORS
# COLORS["primary"]   = 0x5865F2 (Discord Blue)
# COLORS["success"]   = 0x57F287 (Green)
# COLORS["warning"]   = 0xFEE75C (Yellow)
# COLORS["error"]     = 0xED4245 (Red)
# COLORS["info"]      = 0x5865F2 (Blue)
# COLORS["neutral"]   = 0x99AAB5 (Gray)
# COLORS["developer"] = 0x9B59B6 (Purple)
```

### Environment Modes
```python
from config import IS_DEV, IS_PROD, IS_MAINTENANCE, ENV_MODE
# "production" — normal operation
# "development" — restricted to DEV_ALLOWED_IDS
# "maintenance" — bot in maintenance mode
```

---

## Quick Reference: Creating a New Feature

### New Slash Command
1. Read [docs/guides/COMMANDS.md](docs/guides/COMMANDS.md)
2. Create cog in `cogs/`
3. Use `BaseView` for any UI
4. Use i18n for all text
5. Use custom emojis only
6. Add the command name/description to `/locales/commands/*.json`
   (see [docs/guides/COMMAND_LOCALIZATION.md](docs/guides/COMMAND_LOCALIZATION.md))

### New Server Module
1. Read [docs/guides/MODULE_SYSTEM.md](docs/guides/MODULE_SYSTEM.md)
2. Create module in `modules/` extending `ModuleBase`
3. Create config UI in `modules/configs/`
4. Add i18n keys in `locales/`
5. Register in module manager

### New Staff Command
1. Read [docs/guides/STAFF_SYSTEM.md](docs/guides/STAFF_SYSTEM.md)
2. Add command in the appropriate `staff/` file
3. Use the permission decorators

---

## Keeping Documentation Up to Date

**This is a standing rule — do it proactively, without being asked.**

Whenever you make changes to the codebase, check whether the documentation needs updating:

- **This file (`CLAUDE.md`)**: If you add/remove/rename cogs, modules, directories, or change architecture, update the project structure and relevant sections here.
- **Feature docs** (`docs/*.md`): If you modify a feature covered by a doc (e.g., adding a new module, changing the command sync system, adding a DB table), update the corresponding doc.
- **Emojis** (`docs/guides/EMOJIS.md` and `utils/emojis.py`): If new custom emojis are added, update the list.
- **Session logs** (`docs/sessions/`): After each work session, create a summary (see below).

If you create a new system or feature that doesn't fit any existing doc, create a new doc file in `docs/` and add it to the Documentation Index above.

**Do not wait for the user to ask.** Outdated documentation is worse than no documentation.

---

## Session Logs :

After each work session, create a summary in `docs/sessions/` with the format:
```
docs/sessions/YYYY-MM-DD_short-description.md
```

Include:
- What was done
- Files modified
- Decisions made and why
- Any known issues or follow-ups

See → [docs/sessions/README.md](docs/sessions/README.md)
