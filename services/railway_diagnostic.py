"""
Railway Private Network Diagnostic Module.

This module provides comprehensive diagnostics for Railway Private Network connectivity.
It handles DNS resolution timing issues and provides detailed logging for troubleshooting.

Railway DNS is NOT available during the first ~3-5 seconds after service startup.
This module implements proper waiting and retry logic to handle this.
"""

import socket
import asyncio
import os
import logging
from typing import Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger('moddy.services.railway_diagnostic')


@dataclass
class DiagnosticResult:
    """Result of a Railway Private Network diagnostic."""
    success: bool
    dns_resolved: bool
    resolved_ip: Optional[str] = None
    http_success: bool = False
    error_message: Optional[str] = None
    backend_status: Optional[str] = None


async def wait_for_dns_ready(wait_seconds: int = 5) -> None:
    """
    Wait for Railway DNS to be ready.

    Railway Private Network DNS is not available during the first ~3 seconds
    after service startup. This function waits before attempting DNS resolution.

    Args:
        wait_seconds: Number of seconds to wait (default: 5)
    """
    logger.info(f"   Waiting {wait_seconds}s for Railway DNS to be ready...")
    await asyncio.sleep(wait_seconds)


def resolve_dns(hostname: str, port: int) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Attempt to resolve a hostname using both IPv6 and IPv4.

    Railway Private Network uses IPv6 by default for legacy environments
    and IPv4 for environments created after October 2025.

    Args:
        hostname: The hostname to resolve (e.g., 'website-backend.railway.internal')
        port: The port number

    Returns:
        Tuple of (success, resolved_ip, error_message)
    """
    # Try IPv6 first (legacy Railway environments)
    try:
        result = socket.getaddrinfo(
            hostname, port,
            socket.AF_INET6, socket.SOCK_STREAM
        )
        if result:
            resolved_ip = result[0][4][0]
            logger.info(f"   DNS IPv6 OK: {hostname} -> {resolved_ip}")
            return True, resolved_ip, None
    except socket.gaierror as e:
        logger.debug(f"   DNS IPv6 failed: {e}")

    # Try IPv4 (newer Railway environments, post-Oct 2025)
    try:
        result = socket.getaddrinfo(
            hostname, port,
            socket.AF_INET, socket.SOCK_STREAM
        )
        if result:
            resolved_ip = result[0][4][0]
            logger.info(f"   DNS IPv4 OK: {hostname} -> {resolved_ip}")
            return True, resolved_ip, None
    except socket.gaierror as e:
        error_msg = str(e)
        logger.error(f"   DNS IPv4 failed: {e}")
        return False, None, error_msg

    return False, None, "DNS resolution failed for both IPv4 and IPv6"


async def test_http_connection(
    base_url: str,
    api_secret: str,
    timeout: float = 10.0
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Test HTTP connection to the backend.

    Args:
        base_url: The base URL of the backend
        api_secret: The INTERNAL_API_SECRET for authentication
        timeout: Request timeout in seconds

    Returns:
        Tuple of (success, status_message, error_message)
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            url = f"{base_url}/internal/health"
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {api_secret}"}
            )

            if response.status_code == 200:
                data = response.json()
                status = data.get('status', 'unknown')
                return True, status, None
            elif response.status_code == 401:
                return False, None, "HTTP 401 - INTERNAL_API_SECRET incorrect or missing"
            elif response.status_code == 403:
                return False, None, "HTTP 403 - INTERNAL_API_SECRET does not match"
            else:
                return False, None, f"HTTP {response.status_code}: {response.text[:200]}"

    except Exception as e:
        error_type = type(e).__name__
        return False, None, f"{error_type}: {e}"


async def diagnose_railway_private_network(
    backend_hostname: str = "website-backend.railway.internal",
    backend_port: int = 8080,
    initial_wait: int = 5,
    max_retries: int = 3,
    retry_delay: float = 2.0
) -> DiagnosticResult:
    """
    Comprehensive Railway Private Network diagnostic.

    This function:
    1. Waits for Railway DNS to be ready
    2. Tests DNS resolution (IPv6 first, then IPv4)
    3. Tests HTTP connectivity to the backend
    4. Provides detailed logging for troubleshooting

    Args:
        backend_hostname: The hostname of the backend service
        backend_port: The port of the backend service
        initial_wait: Seconds to wait for DNS at startup
        max_retries: Number of retry attempts
        retry_delay: Delay between retries in seconds

    Returns:
        DiagnosticResult with detailed information
    """
    logger.info("=" * 70)
    logger.info("RAILWAY PRIVATE NETWORK DIAGNOSTIC")
    logger.info("=" * 70)

    backend_url = f"http://{backend_hostname}:{backend_port}"
    api_secret = os.getenv("INTERNAL_API_SECRET", "")

    logger.info(f"   Backend URL: {backend_url}")
    logger.info(f"   API Secret configured: {'Yes' if api_secret else 'No'}")

    if not api_secret:
        logger.error("   INTERNAL_API_SECRET not configured!")
        return DiagnosticResult(
            success=False,
            dns_resolved=False,
            error_message="INTERNAL_API_SECRET environment variable not set"
        )

    # Step 1: Wait for DNS to be ready
    logger.info("")
    logger.info("[1/3] Waiting for Railway DNS...")
    await wait_for_dns_ready(initial_wait)

    # Step 2: Test DNS resolution with retries
    logger.info("")
    logger.info(f"[2/3] Testing DNS resolution: {backend_hostname}")

    dns_success = False
    resolved_ip = None
    dns_error = None

    for attempt in range(1, max_retries + 1):
        logger.info(f"   Attempt {attempt}/{max_retries}...")
        dns_success, resolved_ip, dns_error = resolve_dns(backend_hostname, backend_port)

        if dns_success:
            break

        if attempt < max_retries:
            logger.info(f"   Retrying in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay *= 1.5  # Increase delay for next retry

    if not dns_success:
        logger.error("")
        logger.error("=" * 70)
        logger.error("DNS RESOLUTION FAILED - RAILWAY PRIVATE NETWORK NOT WORKING")
        logger.error("=" * 70)
        logger.error("")
        logger.error("POSSIBLE CAUSES:")
        logger.error("  1. Services are NOT in the same Railway project")
        logger.error("  2. Backend service name is incorrect")
        logger.error(f"     -> Name used: {backend_hostname}")
        logger.error("     -> Check the EXACT name on Railway Dashboard")
        logger.error("  3. Services are not in the same environment (production)")
        logger.error("  4. Railway Private Network temporary issue")
        logger.error("")
        logger.error("ACTIONS TO TAKE:")
        logger.error("  -> Open Railway Dashboard")
        logger.error("  -> Verify both services are in the SAME project")
        logger.error("  -> Check the EXACT backend service name")
        logger.error("  -> Update BACKEND_INTERNAL_URL if needed")
        logger.error("=" * 70)

        return DiagnosticResult(
            success=False,
            dns_resolved=False,
            error_message=dns_error
        )

    # Step 3: Test HTTP connection
    logger.info("")
    logger.info(f"[3/3] Testing HTTP connection to backend...")

    http_success = False
    backend_status = None
    http_error = None

    for attempt in range(1, max_retries + 1):
        logger.info(f"   Attempt {attempt}/{max_retries}...")
        http_success, backend_status, http_error = await test_http_connection(
            backend_url, api_secret
        )

        if http_success:
            logger.info(f"   HTTP OK - Backend status: {backend_status}")
            break

        logger.warning(f"   HTTP failed: {http_error}")

        if attempt < max_retries:
            wait_time = retry_delay * attempt
            logger.info(f"   Retrying in {wait_time}s...")
            await asyncio.sleep(wait_time)

    if not http_success:
        logger.error("")
        logger.error("=" * 70)
        logger.error("HTTP CONNECTION FAILED")
        logger.error("=" * 70)
        logger.error("")
        logger.error(f"   Error: {http_error}")
        logger.error("")
        logger.error("POSSIBLE CAUSES:")
        logger.error("  - Backend is not running or still starting")
        logger.error("  - Backend is not listening on the correct port")
        logger.error("  - Backend is not listening on :: (IPv6)")
        logger.error("  - INTERNAL_API_SECRET mismatch")
        logger.error("=" * 70)

        return DiagnosticResult(
            success=False,
            dns_resolved=True,
            resolved_ip=resolved_ip,
            http_success=False,
            error_message=http_error
        )

    # Success!
    logger.info("")
    logger.info("=" * 70)
    logger.info("RAILWAY PRIVATE NETWORK WORKING CORRECTLY")
    logger.info("=" * 70)
    logger.info(f"   DNS: {backend_hostname} -> {resolved_ip}")
    logger.info(f"   HTTP: {backend_url}/internal/health -> OK")
    logger.info(f"   Backend status: {backend_status}")
    logger.info("=" * 70)

    return DiagnosticResult(
        success=True,
        dns_resolved=True,
        resolved_ip=resolved_ip,
        http_success=True,
        backend_status=backend_status
    )


async def quick_dns_check(
    hostname: str = "website-backend.railway.internal",
    port: int = 8080
) -> bool:
    """
    Quick DNS check without detailed logging.

    Useful for checking if DNS is available before making requests.

    Args:
        hostname: The hostname to check
        port: The port number

    Returns:
        True if DNS resolution succeeds, False otherwise
    """
    success, _, _ = resolve_dns(hostname, port)
    return success
