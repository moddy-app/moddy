"""
Commandes de gestion des erreurs pour développeurs
Permet de consulter et gérer les erreurs du bot
"""

import nextcord as discord
from nextcord.ext import commands
from datetime import datetime, timedelta
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.embeds import ModdyEmbed, ModdyResponse
from config import COLORS


class ErrorManagement(commands.Cog):
    """Commandes de gestion des erreurs pour développeurs"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Vérifie que l'utilisateur est développeur"""
        return self.bot.is_developer(ctx.author.id)

    @commands.command(name="error", aliases=["err", "debug"])
    async def error_info(self, ctx, error_code: str = None):
        """Affiche les détails d'une erreur via son code"""
        # Récupère le cog ErrorTracker
        error_tracker = self.bot.get_cog("ErrorTracker")
        if not error_tracker:
            await ctx.send("❌ Système d'erreurs non chargé")
            return

        if not error_code:
            # Affiche les dernières erreurs
            embed = discord.Embed(
                title="Dernières erreurs",
                description="Voici les 10 dernières erreurs enregistrées",
                color=COLORS["info"]
            )

            # D'abord, cherche dans le cache mémoire
            errors_list = list(error_tracker.error_cache)[-10:]

            if not errors_list:
                embed.description = "Aucune erreur en cache mémoire"
            else:
                for error in reversed(errors_list):
                    timestamp = error['timestamp'].strftime("%H:%M:%S")
                    error_type = error['data'].get('type', 'Unknown')
                    embed.add_field(
                        name=f"`{error['code']}` - {timestamp}",
                        value=f"**Type:** `{error_type}`\n**Fichier:** `{error['data'].get('file', 'N/A')}`",
                        inline=True
                    )

            # Note sur la BDD
            if self.bot.db:
                embed.set_footer(text="💡 Utilise le code d'erreur pour plus de détails depuis la BDD")
            else:
                embed.set_footer(text="⚠️ Base de données non connectée")

            await ctx.send(embed=embed)
            return

        # Recherche l'erreur spécifique
        error_code = error_code.upper()
        found_error = None

        # D'abord dans le cache mémoire
        for error in error_tracker.error_cache:
            if error['code'] == error_code:
                found_error = error
                source = "cache"
                break

        # Si pas trouvé et qu'on a une BDD, cherche dedans
        if not found_error and self.bot.db:
            try:
                db_error = await self.bot.db.get_error(error_code)
                if db_error:
                    found_error = {
                        'code': db_error['error_code'],
                        'timestamp': db_error['timestamp'],
                        'data': {
                            'type': db_error['error_type'],
                            'message': db_error['message'],
                            'file': db_error['file_source'],
                            'line': str(db_error['line_number']),
                            'traceback': db_error['traceback'],
                            'command': db_error['command'],
                            'user': f"<@{db_error['user_id']}>" if db_error['user_id'] else 'N/A',
                            'guild': f"ID: {db_error['guild_id']}" if db_error['guild_id'] else 'N/A',
                            'context': db_error.get('context', {})
                        }
                    }
                    source = "database"
            except Exception as e:
                import logging
                logger = logging.getLogger('moddy')
                logger.error(f"Erreur recherche BDD: {e}")

        if not found_error:
            embed = ModdyResponse.error(
                "Erreur introuvable",
                f"Aucune erreur avec le code `{error_code}` n'a été trouvée\n\n"
                f"**Recherché dans :** Cache mémoire{' et base de données' if self.bot.db else ''}"
            )
            await ctx.send(embed=embed)
            return

        # Affiche les détails complets
        data = found_error['data']
        timestamp = found_error['timestamp']

        embed = discord.Embed(
            title=f"Détails de l'erreur {error_code}",
            color=COLORS["warning"],
            timestamp=timestamp
        )

        # Badge de source
        embed.set_author(name=f"Source: {source}")

        # Informations de base
        embed.add_field(
            name="Type d'erreur",
            value=f"`{data.get('type', 'N/A')}`",
            inline=True
        )

        embed.add_field(
            name="Fichier source",
            value=f"`{data.get('file', 'N/A')}:{data.get('line', '?')}`",
            inline=True
        )

        embed.add_field(
            name="Heure",
            value=f"`{timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}`",
            inline=True
        )

        # Message d'erreur
        embed.add_field(
            name="Message d'erreur",
            value=f"```{data.get('message', 'N/A')[:500]}```",
            inline=False
        )

        # Contexte si disponible
        if 'command' in data:
            context_value = (
                f"**Commande:** `{data.get('command', 'N/A')}`\n"
                f"**Utilisateur:** {data.get('user', 'N/A')}\n"
                f"**Serveur:** {data.get('guild', 'N/A')}\n"
                f"**Canal:** {data.get('channel', data.get('context', {}).get('channel', 'N/A'))}"
            )
            embed.add_field(
                name="Contexte",
                value=context_value,
                inline=False
            )

        if 'message' in data:
            embed.add_field(
                name="Message original",
                value=f"```{data.get('message', 'N/A')[:300]}```",
                inline=False
            )

        # Traceback si disponible
        if 'traceback' in data and data['traceback']:
            tb = data['traceback']
            if len(tb) > 800:
                tb = tb[:800] + "\n... (tronqué)"
            embed.add_field(
                name="Traceback",
                value=f"```py\n{tb}```",
                inline=False
            )

        await ctx.send(embed=embed)

        # Log l'action
        if log_cog := self.bot.get_cog("LoggingSystem"):
            await log_cog.log_command(ctx, "error", {"code": error_code, "source": source})

    @commands.command(name="clearerrors", aliases=["cerr", "errorclear"])
    async def clear_errors(self, ctx, days: int = None):
        """Vide le cache d'erreurs et/ou nettoie les vieilles erreurs de la BDD"""
        error_tracker = self.bot.get_cog("ErrorTracker")
        if not error_tracker:
            await ctx.send("❌ Système d'erreurs non chargé")
            return

        # Vide le cache mémoire
        cache_count = len(error_tracker.error_cache)
        error_tracker.error_cache.clear()

        message = f"✅ **Cache mémoire vidé:** `{cache_count}` erreurs supprimées"

        # Si on a spécifié des jours et qu'on a une BDD
        if days and self.bot.db:
            try:
                result = await self.bot.db.cleanup_old_errors(days)
                # Parser le résultat pour obtenir le nombre
                if result and hasattr(result, 'split'):
                    parts = result.split()
                    if len(parts) >= 2 and parts[0] == "DELETE":
                        db_count = parts[1]
                        message += f"\n✅ **Base de données:** `{db_count}` erreurs de plus de {days} jours supprimées"
                else:
                    message += f"\n✅ **Base de données:** Erreurs de plus de {days} jours supprimées"
            except Exception as e:
                message += f"\n❌ **Erreur BDD:** {str(e)[:100]}"

        embed = discord.Embed(
            title="Nettoyage des erreurs",
            description=message,
            color=COLORS["success"]
        )
        await ctx.send(embed=embed)

        # Log l'action
        if log_cog := self.bot.get_cog("LoggingSystem"):
            await log_cog.log_command(ctx, "clearerrors", {"cache": cache_count, "days": days})

    @commands.command(name="errortest", aliases=["testerror", "testerr"])
    async def test_error(self, ctx, error_type: str = "basic"):
        """Génère une erreur de test pour vérifier le système"""
        embed = discord.Embed(
            title="Test d'erreur",
            description=f"Génération d'une erreur de type : `{error_type}`",
            color=COLORS["warning"]
        )
        await ctx.send(embed=embed)

        # Génère différents types d'erreurs selon le paramètre
        if error_type == "basic":
            raise Exception("Ceci est une erreur de test basique")
        elif error_type == "zerodiv":
            result = 1 / 0
        elif error_type == "keyerror":
            test_dict = {"a": 1}
            value = test_dict["b"]
        elif error_type == "attribute":
            None.undefined_method()
        elif error_type == "import":
            import module_qui_nexiste_pas
        elif error_type == "runtime":
            raise RuntimeError("Erreur runtime de test (fatale)")
        else:
            raise ValueError(f"Type d'erreur inconnu : {error_type}")

    @commands.command(name="errorstats", aliases=["errstats"])
    async def error_stats(self, ctx):
        """Affiche des statistiques sur les erreurs"""
        error_tracker = self.bot.get_cog("ErrorTracker")
        if not error_tracker:
            await ctx.send("❌ Système d'erreurs non chargé")
            return

        errors = list(error_tracker.error_cache)

        embed = discord.Embed(
            title="Statistiques des erreurs",
            color=COLORS["info"],
            timestamp=datetime.utcnow()
        )

        # Stats du cache mémoire
        if not errors:
            embed.add_field(
                name="Cache mémoire",
                value="Aucune erreur en cache",
                inline=False
            )
        else:
            # Calcul des stats
            error_types = {}
            error_files = {}
            error_users = {}

            for error in errors:
                data = error['data']

                # Par type
                error_type = data.get('type', 'Unknown')
                error_types[error_type] = error_types.get(error_type, 0) + 1

                # Par fichier
                error_file = data.get('file', 'Unknown')
                error_files[error_file] = error_files.get(error_file, 0) + 1

                # Par utilisateur (si disponible)
                if 'user' in data:
                    user_str = data['user'].split('(')[0].strip()
                    error_users[user_str] = error_users.get(user_str, 0) + 1

            # Trie et limite
            top_types = sorted(error_types.items(), key=lambda x: x[1], reverse=True)[:5]
            top_files = sorted(error_files.items(), key=lambda x: x[1], reverse=True)[:5]
            top_users = sorted(error_users.items(), key=lambda x: x[1], reverse=True)[:5]

            # Cache mémoire
            cache_text = f"**Total:** `{len(errors)}` erreurs\n"

            if errors:
                oldest = errors[0]['timestamp']
                newest = errors[-1]['timestamp']
                cache_text += f"**Période:** {oldest.strftime('%H:%M')} - {newest.strftime('%H:%M')}"

            embed.add_field(
                name="Cache mémoire",
                value=cache_text,
                inline=False
            )

            # Top types d'erreurs
            types_text = "\n".join([f"`{t[0]}` : **{t[1]}**" for t in top_types])
            embed.add_field(
                name="Types d'erreurs",
                value=types_text or "Aucune",
                inline=True
            )

            # Top fichiers
            files_text = "\n".join([f"`{f[0]}` : **{f[1]}**" for f in top_files])
            embed.add_field(
                name="Fichiers affectés",
                value=files_text or "Aucun",
                inline=True
            )

            # Top utilisateurs (si applicable)
            if top_users:
                users_text = "\n".join([f"{u[0]} : **{u[1]}**" for u in top_users])
                embed.add_field(
                    name="Utilisateurs",
                    value=users_text,
                    inline=True
                )

        # Stats de la BDD si disponible
        if self.bot.db:
            try:
                stats = await self.bot.db.get_stats()
                embed.add_field(
                    name="Base de données",
                    value=f"**Total historique:** `{stats.get('errors', 0)}` erreurs",
                    inline=False
                )
            except:
                embed.add_field(
                    name="Base de données",
                    value="⚠️ Impossible de récupérer les stats",
                    inline=False
                )
        else:
            embed.add_field(
                name="Base de données",
                value="❌ Non connectée",
                inline=False
            )

        await ctx.send(embed=embed)

        # Log l'action
        if log_cog := self.bot.get_cog("LoggingSystem"):
            await log_cog.log_command(ctx, "errorstats")

    @commands.command(name="errordb", aliases=["errdb"])
    async def error_database(self, ctx, action: str = "info"):
        """Gestion de la base de données d'erreurs"""
        if not self.bot.db:
            embed = ModdyResponse.error(
                "Base de données non connectée",
                "La base de données n'est pas disponible"
            )
            await ctx.send(embed=embed)
            return

        if action == "info":
            # Affiche des infos sur la BDD
            try:
                stats = await self.bot.db.get_stats()

                embed = discord.Embed(
                    title="Base de données d'erreurs",
                    color=COLORS["info"]
                )

                embed.add_field(
                    name="Statistiques",
                    value=(
                        f"**Erreurs stockées:** `{stats.get('errors', 0)}`\n"
                        f"**Utilisateurs trackés:** `{stats.get('users', 0)}`\n"
                        f"**Serveurs trackés:** `{stats.get('guilds', 0)}`"
                    ),
                    inline=False
                )

                embed.add_field(
                    name="Configuration",
                    value=(
                        f"**Pool min:** `{self.bot.db.pool._minsize}`\n"
                        f"**Pool max:** `{self.bot.db.pool._maxsize}`\n"
                        f"**Pool actuel:** `{self.bot.db.pool._holders.__len__()}`"
                    ),
                    inline=False
                )

                await ctx.send(embed=embed)

            except Exception as e:
                embed = ModdyResponse.error(
                    "Erreur BDD",
                    f"Impossible de récupérer les stats: {str(e)[:200]}"
                )
                await ctx.send(embed=embed)

        elif action == "test":
            # Test de connexion
            try:
                async with self.bot.db.pool.acquire() as conn:
                    result = await conn.fetchval("SELECT 1")

                embed = ModdyResponse.success(
                    "Test réussi",
                    "La connexion à la base de données fonctionne correctement"
                )
                await ctx.send(embed=embed)

            except Exception as e:
                embed = ModdyResponse.error(
                    "Test échoué",
                    f"Erreur de connexion: {str(e)[:200]}"
                )
                await ctx.send(embed=embed)

        else:
            embed = discord.Embed(
                title="Actions disponibles",
                description=(
                    "`errordb info` - Affiche les statistiques\n"
                    "`errordb test` - Test la connexion"
                ),
                color=COLORS["info"]
            )
            await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(ErrorManagement(bot))