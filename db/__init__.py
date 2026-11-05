"""
Moddy database package.
Re-exports the public API for backward compatibility.
"""

from db.base import ModdyDatabase

# Instance globale (sera initialisée dans bot.py)
db = None


async def setup_database(database_url: str = None) -> ModdyDatabase:
    """Initialise et retourne l'instance de base de données"""
    global db
    instance = ModdyDatabase(database_url)
    await instance.connect()  # raises on failure: the global stays untouched
    db = instance
    return db


__all__ = ['ModdyDatabase', 'db', 'setup_database']
