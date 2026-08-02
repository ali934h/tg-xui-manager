"""
Fetch and extract V2Ray/Trojan/SS config links from a public GitHub raw file.

Source: https://github.com/roosterkid/openproxylist  (V2RAY.txt, updated hourly)
Each line format: 🇳🇱 vless://uuid@host:port?params#remark 122ms NL [ISP]

The config link runs from the protocol prefix up to the first space.
"""

import html
import logging
import re

import requests
import config

logger = logging.getLogger(__name__)

RAW_SOURCE_URL = (
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/V2RAY.txt"
)
CONFIG_LINE_RE = re.compile(r"((?:vless|vmess|trojan|ss)://\S+)")


def _headers() -> dict:
    return {"User-Agent": config.USER_AGENT, "Accept": "text/plain"}


def collect_candidate_configs(limit: int | None = None) -> list[str]:
    """
    Download V2RAY.txt and return a list of raw config links (up to *limit*).
    Returns an empty list on network failure.
    """
    limit = limit or config.CANDIDATES_TO_FETCH
    try:
        resp = requests.get(
            RAW_SOURCE_URL,
            headers=_headers(),
            timeout=config.REQUEST_TIMEOUT_SEC,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to download config list: %s", e)
        return []

    links: list[str] = []
    for raw_line in resp.text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = CONFIG_LINE_RE.search(line)
        if not m:
            continue
        links.append(html.unescape(m.group(1)))
        if len(links) >= limit:
            break

    logger.info("Extracted %d candidate configs from source.", len(links))
    return links
