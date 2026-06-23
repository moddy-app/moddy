"""
Entrypoint extension for the staff command framework.

``bot.load_extensions`` auto-discovers ``staff/*.py`` files; this thin module
re-exports the framework cog's ``setup`` so the whole staff command system loads
as a single extension. The actual engine lives in ``staff/framework/``.
"""

from staff.framework.cog import setup  # noqa: F401  (re-exported for load_extension)
