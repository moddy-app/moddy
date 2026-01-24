Instructions for Claude Code:

* If the code you need to write is a server module, you must read `/docs/Module_System.md`.
* If the code you need to write contains slash commands, you must read `/docs/COMMANDS.md`.
* If the code you need to write concerns staff/dev commands or anything related to Moddy's staff, you must read `/docs/STAFF_SYSTEM.md`.
* **If the code you need to write contains discord.ui Views or Modals, you MUST read `/docs/Error_Handling.md`** to ensure ALL errors are properly handled.
* **If the code you need to write involves any user interface, configuration panels, or interactive components, you MUST read `/docs/DESIGN.md`** for design guidelines and best practices.

In all cases, you must read `/docs/Components_V2.md` to know how to create V2 components. Also, all the text you write must use Moddy's i18n system (see `/utils/i18n.py`). Furthermore, unless explicitly stated otherwise, you must only use Moddy's "custom" emojis and not default emojis. The list of all custom emojis to use is available in `/docs/emojis.md`. If you need an emoji, icon, or anything else, don't hesitate to ask.

Additionally, **ALL UI components (Views, Modals) MUST inherit from `BaseView` or `BaseModal`** (see `/docs/Error_Handling.md`). In cogs and modules concerning "unexpected" errors, you must let the bot's global error system handle them (`error_handler.py`). Also, embed/container titles must always start with `###` followed by the icon, then the title. Example:
`### [module or command icon/emoji] module or command name`.
Additionally, commits, PRs, and code comments must be written in English.
