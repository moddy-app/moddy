# Server Language

> One server, one language. Everything Moddy says **to a server** — welcome
> messages, ticket channels, AltGuard's panel, the logs, automod sanction DMs
> — is written in the same language, chosen once.

- Module: [`utils/guild_language.py`](../utils/guild_language.py)
- Config UI: [`modules/configs/server_settings_config.py`](../modules/configs/server_settings_config.py)
  (`/config` → **Server settings**)
- Tests: `tests/test_guild_language.py`

---

## 1. Why it exists

Every module used to answer the question "which language do I write in?" on
its own. AltGuard had a *panel language* dropdown, Automod AI a
`langue_serveur`, the logs a `locale`, each ticket category its own language,
and everything else just read `guild.preferred_locale`. The same server could
be greeted in English, warned in French and logged in German — and an admin
had to find and set five different dropdowns to get one consistent result.

There is now a single setting, stored **outside of any module**, that every
one of them reads.

---

## 2. The setting

Stored in `guilds.data.settings.language`:

| Value | Meaning |
|---|---|
| `"auto"` (default) | Follow the server's own language — `guild.preferred_locale` — **but only when the Community feature is enabled**. Anything else falls back to `en-US`. |
| `"en-US"`, `"fr"`, `"es-ES"`, `"pt-BR"`, `"de"` | That language, whatever Discord thinks. |

**Why Community gates the automatic mode.** Outside a Community server,
`preferred_locale` is an account-level default nobody on the server ever
chose; speaking it would be guessing. Community is the point where Discord
actually asks the owner to pick the server's language, so that is the only
signal worth trusting.

The supported set is exactly the `locales/<code>.json` files. A server whose
preferred locale is a language Moddy is not translated into (`ja`) gets
English rather than a half-translated interface. Close variants map onto the
language Moddy has: `en-GB` → `en-US`, `es-419` → `es-ES`, `pt-PT` → `pt-BR`.

---

## 3. Reading it from code

```python
from utils.guild_language import guild_locale

locale = await guild_locale(bot, guild)          # guild object or guild id
```

That is the call to use — in a module, a cog, a service, anywhere a message is
written for a server rather than for one person.

For a **sync hot path** that genuinely cannot await (automod runs once per
moderated message, the appeal cards render synchronously):

```python
from utils.guild_language import guild_locale_cached

locale = guild_locale_cached(bot, guild)
```

It answers from the in-process cache; on a cold cache it returns the automatic
language and warms the cache in the background, so at worst the first message
after a restart is rendered automatically instead of with an explicit
override.

### What still follows the *user's* language

The rule is who reads it:

| Audience | Language | How |
|---|---|---|
| The server (a channel message, a panel, a DM about this server's sanction) | Server language | `guild_locale()` |
| One person, privately (an ephemeral reply, a `/config` screen, an error) | Their own Discord language | `i18n.get_user_locale(interaction)` |

A `/config` panel is rendered in the **admin's** language even while it edits
what the server will speak — the admin is the only reader of that message.

---

## 4. Changing it

`/config` → **Server settings** → the language dropdown. A single setting, so
it saves on the spot; there is no Save/Cancel pair to explain.

Most of the bot reads the language when it writes a message, so a new language
simply takes effect. A **panel** is different: it is a message already sitting
in a channel, written in the previous language. Modules that own one declare

```python
LANGUAGE_DEPENDENT_MESSAGES = True
```

and `ModuleManager.apply_language_change(guild_id)` reloads them through the
same path a dashboard push uses, which re-posts their messages. Today that is
**AltGuard** (the verification panel) and **Tickets** (the ticket panels).

---

## 5. Dashboard / backend contract

The dashboard writes `guilds.data.settings.language` straight to the database,
then publishes on `moddy:bot`:

```json
{ "type": "settings_updated", "guild_id": "123456789012345678" }
```

The bot drops its cached value and re-applies the panels
(`bot.py::_handle_settings_push`). **Without that event the bot keeps serving
the previous language** until it restarts — the value is cached in-process,
one read per guild.

Accepted values are `auto` plus the five locale codes; anything else is stored
and read as `auto`.

---

## 6. Adding a language

1. Add `locales/<code>.json`, fully translated.
2. Add the code to `SUPPORTED_LOCALES` in `utils/guild_language.py` (the tuple
   order is the order of the dropdown).
3. Check `languages.<code>` exists in all five locale files — the dropdown
   labels come from that shared block.

Nothing else: no module carries its own list any more.
