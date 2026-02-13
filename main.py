#!/usr/bin/env python3
"""
Moddy - Startup script with integrated services
Launches the Discord bot and all associated services.
"""

import asyncio
import sys
import logging
import subprocess
import threading
import time
import os
from pathlib import Path
from typing import Optional, Dict, Any
import signal
import atexit

# Windows: color fix
if sys.platform == "win32":
    try:
        import colorama

        colorama.init()
    except ImportError:
        pass

# Checking Python version
if sys.version_info < (3, 11):
    print("❌ Python 3.11+ is required!")
    sys.exit(1)


class ServiceManager:
    """Moddy Service Manager"""

    def __init__(self):
        self.services: Dict[str, Dict[str, Any]] = {}
        self.logger = logging.getLogger('moddy.services')
        self.running = True

        # Ensures everything is cleaned up on exit
        atexit.register(self.cleanup)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handles shutdown signals."""
        self.logger.info("📍 Shutdown signal received, closing services...")
        self.cleanup()

    def start_service(self, name: str, command: list, health_check_url: Optional[str] = None):
        """
        Starts a service in a subprocess with log redirection.

        Args:
            name: Service name
            command: Command to execute
            health_check_url: URL to check if the service is ready
        """
        if name in self.services and self.services[name].get('process'):
            if self.services[name]['process'].poll() is None:
                self.logger.info(f"✅ {name} is already active")
                return True

        try:
            self.logger.info(f"🚀 Starting service {name}...")

            # Starts the process
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                cwd=Path(__file__).parent
            )

            # Thread to read and display logs
            log_thread = threading.Thread(
                target=self._stream_logs,
                args=(name, process),
                daemon=True
            )
            log_thread.start()

            self.services[name] = {
                'process': process,
                'command': command,
                'log_thread': log_thread,
                'health_check_url': health_check_url
            }

            # Waiting for the service to be ready
            if health_check_url:
                if self._wait_for_service(name, health_check_url):
                    self.logger.info(f"✅ {name} is operational")
                    return True
                else:
                    self.logger.error(f"❌ {name} did not start correctly")
                    self.stop_service(name)
                    return False
            else:
                # No health check, assuming it's okay after a delay
                time.sleep(2)
                if process.poll() is None:
                    self.logger.info(f"✅ {name} launched (no health check)")
                    return True
                else:
                    self.logger.error(f"❌ {name} stopped immediately")
                    return False

        except Exception as e:
            self.logger.error(f"❌ Error starting {name}: {e}")
            return False

    def _stream_logs(self, service_name: str, process: subprocess.Popen):
        """
        Reads and displays logs from a service in real time.
        """
        logger = logging.getLogger(f'moddy.services.{service_name}')

        try:
            while self.running:
                if process.stdout:
                    line = process.stdout.readline()
                    if not line:
                        break

                    # Cleans and displays the line
                    line = line.strip()
                    if line:
                        # Determines the log level
                        if 'ERROR' in line or 'CRITICAL' in line or '❌' in line:
                            logger.error(line)
                        elif 'WARNING' in line or 'WARN' in line or '⚠️' in line:
                            logger.warning(line)
                        elif 'DEBUG' in line:
                            logger.debug(line)
                        else:
                            logger.info(line)

                # Checks if the process is still active
                if process.poll() is not None:
                    if self.running:
                        logger.warning(f"⚠️ {service_name} has stopped (code: {process.returncode})")
                    break

        except Exception as e:
            if self.running:
                logger.error(f"Error reading logs: {e}")

    def _wait_for_service(self, name: str, health_check_url: str, timeout: int = 10) -> bool:
        """
        Waits for a service to be ready by checking its health endpoint.
        """
        import urllib.request
        import urllib.error

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                with urllib.request.urlopen(health_check_url, timeout=1) as response:
                    if response.status == 200:
                        return True
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.5)
                continue

            # Checks if the process is still active
            if name in self.services and self.services[name]['process'].poll() is not None:
                return False

        return False

    def stop_service(self, name: str):
        """Stops a service cleanly."""
        if name not in self.services:
            return

        service = self.services[name]
        if service.get('process'):
            process = service['process']

            if process.poll() is None:
                self.logger.info(f"⏹️  Stopping service {name}...")

                # Trying to stop cleanly
                process.terminate()
                try:
                    process.wait(timeout=5)
                    self.logger.info(f"✅ {name} stopped cleanly")
                except subprocess.TimeoutExpired:
                    # Forcing stop
                    self.logger.warning(f"⚠️ Forcing stop of {name}")
                    process.kill()
                    process.wait()

            del self.services[name]

    def restart_service(self, name: str):
        """Restarts a service."""
        if name in self.services:
            service_info = self.services[name].copy()
            self.stop_service(name)
            time.sleep(1)
            self.start_service(
                name,
                service_info['command'],
                service_info.get('health_check_url')
            )

    def cleanup(self):
        """Stops all services."""
        self.running = False
        for name in list(self.services.keys()):
            self.stop_service(name)

    def get_status(self) -> Dict[str, str]:
        """Returns the status of all services."""
        status = {}
        for name, service in self.services.items():
            if service.get('process'):
                if service['process'].poll() is None:
                    status[name] = "🟢 Running"
                else:
                    status[name] = f"🔴 Stopped (code: {service['process'].returncode})"
            else:
                status[name] = "⚫ Not started"
        return status


# Global instance of the service manager
service_manager = ServiceManager()


class CompactExceptionFormatter(logging.Formatter):
    """
    Custom formatter that displays exceptions on a SINGLE log line.
    Instead of multiple lines, tracebacks are compacted with ⮐ as separator.
    """

    def format(self, record):
        # Format the base message
        result = super().format(record)

        # If there's exception info, format it compactly
        if record.exc_info:
            # Get the exception traceback as a string
            import traceback
            exc_text = ''.join(traceback.format_exception(*record.exc_info))
            # Replace newlines with arrow separator to keep it on one line
            compact_exc = exc_text.replace('\n', ' ⮐ ')
            result = f"{result} ⮐ TRACEBACK: {compact_exc}"

        return result


def setup_logging():
    """Configures the unified logging system."""

    # Log format
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    # Custom formatter with colors (if available)
    try:
        from colorlog import ColoredFormatter

        class CompactColoredFormatter(ColoredFormatter, CompactExceptionFormatter):
            """Combines colored formatting with compact exceptions"""
            pass

        colored_format = '%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        console_handler.setFormatter(CompactColoredFormatter(
            colored_format,
            datefmt=date_format,
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'red,bg_white',
            }
        ))
    except ImportError:
        console_handler.setFormatter(CompactExceptionFormatter(log_format, date_format))

    # File handler
    log_dir = Path(__file__).parent / 'logs'
    log_dir.mkdir(exist_ok=True)

    file_handler = logging.FileHandler(
        log_dir / f'moddy_{time.strftime("%Y%m%d")}.log',
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(CompactExceptionFormatter(log_format, date_format))

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Reduces noise from certain modules
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('discord.http').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)

    return logging.getLogger('moddy')


async def start_services():
    """Starts all necessary services before the bot."""
    logger = logging.getLogger('moddy')

    # Other services can be added here if needed
    # service_manager.start_service('other-service', [...])

    # Displays the status of services, if any
    status = service_manager.get_status()
    if status:
        logger.info("📊 Services status:")
        for name, state in status.items():
            logger.info(f"  • {name}: {state}")


async def start_api_server():
    """Starts the FastAPI server for health checks and internal API."""
    import uvicorn
    from internal_api.server import app, set_bot

    # Railway uses PORT environment variable
    port = int(os.getenv("PORT", os.getenv("INTERNAL_PORT", 3000)))

    logger = logging.getLogger('moddy')
    logger.info(f"🌐 Starting API server on port {port}...")

    config = uvicorn.Config(
        app,
        host="0.0.0.0",  # Railway requires binding to 0.0.0.0
        port=port,
        log_level="info"
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    """Launches the bot and all services."""

    # Configuring logging
    logger = setup_logging()
    logger.info("🔧 Initializing Moddy...")

    try:
        # Starting external services
        await start_services()

        # Import here to get errors after logging
        from bot import ModdyBot
        from config import TOKEN

        if not TOKEN:
            logger.error("❌ Discord token missing! Check your .env file")
            return

        # Creates the bot with a reference to the service manager
        bot = ModdyBot()
        bot.service_manager = service_manager  # Adds the reference

        # Adds a command to manage services (for devs)
        @bot.command(name='services')
        async def services_command(ctx):
            """Displays the status of services (dev only)."""
            if not bot.is_developer(ctx.author.id):
                return

            status = service_manager.get_status()
            if not status:
                await ctx.send("📭 No active services")
                return

            embed = discord.Embed(
                title="📊 Services Status",
                color=discord.Color.blue()
            )

            for name, state in status.items():
                embed.add_field(name=name, value=state, inline=False)

            await ctx.send(embed=embed)

        @bot.command(name='restart-service')
        async def restart_service_command(ctx, service_name: str):
            """Restarts a specific service (dev only)."""
            if not bot.is_developer(ctx.author.id):
                return

            if service_name not in service_manager.services:
                await ctx.send(f"❌ Service '{service_name}' not found")
                return

            await ctx.send(f"🔄 Restarting {service_name}...")
            service_manager.restart_service(service_name)
            await asyncio.sleep(3)

            status = service_manager.get_status()
            state = status.get(service_name, "Unknown")
            await ctx.send(f"Service {service_name}: {state}")

        # Start API server and Discord bot in parallel
        logger.info("🚀 Starting API server and Discord bot...")

        async def start_bot_with_retry():
            """Starts the bot with retry logic for network issues."""
            max_retries = 5
            retry_delay = 2  # seconds

            for attempt in range(1, max_retries + 1):
                try:
                    await bot.start(TOKEN)
                    break  # Success, exit retry loop
                except asyncio.TimeoutError:
                    if attempt < max_retries:
                        logger.warning(f"⚠️ Connection timeout (attempt {attempt}/{max_retries})")
                        logger.info(f"🔄 Retrying in {retry_delay} seconds...")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                    else:
                        logger.error(f"❌ Failed to connect after {max_retries} attempts")
                        raise
                except discord.HTTPException as e:
                    # Handle Discord rate limits (429) with longer delay
                    if e.status == 429:
                        if attempt < max_retries:
                            # Check if Discord provided a Retry-After header
                            retry_after = None
                            if hasattr(e.response, 'headers') and 'Retry-After' in e.response.headers:
                                try:
                                    retry_after = int(e.response.headers['Retry-After'])
                                except (ValueError, TypeError):
                                    pass

                            # Use Discord's suggested delay or default to 60 seconds
                            rate_limit_delay = retry_after if retry_after else 60

                            logger.warning(f"⚠️ Discord rate limit hit (429) - attempt {attempt}/{max_retries}")
                            if retry_after:
                                logger.info(f"🔄 Discord requests waiting {rate_limit_delay} seconds (Retry-After header)")
                            else:
                                logger.info(f"🔄 Waiting {rate_limit_delay} seconds before retry...")
                            logger.info("💡 Tip: Too many restarts can trigger rate limits. The timer does NOT reset on retries.")
                            await asyncio.sleep(rate_limit_delay)
                        else:
                            logger.error(f"❌ Failed to connect after {max_retries} attempts (rate limited)")
                            logger.error("💡 Discord is blocking the bot due to too many requests.")
                            logger.error("💡 Wait 5-10 minutes before trying again, or check for multiple instances.")
                            raise
                    else:
                        # Other HTTP errors should not be retried
                        logger.error(f"❌ Discord HTTP error: {e}")
                        raise
                except Exception as e:
                    # Other exceptions should not be retried
                    logger.error(f"❌ Connection error: {e}")
                    raise

        # Run both API server and bot concurrently
        await asyncio.gather(
            start_api_server(),
            start_bot_with_retry()
        )

    except ImportError as e:
        logger.error(f"❌ Import error: {e}")
        logger.error("Check that bot.py and config.py exist.")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("⏹️ Shutdown requested")
    except RuntimeError as e:
        if "Session is closed" in str(e):
            logger.info("🔄 Closing for restart")
        else:
            logger.error(f"❌ Runtime error: {e}")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Cleaning up services
        logger.info("🧹 Cleaning up services...")
        service_manager.cleanup()


if __name__ == "__main__":
    try:
        # Imports discord for the services command
        import discord

        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    finally:
        # Ensures everything is properly cleaned up
        service_manager.cleanup()