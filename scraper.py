"""
Fetch and extract V2Ray/Trojan/SS config links (and their country code) from
a public GitHub raw file.

Source: https://github.com/roosterkid/openproxylist (V2RAY.txt, updated hourly)

Each line format:
    🇣🇱 vless://uuid@host:port?params#remark 122ms NL [ISP]

The config link runs from the protocol prefix up to the first space.
The country code (2-letter, ISO 3166-1 alpha-2) is the token right before
the "[ISP]" part. It can be missing on some lines ("🏳" unknown-flag
candidates) - in that case an empty string is returned instead.
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
# Matches "<latency>ms" followed by an optional 2-letter country code, then "["
LOCATION_RE = re.compile(r"\d+ms\s+(?:([A-Z]{2})\s+)?\[")


def _headers() -> dict:
    return {"User-Agent": config.USER_AGENT, "Accept": "text/plain"}


def collect_candidate_configs(limit: int | None = None) -> list[tuple[str, str]]:
    """
    Download V2RAY.txt and return a list of (config_link, country_code)
    tuples, up to *limit* entries. country_code is "" when the source line
    carries no country tag.

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

    results: list[tuple[str, str]] = []
    for raw_line in resp.text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        m = CONFIG_LINE_RE.search(line)
        if not m:
            continue
        link = html.unescape(m.group(1))

        loc_m = LOCATION_RE.search(line)
        country = loc_m.group(1) if (loc_m and loc_m.group(1)) else ""

        results.append((link, country))
        if len(results) >= limit:
            break

    logger.info("Extracted %d candidate configs from source.", len(results))
    return results
