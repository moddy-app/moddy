"""
Serveur HTTP interne du bot.

Expose :
- GET  /health              : health check Railway (toujours 200 si le process tourne)
- GET  /status              : métriques du bot, appelé par le backend quand un staff
                              demande le statut du bot via le dashboard.
- POST /automod/rules_check : contrôle anti-injection des `indications` automod
                              (routes/automod.py).

Authentification : header `Authorization: Bearer {INTERNAL_API_SECRET}`
requis partout sauf /health et /ping (protège les données des accès non autorisés).
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import logging
import os
import time
import psutil

logger = logging.getLogger('moddy.internal_api')

app = FastAPI(
    title="Moddy Bot Internal API",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
)

# Référence globale au bot
_bot = None


def set_bot(bot):
    global _bot
    _bot = bot
    logger.info("Bot instance configured for internal API")


def get_bot():
    """Instance du bot (None tant que set_bot n'a pas été appelé)."""
    return _bot


def check_auth(request: Request) -> bool:
    """Vérifie le header Authorization si INTERNAL_API_SECRET est configuré."""
    secret = os.getenv("INTERNAL_API_SECRET")
    if not secret:
        return True  # Pas de secret configuré → accès libre (dev)
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {secret}"


# Backward-compatible alias (ancien nom privé).
_check_auth = check_auth


@app.get("/health")
async def health():
    """Health check pour Railway — toujours 200 quand le process est vivant."""
    return {"status": "healthy", "service": "moddy-bot"}


@app.get("/ping")
async def ping():
    return {"ping": "pong"}


@app.get("/status")
async def status(request: Request):
    """
    Métriques du bot Discord pour le dashboard staff.
    Authentification requise si INTERNAL_API_SECRET est configuré.
    """
    if not _check_auth(request):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    bot = _bot

    if bot is None or not bot.is_ready():
        return JSONResponse(
            status_code=503,
            content={"status": "starting", "guilds": 0, "users": 0,
                     "shards": [], "latency_ms": 0, "uptime_seconds": 0, "memory_mb": 0}
        )

    # Shards
    if bot.shard_count:
        shards = [
            {"id": sid, "latency": round(shard.latency * 1000, 2), "is_closed": shard.is_closed()}
            for sid, shard in bot.shards.items()
        ]
    else:
        shards = [{"id": 0, "latency": round(bot.latency * 1000, 2), "is_closed": False}]

    # Memory
    try:
        process = psutil.Process()
        memory_mb = round(process.memory_info().rss / 1024 / 1024, 2)
    except Exception:
        memory_mb = 0

    # Uptime
    start_time = getattr(bot, "_start_time", time.time())
    uptime_seconds = int(time.time() - start_time)

    return {
        "status": "online",
        "guilds": len(bot.guilds),
        "users": len(bot.users),
        "shards": shards,
        "latency_ms": round(bot.latency * 1000, 2),
        "uptime_seconds": uptime_seconds,
        "memory_mb": memory_mb,
    }


# --------------------------------------------------------------------------- #
# Routers
# --------------------------------------------------------------------------- #
# Imported last: routes/ modules import check_auth/get_bot from this module.
from .routes.automod import router as automod_router  # noqa: E402

app.include_router(automod_router)
