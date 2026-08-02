"""
Real connectivity check via the local Xray binary.

For each candidate outbound:
  1. Pick a free local port
  2. Build a minimal Xray config: SOCKS5 inbound -> candidate outbound
  3. Spawn xray subprocess
  4. Make HTTP request via curl through the SOCKS5 proxy to https://api.ipify.org
  5. Return the exit IP (real origin server IP, even for CDN-fronted configs)
  6. Terminate subprocess and clean up temp files

Returns None if the candidate fails for any reason.

Xray binary: /usr/local/x-ui/bin/xray-linux-amd64 (installed by 3x-ui)

IMPORTANT: Xray 26.x removed 'allowInsecure' from tlsSettings.
All configs passed here must NOT include that field.
"""

import json
import logging
import os
import socket
import subprocess
import tempfile
import time

import config

logger = logging.getLogger(__name__)

XRAY_BINARY = getattr(config, "XRAY_BINARY", "/usr/local/x-ui/bin/xray-linux-amd64")
STARTUP_WAIT_SEC = getattr(config, "XRAY_STARTUP_WAIT_SEC", 2.0)
REQUEST_TIMEOUT_SEC = getattr(config, "XRAY_REQUEST_TIMEOUT_SEC", 8)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_xray_config(outbound: dict, socks_port: int) -> dict:
    """Build minimal Xray config: SOCKS5 inbound -> candidate outbound."""
    clean_ob = {k: v for k, v in outbound.items() if k != "_meta"}
    clean_ob["tag"] = "proxy"
    return {
        "log": {"loglevel": "none"},
        "inbounds": [{
            "tag": "socks-in",
            "listen": "127.0.0.1",
            "port": socks_port,
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": False},
        }],
        "outbounds": [clean_ob],
    }


def check_outbound(outbound: dict) -> str | None:
    """
    Test a candidate by connecting through it via the local Xray binary.

    Returns:
        str  - exit IP address on success
        None - on any failure
    """
    if not os.path.exists(XRAY_BINARY):
        logger.error("Xray binary not found at %s", XRAY_BINARY)
        return None

    socks_port = _free_port()
    xray_cfg = _build_xray_config(outbound, socks_port)

    tmp_cfg = None
    proc = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, dir="/tmp"
        ) as f:
            json.dump(xray_cfg, f)
            tmp_cfg = f.name

        proc = subprocess.Popen(
            [XRAY_BINARY, "-c", tmp_cfg],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        time.sleep(STARTUP_WAIT_SEC)

        if proc.poll() is not None:
            logger.debug("Xray exited immediately (bad config?) for port %s", socks_port)
            return None

        # Use curl for SOCKS5 request (most reliable, no Python SOCKS deps needed)
        result = subprocess.run(
            [
                "curl", "-s",
                "--max-time", str(REQUEST_TIMEOUT_SEC),
                "--proxy", f"socks5h://127.0.0.1:{socks_port}",
                "https://api.ipify.org",
            ],
            capture_output=True,
            text=True,
            timeout=REQUEST_TIMEOUT_SEC + 3,
        )

        if result.returncode == 0:
            exit_ip = result.stdout.strip()
            if _is_valid_ip(exit_ip):
                logger.info("Candidate OK - exit IP: %s", exit_ip)
                return exit_ip
            logger.debug("curl returned non-IP: %r", exit_ip)
        else:
            logger.debug("curl failed (code %s): %s", result.returncode, result.stderr.strip()[:100])

        return None

    except Exception as e:
        logger.debug("check_outbound error: %s", e)
        return None
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        if tmp_cfg and os.path.exists(tmp_cfg):
            os.unlink(tmp_cfg)


def _is_valid_ip(s: str) -> bool:
    parts = s.strip().split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False
