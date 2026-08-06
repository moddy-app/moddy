"""Pytest bootstrap — ensure the repository root is importable.

Keeps ``import automod`` / ``import utils`` working regardless of the directory
pytest is invoked from or the import mode it uses for collected test files.
"""

import os
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# config.py calls sys.exit(1) at import time if DISCORD_TOKEN is unset (it is
# meant to run under Railway). Tests never talk to Discord, so a placeholder
# satisfies validate_config() without requiring real credentials.
os.environ.setdefault("DISCORD_TOKEN", "test-token-not-a-real-credential")
