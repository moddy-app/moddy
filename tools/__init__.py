"""Pure logic of the utility tools (/define, /hash, /regex, /whois, /image...).

No Discord, no database: every module here takes plain values and returns plain
values (dataclasses, dicts, bytes), so it is unit-testable in isolation —
like ``aidetector/`` and ``bumpreminder/``. Network calls go through
``services/public_api.py`` (keyless APIs) or the gateway (keyed APIs) and are
made by the cogs/services, never imported here at module level.

See docs/features/TOOLS.md.
"""
