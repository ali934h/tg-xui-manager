"""
Simple JSON-backed store for extra per-slot metadata that must NOT be
written into the Xray outbound objects themselves (the panel outbound
tag, e.g. "out01", stays exactly as-is - see merger.py / bot.py).

Currently used to remember which country each managed slot's candidate
was assigned from, so /locations can answer instantly without needing a
GeoIP lookup: the country code is read directly from the source list
(scraper.py) at fetch time and stored here whenever a slot is filled or
replaced.

Storage location: slots_meta.json, next to config.py.
Format: {"out01": {"country": "NL"}, "out02": {"country": "DE"}, ...}
Not tracked by git (see .gitignore) - it is local runtime state, not code.
"""

import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

_PATH = os.path.join(os.path.dirname(__file__), "slots_meta.json")
_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(_PATH):
        return {}
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read %s: %s", _PATH, e)
        return {}


def _save(data: dict) -> None:
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, _PATH)


def set_country(tag: str, country: str) -> None:
    """Record the country code for a slot tag. No-op if country is empty."""
    if not country:
        return
    with _lock:
        data = _load()
        data.setdefault(tag, {})["country"] = country
        _save(data)


def get_country(tag: str) -> str | None:
    with _lock:
        data = _load()
        return data.get(tag, {}).get("country")


def all_countries() -> dict[str, str]:
    """Return {tag: country} for every tag with a known country."""
    with _lock:
        data = _load()
        return {tag: v.get("country") for tag, v in data.items() if v.get("country")}


def remove(tag: str) -> None:
    """Drop stored metadata for a tag (e.g. the slot no longer exists)."""
    with _lock:
        data = _load()
        if tag in data:
            del data[tag]
            _save(data)
