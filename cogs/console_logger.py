"""
System for redirecting console logs to Discord
Captures everything that is displayed in the Python console
"""

import discord
from discord.ext import commands, tasks
import logging
import sys
import io
import asyncio
from datetime import datetime, timezone
from collections import deque
import traceback

from config import COLORS
from utils.log_cards import build_log_card


class ConsoleColors:
    """ANSI color codes for the console"""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"

    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"


class ColoredFormatter(logging.Formatter):
    """
    Log formatter that adds colors based on the level
    """
    LOG_COLORS = {
        logging.DEBUG: ConsoleColors.CYAN,
        logging.INFO: ConsoleColors.GREEN,
        logging.WARNING: ConsoleColors.YELLOW,
        logging.ERROR: ConsoleColors.RED,
        logging.CRITICAL: ConsoleColors.BOLD + ConsoleColors.RED,
    }

    def format(self, record):
        """Formats the log with colors"""
        # Copy of the record to avoid modifying the original
        record_copy = logging.makeLogRecord(record.__dict__)

        # Color for the level
        level_color = self.LOG_COLORS.get(record_copy.levelno, ConsoleColors.WHITE)

        # Add the color to the level name
        record_copy.levelname = f"{level_color}{record_copy.levelname}{ConsoleColors.RESET}"

        # Format the complete message
        message = super().format(record_copy)
        return message


class InfoFilter(logging.Filter):
    """Filter to keep only INFO and DEBUG logs"""
    def filter(self, record):
        return record.levelno <= logging.INFO


class ConsoleLogger(commands.Cog):
    """Redirects all console logs to Discord"""

    def __init__(self, bot):
        self.bot = bot
        self.console_channel_id = 1386749469734998186
        self.log_buffer = deque(maxlen=50)  # Buffer of the latest logs
        self.log_queue = asyncio.Queue()
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr

        # Filters to ignore certain logs
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

        # Configure logging
        self.setup_logging()

        # Start the sending task
        self.send_logs_task.start()

    def cog_unload(self):
        """Restores standard outputs on unload"""
        self.send_logs_task.cancel()
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr

        # Remove our handler
        logger = logging.getLogger()
        for handler in logger.handlers[:]:
            if isinstance(handler, DiscordLogHandler):
                logger.removeHandler(handler)

    def should_log(self, content: str) -> bool:
        """Checks if a log should be sent or ignored"""
        # Ignore empty logs
        if not content or content.strip() == "":
            return False

        # Check patterns to ignore
        for pattern in self.ignored_patterns:
            if pattern in content:
                return False

        return True

    def setup_logging(self):
        """Configures the logging system to capture everything"""
        root_logger = logging.getLogger()
        root_logger.handlers.clear()  # Remove old handlers
        root_logger.setLevel(logging.INFO)  # Global level

        console_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        console_formatter = ColoredFormatter(console_format, datefmt='%H:%M:%S')

        # --- Handler for stdout (INFO and below) ---
        info_handler = logging.StreamHandler(sys.stdout)
        info_handler.setFormatter(console_formatter)
        info_handler.setLevel(logging.INFO)
        info_handler.addFilter(InfoFilter())
        root_logger.addHandler(info_handler)

        # --- Handler for stderr (WARNING and above) ---
        error_handler = logging.StreamHandler(sys.stderr)
        error_handler.setFormatter(console_formatter)
        error_handler.setLevel(logging.WARNING)
        root_logger.addHandler(error_handler)


        # --- Handler for Discord (via the cog) ---
        discord_handler = DiscordLogHandler(self)
        discord_format = '%(name)s - %(levelname)s - %(message)s'
        discord_formatter = logging.Formatter(discord_format)
        discord_handler.setFormatter(discord_formatter)
        discord_handler.setLevel(logging.INFO)
        root_logger.addHandler(discord_handler)

        # Redirect stdout and stderr
        sys.stdout = ConsoleCapture(self, 'stdout')
        sys.stderr = ConsoleCapture(self, 'stderr')

    async def get_console_channel(self):
        """Gets the console channel"""
        return self.bot.get_channel(self.console_channel_id)

    def add_log(self, content: str, log_type: str = 'info'):
        """Adds a log to the buffer"""
        # Check if we should log
        if not self.should_log(content):
            return

        timestamp = datetime.now().strftime('%H:%M:%S')
        formatted_log = f"[{timestamp}] {content}"

        # Add to local buffer
        self.log_buffer.append({
            'content': formatted_log,
            'type': log_type,
            'timestamp': datetime.now(timezone.utc)
        })

        # Add to the sending queue
        try:
            self.log_queue.put_nowait({
                'content': formatted_log,
                'type': log_type
            })
        except asyncio.QueueFull:
            # If the queue is full, we ignore (prevents spam)
            pass

    @tasks.loop(seconds=5)  # Increased to 5 seconds to reduce spam
    async def send_logs_task(self):
        """Sends accumulated logs to Discord"""
        if self.log_queue.empty():
            return

        channel = await self.get_console_channel()
        if not channel:
            return

        # Collect all pending logs
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

        # Group logs by type
        grouped_logs = {}
        for log in logs_to_send:
            log_type = log['type']
            if log_type not in grouped_logs:
                grouped_logs[log_type] = []
            grouped_logs[log_type].append(log['content'])

        titles = {
            'info': "Logs Info",
            'warning': "Logs Warning",
            'error': "Logs Error",
            'debug': "Logs Debug",
            'stdout': "Console Output",
            'stderr': "Console Error"
        }

        # One card per type (a Components V2 message holds at most 4000 characters)
        try:
            for log_type, contents in grouped_logs.items():
                content = '\n'.join(contents)
                if len(content) > 3400:
                    content = content[:3397] + '...'

                view = build_log_card(
                    titles.get(log_type, "Logs"),
                    accent=colors.get(log_type, COLORS["primary"]),
                    description=f"```\n{content}\n```",
                )
                await channel.send(view=view)
        except Exception as e:
            # In case of an error, we only log locally
            print(f"Error sending Discord logs: {e}")

    @send_logs_task.before_loop
    async def before_send_logs(self):
        """Waits for the bot to be ready"""
        await self.bot.wait_until_ready()


class DiscordLogHandler(logging.Handler):
    """Logging handler that sends to Discord"""

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    def emit(self, record):
        """Emits a log to Discord"""
        try:
            # Ignore discord.py logs
            if record.name.startswith('discord.'):
                return

            # Format the message
            log_entry = self.format(record)

            # Check if we should log
            if not self.cog.should_log(log_entry):
                return

            # Determine the type based on the level
            if record.levelno >= logging.ERROR:
                log_type = 'error'
            elif record.levelno >= logging.WARNING:
                log_type = 'warning'
            elif record.levelno >= logging.INFO:
                log_type = 'info'
            else:
                log_type = 'debug'

            # Add to the logging system
            self.cog.add_log(log_entry, log_type)

        except Exception:
            # In case of an error, do nothing to avoid loops
            pass


class ConsoleCapture(io.TextIOBase):
    """Captures console outputs (stdout/stderr)"""

    def __init__(self, cog, stream_type):
        self.cog = cog
        self.stream_type = stream_type
        self.buffer = []

    def write(self, text):
        """Captures writing"""
        if not text or text == '\n':
            return

        # Accumulate in the buffer
        self.buffer.append(text)

        # If we have a complete line
        if '\n' in text or len(self.buffer) > 5:
            full_text = ''.join(self.buffer).strip()
            if full_text and self.cog.should_log(full_text):
                self.cog.add_log(full_text, self.stream_type)
            self.buffer.clear()

        # Also write to the original output
        if self.stream_type == 'stdout':
            self.cog.original_stdout.write(text)
        else:
            self.cog.original_stderr.write(text)

    def flush(self):
        """Flush the buffer"""
        if self.buffer:
            full_text = ''.join(self.buffer).strip()
            if full_text and self.cog.should_log(full_text):
                self.cog.add_log(full_text, self.stream_type)
            self.buffer.clear()


async def setup(bot):
    await bot.add_cog(ConsoleLogger(bot))