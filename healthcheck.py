"""
Simple TCP health check: attempt a connection to address:port and measure latency.
Returns None on failure or if latency exceeds MAX_LATENCY_MS.
"""

import logging
import socket
import time

import config

logger = logging.getLogger(__name__)


def check_latency_ms(address: str, port: int) -> float | None:
    """Return TCP connect latency in ms, or None on failure."""
    start = time.monotonic()
    try:
        with socket.create_connection(
            (address, port), timeout=config.TCP_CONNECT_TIMEOUT_SEC
        ):
            return (time.monotonic() - start) * 1000
    except OSError as e:
        logger.debug("TCP connect to %s:%s failed: %s", address, port, e)
        return None


def is_healthy(outbound: dict) -> bool:
    """Return True if the outbound passes the TCP latency check."""
    meta = outbound.get("_meta", {})
    address = meta.get("address")
    port = meta.get("port")
    if not address or not port:
        return False

    latency = check_latency_ms(address, port)
    if latency is None:
        return False

    ok = latency <= config.MAX_LATENCY_MS
    logger.info(
        "%s:%s  latency=%.1fms  threshold=%sms  result=%s",
        address,
        port,
        latency,
        config.MAX_LATENCY_MS,
        "OK" if ok else "REJECTED",
    )
    return ok
