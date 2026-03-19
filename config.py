"""
Configuration de Moddy pour Railway
Les variables sont récupérées directement depuis l'environnement Railway
"""

import os
import sys
from pathlib import Path
from typing import List, Optional

# =============================================================================
# CONFIGURATION DISCORD
# =============================================================================

# Token du bot (obligatoire) - Variable Railway: DISCORD_TOKEN
TOKEN: str = os.environ.get("DISCORD_TOKEN", "")

# Préfixe par défaut pour les commandes
DEFAULT_PREFIX: str = os.environ.get("DEFAULT_PREFIX", "!")

# Mode debug
DEBUG: bool = os.environ.get("DEBUG", "False").lower() in ("true", "1", "yes", "on")

# IDs des développeurs (optionnel, le bot récupère depuis l'API Discord)
dev_ids_str = os.environ.get("DEVELOPER_IDS", "")
DEVELOPER_IDS: List[int] = [int(id.strip()) for id in dev_ids_str.split(",") if id.strip()]

# =============================================================================
# BASE DE DONNÉES
# =============================================================================

# URL de connexion Neon PostgreSQL - Variable Railway: DATABASE_URL
DATABASE_URL: Optional[str] = os.environ.get("DATABASE_URL")

# Pool de connexions
DB_POOL_MIN_SIZE: int = int(os.environ.get("DB_POOL_MIN_SIZE", "1"))
DB_POOL_MAX_SIZE: int = int(os.environ.get("DB_POOL_MAX_SIZE", "10"))

# =============================================================================
# API KEYS
# =============================================================================

# DeepL API pour les traductions - Variable Railway: DEEPL_API_KEY
DEEPL_API_KEY: str = os.environ.get("DEEPL_API_KEY", "")

# =============================================================================
# PARAMÈTRES DU BOT
# =============================================================================

# Intervalle de mise à jour du statut (en minutes)
STATUS_UPDATE_INTERVAL: int = int(os.environ.get("STATUS_UPDATE_INTERVAL", "10"))

# Intervalle de vérification des rappels (en secondes)
REMINDER_CHECK_INTERVAL: int = int(os.environ.get("REMINDER_CHECK_INTERVAL", "60"))

# Taille maximale du cache de préfixes
PREFIX_CACHE_SIZE: int = int(os.environ.get("PREFIX_CACHE_SIZE", "1000"))

# Timeout des commandes (en secondes)
COMMAND_TIMEOUT: int = int(os.environ.get("COMMAND_TIMEOUT", "60"))

# =============================================================================
# LIMITES ET SÉCURITÉ
# =============================================================================

# Nombre max de rappels par utilisateur
MAX_REMINDERS_PER_USER: int = int(os.environ.get("MAX_REMINDERS_PER_USER", "10"))

# Longueur max d'un tag
MAX_TAG_LENGTH: int = int(os.environ.get("MAX_TAG_LENGTH", "2000"))

# Nombre max de tags par serveur
MAX_TAGS_PER_GUILD: int = int(os.environ.get("MAX_TAGS_PER_GUILD", "50"))

# =============================================================================
# CHEMINS DU PROJET
# =============================================================================

# Racine du projet
ROOT_DIR: Path = Path(__file__).parent

# Dossiers principaux
COGS_DIR: Path = ROOT_DIR / "cogs"
STAFF_DIR: Path = ROOT_DIR / "staff"

# Fichier de logs
LOG_FILE: Path = ROOT_DIR / "moddy.log"

# =============================================================================
# COULEURS DU BOT
# =============================================================================

COLORS = {
    "primary": 0x5865F2,  # Bleu Discord
    "success": 0x57F287,  # Vert
    "warning": 0xFEE75C,  # Jaune
    "error": 0xED4245,  # Rouge
    "info": 0x5865F2,  # Bleu
    "neutral": 0x99AAB5,  # Gris
    "developer": 0x9B59B6 # Violet
}

# =============================================================================
# ENVIRONMENT MODE
# =============================================================================

# Environment mode: "production", "development", "maintenance"
ENV_MODE: str = os.environ.get("ENV_MODE", "production").lower()

# In development mode, only these user IDs can use the bot
# Comma-separated list in env var, or falls back to DEVELOPER_IDS
dev_allowed_str = os.environ.get("DEV_ALLOWED_IDS", "")
DEV_ALLOWED_IDS: List[int] = [int(id.strip()) for id in dev_allowed_str.split(",") if id.strip()] or DEVELOPER_IDS

# Convenience helpers
IS_DEV = ENV_MODE == "development"
IS_PROD = ENV_MODE == "production"
IS_MAINTENANCE = ENV_MODE == "maintenance"

# =============================================================================
# VALIDATION DE LA CONFIGURATION
# =============================================================================

def validate_config():
    """Vérifie que la configuration est valide"""
    errors = []

    # Validate environment mode
    valid_modes = ("production", "development", "maintenance")
    if ENV_MODE not in valid_modes:
        errors.append(f"[FAIL] ENV_MODE '{ENV_MODE}' is invalid. Must be one of: {', '.join(valid_modes)}")

    print(f"Environment mode: {ENV_MODE.upper()}")

    # Token obligatoire
    if not TOKEN:
        errors.append("[FAIL] DISCORD_TOKEN manquant dans les variables d'environnement Railway")

    # Vérifier que les dossiers existent
    if not COGS_DIR.exists():
        COGS_DIR.mkdir(exist_ok=True)
        print(f"Directory created: {COGS_DIR}")

    if not STAFF_DIR.exists():
        STAFF_DIR.mkdir(exist_ok=True)
        print(f"Directory created: {STAFF_DIR}")

    # Avertissements non bloquants
    if not DATABASE_URL:
        print("[WARN] DATABASE_URL not configured - running without database")

    if not DEEPL_API_KEY:
        print("[WARN] DEEPL_API_KEY not configured - translate command disabled")

    if DEBUG:
        print("Debug mode enabled")
        print("Railway environment detected")

    # Si erreurs critiques, arrêter
    if errors:
        for error in errors:
            print(error)
        sys.exit(1)

    print("Configuration validated")


# Valider au chargement du module
if __name__ != "__main__":
    validate_config()

# =============================================================================
# EXPORT POUR DEBUG
# =============================================================================

if __name__ == "__main__":
    # Pour tester la config : python config.py
    print("\nRailway Configuration:")
    print(f"  ENV_MODE: {ENV_MODE}")
    print(f"  DISCORD_TOKEN: {'configured' if TOKEN else 'MISSING'}")
    print(f"  DATABASE_URL: {'configured' if DATABASE_URL else 'not configured'}")
    print(f"  DEEPL_API_KEY: {'configured' if DEEPL_API_KEY else 'not configured'}")
    print(f"  DEBUG: {DEBUG}")
    print(f"  DEFAULT_PREFIX: {DEFAULT_PREFIX}")
    print(f"  DEVELOPER_IDS: {DEVELOPER_IDS or 'Auto-detection'}")
    print(f"\nPaths:")
    print(f"  ROOT_DIR: {ROOT_DIR}")
    print(f"  COGS_DIR: {COGS_DIR}")
    print(f"  STAFF_DIR: {STAFF_DIR}")
    print(f"  LOG_FILE: {LOG_FILE}")

    # Affiche toutes les variables d'environnement Railway (pour debug)
    if DEBUG:
        print(f"\nRailway environment variables detected:")
        railway_vars = [k for k in os.environ.keys() if
                        'RAILWAY' in k or 'DISCORD' in k or 'DATABASE' in k or 'DEEPL' in k]
        for var in sorted(railway_vars):
            value = os.environ.get(var)
            if 'TOKEN' in var or 'KEY' in var or 'PASSWORD' in var:
                value = '***' if value else 'Not set'
            print(f"  {var}: {value}")