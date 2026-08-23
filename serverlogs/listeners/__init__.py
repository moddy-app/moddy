"""Log builders, one module per category.

Every function here has the same shape::

    async def on_something(service: LogService, ...discord objects...) -> None:
        entry = await service.open(guild, "<event>", ...)
        if entry is None:
            return
        entry.line("<field>", <formatted value>)
        await service.submit(guild, entry)

* ``service.open`` returns ``None`` when the server does not log the event,
  so the very first line of every builder is also its cost on a server that
  has logs turned off.
* Values are always formatted through :mod:`serverlogs.renderer` helpers
  (``fmt_user``, ``fmt_channel``, ``fmt_time``…) — never with f-strings — so
  a user or a channel reads identically in all 163 events.
* Labels and titles are i18n keys, never literals.

``cogs/logs.py`` owns the ``@commands.Cog.listener()`` methods and does
nothing but forward to these functions: the Discord wiring and the content
of the logs stay separate.
"""
