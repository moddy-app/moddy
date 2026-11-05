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
    print("[FAIL] Python 3.11+ is required!")
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
        self.logger.info("Shutdown signal received, closing services...")
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
                self.logger.info(f"{name} is already active")
                return True

        try:
            self.logger.info(f"Starting service {name}...")

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
                    self.logger.info(f"{name} is operational")
                    return True
                else:
                    self.logger.error(f"[FAIL] {name} did not start correctly")
                    self.stop_service(name)
                    return False
            else:
                # No health check, assuming it's okay after a delay
                time.sleep(2)
                if process.poll() is None:
                    self.logger.info(f"{name} launched (no health check)")
                    return True
                else:
                    self.logger.error(f"[FAIL] {name} stopped immediately")
                    return False

        except Exception as e:
            self.logger.error(f"[FAIL] Error starting {name}: {e}")
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
                        if 'ERROR' in line or 'CRITICAL' in line:
                            logger.error(line)
                        elif 'WARNING' in line or 'WARN' in line:
                            logger.warning(line)
                        elif 'DEBUG' in line:
                            logger.debug(line)
                        else:
                            logger.info(line)

                # Checks if the process is still active
                if process.poll() is not None:
                    if self.running:
                        logger.warning(f"[WARN] {service_name} has stopped (code: {process.returncode})")
                    break

        except Exception as e:
            if self.running:
                logger.error(f"Error reading logs: {e}")

    def _wait_for_service(self, name: str, health_check_url: str, timeout: int = 10) -> bool:
        """
        Waits for a service to be ready by checking its health endpoint.
        """
        import http.client
        from urllib.parse import urlsplit

        parsed = urlsplit(health_check_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username is not None
            or parsed.password is not None
        ):
            logger.error("Health checks are restricted to local HTTP services")
            return False
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        start_time = time.time()

        while time.time() - start_time < timeout:
            connection = http.client.HTTPConnection(
                parsed.hostname, parsed.port or 80, timeout=1
            )
            try:
                connection.request("GET", path)
                if connection.getresponse().status == 200:
                    return True
            except (OSError, TimeoutError, http.client.HTTPException):
                time.sleep(0.5)
                continue
            finally:
                connection.close()

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
                self.logger.info(f"Stopping service {name}...")

                # Trying to stop cleanly
                process.terminate()
                try:
                    process.wait(timeout=5)
                    self.logger.info(f"{name} stopped cleanly")
                except subprocess.TimeoutExpired:
                    # Forcing stop
                    self.logger.warning(f"[WARN] Forcing stop of {name}")
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
                    status[name] = "Running"
                else:
                    status[name] = f"Stopped (code: {service['process'].returncode})"
            else:
                status[name] = "Not started"
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
    log_format = '%(asctime)s [%(levelname)-8s] %(name)-25s | %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    # DEBUG is opt-in. Leaving the root logger at DEBUG means every library on the
    # stack formats and allocates log records that are then thrown away, which is
    # pure memory churn in a container where nobody reads them.
    debug_enabled = os.getenv("DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
    level = logging.DEBUG if debug_enabled else logging.INFO

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(CompactExceptionFormatter(log_format, date_format))

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)

    # File handler. On Railway stdout is already collected and the container disk is
    # ephemeral, so writing a second copy only grows a file nobody reads. Everywhere
    # else the file is capped and rotated instead of growing forever.
    if not os.getenv("RAILWAY_ENVIRONMENT"):
        from logging.handlers import RotatingFileHandler

        log_dir = Path(__file__).parent / 'logs'
        log_dir.mkdir(exist_ok=True)

        file_handler = RotatingFileHandler(
            log_dir / 'moddy.log',
            maxBytes=5_000_000,
            backupCount=2,
            encoding='utf-8',
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(CompactExceptionFormatter(log_format, date_format))
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
        logger.info("Services status:")
        for name, state in status.items():
            logger.info(f"  • {name}: {state}")


async def start_api_server():
    """Starts the FastAPI server for health checks and internal API."""
    import uvicorn
    from internal_api.server import app, set_bot

    # Railway uses PORT environment variable
    port = int(os.getenv("PORT", os.getenv("INTERNAL_PORT", 3000)))

    logger = logging.getLogger('moddy')
    logger.info(f"Starting API server on port {port}...")

    config = uvicorn.Config(
        app,
        host="0.0.0.0",  # Railway requires binding to 0.0.0.0
        port=port,
        log_level="info"
    )
    server = uvicorn.Server(config)
    await server.serve()


async def supervised_api_server():
    """Run the internal API without ever taking the bot down with it.

    A port already in use or an uvicorn crash used to propagate through
    ``asyncio.gather`` into ``main()``'s ``sys.exit(1)``, killing the Discord
    bot over a side service. The API is restarted with backoff instead.
    """
    logger = logging.getLogger('moddy')
    delay = 5.0
    while True:
        try:
            await start_api_server()
            return  # clean stop (shutdown requested)
        except asyncio.CancelledError:
            raise
        except SystemExit:
            # uvicorn calls sys.exit(1) when it cannot bind its port
            logger.error("[FAIL] API server exited (port busy?); the bot keeps running, retrying in %.0fs", delay)
        except Exception as e:
            logger.error("[FAIL] API server crashed: %s; the bot keeps running, retrying in %.0fs",
                         e, delay, exc_info=True)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 120.0)


def _is_transient_login_error(exc: BaseException) -> bool:
    """Errors worth retrying while still BEFORE the bot's setup_hook ran:
    network trouble reaching Discord and 5xx answers from it."""
    import aiohttp
    import discord
    if isinstance(exc, (asyncio.TimeoutError, aiohttp.ClientError, OSError)):
        return True
    if isinstance(exc, discord.GatewayNotFound):
        return True
    if isinstance(exc, discord.HTTPException) and (exc.status >= 500 or exc.status == 429):
        return True
    return False


async def main():
    """Launches the bot and all services."""

    # Configuring logging
    logger = setup_logging()
    logger.info("Initializing Moddy...")

    try:
        # Starting external services
        await start_services()

        # Import here to get errors after logging
        from bot import ModdyBot
        from config import TOKEN

        if not TOKEN:
            logger.error("[FAIL] Discord token missing! Check your .env file")
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
        logger.info("Starting API server and Discord bot...")

        async def start_bot_with_retry():
            """Starts the bot with retry logic for network issues.

            Only the login phase is retried: once ``setup_hook`` has run, a
            second ``bot.start()`` would run it again (duplicate loops, cogs
            already loaded), and discord.py already reconnects the gateway by
            itself after that point.
            """
            import discord
            max_retries = 8
            retry_delay = 2  # seconds

            for attempt in range(1, max_retries + 1):
                try:
                    await bot.start(TOKEN)
                    break  # Success, exit retry loop
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if getattr(bot, "_setup_hook_ran", False) or not _is_transient_login_error(e):
                        # Bad token, privileged intents, failure after boot...
                        logger.error(f"[FAIL] Connection error (not retried): {e}")
                        raise
                    if attempt >= max_retries:
                        logger.error(f"[FAIL] Failed to connect after {max_retries} attempts: {e}")
                        raise
                    wait = retry_delay
                    if isinstance(e, discord.HTTPException) and e.status == 429:
                        # Discord rate limit: honour Retry-After, default 60 s
                        wait = 60
                        headers = getattr(getattr(e, "response", None), "headers", None) or {}
                        try:
                            wait = int(headers.get("Retry-After", 60))
                        except (ValueError, TypeError):
                            wait = 60
                        logger.warning(f"[WARN] Discord rate limit hit (429) - attempt {attempt}/{max_retries}, waiting {wait}s")
                    else:
                        logger.warning(f"[WARN] Connection problem ({type(e).__name__}: {e}) - attempt {attempt}/{max_retries}, retrying in {wait}s")
                        retry_delay = min(retry_delay * 2, 120)
                    await asyncio.sleep(wait)

        # Run both API server and bot concurrently
        await asyncio.gather(
            supervised_api_server(),
            start_bot_with_retry()
        )

    except ImportError as e:
        logger.error(f"[FAIL] Import error: {e}")
        logger.error("Check that bot.py and config.py exist.")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except RuntimeError as e:
        if "Session is closed" in str(e):
            logger.info("Closing for restart")
        else:
            logger.error(f"[FAIL] Runtime error: {e}")
    except Exception as e:
        logger.error(f"[FAIL] Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Cleaning up services
        logger.info("Cleaning up services...")
        service_manager.cleanup()


if __name__ == "__main__":
    try:
        # Imports discord for the services command
        import discord

        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nGoodbye!")
    finally:
        # Ensures everything is properly cleaned up
        service_manager.cleanup()
