"""
Real connectivity check via the local Xray binary.

For each candidate outbound, this module:
  1. Picks a free local port
  2. Builds a minimal Xray config: SOCKS5 inbound on that port → candidate outbound
  3. Spawns xray as a subprocess
  4. Makes an HTTP request through the SOCKS5 proxy to https://api.ipify.org
  5. Returns the exit IP (= real origin server IP)
  6. Tears down the subprocess and temp files

If the candidate fails to connect within the timeout, returns None.

This replaces the TCP-only healthcheck for candidate evaluation:
  - TCP check only verifies port is open
  - This check verifies the full protocol stack works AND reveals the real exit IP
    (even for Cloudflare-fronted configs, the exit IP is the origin server IP)

Xray binary path: /usr/local/x-ui/bin/xray-linux-amd64

Usage:
    result = check_outbound(outbound_dict)
    if result is None:
        # candidate failed — skip it
    else:
        exit_ip = result  # use for duplicate detection
"""

import json
import logging
import os
import socket
import subprocess
import tempfile
import time
import urllib.request

import config

logger = logging.getLogger(__name__)

XRAY_BINARY = "/usr/local/x-ui/bin/xray-linux-amd64"
IP_CHECK_URL = "https://api.ipify.org"
STARTUP_WAIT_SEC = 2.0      # time to let xray start before sending traffic
REQUEST_TIMEOUT_SEC = 8     # HTTP request timeout through SOCKS5
PROCESS_TIMEOUT_SEC = 12    # total time allowed per candidate


def _free_port() -> int:
    """Find a free local TCP port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_xray_config(outbound: dict, socks_port: int) -> dict:
    """
    Build a minimal Xray config:
      - one SOCKS5 inbound on 127.0.0.1:socks_port
      - the candidate as the only outbound (tag: 'proxy')
    """
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


def _request_through_socks(socks_port: int, url: str, timeout: int) -> str | None:
    """
    Make an HTTP GET request through a local SOCKS5 proxy.
    Returns the response body as a string, or None on failure.
    """
    try:
        import urllib.request
        proxies = {"http": f"socks5h://127.0.0.1:{socks_port}",
                   "https": f"socks5h://127.0.0.1:{socks_port}"}

        # urllib doesn't support SOCKS natively; use requests if available,
        # otherwise fall back to curl subprocess
        try:
            import requests
            resp = requests.get(
                url,
                proxies=proxies,
                timeout=timeout,
            )
            return resp.text.strip()
        except ImportError:
            pass

        # fallback: curl
        result = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout),
             "--proxy", f"socks5h://127.0.0.1:{socks_port}",
             url],
            capture_output=True, text=True, timeout=timeout + 2
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception as e:
        logger.debug("SOCKS request failed: %s", e)
        return None


def check_outbound(outbound: dict) -> str | None:
    """
    Test a candidate outbound by actually connecting through it.

    Returns:
        str  — the exit IP address (real origin server IP) on success
        None — if the candidate fails for any reason
    """
    if not os.path.exists(XRAY_BINARY):
        logger.error("Xray binary not found at %s", XRAY_BINARY)
        return None

    socks_port = _free_port()
    xray_cfg = _build_xray_config(outbound, socks_port)

    tmp_cfg = None
    proc = None
    try:
        # Write temp config file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, dir="/tmp"
        ) as f:
            json.dump(xray_cfg, f)
            tmp_cfg = f.name

        # Start xray
        proc = subprocess.Popen(
            [XRAY_BINARY, "-c", tmp_cfg],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for xray to start
        time.sleep(STARTUP_WAIT_SEC)

        # Check xray didn't crash immediately
        if proc.poll() is not None:
            logger.debug("Xray exited immediately for port %s", socks_port)
            return None

        # Make request through SOCKS5
        exit_ip = _request_through_socks(socks_port, IP_CHECK_URL, REQUEST_TIMEOUT_SEC)

        if exit_ip and _is_valid_ip(exit_ip):
            logger.info("Candidate OK — exit IP: %s", exit_ip)
            return exit_ip
        else:
            logger.debug("No valid IP returned (got: %r)", exit_ip)
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
    """Basic check that the returned string looks like an IPv4 address."""
    parts = s.strip().split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False
