"""
Database module - backward compatibility shim.
All implementation has moved to the db/ package.
"""
import db as _db_package
from db import ModdyDatabase, setup_database


def __getattr__(name):
    if name == 'db':
        return _db_package.db
    raise AttributeError(f"module 'database' has no attribute {name!r}")


__all__ = ['ModdyDatabase', 'db', 'setup_database']
