"""
Mini-bots: small single-feature Discord apps that share Moddy's code and DB.

Each mini-bot (``Bump Reminder``, ...) is its own Discord application, but every
one of them runs inside ONE process (``python -m minibots.main``), on top of the
same modules and the same PostgreSQL database as Moddy. Which app runs a given
module in a given server is written in the module's own config
(``guilds.data.modules.<id>.app``), see :mod:`minibots.ownership`.

Importing this package must stay cheap: Moddy imports :mod:`minibots.ownership`
and :mod:`minibots.spec` on its hot paths, and the mini-bots process must never
pull Moddy's heavy dependencies. ``tests/test_minibots.py`` guards it.

See docs/features/MINIBOTS.md.
"""
