"""
Serveur HTTP interne pour recevoir les requêtes du backend.

Ce serveur écoute sur un port SÉPARÉ (3000) pour éviter d'exposer
les endpoints internes publiquement.

Basé sur /documentation/internal-api.md
"""

from fastapi import FastAPI
import logging
import os
from internal_api.middleware.auth import verify_internal_auth
from internal_api.routes.internal import router as internal_router, set_bot_instance

# Configuration du logging
logger = logging.getLogger('moddy.internal_api')

# Créer l'application FastAPI
app = FastAPI(
    title="Moddy Bot Internal API",
    description="API interne pour la communication avec le backend",
    version="1.0.0",
    docs_url=None,  # Désactiver la documentation Swagger publique
    redoc_url=None  # Désactiver ReDoc
)

# Ajouter le middleware d'authentification
app.middleware("http")(verify_internal_auth)

# Ajouter les routes internes
app.include_router(internal_router)


@app.on_event("startup")
async def startup_event():
    """Event exécuté au démarrage du serveur."""
    logger.info("🚀 Internal API server starting...")
    logger.info(f"📡 Listening on port {os.getenv('INTERNAL_PORT', 3000)}")

    # Vérifier que le secret est configuré
    if not os.getenv("INTERNAL_API_SECRET"):
        logger.warning("⚠️ INTERNAL_API_SECRET not configured - API is INSECURE")
    else:
        logger.info("✅ INTERNAL_API_SECRET configured")


@app.on_event("shutdown")
async def shutdown_event():
    """Event exécuté à l'arrêt du serveur."""
    logger.info("⏹️ Internal API server shutting down...")


@app.get("/")
async def root():
    """Endpoint racine."""
    return {
        "service": "moddy-bot-internal",
        "status": "running",
        "version": "1.0.0"
    }


@app.get("/ping")
async def ping():
    """Endpoint de test simple."""
    return {"ping": "pong"}


@app.get("/health")
async def health():
    """
    Endpoint de health check pour Railway et autres services de monitoring.

    Retourne toujours un statut 200 quand le serveur est en ligne,
    indépendamment de l'état du bot Discord.

    Pour vérifier l'état du bot Discord, utilisez /internal/health (authentification requise).
    """
    return {
        "status": "healthy",
        "service": "moddy-bot-api",
        "version": "1.0.0"
    }


def set_bot(bot):
    """
    Définit l'instance du bot Discord pour l'API interne.

    Args:
        bot: Instance de ModdyBot
    """
    set_bot_instance(bot)
    logger.info("✅ Bot instance configured for internal API")


if __name__ == "__main__":
    import uvicorn

    # Lancer le serveur sur :: (IPv4 + IPv6)
    # Port 3000 par défaut (privé, non exposé publiquement)
    port = int(os.getenv("INTERNAL_PORT", 3000))
    logger.info(f"🚀 Démarrage du serveur interne sur le port {port}")

    uvicorn.run(
        "internal_api.server:app",
        host="::",
        port=port,
        log_level="info"
    )
