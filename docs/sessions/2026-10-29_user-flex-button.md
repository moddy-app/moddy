# 2026-10-29 — Flex button under `/user` in DMs

## What was done
- `/user` on **yourself**, in a DM with Moddy or a DM / group DM between users
  (`interaction.context.dm_channel` / `private_channel`), **as staff**: the card
  gets a **Flex** button. Clicking it answers the interaction with the `t.flex`
  card **non-ephemerally**, so the other person in the DM sees the proof
  (staff commands do not exist in DMs).
- The flex card moved to `utils/staff_flex.py` (`build_flex_view`), shared by
  `staff/commands/team/flex.py` and the button.
- `FlexSendButton` is a persistent `DynamicItem` (`moddy:user:flex:send:<id>`):
  only that staffer may click it (`NotYours` otherwise), and their staff roles
  are re-read on every click — a former staffer gets an ephemeral refusal.
  Registered through `UserFlexPersistence`.
- i18n `commands.user.buttons.flex` (5 locales). Tests: `tests/test_staff_flex.py`.

## Decisions
- `UserInfoView` itself stays non-persistent (existing documented exclusion);
  only the new button is persistent, as a DynamicItem it works after the
  view's 180 s timeout and after a restart.
- The flex card is in the clicker's language (`i18n.get_user_locale`).

## Not verified
Not tested live on Discord (unit tests only).
