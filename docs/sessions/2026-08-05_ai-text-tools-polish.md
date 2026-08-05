# 2026-08-05 — AI text tools: review pass

Follow-up to [`2026-08-05_ai-text-tools.md`](2026-08-05_ai-text-tools.md), applying
review feedback on `/fix`, `/rephrase`, `/summarize`.

## What changed

### Context menus — Discord limit hit

Shipping one message context menu per tool broke cog loading in production:

```
[FAIL] Cog error emoji: CommandLimitReached:
maximum number of message context menu commands exceeded 5 globally
```

Discord allows **5 message context menus globally** and the bot already had three
(`Save Message`, `Get Emojis`, `Translate`). The three tools now share a single
`AI text tools` menu whose modal (`MenuModal`) adds one select carrying the action
*and* its preset in a single choice (`fix`, `rephrase:professional`,
`summarize:bullets`, …). Slot usage is back to 4/5.

The entry-point contract is unchanged: slash → empty modal, context menu →
pre-filled modal.

### UI

- Containers now carry an **accent bar**, one colour per command:
  `success` (fix), `primary` (rephrase), `developer` (summarize).
- The `<:text:…>` emoji is used for all three card titles (was `edit` / `note`).
- The attribution line lost its separator and its emoji: plain
  `-# Corrected by **ChatGPT**`, in its own `TextDisplay`, distinct from the
  result text.
- The explanatory `description` under each modal's text field is gone — the label
  says enough.

### Behaviour

- **Length limit lives on the modal field only.** `TextInput.max_length` already
  makes Discord refuse an over-long submission client-side, so the redundant
  server-side check and its `errors.too_long` key were removed.
- **Mentions are kept on incognito answers.** `sanitize_text()` now only runs on
  public answers (input and output), and the `'@'` rule is only injected in the
  system prompt in that case. `AllowedMentions.none()` still applies on every
  send, so an incognito mention renders without notifying anyone.
- **`/summarize` writes in the reader's language.** `_user_language()` resolves
  `interaction.locale` to an English language name via the existing `languages.*`
  i18n namespace and the prompt asks for a translated summary when the source is
  in another language. `/fix` and `/rephrase` keep the input's language, with the
  prompt stating explicitly that the input may be in any language.
- The privacy notice replaced the mention wording: *"Do not include any personal
  or sensitive data"* + a link to <https://moddy.app/privacy>.

## Files modified

| File | Change |
|---|---|
| `cogs/text_tools.py` | Single context menu + `MenuModal`, accents, conditional sanitizing, per-command language rules, no length check |
| `locales/fr.json`, `locales/en-US.json` | `text_tools.menu.*` added, `ai_notice` rewritten, `errors.too_long` and `modal.description` removed, titles/footers updated |
| `docs/TEXT_TOOLS.md` | Rewritten to match |

## Known issues / follow-ups

- The context-menu select grows with every preset added (currently 10 options,
  cap is 25). Past that, the presets would need a second step.
- The `AI text tools` menu name is not localized (no context menu in the codebase
  is, yet).
