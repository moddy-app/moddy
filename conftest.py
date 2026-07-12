"""Pytest bootstrap — ensure the repository root is importable.

Keeps ``import automod`` / ``import utils`` working regardless of the directory
pytest is invoked from or the import mode it uses for collected test files.
"""

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
