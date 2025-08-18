"""
Commande reboot pour développeurs
Redémarre le bot et modifie le message original
"""

import nextcord as discord
from nextcord.ext import commands
import asyncio
import os
import sys
import subprocess
import json
import tempfile
from datetime import datetime

# Import du système d'embeds épuré
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.embeds import ModdyEmbed, ModdyResponse, ModdyColors
from config import COLORS


class Reboot(commands.Cog):
    """Commande pour redémarrer le bot"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Vérifie que l'utilisateur est développeur"""
        return self.bot.is_developer(ctx.author.id)

    @commands.command(name="reboot", aliases=["restart", "reload"])
    async def reboot(self, ctx):
        """Redémarre le bot automatiquement"""

        # Embed initial
        embed = discord.Embed(
            title="Redémarrage en cours...",
            description="Le bot va redémarrer dans quelques secondes.",
            color=COLORS["warning"],
            timestamp=datetime.utcnow()
        )
        embed.set_footer(
            text=f"Demandé par {ctx.author}",
            icon_url=ctx.author.display_avatar.url
        )

        # Envoyer le message
        msg = await ctx.send(embed=embed)

        # Log l'action
        import logging
        logger = logging.getLogger('moddy')
        logger.info(f"Reboot demandé par {ctx.author} ({ctx.author.id})")

        # Sauvegarder les infos pour après le reboot
        reboot_info = {
            "channel_id": ctx.channel.id,
            "message_id": msg.id,
            "author_name": str(ctx.author),
            "author_avatar": str(ctx.author.display_avatar.url),
            "start_time": datetime.utcnow().isoformat()
        }

        # Fichier temporaire pour stocker les infos
        temp_file = os.path.join(tempfile.gettempdir(), "moddy_reboot.json")

        with open(temp_file, 'w') as f:
            json.dump(reboot_info, f)

        # Petit délai pour s'assurer que le message est envoyé
        await asyncio.sleep(0.5)

        # Log pour debug
        logger.info(f"Fichier temporaire créé : {temp_file}")
        logger.info(f"Plateforme : {sys.platform}")
        logger.info(f"Executable : {sys.executable}")
        logger.info(f"Arguments : {sys.argv}")

        try:
            # Fermer proprement toutes les connexions
            await self.bot.close()

            # Attendre que le bot soit complètement fermé
            await asyncio.sleep(1)

            # Préparer les arguments pour le redémarrage
            args = [sys.executable] + sys.argv

            # Sur Windows
            if sys.platform == "win32":
                # Utiliser CREATE_NEW_CONSOLE pour s'assurer que le processus continue
                subprocess.Popen(
                    args,
                    creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS,
                    close_fds=True
                )
            else:
                # Sur Linux/Mac, utiliser subprocess avec nohup pour détacher le processus
                subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    close_fds=True,
                    preexec_fn=os.setsid  # Crée une nouvelle session
                )

            # Forcer la sortie du processus actuel
            os._exit(0)

        except Exception as e:
            logger.error(f"Erreur lors du reboot : {e}")
            # En cas d'erreur, essayer quand même de fermer proprement
            sys.exit(1)


class RebootNotifier(commands.Cog):
    """Met à jour le message après le reboot"""

    def __init__(self, bot):
        self.bot = bot
        self._checked = False

    @commands.Cog.listener()
    async def on_ready(self):
        """Vérifie et met à jour le message de reboot"""
        # Éviter de vérifier plusieurs fois
        if self._checked:
            return
        self._checked = True

        temp_file = os.path.join(tempfile.gettempdir(), "moddy_reboot.json")

        # Log pour debug
        import logging
        logger = logging.getLogger('moddy')
        logger.info(f"Vérification du fichier de reboot : {temp_file}")

        if not os.path.exists(temp_file):
            logger.info("Pas de fichier de reboot trouvé")
            return

        try:
            # Lire les infos
            with open(temp_file, 'r') as f:
                info = json.load(f)

            logger.info(f"Infos de reboot trouvées : canal {info['channel_id']}, message {info['message_id']}")

            # Calculer le temps de reboot
            start_time = datetime.fromisoformat(info["start_time"])
            reboot_duration = (datetime.utcnow() - start_time).total_seconds()

            # Récupérer le canal et le message
            channel = self.bot.get_channel(info["channel_id"])
            if not channel:
                logger.warning(f"Canal {info['channel_id']} introuvable")
                os.remove(temp_file)
                return

            try:
                message = await channel.fetch_message(info["message_id"])
            except Exception as e:
                logger.warning(f"Message introuvable : {e}")
                os.remove(temp_file)
                return

            # Déterminer la vitesse
            if reboot_duration < 5:
                speed = "Ultra rapide"
            elif reboot_duration < 10:
                speed = "Rapide"
            elif reboot_duration < 20:
                speed = "Normal"
            else:
                speed = "Lent"

            # Créer le nouvel embed
            embed = discord.Embed(
                title="Redémarrage terminé !",
                color=COLORS["success"],
                timestamp=datetime.utcnow()
            )

            # Description avec les détails
            embed.description = f"**{speed}** - `{reboot_duration:.1f}` secondes"

            # Ajout des champs
            embed.add_field(
                name="Étapes",
                value="✓ Connexion Discord\n"
                      "✓ Chargement des modules\n"
                      "✓ Base de données\n"
                      "✓ Commandes synchronisées",
                inline=False
            )

            embed.add_field(
                name="Statistiques",
                value=f"**Serveurs:** `{len(self.bot.guilds)}`\n"
                      f"**Utilisateurs:** `{len(self.bot.users)}`\n"
                      f"**Latence:** `{round(self.bot.latency * 1000)}ms`",
                inline=True
            )

            embed.add_field(
                name="Système",
                value=f"**Commandes:** `{len(self.bot.commands)}`\n"
                      f"**Cogs:** `{len(self.bot.cogs)}`\n"
                      f"**Version:** discord.py `{discord.__version__}`",
                inline=True
            )

            # Footer avec les infos originales
            embed.set_footer(
                text=f"Demandé par {info['author_name']}",
                icon_url=info["author_avatar"]
            )

            # Mettre à jour le message
            await message.edit(embed=embed)

            # Supprimer le fichier temporaire
            os.remove(temp_file)

            logger.info(f"Notification de reboot envoyée (durée: {reboot_duration:.1f}s)")

        except Exception as e:
            logger.error(f"Erreur notification reboot: {e}")

            # Supprimer le fichier en cas d'erreur
            if os.path.exists(temp_file):
                os.remove(temp_file)


def setup(bot):
    bot.add_cog(Reboot(bot))
    bot.add_cog(RebootNotifier(bot))