"""
Système de gestion d'erreurs avancé pour Moddy
Tracking, logs Discord et notifications avec base de données
"""

import nextcord
from nextcord.ext import commands
import traceback
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import asyncio
from collections import deque

from config import COLORS


class ErrorTracker(commands.Cog):
    """Système de tracking et gestion des erreurs"""

    def __init__(self, bot):
        self.bot = bot
        self.error_cache = deque(maxlen=100)  # Garde les 100 dernières erreurs en mémoire
        self.error_channel_id = 1392439223717724160
        self.dev_user_id = 1164597199594852395

    def generate_error_code(self, error: Exception, ctx: Optional[commands.Context] = None) -> str:
        """Génère un code d'erreur unique"""
        # Utilise le hash de l'erreur + timestamp pour l'unicité
        error_str = f"{type(error).__name__}:{str(error)}:{datetime.now().timestamp()}"
        hash_obj = hashlib.md5(error_str.encode())
        return hash_obj.hexdigest()[:8].upper()

    def store_error(self, error_code: str, error_data: Dict[str, Any]):
        """Stocke l'erreur dans le cache mémoire"""
        self.error_cache.append({
            "code": error_code,
            "timestamp": datetime.now(timezone.utc),
            "data": error_data
        })

    async def store_error_db(self, error_code: str, error_data: Dict[str, Any], ctx: Optional[commands.Context] = None):
        """Stocke l'erreur dans la base de données"""
        if not self.bot.db:
            return

        try:
            # Prépare les données pour la BDD
            db_data = {
                "type": error_data.get("type"),
                "message": error_data.get("message"),
                "file": error_data.get("file"),
                "line": int(error_data.get("line")) if error_data.get("line", "").isdigit() else None,
                "traceback": error_data.get("traceback"),
                "user_id": None,
                "guild_id": None,
                "command": error_data.get("command"),
                "context": {}
            }

            # Ajoute les infos de contexte si disponibles
            if ctx:
                db_data["user_id"] = ctx.author.id
                db_data["guild_id"] = ctx.guild.id if ctx.guild else None
                db_data["context"] = {
                    "channel": str(ctx.channel),
                    "message": ctx.message.content[:200] if hasattr(ctx, 'message') else None
                }

            # Stocke dans la BDD
            await self.bot.db.log_error(error_code, db_data)

        except Exception as e:
            import logging
            logger = logging.getLogger('moddy')
            logger.error(f"Erreur lors du stockage en BDD: {e}")

    async def get_error_channel(self) -> Optional[nextcord.TextChannel]:
        """Récupère le canal d'erreurs"""
        return self.bot.get_channel(self.error_channel_id)

    def format_error_details(self, error: Exception, ctx: Optional[commands.Context] = None) -> Dict[str, Any]:
        """Formate les détails de l'erreur"""
        tb = traceback.format_exception(type(error), error, error.__traceback__)

        # Trouve le fichier source
        source_file = "Inconnu"
        line_number = "?"
        for line in tb:
            if "File" in line and "site-packages" not in line:
                parts = line.strip().split('"')
                if len(parts) >= 2:
                    source_file = parts[1].split('/')[-1]
                    line_parts = line.split("line ")
                    if len(line_parts) >= 2:
                        line_number = line_parts[1].split(",")[0]
                    break

        details = {
            "type": type(error).__name__,
            "message": str(error),
            "file": source_file,
            "line": line_number,
            "traceback": ''.join(tb[-3:])  # Dernières 3 lignes du traceback
        }

        if ctx:
            details.update({
                "command": str(ctx.command) if ctx.command else "Aucune",
                "user": f"{ctx.author} ({ctx.author.id})",
                "guild": f"{ctx.guild.name} ({ctx.guild.id})" if ctx.guild else "DM",
                "channel": f"#{ctx.channel.name}" if hasattr(ctx.channel, 'name') else "DM",
                "message": ctx.message.content[:100] + "..." if len(ctx.message.content) > 100 else ctx.message.content
            })

        return details

    async def send_error_log(self, error_code: str, error_details: Dict[str, Any], is_fatal: bool = False):
        """Envoie le log d'erreur dans le canal Discord"""
        channel = await self.get_error_channel()
        if not channel:
            return

        # Détermine la couleur selon la gravité
        color = COLORS["error"] if is_fatal else COLORS["warning"]

        embed = nextcord.Embed(
            title=f"{'Erreur Fatale' if is_fatal else 'Erreur'} Détectée",
            color=color,
            timestamp=datetime.now(timezone.utc)
        )

        # En-tête avec le code d'erreur
        embed.add_field(
            name="Code d'erreur",
            value=f"`{error_code}`",
            inline=True
        )

        embed.add_field(
            name="Type",
            value=f"`{error_details['type']}`",
            inline=True
        )

        embed.add_field(
            name="Fichier",
            value=f"`{error_details['file']}:{error_details['line']}`",
            inline=True
        )

        # Message d'erreur
        embed.add_field(
            name="Message",
            value=f"```{error_details['message'][:500]}```",
            inline=False
        )

        # Contexte si disponible
        if 'command' in error_details:
            embed.add_field(
                name="Contexte",
                value=(
                    f"**Commande:** `{error_details['command']}`\n"
                    f"**Utilisateur:** {error_details['user']}\n"
                    f"**Serveur:** {error_details['guild']}\n"
                    f"**Canal:** {error_details['channel']}"
                ),
                inline=False
            )

            if 'message' in error_details:
                embed.add_field(
                    name="Message original",
                    value=f"```{error_details['message']}```",
                    inline=False
                )

        # Traceback pour les erreurs fatales
        if is_fatal and 'traceback' in error_details:
            embed.add_field(
                name="Traceback",
                value=f"```py\n{error_details['traceback'][:500]}```",
                inline=False
            )

        # Note sur la BDD
        if self.bot.db:
            embed.set_footer(text="✅ Erreur enregistrée dans la base de données")
        else:
            embed.set_footer(text="⚠️ Base de données non connectée - Erreur en cache uniquement")

        # Ping pour les erreurs fatales
        content = f"<@{self.dev_user_id}> Erreur fatale détectée !" if is_fatal else None

        await channel.send(content=content, embed=embed)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Gestion des erreurs de commandes"""
        # Erreurs ignorées (déjà gérées)
        ignored = (
            commands.CommandNotFound,
            commands.NotOwner,
            commands.CheckFailure,
            commands.DisabledCommand,
            commands.NoPrivateMessage
        )

        if isinstance(error, ignored):
            return

        # Erreurs avec gestion spécifique (pas de log)
        if isinstance(error, commands.MissingPermissions):
            embed = nextcord.Embed(
                title="Permissions insuffisantes",
                description=f"Permissions manquantes : `{', '.join(error.missing_permissions)}`",
                color=COLORS["error"]
            )
            await ctx.send(embed=embed)
            return

        if isinstance(error, commands.CommandOnCooldown):
            embed = nextcord.Embed(
                title="Cooldown actif",
                description=f"Réessaye dans `{error.retry_after:.1f}` secondes",
                color=COLORS["warning"]
            )
            await ctx.send(embed=embed)
            return

        if isinstance(error, commands.MissingRequiredArgument):
            embed = nextcord.Embed(
                title="Argument manquant",
                description=f"L'argument `{error.param.name}` est requis",
                color=COLORS["error"]
            )
            await ctx.send(embed=embed)
            return

        # Pour toutes les autres erreurs, on log
        error_code = self.generate_error_code(error, ctx)
        error_details = self.format_error_details(error.original if hasattr(error, 'original') else error, ctx)

        # Stocke l'erreur en mémoire
        self.store_error(error_code, error_details)

        # Stocke dans la BDD si disponible
        await self.store_error_db(error_code, error_details, ctx)

        # Détermine si c'est fatal
        is_fatal = isinstance(error.original if hasattr(error, 'original') else error, (
            RuntimeError,
            AttributeError,
            ImportError,
            MemoryError,
            SystemError
        ))

        # Log dans Discord
        await self.send_error_log(error_code, error_details, is_fatal)

        # Message à l'utilisateur
        embed = nextcord.Embed(
            title="Une erreur est survenue",
            description=(
                f"**Code d'erreur :** `{error_code}`\n\n"
                "Cette erreur a été enregistrée et sera analysée.\n"
                "Tu peux fournir ce code au développeur si nécessaire."
            ),
            color=COLORS["error"],
            timestamp=datetime.now(timezone.utc)
        )

        try:
            # Pour les commandes slash
            if hasattr(ctx, 'interaction') and ctx.interaction:
                if ctx.interaction.response.is_done():
                    await ctx.interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                # Pour les commandes texte
                await ctx.send(embed=embed)
        except:
            # Si on ne peut pas envoyer dans le canal, essayer en DM
            try:
                await ctx.author.send(embed=embed)
            except:
                pass

    @commands.Cog.listener()
    async def on_error(self, event: str, *args, **kwargs):
        """Gestion des erreurs d'événements (non-commandes)"""
        error = asyncio.get_event_loop().get_exception_handler()

        error_code = self.generate_error_code(Exception(f"Event error: {event}"))

        error_details = {
            "type": "EventError",
            "message": f"Erreur dans l'événement: {event}",
            "file": "Événement Discord",
            "line": "N/A",
            "event": event,
            "traceback": traceback.format_exc()
        }

        self.store_error(error_code, error_details)

        # Stocke dans la BDD si disponible
        if self.bot.db:
            await self.store_error_db(error_code, error_details)

        await self.send_error_log(error_code, error_details, is_fatal=True)


async def setup(bot):
    await bot.add_cog(ErrorTracker(bot))