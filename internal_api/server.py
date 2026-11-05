"""
Serveur HTTP interne du bot.

Expose :
- GET  /health              : health check Railway (toujours 200 si le process tourne)
- GET  /status              : métriques du bot, appelé par le backend quand un staff
                              demande le statut du bot via le dashboard.
- POST /automod/rules_check : contrôle anti-injection d'une infraction personnalisée automod
                              (routes/automod.py).

Authentification : header `Authorization: Bearer {INTERNAL_API_SECRET}`
requis partout sauf /health et /ping (protège les données des accès non autorisés).
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import hmac
import logging
import math
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
    """Vérifie le secret partagé et échoue fermé s'il est absent."""
    secret = os.getenv("INTERNAL_API_SECRET")
    if not secret:
        logger.error("INTERNAL_API_SECRET absent: accès interne refusé")
        return False
    auth = request.headers.get("Authorization", "")
    scheme, separator, value = auth.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not value:
        return False
    return hmac.compare_digest(value.encode(), secret.encode())


# Backward-compatible alias (ancien nom privé).
_check_auth = check_auth


@app.get("/health")
async def health():
    """Health check pour Railway — toujours 200 quand le process est vivant.

    Always HTTP 200 (a restart would not fix a Postgres or Redis outage, it
    would only turn a partial outage into a total one), but the body is honest:
    ``status`` is ``healthy``, ``degraded`` (a dependency is down) or
    ``starting`` (the gateway is not ready), with the individual ``checks``.
    This route never raises, whatever state the bot is in.
    """
    checks = {}
    try:
        bot = _bot
        if bot is None:
            checks["bot"] = "starting"
        else:
            ready = bool(bot.is_ready())
            checks["discord"] = "ok" if ready else "starting"
            if os.getenv("DATABASE_URL"):
                db = getattr(bot, "db", None)
                checks["database"] = (
                    "ok" if (db is not None and getattr(db, "pool", None) is not None)
                    else "down")
            if os.getenv("REDIS_URL"):
                checks["redis"] = "ok" if getattr(bot, "redis", None) is not None else "down"
    except Exception as e:  # noqa: BLE001 - /health must never 500
        logger.warning("health check inspection failed: %s", e)
        checks["inspection"] = "error"

    if "starting" in checks.values():
        state = "starting"
    elif any(v != "ok" for v in checks.values()):
        state = "degraded"
    else:
        state = "healthy"
    return {"status": state, "service": "moddy-bot", "checks": checks}


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

    def _ms(value) -> float:
        # latency is NaN until the gateway answered once; NaN is not valid JSON
        try:
            return round(value * 1000, 2) if math.isfinite(value) else 0
        except (TypeError, ValueError):
            return 0

    # Shards
    if bot.shard_count:
        shards = [
            {"id": sid, "latency": _ms(shard.latency), "is_closed": shard.is_closed()}
            for sid, shard in bot.shards.items()
        ]
    else:
        shards = [{"id": 0, "latency": _ms(bot.latency), "is_closed": False}]

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
        "latency_ms": _ms(bot.latency),
        "uptime_seconds": uptime_seconds,
        "memory_mb": memory_mb,
    }


# --------------------------------------------------------------------------- #
# Routers
# --------------------------------------------------------------------------- #
# Imported last: routes/ modules import check_auth/get_bot from this module.
from .routes.automod import router as automod_router  # noqa: E402

app.include_router(automod_router)
