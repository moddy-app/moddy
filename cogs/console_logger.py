"""
Système de redirection des logs console vers Discord
Capture tout ce qui s'affiche dans la console Python
"""

import nextcord as discord
from nextcord.ext import commands, tasks
import logging
import sys
import io
import asyncio
from datetime import datetime, timezone
from collections import deque
import traceback

from config import COLORS


class ConsoleLogger(commands.Cog):
    """Redirige tous les logs de la console vers Discord"""

    def __init__(self, bot):
        self.bot = bot
        self.console_channel_id = 1386749469734998186
        self.log_buffer = deque(maxlen=50)  # Buffer des derniers logs
        self.log_queue = asyncio.Queue()
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr

        # Filtres pour ignorer certains logs
        self.ignored_patterns = [
            "discord.gateway",
            "discord.client",
            "discord.http",
            "discord.state",
            "WebSocket Event",
            "Dispatching event",
            "POST https://discord.com",
            "PUT https://discord.com",
            "GET https://discord.com",
            "has returned",
            "has received",
            "rate limit bucket"
        ]

        # Configure le logging
        self.setup_logging()

        # Démarre la tâche d'envoi
        self.send_logs_task.start()

    def cog_unload(self):
        """Restaure les sorties standard lors du déchargement"""
        self.send_logs_task.cancel()
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr

        # Retire notre handler
        logger = logging.getLogger()
        for handler in logger.handlers[:]:
            if isinstance(handler, DiscordLogHandler):
                logger.removeHandler(handler)

    def should_log(self, content: str) -> bool:
        """Vérifie si un log doit être envoyé ou ignoré"""
        # Ignore les logs vides
        if not content or content.strip() == "":
            return False

        # Vérifie les patterns à ignorer
        for pattern in self.ignored_patterns:
            if pattern in content:
                return False

        return True

    def setup_logging(self):
        """Configure le système de logging pour capturer tout"""
        # Crée notre handler personnalisé
        discord_handler = DiscordLogHandler(self)

        # Ne log que INFO et plus (pas DEBUG)
        discord_handler.setLevel(logging.INFO)

        # Format des logs
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%H:%M:%S'
        )
        discord_handler.setFormatter(formatter)

        # Ajoute le handler au logger root
        root_logger = logging.getLogger()
        root_logger.addHandler(discord_handler)

        # Met le niveau global à INFO pour éviter le spam de DEBUG
        root_logger.setLevel(logging.INFO)

        # Redirige stdout et stderr
        sys.stdout = ConsoleCapture(self, 'stdout')
        sys.stderr = ConsoleCapture(self, 'stderr')

    async def get_console_channel(self):
        """Récupère le canal de console"""
        return self.bot.get_channel(self.console_channel_id)

    def add_log(self, content: str, log_type: str = 'info'):
        """Ajoute un log au buffer"""
        # Vérifie si on doit logger
        if not self.should_log(content):
            return

        timestamp = datetime.now().strftime('%H:%M:%S')
        formatted_log = f"[{timestamp}] {content}"

        # Ajoute au buffer local
        self.log_buffer.append({
            'content': formatted_log,
            'type': log_type,
            'timestamp': datetime.now(timezone.utc)
        })

        # Ajoute à la queue d'envoi
        try:
            self.log_queue.put_nowait({
                'content': formatted_log,
                'type': log_type
            })
        except asyncio.QueueFull:
            # Si la queue est pleine, on ignore (évite le spam)
            pass

    @tasks.loop(seconds=5)  # Augmenté à 5 secondes pour réduire le spam
    async def send_logs_task(self):
        """Envoie les logs accumulés vers Discord"""
        if self.log_queue.empty():
            return

        channel = await self.get_console_channel()
        if not channel:
            return

        # Collecte tous les logs en attente
        logs_to_send = []
        colors = {
            'info': COLORS["info"],
            'warning': COLORS["warning"],
            'error': COLORS["error"],
            'debug': COLORS["developer"],
            'stdout': COLORS["primary"],
            'stderr': COLORS["error"]
        }

        try:
            while not self.log_queue.empty() and len(logs_to_send) < 10:
                log = await asyncio.wait_for(self.log_queue.get(), timeout=0.1)
                logs_to_send.append(log)
        except asyncio.TimeoutError:
            pass

        if not logs_to_send:
            return

        # Groupe les logs par type
        grouped_logs = {}
        for log in logs_to_send:
            log_type = log['type']
            if log_type not in grouped_logs:
                grouped_logs[log_type] = []
            grouped_logs[log_type].append(log['content'])

        # Crée un embed pour chaque type
        embeds = []
        for log_type, contents in grouped_logs.items():
            # Limite le contenu pour respecter les limites Discord
            content = '\n'.join(contents)
            if len(content) > 4000:
                content = content[:3997] + '...'

            embed = discord.Embed(
                description=f"```\n{content}\n```",
                color=colors.get(log_type, COLORS["primary"]),
                timestamp=datetime.now(timezone.utc)
            )

            # Titre selon le type
            titles = {
                'info': "Logs Info",
                'warning': "Logs Warning",
                'error': "Logs Error",
                'debug': "Logs Debug",
                'stdout': "Console Output",
                'stderr': "Console Error"
            }
            embed.set_author(name=titles.get(log_type, "Logs"))

            embeds.append(embed)

        # Envoie les embeds (max 10 par message)
        try:
            await channel.send(embeds=embeds[:10])
        except Exception as e:
            # En cas d'erreur, on log localement seulement
            print(f"Erreur envoi logs Discord: {e}")

    @send_logs_task.before_loop
    async def before_send_logs(self):
        """Attend que le bot soit prêt"""
        await self.bot.wait_until_ready()


class DiscordLogHandler(logging.Handler):
    """Handler de logging qui envoie vers Discord"""

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    def emit(self, record):
        """Émet un log vers Discord"""
        try:
            # Ignore les logs discord.py
            if record.name.startswith('discord.'):
                return

            # Formate le message
            log_entry = self.format(record)

            # Vérifie si on doit logger
            if not self.cog.should_log(log_entry):
                return

            # Détermine le type selon le niveau
            if record.levelno >= logging.ERROR:
                log_type = 'error'
            elif record.levelno >= logging.WARNING:
                log_type = 'warning'
            elif record.levelno >= logging.INFO:
                log_type = 'info'
            else:
                log_type = 'debug'

            # Ajoute au système de logs
            self.cog.add_log(log_entry, log_type)

        except Exception:
            # En cas d'erreur, on ne fait rien pour éviter les boucles
            pass


class ConsoleCapture(io.TextIOBase):
    """Capture les sorties console (stdout/stderr)"""

    def __init__(self, cog, stream_type):
        self.cog = cog
        self.stream_type = stream_type
        self.buffer = []

    def write(self, text):
        """Capture l'écriture"""
        if not text or text == '\n':
            return

        # Accumule dans le buffer
        self.buffer.append(text)

        # Si on a une ligne complète
        if '\n' in text or len(self.buffer) > 5:
            full_text = ''.join(self.buffer).strip()
            if full_text and self.cog.should_log(full_text):
                self.cog.add_log(full_text, self.stream_type)
            self.buffer.clear()

        # Écrit aussi dans la sortie originale
        if self.stream_type == 'stdout':
            self.cog.original_stdout.write(text)
        else:
            self.cog.original_stderr.write(text)

    def flush(self):
        """Flush le buffer"""
        if self.buffer:
            full_text = ''.join(self.buffer).strip()
            if full_text and self.cog.should_log(full_text):
                self.cog.add_log(full_text, self.stream_type)
            self.buffer.clear()


def setup(bot):
    bot.add_cog(ConsoleLogger(bot))