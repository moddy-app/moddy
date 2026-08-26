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

# Moddy team guild + the channel where automod sanction appeals routed to the
# Moddy team are reviewed by staff.
MODDY_TEAM_GUILD_ID: int = int(os.environ.get("MODDY_GUILD_ID", "1394001780148535387"))
MODDY_APPEAL_CHANNEL_ID: int = int(os.environ.get("MODDY_APPEAL_CHANNEL_ID", "1521246998127317114"))

# Centralized notifications (docs/NOTIFICATIONS.md): where abuse reports filed
# from a DM's flag button are reviewed, and where every step of their handling
# is logged. Both live in the Moddy team guild.
MODDY_NOTIF_REPORT_CHANNEL_ID: int = int(
    os.environ.get("MODDY_NOTIF_REPORT_CHANNEL_ID", "1541231528754028594"))
MODDY_NOTIF_REPORT_LOG_CHANNEL_ID: int = int(
    os.environ.get("MODDY_NOTIF_REPORT_LOG_CHANNEL_ID", "1541233478522241034"))

# Support requests (docs/SUPPORT_REQUESTS.md): where /bug-report lands, and
# where a server owner asking the team to configure Moddy for them lands. Both
# live in the Moddy team guild and are answered from the card itself.
MODDY_BUG_REPORT_CHANNEL_ID: int = int(
    os.environ.get("MODDY_BUG_REPORT_CHANNEL_ID", "1542307806055759943"))
MODDY_CONFIG_HELP_CHANNEL_ID: int = int(
    os.environ.get("MODDY_CONFIG_HELP_CHANNEL_ID", "1542307892970131516"))

# Public Moddy URLs, referenced from panels, notifications and welcome cards.
SUPPORT_URL: str = os.environ.get("MODDY_SUPPORT_URL", "https://moddy.app/support")
DASHBOARD_URL: str = os.environ.get("MODDY_DASHBOARD_URL", "https://dashboard.moddy.app")
DOCS_URL: str = os.environ.get("MODDY_DOCS_URL", "https://docs.moddy.app")

# Clickable mention of /config, used inside notification bodies. A command
# mention needs the registered command id, which is stable for the application
# but changes if the command is ever re-created — hence the override.
CONFIG_COMMAND_MENTION: str = os.environ.get(
    "MODDY_CONFIG_COMMAND_MENTION", "</config:1444430277970497653>")

# =============================================================================
# BASE DE DONNÉES
# =============================================================================

# URL de connexion PostgreSQL - Variable Railway: DATABASE_URL
DATABASE_URL: Optional[str] = os.environ.get("DATABASE_URL")

# Pool de connexions — utilisé par db/base.py::connect().
# Chaque connexion asyncpg garde un cache de requêtes préparées et ses buffers,
# côté bot comme côté Postgres (service Railway facturé séparément). À l'échelle
# actuelle le pool n'est jamais saturé, d'où des défauts volontairement bas.
DB_POOL_MIN_SIZE: int = int(os.environ.get("DB_POOL_MIN_SIZE", "1"))
DB_POOL_MAX_SIZE: int = int(os.environ.get("DB_POOL_MAX_SIZE", "8"))

# =============================================================================
# REDIS
# =============================================================================

# URL de connexion Redis - Variable Railway: REDIS_URL
REDIS_URL: Optional[str] = os.environ.get("REDIS_URL", "redis://localhost:6379")

# Mot de passe Redis (optionnel)
REDIS_PASSWORD: Optional[str] = os.environ.get("REDIS_PASSWORD") or None

# Secret HMAC partagé backend ⇄ bot pour signer les entrées du stream
# `moddy:tasks` - Variable Railway: TASK_STREAM_SECRET
# Générer avec: python -c "import secrets; print(secrets.token_urlsafe(48))"
# NE JAMAIS réutiliser REDIS_PASSWORD : le modèle de menace est précisément
# celui d'un attaquant qui possède déjà l'accès Redis. Voir docs/TASK_SIGNATURE.md
TASK_STREAM_SECRET: str = os.environ.get("TASK_STREAM_SECRET", "")

# Fenêtre de déploiement uniquement (docs/TASK_SIGNATURE.md §6) : accepte les
# entrées non signées tant que le backend ne signe pas encore. À remettre à
# false dès que le backend est en production.
TASK_STREAM_ALLOW_UNSIGNED: bool = os.environ.get(
    "TASK_STREAM_ALLOW_UNSIGNED", "false"
).lower() in ("true", "1", "yes")

# =============================================================================
# API KEYS
# =============================================================================

# DeepL API pour les traductions - Variable Railway: DEEPL_API_KEY
DEEPL_API_KEY: str = os.environ.get("DEEPL_API_KEY", "")

# OpenAI API - Variable Railway: OPENAI_API_KEY
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")

# Fernet key for token_detector in-memory cache encryption - Variable Railway: TOKEN_DETECTOR_KEY
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOKEN_DETECTOR_KEY: str = os.environ.get("TOKEN_DETECTOR_KEY", "")

# =============================================================================
# ALTGUARD (anti multi-account verification service)
# =============================================================================
# The bot never talks to AltGuard's database: it calls two HTTP endpoints and
# exchanges two Redis Pub/Sub channels with it (see docs/ALTGUARD.md).

# Base URL of the AltGuard service - Variable Railway: ALTGUARD_API_URL
ALTGUARD_API_URL: str = os.environ.get(
    "ALTGUARD_API_URL", "https://verify.moddy.app"
).rstrip("/")

# Shared secret sent as `Authorization: Bearer` - Variable Railway: ALTGUARD_BOT_TOKEN
ALTGUARD_BOT_TOKEN: str = os.environ.get("ALTGUARD_BOT_TOKEN", "")

# =============================================================================
# MODDY HEALTH MONITOR (dead man's switch — see docs/HEALTH_MONITOR.md)
# =============================================================================
# The bot pushes a heartbeat every 20s; the monitor never polls it. Either
# variable missing disables the heartbeat with a warning, never a crash.

# Base URL of the health monitor, no trailing slash - Variable Railway: HM_URL
HM_URL: str = os.environ.get("HM_URL", "").rstrip("/")

# Shared secret sent as `X-Health-Token` - Variable Railway: HM_INGEST_TOKEN
HM_INGEST_TOKEN: str = os.environ.get("HM_INGEST_TOKEN", "")

# =============================================================================
# BETTER STACK HEARTBEAT MONITOR (cron/heartbeat monitor, see docs/HEALTH_MONITOR.md)
# =============================================================================
# Separate from the Moddy Health Monitor above: a plain GET every 3 minutes to
# a secret Better Stack URL means "alive"; GET .../fail reports a failure
# explicitly. Missing this variable disables the ping with a warning.

# Full secret heartbeat URL from the Better Stack heartbeat detail page,
# e.g. https://uptime.betterstack.com/api/v1/heartbeat/<TOKEN>
# Variable Railway: BETTERSTACK_HEARTBEAT_URL
BETTERSTACK_HEARTBEAT_URL: str = os.environ.get("BETTERSTACK_HEARTBEAT_URL", "").rstrip("/")

# =============================================================================
# TECHNICAL LOGS (internal staff webhooks)
# =============================================================================
# Internal technical logs are NOT sent by the bot itself but through Discord
# webhooks, one channel per event type. Each category reads its own webhook URL
# from a Railway environment variable. Any category left unset silently falls
# back to LOG_WEBHOOK_DEFAULT (and if that is unset too, the category is muted).
# See docs/TECHNICAL_LOGS.md for the full contract.

# category -> environment variable name
LOG_WEBHOOK_ENV = {
    "guild_join": "LOG_WEBHOOK_GUILD_JOIN",
    "guild_remove": "LOG_WEBHOOK_GUILD_REMOVE",
    "error": "LOG_WEBHOOK_ERROR",
    "lifecycle": "LOG_WEBHOOK_LIFECYCLE",      # startup / shutdown / health
    "staff_command": "LOG_WEBHOOK_STAFF_COMMAND",
    "staff_action": "LOG_WEBHOOK_STAFF_ACTION",
    "command": "LOG_WEBHOOK_COMMAND",          # non-staff command usage
    "database": "LOG_WEBHOOK_DATABASE",        # config changes / important writes
    "security": "LOG_WEBHOOK_SECURITY",        # blacklist blocks & sensitive events
    "api_call": "LOG_WEBHOOK_API_CALL",        # gateway: every outbound API call
    # bot identity changed on a server (nickname / avatar / bio / name style)
    "bot_customization": "LOG_WEBHOOK_BOT_CUSTOMIZATION",
}

# Resolved category -> webhook URL (only those actually configured)
LOG_WEBHOOKS = {
    category: os.environ.get(env_var, "").strip()
    for category, env_var in LOG_WEBHOOK_ENV.items()
    if os.environ.get(env_var, "").strip()
}

# Optional single fallback webhook for any category without a dedicated URL
LOG_WEBHOOK_DEFAULT: str = os.environ.get("LOG_WEBHOOK_DEFAULT", "").strip()

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

    if not REDIS_URL:
        print("[WARN] REDIS_URL not configured - Redis features disabled")

    if not TASK_STREAM_SECRET:
        print(
            "[ERROR] TASK_STREAM_SECRET not configured - every moddy:tasks entry "
            "will be rejected (bot customization, staff announcements, panel "
            "updates and dashboard sanctions are disabled). Generate one with "
            "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"` and "
            "set the SAME value on the bot and the backend."
        )
    elif len(TASK_STREAM_SECRET) < 32:
        print(
            f"[ERROR] TASK_STREAM_SECRET is too short ({len(TASK_STREAM_SECRET)} "
            "chars, 32 minimum) - the backend refuses to sign below that length, "
            "so no task will ever verify."
        )

    if TASK_STREAM_ALLOW_UNSIGNED:
        print(
            "[WARN] TASK_STREAM_ALLOW_UNSIGNED is enabled - unsigned moddy:tasks "
            "entries are executed. Deployment window only, turn it off."
        )

    if not DEEPL_API_KEY:
        print("[WARN] DEEPL_API_KEY not configured - translate command disabled")

    if not OPENAI_API_KEY:
        print("[WARN] OPENAI_API_KEY not configured - OpenAI features disabled")

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
