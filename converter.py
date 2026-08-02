"""
Parse subscription links (vless://, vmess://, trojan://, ss://) into
outbound dicts compatible with the 3x-ui panel (v2.9.3).

Verified against web/assets/js/model/outbound.js from 3x-ui source:

  VLESS  settings: {address, port, id, flow, encryption}          ← flat (panel format)
  VMess  settings: {vnext: [{address, port, users: [{id, security}]}]}
  Trojan settings: {servers: [{address, port, password}]}
  SS     settings: {servers: [{address, port, method, password}]}

Each parser also attaches a "_meta" key for internal health-check use.
merger.py strips _meta before sending the config to the panel.
"""

import base64
import json
import logging
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


def _b64_decode(s: str) -> str:
    s = s.strip()
    padding = "=" * (-len(s) % 4)
    return base64.b64decode(s + padding).decode("utf-8", errors="ignore")


def _p(params: dict, key: str, default: str = "") -> str:
    """Safe single-value getter for parse_qs dicts."""
    v = params.get(key, [default])
    return v[0] if isinstance(v, list) else v


def _build_stream_settings(
    net: str, security: str, params: dict, sni_fallback: str = ""
) -> dict:
    stream: dict = {"network": net or "tcp"}

    if net == "ws":
        stream["wsSettings"] = {
            "path": _p(params, "path", "/"),
            "host": _p(params, "host"),
            "heartbeatPeriod": 0,
        }
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": _p(params, "serviceName")}
    elif net == "tcp":
        stream["tcpSettings"] = {
            "header": {"type": _p(params, "headerType", "none")}
        }

    if security == "tls":
        stream["security"] = "tls"
        sni = _p(params, "sni", sni_fallback)
        allow_insecure = _p(params, "allowInsecure", "0")
        stream["tlsSettings"] = {
            "serverName": sni,
            "alpn": [],
            "fingerprint": _p(params, "fp", "chrome"),
            "allowInsecure": allow_insecure in ("1", "true", "True"),
        }
    elif security == "reality":
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "serverName": _p(params, "sni", sni_fallback),
            "publicKey": _p(params, "pbk"),
            "shortId": _p(params, "sid"),
            "fingerprint": _p(params, "fp", "chrome"),
            "spiderX": _p(params, "spx", ""),
        }
    else:
        stream["security"] = "none"

    return stream


def parse_vless(link: str) -> dict | None:
    """
    VLESS outbound settings use a flat structure in 3x-ui (not vnext).
    Source: Outbound.VLESSSettings.toJson() in outbound.js
    """
    try:
        parsed = urlparse(link)
        params = parse_qs(parsed.query)
        address = parsed.hostname
        port = parsed.port

        outbound = {
            "protocol": "vless",
            "settings": {
                "address": address,
                "port": port,
                "id": parsed.username,
                "flow": _p(params, "flow"),
                "encryption": _p(params, "encryption", "none"),
            },
            "streamSettings": _build_stream_settings(
                _p(params, "type", "tcp"),
                _p(params, "security", "none"),
                params,
                sni_fallback=address,
            ),
            "_meta": {"address": address, "port": port},
        }
        return outbound
    except Exception as e:
        logger.warning("vless parse failed: %s | %s", e, link[:80])
        return None


def parse_trojan(link: str) -> dict | None:
    """
    Trojan settings: servers array.
    Source: Outbound.TrojanSettings.toJson() in outbound.js
    """
    try:
        parsed = urlparse(link)
        params = parse_qs(parsed.query)
        address = parsed.hostname
        port = parsed.port

        outbound = {
            "protocol": "trojan",
            "settings": {
                "servers": [
                    {
                        "address": address,
                        "port": port,
                        "password": parsed.username,
                    }
                ]
            },
            "streamSettings": _build_stream_settings(
                _p(params, "type", "tcp"),
                _p(params, "security", "tls"),
                params,
                sni_fallback=address,
            ),
            "_meta": {"address": address, "port": port},
        }
        return outbound
    except Exception as e:
        logger.warning("trojan parse failed: %s | %s", e, link[:80])
        return None


def parse_vmess(link: str) -> dict | None:
    """
    VMess settings: vnext array (same as Xray core format).
    Source: Outbound.VmessSettings.toJson() in outbound.js
    """
    try:
        data = json.loads(_b64_decode(link[len("vmess://"):]))
        address = data.get("add")
        port = int(data.get("port"))
        host = data.get("host", "")
        sni = data.get("sni", host or address)
        params = {
            "host": [host],
            "path": [data.get("path", "/")],
            "sni": [sni],
            "fp": [data.get("fp", "chrome")],
            "headerType": [data.get("type", "none")],
        }

        outbound = {
            "protocol": "vmess",
            "settings": {
                "vnext": [
                    {
                        "address": address,
                        "port": port,
                        "users": [
                            {
                                "id": data.get("id"),
                                "alterId": int(data.get("aid", 0)),
                                "security": data.get("scy", "auto"),
                            }
                        ],
                    }
                ]
            },
            "streamSettings": _build_stream_settings(
                data.get("net", "tcp"),
                "tls" if data.get("tls") == "tls" else "none",
                params,
                sni_fallback=address,
            ),
            "_meta": {"address": address, "port": port},
        }
        return outbound
    except Exception as e:
        logger.warning("vmess parse failed: %s | %s", e, link[:80])
        return None


def parse_shadowsocks(link: str) -> dict | None:
    """
    Shadowsocks settings: servers array.
    Source: Outbound.ShadowsocksSettings.toJson() in outbound.js
    """
    try:
        body = link[len("ss://"):].split("#", 1)[0]
        if "@" in body:
            userinfo_part, hostport_part = body.split("@", 1)
            try:
                decoded = _b64_decode(userinfo_part)
                method, password = decoded.split(":", 1)
            except Exception:
                method, password = userinfo_part.split(":", 1)
            host, port = hostport_part.split(":", 1)
            port = port.split("/")[0].split("?")[0]
        else:
            decoded = _b64_decode(body)
            method_password, hostport = decoded.rsplit("@", 1)
            method, password = method_password.split(":", 1)
            host, port = hostport.split(":", 1)

        outbound = {
            "protocol": "shadowsocks",
            "settings": {
                "servers": [
                    {
                        "address": host,
                        "port": int(port),
                        "method": method,
                        "password": password,
                    }
                ]
            },
            "streamSettings": {"network": "tcp", "security": "none"},
            "_meta": {"address": host, "port": int(port)},
        }
        return outbound
    except Exception as e:
        logger.warning("shadowsocks parse failed: %s | %s", e, link[:80])
        return None


def parse_link(link: str) -> dict | None:
    link = link.strip()
    if link.startswith("vless://"):
        return parse_vless(link)
    if link.startswith("trojan://"):
        return parse_trojan(link)
    if link.startswith("vmess://"):
        return parse_vmess(link)
    if link.startswith("ss://"):
        return parse_shadowsocks(link)
    logger.warning("Unknown protocol, skipping: %s", link[:30])
    return None
