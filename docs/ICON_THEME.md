# Icon theme (light Discord theme support)

## Why

Most custom emojis in `utils/emojis.py` are white. On Discord's light theme they
are invisible. Black twins were uploaded, named `<original name>_dark`. Discord
does not expose a user's theme to bots, so each user chooses it in
`/preferences` (`utils.user_preferences.get_icon_theme(user_id)` returns
`"dark"` (default, white icons as written) or `"light"`).

No call site changes: the swap happens centrally, at send time.

## Mechanism

All of it lives in `utils/icon_theme.py`; `icon_theme.install()` is the only
entry point (called once from `bot.setup_hook`, idempotent, never raises).

1. **Mapping** (`utils/emojis.py`): `DARK_VARIANTS` (dark name -> id) and
   `LIGHT_TO_DARK` (original id -> `(dark name, dark id)`), built by name: every
   `<:name:id>` found in the module constants (plus `_EXTRA_ORIGINAL_IDS`, ids
   hardcoded elsewhere in the repo or in locales) is matched with `name_dark`.
   Several ids sharing one name are all mapped.
2. **Active theme**: a `ContextVar` (`current_theme()`), set with
   `use_icon_theme(theme)` or `use_icon_theme_for(user_id)`.
3. **Rewrite**: when the theme is `"light"`, the payload that leaves for Discord
   goes through `swap_payload` (strings via `swap_text`, emoji dicts
   `{"id", "name"}` by id). It returns a copy; shared views are never mutated.
   Any error falls back to the untouched payload.
4. **Theme selection**: interaction paths set the ContextVar from
   `interaction.user.id`; `NotificationService.send_dm` wraps delivery in
   `use_icon_theme_for(recipient_id)` so DMs follow the recipient.

## What is patched (discord.py 2.7.1)

Payload builders (wrapped in every module that imported them by name):
- `handle_message_parameters` in `discord.http`, `discord.interactions`,
  `discord.webhook.async_`, `discord.webhook.sync`, `discord.message`,
  `discord.abc`, `discord.channel` (channel sends, message edits, webhook and
  follow-up sends, `edit_original_response`).
- `interaction_message_response_params` (`InteractionResponse.send_message` /
  `edit_message` do NOT use `handle_message_parameters`) in `discord.interactions`
  and `discord.webhook.async_`.
- `interaction_response_params` (used by `send_modal`, also autocomplete) in the
  same two modules.

Theme selection:
- `InteractionResponse.send_message`, `edit_message`, `send_modal`,
  `Interaction.edit_original_response`: theme of `interaction.user`.
- `Interaction.followup` (property): records `token -> user id`.
- `Webhook.send`, `Webhook.edit_message`: look the token up, so follow-ups and
  `WebhookMessage.edit` follow the interactor.

## Limitations

- Discord never tells us the theme: users must set it in `/preferences`.
- A public message sent as an interaction response follows the **interactor's**
  theme; everyone else sees those icons as that person would. Messages sent by
  the bot on its own (`channel.send`, logs, webhooks, welcome messages) keep
  white icons unless a code path wraps the send in `use_icon_theme(...)`.
- Only custom emojis that have a `_dark` twin are swapped.
- Dark emojis with no matching original (names do not exist in the repo):
  `1__dark 2__dark 3__dark 4__dark color_dark commit_dark cooldown_dark
  copyright_dark explore_dark eye_m_dark ia_dark idea_dark import_dark
  invalidsnowflake_dark lock_dark person_off_dark policy_dark track_dark
  update_dark verified1_dark`.
- Follow-up token memory is bounded (8192 tokens, in process).

## Adding an icon

Upload the black version as `<original name>_dark` and add `"<name>_dark": <id>`
to `DARK_VARIANTS` in `utils/emojis.py`. If the original is hardcoded outside
`emojis.py` (a locale file, another module) add its id to `_EXTRA_ORIGINAL_IDS`.

## discord.py upgrades

`tests/test_icon_theme.py::test_every_patched_discord_attribute_exists_and_is_patched`
fails if a patched function/method moves or is renamed. When it does, re-read the
new source (which builders do `send_message`, `edit_message`, `send_modal` use?)
and update the target lists in `utils/icon_theme.py`.

## Coloured buttons keep the white icon

A button drawn on a coloured background — `primary` (blurple), `success`
(green), `danger` (red) — shows its icon on that colour, not on the client
background, so the white icon is the readable one on both themes.
`swap_payload` leaves those buttons untouched. Grey buttons (`secondary`,
`link`) follow the user's theme like the rest of the message.
