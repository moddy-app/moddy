# Moddy Bot — Guide d'intégration avec le Backend

> Ce document est destiné au développeur du bot Discord (`discord.py`).
> Il couvre tout ce que le bot doit implémenter pour s'intégrer correctement avec le backend FastAPI.

---

## Table des matières

1. [Architecture de communication](#1-architecture-de-communication)
2. [Connexion PostgreSQL (asyncpg)](#2-connexion-postgresql-asyncpg)
3. [Connexion Redis](#3-connexion-redis)
4. [Pub/Sub — Recevoir les notifications du backend](#4-pubsub--recevoir-les-notifications-du-backend)
5. [Redis Streams — Consommer les tâches critiques](#5-redis-streams--consommer-les-tâches-critiques)
6. [Endpoint HTTP interne `/status`](#6-endpoint-http-interne-status)
7. [Gestion des modules](#7-gestion-des-modules)
8. [Système d'attributs](#8-système-dattributs)
9. [Système de cache partagé](#9-système-de-cache-partagé)
10. [Gestion des guilds (entrée/sortie du bot)](#10-gestion-des-guilds-entréesortie-du-bot)
11. [Système Stripe (Premium)](#11-système-stripe-premium)
12. [Système staff](#12-système-staff)
13. [Création automatique users/guilds](#13-création-automatique-usersguilds)
14. [Référence des clés Redis](#14-référence-des-clés-redis)
15. [Variables d'environnement](#15-variables-denvironnement)

---

## 1. Architecture de communication

```
┌─────────────┐    HTTP REST     ┌─────────────┐
│   Frontend  │ ───────────────> │   Backend   │
│  (Vite)     │                  │  (FastAPI)  │
└─────────────┘                  └──────┬──────┘
                                        │
                          ┌─────────────┼─────────────┐
                          │             │             │
                     PostgreSQL      Redis         Redis
                     (données)    (sessions     (Pub/Sub
                                   + cache)      + Streams)
                          │             │             │
                          └─────────────┼─────────────┘
                                        │
                                 ┌──────┴──────┐
                                 │     Bot     │
                                 │ (discord.py)│
                                 └─────────────┘
```

### Règles fondamentales

1. **Le bot et le backend partagent la même base PostgreSQL** — pas de duplication de données
2. **Redis est le bus de communication** — le backend ne fait jamais d'appel HTTP vers le bot sauf pour `/status`
3. **Pub/Sub** = notifications non-critiques (fire-and-forget, le bot peut manquer)
4. **Streams** = tâches critiques garanties (XREAD BLOCK, le bot reprend où il s'est arrêté)
5. **Le bot gère les entités Discord** — il crée les lignes users/guilds dans PostgreSQL

---

## 2. Connexion PostgreSQL (asyncpg)

Le bot utilise le même `DATABASE_URL` que le backend. Le pool est géré indépendamment.

```python
import asyncpg

# Initialisation dans le bot
class MoodyBot(commands.Bot):
    async def setup_hook(self):
        self.db_pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=1,
            max_size=10,
        )

    async def close(self):
        await self.db_pool.close()
        await super().close()
```

### Utilisation dans les commandes/events

```python
# Acquérir une connexion depuis le pool
async with bot.db_pool.acquire() as conn:
    user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

# Ou directement avec le pool
row = await bot.db_pool.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
rows = await bot.db_pool.fetch("SELECT * FROM guilds WHERE attributes ? 'PREMIUM'")
value = await bot.db_pool.fetchval("SELECT COUNT(*) FROM users")
await bot.db_pool.execute("UPDATE users SET updated_at = NOW() WHERE user_id = $1", user_id)
```

### Requêtes fondamentales

#### Upsert user (créer si inexistant)

```python
async def ensure_user(pool: asyncpg.Pool, user_id: int) -> None:
    await pool.execute(
        """
        INSERT INTO users (user_id)
        VALUES ($1)
        ON CONFLICT (user_id) DO NOTHING
        """,
        user_id,
    )
```

#### Upsert guild

```python
async def ensure_guild(pool: asyncpg.Pool, guild_id: int) -> None:
    await pool.execute(
        """
        INSERT INTO guilds (guild_id)
        VALUES ($1)
        ON CONFLICT (guild_id) DO NOTHING
        """,
        guild_id,
    )
```

#### Vérifier un attribut

```python
async def has_attribute(pool: asyncpg.Pool, table: str, entity_id: int, attr: str) -> bool:
    # table = "users" ou "guilds"
    # ATTENTION : valider table avant d'interpoler (pas de paramètre $N pour les noms de tables)
    assert table in ("users", "guilds")
    pk = "user_id" if table == "users" else "guild_id"
    return await pool.fetchval(
        f"SELECT attributes ? $2 FROM {table} WHERE {pk} = $1",
        entity_id, attr,
    )

# Exemple
is_premium = await has_attribute(pool, "guilds", guild_id, "PREMIUM")
is_blacklisted = await has_attribute(pool, "users", user_id, "BLACKLISTED")
```

#### Modifier un attribut

```python
async def set_attribute(
    pool: asyncpg.Pool,
    table: str,
    entity_id: int,
    attr: str,
    value,  # True pour activer, None/False pour supprimer
) -> None:
    assert table in ("users", "guilds")
    pk = "user_id" if table == "users" else "guild_id"

    if value and value is not False:
        if isinstance(value, bool):
            json_val = '{"' + attr + '": true}'
        else:
            json_val = '{"' + attr + '": "' + str(value) + '"}'
        await pool.execute(
            f"UPDATE {table} SET attributes = attributes || $2::jsonb, updated_at = NOW() WHERE {pk} = $1",
            entity_id, json_val,
        )
    else:
        # Supprimer la clé (ne JAMAIS stocker false)
        await pool.execute(
            f"UPDATE {table} SET attributes = attributes - $2, updated_at = NOW() WHERE {pk} = $1",
            entity_id, attr,
        )
```

#### Lire la config d'un module

```python
async def get_module_config(pool: asyncpg.Pool, guild_id: int, module_id: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT data->'modules'->$2 AS config FROM guilds WHERE guild_id = $1",
        guild_id, module_id,
    )
    if not row or not row["config"]:
        return None
    import json
    return json.loads(row["config"])

# Exemple
starboard = await get_module_config(pool, guild_id, "starboard")
if starboard:
    channel_id = starboard["channel_id"]
    reaction_count = starboard.get("reaction_count", 5)
```

---

## 3. Connexion Redis

Le bot partage le même Redis que le backend.

```python
import redis.asyncio as aioredis

class MoodyBot(commands.Bot):
    async def setup_hook(self):
        self.redis = aioredis.from_url(
            REDIS_URL,
            password=REDIS_PASSWORD or None,
            decode_responses=True,
        )
        await self.redis.ping()

    async def close(self):
        await self.redis.aclose()
        await super().close()
```

---

## 4. Pub/Sub — Recevoir les notifications du backend

Le bot écoute le canal `moddy:bot`. C'est du **fire-and-forget** : si le bot n'est pas connecté au moment de la publication, le message est perdu. Utiliser pour les **recharges de config, stats, notifications non critiques**.

### Démarrer le subscriber

```python
import asyncio
import json

async def listen_pubsub(bot: MoodyBot):
    pubsub = bot.redis.pubsub()
    await pubsub.subscribe("moddy:bot")

    async for message in pubsub.listen():
        if message["type"] != "message":
            continue

        try:
            data = json.loads(message["data"])
            await handle_bot_event(bot, data)
        except Exception as e:
            print(f"[PubSub] Erreur: {e}")

# Lancer dans setup_hook
class MoodyBot(commands.Bot):
    async def setup_hook(self):
        # ...
        asyncio.create_task(listen_pubsub(self))
```

### Gestionnaire d'événements Pub/Sub

```python
async def handle_bot_event(bot: MoodyBot, data: dict):
    event_type = data.get("type")
    guild_id = data.get("guild_id")

    match event_type:
        case "config_updated":
            # Le backend a modifié les settings généraux d'un serveur
            # Invalider le cache local du bot pour ce serveur
            await invalidate_guild_cache(bot, guild_id)

        case "module_updated":
            # Un module a été modifié depuis le dashboard
            module_id = data.get("module_id")
            await reload_module(bot, guild_id, module_id)

        case "module_disabled":
            # Un module a été désactivé depuis le dashboard
            module_id = data.get("module_id")
            await disable_module_cache(bot, guild_id, module_id)

        case "logging_updated":
            # Config du logging modifiée
            await reload_logging_config(bot, guild_id)

        case "premium_activated":
            # Un serveur vient de passer Premium
            await on_premium_activated(bot, guild_id)

        case "premium_deactivated":
            # Un abonnement Premium a été annulé ou a expiré
            await on_premium_deactivated(bot, guild_id)

        case "payment_failed":
            # Un paiement a échoué
            user_id = data.get("user_id")
            await on_payment_failed(bot, user_id)

        case _:
            print(f"[PubSub] Événement inconnu: {event_type}")
```

### Publier vers le dashboard (Bot → API)

Si le bot a besoin d'envoyer des notifications au backend (stats en temps réel, etc.) :

```python
async def publish_to_dashboard(redis, event_type: str, data: dict):
    import json
    payload = json.dumps({"type": event_type, **data})
    await redis.publish("moddy:dashboard", payload)

# Exemple : notifier d'une nouvelle erreur
await publish_to_dashboard(bot.redis, "bot_error", {"error_code": "A1B2C3D4"})
```

---

## 5. Redis Streams — Consommer les tâches critiques

Le stream `moddy:tasks` contient les **tâches que le bot DOIT exécuter**. Contrairement au Pub/Sub, les messages sont persistants et le bot peut reprendre là où il s'est arrêté.

### Structure d'un message du stream

```
Clés du message Redis :
  type     : str   — type de tâche
  guild_id : str   — ID du serveur (string, convertir en int)
  payload  : str   — JSON sérialisé avec les détails
```

### Consumer principal

```python
import asyncio
import json

TASK_STREAM = "moddy:tasks"
LAST_ID_KEY = "moddy:tasks:last_id"   # Sauvegarder l'ID pour reprendre

async def consume_task_stream(bot: MoodyBot):
    # Récupérer le dernier ID traité pour reprendre sans sauter de tâches
    last_id = await bot.redis.get(LAST_ID_KEY) or "0"

    while True:
        try:
            messages = await bot.redis.xread(
                {TASK_STREAM: last_id},
                block=5000,   # Attendre 5s max, puis retry (évite un blocage infini)
                count=10,     # Traiter 10 messages à la fois max
            )

            if not messages:
                continue

            for stream_name, entries in messages:
                for entry_id, fields in entries:
                    try:
                        await process_task(bot, fields)
                        last_id = entry_id
                        # Sauvegarder la progression
                        await bot.redis.set(LAST_ID_KEY, last_id)
                    except Exception as e:
                        print(f"[Stream] Erreur tâche {entry_id}: {e}")
                        # Ne pas mettre à jour last_id → la tâche sera retentée

        except Exception as e:
            print(f"[Stream] Erreur connexion: {e}")
            await asyncio.sleep(1)

class MoodyBot(commands.Bot):
    async def setup_hook(self):
        # ...
        asyncio.create_task(consume_task_stream(self))
```

### Gestionnaire de tâches

```python
async def process_task(bot: MoodyBot, fields: dict):
    task_type = fields.get("type")
    guild_id = int(fields.get("guild_id", 0))
    payload = json.loads(fields.get("payload", "{}"))

    match task_type:
        case "update_panel":
            # Mettre à jour un message interactif Discord (panel tickets, etc.)
            await handle_update_panel(bot, guild_id, payload)

        case "send_announcement":
            # Envoyer une annonce sur des serveurs
            await handle_send_announcement(bot, payload)

        case _:
            print(f"[Stream] Type de tâche inconnu: {task_type}")


async def handle_update_panel(bot: MoodyBot, guild_id: int, payload: dict):
    """Met à jour le message d'un panel interactif sur Discord."""
    module_id = payload.get("module_id")
    guild = bot.get_guild(guild_id)
    if not guild:
        return

    # Charger la config du module depuis la DB
    config = await get_module_config(bot.db_pool, guild_id, module_id)
    if not config:
        return

    # Trouver le salon et mettre à jour le message
    channel_id = config.get("channel_id")
    channel = guild.get_channel(channel_id)
    if not channel:
        return

    # Ici : logique spécifique au module (tickets, etc.)
    # ...


async def handle_send_announcement(bot: MoodyBot, payload: dict):
    """Envoie une annonce sur les serveurs ciblés."""
    message = payload.get("message", "")
    guild_ids = payload.get("guild_ids")  # None = tous les serveurs

    target_guilds = bot.guilds if not guild_ids else [
        bot.get_guild(gid) for gid in guild_ids
    ]

    for guild in target_guilds:
        if not guild:
            continue
        # Trouver le canal système ou un salon approprié
        channel = guild.system_channel
        if channel:
            try:
                await channel.send(message)
            except Exception as e:
                print(f"[Announce] Erreur sur {guild.id}: {e}")
```

---

## 6. Endpoint HTTP interne `/status`

Le backend appelle `GET {BOT_INTERNAL_URL}/status` quand un staff demande le statut du bot. Le bot doit exposer un serveur HTTP interne sur le port `3000` (configurable).

```python
from aiohttp import web

async def create_status_server(bot: MoodyBot) -> web.Application:
    app = web.Application()

    async def status_handler(request):
        # Calculer les métriques
        shards = []
        if bot.shard_count:
            for shard_id, shard in bot.shards.items():
                shards.append({
                    "id": shard_id,
                    "latency": round(shard.latency * 1000, 2),
                    "is_closed": shard.is_closed(),
                })
        else:
            shards = [{"id": 0, "latency": round(bot.latency * 1000, 2), "is_closed": False}]

        import psutil, time
        process = psutil.Process()
        mem = process.memory_info()

        return web.json_response({
            "status": "online",
            "guilds": len(bot.guilds),
            "users": len(bot.users),
            "shards": shards,
            "latency_ms": round(bot.latency * 1000, 2),
            "uptime_seconds": int(time.time() - bot._start_time),
            "memory_mb": round(mem.rss / 1024 / 1024, 2),
        })

    app.router.add_get("/status", status_handler)
    return app


class MoodyBot(commands.Bot):
    async def setup_hook(self):
        self._start_time = __import__("time").time()
        # Démarrer le serveur HTTP interne
        app = await create_status_server(self)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 3000)
        await site.start()
```

**Important :** sur Railway, le bot écoute en IPv6 aussi pour le Railway Private Network :

```python
site = web.TCPSite(runner, "::", 3000)
```

---

## 7. Gestion des modules

### Lire la config au démarrage d'un événement

```python
async def get_starboard_config(pool, guild_id: int) -> dict | None:
    config = await get_module_config(pool, guild_id, "starboard")
    if not config or not config.get("channel_id"):
        return None  # Module non configuré
    return config


# Exemple d'usage dans un event
@bot.listen("on_reaction_add")
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    config = await get_starboard_config(bot.db_pool, reaction.message.guild.id)
    if not config:
        return  # Starboard non configuré

    required_count = config.get("reaction_count", 5)
    target_emoji = config.get("emoji", "⭐")
    channel_id = config["channel_id"]

    if str(reaction.emoji) != target_emoji:
        return
    if reaction.count < required_count:
        return

    channel = reaction.message.guild.get_channel(int(channel_id))
    if channel:
        await channel.send(f"⭐ **{reaction.count}** {reaction.message.jump_url}")
```

### Cache local des configs (recommandé)

Pour éviter des appels DB à chaque événement Discord, maintenir un cache local avec TTL court.

```python
import time
from dataclasses import dataclass, field

@dataclass
class CachedConfig:
    data: dict
    expires_at: float

class ConfigCache:
    def __init__(self, ttl: int = 60):  # 60 secondes
        self._cache: dict[str, CachedConfig] = {}
        self._ttl = ttl

    def get(self, key: str) -> dict | None:
        entry = self._cache.get(key)
        if entry and time.monotonic() < entry.expires_at:
            return entry.data
        return None

    def set(self, key: str, data: dict):
        self._cache[key] = CachedConfig(
            data=data,
            expires_at=time.monotonic() + self._ttl,
        )

    def invalidate(self, key: str):
        self._cache.pop(key, None)

    def invalidate_guild(self, guild_id: int):
        prefix = f"guild:{guild_id}:"
        keys = [k for k in self._cache if k.startswith(prefix)]
        for k in keys:
            del self._cache[k]

# Utilisation
config_cache = ConfigCache(ttl=60)

async def get_cached_module(pool, guild_id: int, module_id: str) -> dict | None:
    key = f"guild:{guild_id}:{module_id}"
    cached = config_cache.get(key)
    if cached is not None:
        return cached

    config = await get_module_config(pool, guild_id, module_id)
    if config:
        config_cache.set(key, config)
    return config

# Quand on reçoit un event Pub/Sub module_updated
async def reload_module(bot, guild_id, module_id):
    config_cache.invalidate(f"guild:{guild_id}:{module_id}")
    # Optionnel : recharger immédiatement depuis DB
```

---

## 8. Système d'attributs

**Règle absolue : ne jamais stocker `false` dans les attributs JSONB.** Pour désactiver, supprimer la clé.

### Activer / désactiver un attribut

```python
# Activer PREMIUM sur un serveur
await bot.db_pool.execute(
    "UPDATE guilds SET attributes = attributes || '{\"PREMIUM\": true}'::jsonb, updated_at = NOW() WHERE guild_id = $1",
    guild_id,
)

# Désactiver PREMIUM (supprimer la clé, jamais false)
await bot.db_pool.execute(
    "UPDATE guilds SET attributes = attributes - 'PREMIUM', updated_at = NOW() WHERE guild_id = $1",
    guild_id,
)

# Setter LANG
await bot.db_pool.execute(
    "UPDATE users SET attributes = attributes || $2::jsonb, updated_at = NOW() WHERE user_id = $1",
    user_id, '{"LANG": "FR"}',
)
```

### Logger dans attribute_changes

Pour tout changement significatif (PREMIUM, BLACKLISTED, TEAM), insérer un audit log :

```python
async def log_attribute_change(
    pool,
    entity_type: str,  # "user" ou "guild"
    entity_id: int,
    attribute_name: str,
    old_value,
    new_value,
    changed_by: int,
    reason: str | None = None,
):
    await pool.execute(
        """
        INSERT INTO attribute_changes
            (entity_type, entity_id, attribute_name, old_value, new_value, changed_by, reason)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        entity_type, entity_id, attribute_name,
        str(old_value) if old_value is not None else None,
        str(new_value) if new_value is not None else None,
        changed_by, reason,
    )
```

---

## 9. Système de cache partagé

Certaines clés Redis sont partagées entre le backend et le bot. **Le bot doit respecter les mêmes conventions de clés.**

### Clés Redis partagées

| Clé | TTL | Propriétaire | Description |
|---|---|---|---|
| `session:{token}` | 30j | Backend | Sessions utilisateur (bot en lecture seule) |
| `guild:{id}:config` | 5min | Backend | Config DB du serveur (backend invalide, bot peut lire) |
| `moddy:bot_guilds` | 5min | Backend | Liste des guild IDs où le bot est présent |
| `discord:guild:{id}:info` | 5min | Backend | Infos Discord du serveur |
| `discord:guild:{id}:channels` | 2min | Backend | Salons du serveur |
| `discord:guild:{id}:roles` | 2min | Backend | Rôles du serveur |
| `moddy:tasks:last_id` | permanent | Bot | Dernier ID de stream traité |

### Convention pour les clés spécifiques au bot

Utiliser le préfixe `bot:` pour les clés propres au bot (pour éviter les collisions) :

```python
# Exemples de clés bot-only
f"bot:guild:{guild_id}:module:{module_id}"   # Cache config module
f"bot:user:{user_id}:lang"                    # Langue d'un user (courte durée)
f"bot:interserver:networks"                   # Cache des réseaux interserver
```

### Mettre à jour `moddy:bot_guilds`

Quand le bot rejoint ou quitte un serveur, il doit invalider cette clé pour que le backend recalcule la liste :

```python
@bot.event
async def on_guild_join(guild):
    await ensure_guild(bot.db_pool, guild.id)
    # Invalider le cache des guilds du bot
    await bot.redis.delete("moddy:bot_guilds")

@bot.event
async def on_guild_remove(guild):
    await bot.redis.delete("moddy:bot_guilds")
```

---

## 10. Gestion des guilds (entrée/sortie du bot)

```python
@bot.event
async def on_guild_join(guild: discord.Guild):
    # 1. Créer l'entrée en base si elle n'existe pas
    await ensure_guild(bot.db_pool, guild.id)

    # 2. Invalider le cache backend des guilds du bot
    await bot.redis.delete("moddy:bot_guilds")

    # 3. Log optionnel
    print(f"[Bot] Rejoint: {guild.name} ({guild.id}) — {guild.member_count} membres")


@bot.event
async def on_guild_remove(guild: discord.Guild):
    # 1. Invalider le cache
    await bot.redis.delete("moddy:bot_guilds")
    await bot.redis.delete(f"discord:guild:{guild.id}:info")
    await bot.redis.delete(f"discord:guild:{guild.id}:channels")
    await bot.redis.delete(f"discord:guild:{guild.id}:roles")

    # Note : NE PAS supprimer les données de la guild en DB
    # Le backend et les admins peuvent avoir besoin de l'historique
    print(f"[Bot] Quitté: {guild.name} ({guild.id})")


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return

    # Créer l'utilisateur en base si inexistant
    await ensure_user(bot.db_pool, member.id)

    # Vérifier si l'utilisateur est blacklisté
    blacklisted = await has_attribute(bot.db_pool, "users", member.id, "BLACKLISTED")
    if blacklisted:
        # Action selon la policy (kick, ban, notifier les mods...)
        return

    # Charger le module auto_role
    config = await get_cached_module(bot.db_pool, member.guild.id, "auto_role")
    if config:
        role_ids = config.get("role_ids", [])
        for role_id in role_ids:
            role = member.guild.get_role(int(role_id))
            if role:
                try:
                    await member.add_roles(role, reason="Auto Role Moddy")
                except discord.Forbidden:
                    pass

    # Module welcome_channel
    welcome = await get_cached_module(bot.db_pool, member.guild.id, "welcome_channel")
    if welcome and welcome.get("channel_id"):
        channel = member.guild.get_channel(int(welcome["channel_id"]))
        if channel:
            template = welcome.get("message_template", "Bienvenue {user} !")
            msg = template.replace("{user}", member.mention)
            await channel.send(msg)
```

---

## 11. Système Stripe (Premium)

Le bot **ne gère pas Stripe directement** — c'est le backend qui traite les webhooks. Mais le bot réagit aux événements Pub/Sub :

```python
async def on_premium_activated(bot: MoodyBot, guild_id: int):
    """Appelé quand un serveur passe Premium (via Pub/Sub)."""
    # Invalider le cache local
    config_cache.invalidate_guild(guild_id)

    # Optionnel : envoyer un message de félicitations dans le canal système
    guild = bot.get_guild(guild_id)
    if guild and guild.system_channel:
        try:
            await guild.system_channel.send(
                "🎉 Ce serveur est maintenant **Premium** ! Toutes les fonctionnalités sont débloquées."
            )
        except discord.Forbidden:
            pass


async def on_premium_deactivated(bot: MoodyBot, guild_id: int):
    """Appelé quand l'abonnement Premium expire ou est annulé."""
    config_cache.invalidate_guild(guild_id)

    guild = bot.get_guild(guild_id)
    if guild and guild.system_channel:
        try:
            await guild.system_channel.send(
                "⚠️ L'abonnement Premium de ce serveur a expiré. Certaines fonctionnalités sont désactivées."
            )
        except discord.Forbidden:
            pass
```

### Vérifier le statut Premium dans les commandes

```python
async def require_premium(ctx: commands.Context) -> bool:
    """Vérifie si le serveur a le Premium avant d'exécuter une commande."""
    is_premium = await has_attribute(bot.db_pool, "guilds", ctx.guild.id, "PREMIUM")
    if not is_premium:
        await ctx.send("❌ Cette fonctionnalité nécessite **Moddy Premium**. Abonnez-vous sur https://moddy.app")
        return False
    return True
```

---

## 12. Système staff

### Vérifier si un utilisateur est staff

```python
async def is_staff(pool, user_id: int) -> bool:
    return await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM staff_permissions WHERE user_id = $1)",
        user_id,
    )


async def get_staff_roles(pool, user_id: int) -> list[str]:
    row = await pool.fetchrow(
        "SELECT roles FROM staff_permissions WHERE user_id = $1",
        user_id,
    )
    if not row:
        return []
    import json
    return json.loads(row["roles"])
```

### Ajouter un membre au staff

```python
async def add_staff_role(pool, user_id: int, role: str, added_by: int) -> None:
    """Ajoute un rôle staff à un utilisateur. Crée l'entrée si inexistante."""
    existing = await pool.fetchrow(
        "SELECT roles FROM staff_permissions WHERE user_id = $1", user_id
    )

    if existing:
        # Ajouter le rôle
        await pool.execute(
            """
            UPDATE staff_permissions
            SET roles = roles || $2::jsonb, updated_by = $3, updated_at = NOW()
            WHERE user_id = $1
            """,
            user_id, f'["{role}"]', added_by,
        )
    else:
        # Créer l'entrée
        await pool.execute(
            """
            INSERT INTO staff_permissions (user_id, roles, created_by, updated_by)
            VALUES ($1, $2::jsonb, $3, $3)
            """,
            user_id, f'["{role}"]', added_by,
        )

    # Mettre le flag TEAM (OBLIGATOIRE)
    await pool.execute(
        "UPDATE users SET attributes = attributes || '{\"TEAM\": true}'::jsonb WHERE user_id = $1",
        user_id,
    )
    await log_attribute_change(pool, "user", user_id, "TEAM", None, True, added_by, f"Ajout rôle {role}")
```

### Retirer un membre du staff

```python
async def remove_staff_role(pool, user_id: int, role: str, removed_by: int) -> None:
    """Retire un rôle staff. Supprime l'entrée si plus aucun rôle."""
    # Retirer le rôle du tableau JSONB
    await pool.execute(
        """
        UPDATE staff_permissions
        SET roles = (
            SELECT COALESCE(jsonb_agg(r), '[]'::jsonb)
            FROM jsonb_array_elements_text(roles) r
            WHERE r != $2
        ),
        updated_by = $3, updated_at = NOW()
        WHERE user_id = $1
        """,
        user_id, role, removed_by,
    )

    # Vérifier s'il reste des rôles
    remaining = await pool.fetchval(
        "SELECT jsonb_array_length(roles) FROM staff_permissions WHERE user_id = $1",
        user_id,
    )

    if remaining == 0:
        # Plus aucun rôle → supprimer l'entrée et retirer TEAM
        await pool.execute("DELETE FROM staff_permissions WHERE user_id = $1", user_id)
        await pool.execute(
            "UPDATE users SET attributes = attributes - 'TEAM', updated_at = NOW() WHERE user_id = $1",
            user_id,
        )
        await log_attribute_change(pool, "user", user_id, "TEAM", True, None, removed_by, "Dernier rôle staff retiré")
```

---

## 13. Création automatique users/guilds

Tout contact avec la DB doit s'assurer que l'entité existe. Pattern standard :

```python
@bot.listen("on_message")
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not message.guild:
        return

    # Upsert silencieux — ON CONFLICT DO NOTHING = pas d'erreur si déjà existant
    await bot.db_pool.execute(
        "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
        message.author.id,
    )
    await bot.db_pool.execute(
        "INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT DO NOTHING",
        message.guild.id,
    )
```

### Logger une erreur dans la DB

```python
import secrets
import traceback

async def log_error(pool, error: Exception, user_id: int | None = None, guild_id: int | None = None, command: str | None = None):
    error_code = secrets.token_hex(4).upper()
    tb = traceback.format_exc()

    await pool.execute(
        """
        INSERT INTO errors (error_code, error_type, message, traceback, user_id, guild_id, command)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        error_code,
        type(error).__name__,
        str(error),
        tb,
        user_id,
        guild_id,
        command,
    )
    return error_code
```

---

## 14. Référence des clés Redis

### Clés gérées par le backend (lire uniquement depuis le bot)

| Clé | Type | TTL | Description |
|---|---|---|---|
| `session:{token}` | String (JSON) | 30j | Session utilisateur |
| `guild:{id}:config` | String (JSON) | 5min | Config DB du serveur |
| `moddy:bot_guilds` | String (JSON) | 5min | Liste des guild IDs du bot |
| `discord:guild:{id}:info` | String (JSON) | 5min | Infos Discord |
| `discord:guild:{id}:channels` | String (JSON) | 2min | Salons |
| `discord:guild:{id}:roles` | String (JSON) | 2min | Rôles |
| `discord:guild:{id}:emojis` | String (JSON) | 5min | Emojis |

### Canaux Pub/Sub

| Canal | Direction | Usage |
|---|---|---|
| `moddy:bot` | Backend → Bot | Notifications de config, premium, etc. |
| `moddy:dashboard` | Bot → Backend | Notifications du bot vers le dashboard |

### Streams

| Stream | Producteur | Consommateur |
|---|---|---|
| `moddy:tasks` | Backend | Bot |

### Clés à usage du bot

| Clé | Type | Description |
|---|---|---|
| `moddy:tasks:last_id` | String | Dernier ID de stream consommé |

---

## 15. Variables d'environnement

Le bot a besoin des variables suivantes :

```env
# Base de données (même que le backend)
DATABASE_URL=postgresql://user:password@host:5432/dbname

# Redis (même que le backend)
REDIS_URL=redis://host:6379
REDIS_PASSWORD=

# Discord
DISCORD_TOKEN=Bot token du bot
DISCORD_CLIENT_ID=ID de l'application Discord

# Port HTTP interne (pour /status)
PORT=3000

# Environnement
ENVIRONMENT=production
```

---

## Checklist d'intégration

- [ ] Pool asyncpg initialisé avec `min_size=1, max_size=10`
- [ ] Redis initialisé avec `decode_responses=True`
- [ ] `ensure_user` et `ensure_guild` appelés sur chaque interaction
- [ ] Subscriber Pub/Sub `moddy:bot` actif et gérant tous les event types
- [ ] Consumer Stream `moddy:tasks` actif avec sauvegarde du `last_id`
- [ ] Serveur HTTP `/status` exposé sur le port `3000`
- [ ] Invalidation de `moddy:bot_guilds` sur `on_guild_join` et `on_guild_remove`
- [ ] Attribut `TEAM` toujours synchronisé avec `staff_permissions` (ajout ET suppression ensemble)
- [ ] Jamais de `false` stocké dans les attributs JSONB
- [ ] Audit log dans `attribute_changes` pour PREMIUM, BLACKLISTED, TEAM
- [ ] `log_error` appelé dans les handlers d'exception globaux
- [ ] IDs Discord stockés en `BIGINT` (jamais en `INTEGER`)
- [ ] Timestamps toujours en UTC
