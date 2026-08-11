"""
3x-ui panel API client.

Confirmed endpoints (tested on panel v2.9.3 with DevTools):
  - Login:        POST {base}/login              (form: username, password)
  - Read config:  POST {base}/panel/xray         -> {success, msg, obj: "<json string>"}
  - Save config:  POST {base}/panel/xray/update  (Content-Type: application/x-www-form-urlencoded)
                  body: xraySetting=<serialized json>&outboundTestUrl=<url>
  - Restart core: POST {base}/panel/api/server/restartXrayService

Note: Content-Type must be form-urlencoded, NOT application/json.
      outboundTestUrl must be included in the save request or the panel returns a validation error.

IMPORTANT: save_xray_config() only hot-reloads the running Xray process with
the new config. It does NOT reset connection pools, DNS cache, or mux state
that Xray accumulates over long uptime. That stale state can cause outbounds
to intermittently fail external health checks (e.g. ECONNRESET) even though
the config itself is correct. Call restart_xray() after saving to force a
full process restart and clear that state — see bot.py's _save_and_restart().
"""

import json
import logging

import requests

import config

logger = logging.getLogger(__name__)

LOGIN_ENDPOINT = "/login"
GET_SETTINGS_ENDPOINT = "/panel/xray"
SAVE_ENDPOINT = "/panel/xray/update"
RESTART_XRAY_ENDPOINT = "/panel/api/server/restartXrayService"


class PanelClient:
    def __init__(
        self,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool | None = None,
    ):
        self.base_url = (base_url or config.PANEL_BASE_URL).rstrip("/")
        self.username = username or config.PANEL_USERNAME
        self.password = password or config.PANEL_PASSWORD
        self.verify_ssl = verify_ssl if verify_ssl is not None else config.VERIFY_SSL
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT})
        self._outbound_test_url = "https://www.google.com/generate_204"

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def login(self) -> None:
        resp = self.session.post(
            self._url(LOGIN_ENDPOINT),
            data={"username": self.username, "password": self.password},
            timeout=config.REQUEST_TIMEOUT_SEC,
            verify=self.verify_ssl,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(
                f"Login response was not JSON (check PANEL_BASE_URL). "
                f"status={resp.status_code}, body={resp.text[:300]!r}"
            )
        if not data.get("success"):
            raise RuntimeError(f"Login failed: {data}")
        logger.info("Panel login successful.")

    def get_xray_config(self) -> dict:
        """Return the full Xray config as a parsed dict."""
        resp = self.session.post(
            self._url(GET_SETTINGS_ENDPOINT),
            timeout=config.REQUEST_TIMEOUT_SEC,
            verify=self.verify_ssl,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Failed to read config: {data}")

        raw_obj = data.get("obj")
        if raw_obj is None:
            raise RuntimeError(
                f"'obj' key missing from response. Keys present: {list(data.keys())}"
            )

        obj = json.loads(raw_obj) if isinstance(raw_obj, str) else raw_obj
        xray_cfg = obj.get("xraySetting")
        if xray_cfg is None:
            raise RuntimeError(
                f"'xraySetting' key missing inside 'obj'. Keys: {list(obj.keys())}"
            )

        self._outbound_test_url = obj.get(
            "outboundTestUrl", self._outbound_test_url
        )

        if isinstance(xray_cfg, str):
            xray_cfg = json.loads(xray_cfg)

        return xray_cfg

    def save_xray_config(self, xray_cfg: dict) -> None:
        """Push the full Xray config dict back to the panel (hot-reload only)."""
        resp = self.session.post(
            self._url(SAVE_ENDPOINT),
            data={
                "xraySetting": json.dumps(xray_cfg, ensure_ascii=False),
                "outboundTestUrl": self._outbound_test_url,
            },
            timeout=config.REQUEST_TIMEOUT_SEC,
            verify=self.verify_ssl,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(
                f"Save response was not JSON. status={resp.status_code}, "
                f"body={resp.text[:300]!r}"
            )
        if not data.get("success"):
            raise RuntimeError(f"Failed to save config: {data}")
        logger.info("Xray config saved and applied on panel.")

    def restart_xray(self) -> None:
        """
        Force a full Xray-core process restart via the panel's server API.

        Unlike save_xray_config()'s hot-reload, this clears stale connection
        pools, DNS cache, and mux session state accumulated over long uptime.
        Without this, outbounds can intermittently fail external health
        checks (e.g. ECONNRESET) even though the config is correct.
        """
        resp = self.session.post(
            self._url(RESTART_XRAY_ENDPOINT),
            timeout=config.REQUEST_TIMEOUT_SEC,
            verify=self.verify_ssl,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(
                f"Restart response was not JSON. status={resp.status_code}, "
                f"body={resp.text[:300]!r}"
            )
        if not data.get("success"):
            raise RuntimeError(f"Failed to restart Xray: {data}")
        logger.info("Xray service restarted successfully.")
