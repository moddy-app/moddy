# 2026-11-09 — AltGuard: raw i18n keys in the "fields must be filled" error

## What was reported
Saving the AltGuard `/config` panel (seen on the AltGuard mini-bot) without
both gate roles showed:

> Invalid configuration - The following fields must be filled:
> • [modules.altguard.config.unverified_role_id.section_title]
> • [modules.altguard.config.verified_role_id.section_title]

Two defects: raw keys instead of field names, and the body in the server
language under a header in the user's language.

## Cause
- `ModuleBase.get_field_label` built `modules.<id>.config.<field>.section_title`
  from the config key (`unverified_role_id`), but the panel's i18n section is
  `unverified_role`. Its `try/except` fallback never fired: `t()` does not raise
  on a missing key, it returns `[key]`.
- `save_module_config` always used the server language for the required-fields
  message, which is read privately by whoever pressed Save.

## Changes
- `modules/module_manager.py`
  - `get_field_label`: tries `<field>` then `<field>` without `_id`, treats a
    `[key]` answer as missing, then falls back to a readable name. Fixes every
    module whose required keys end in `_id` (AltGuard; Interserver already had
    both keys).
  - `save_module_config(..., locale=None)`: optional locale of the person
    saving, used for the required-fields and "owned by another app" messages;
    without it the server language is used as before.
- `modules/configs/altguard_config.py`: passes the user's locale on save.
- `tests/test_altguard.py`: labels resolve on the 5 locales + fallback.

## Follow-ups
- Other panels still get the required-fields message in the server language
  (they don't pass `locale=` yet); `validate_config` messages of every module
  also use the server language. Passing the user's locale everywhere is a
  separate, wider change.
