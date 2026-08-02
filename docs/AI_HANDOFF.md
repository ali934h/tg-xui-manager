# AI Handoff Document — tg-xui-manager

This document is written for an AI assistant (or developer) taking over this project. It covers everything needed to understand the codebase, make changes safely, and continue development.

---

## Project summary

`tg-xui-manager` is a **Telegram bot** that manages the outbound section of a [3x-ui](https://github.com/MHSanaei/3x-ui) Xray panel. It fetches free V2Ray/Trojan/SS configs from a public GitHub source, verifies each one by making a real HTTP request through the local Xray binary, and pushes verified, deduplicated configs into the panel — all triggered by Telegram commands.

**Stack:** Python 3.10+, `python-telegram-bot` v20+, `requests`, `PySocks`, `curl`, `systemd` on Ubuntu.
**Tested on:** 3x-ui v2.9.3, Xray 26.4.25, Ubuntu 22.04/24.04.

---

## Repository layout

```
bot.py              Entry point. All Telegram command handlers.
panel_client.py     HTTP client for the 3x-ui panel API.
converter.py        Parses vless/vmess/trojan/ss links into outbound dicts.
ipcheck.py          Real connectivity check via Xray binary; returns exit IP.
healthcheck.py      TCP connect latency check (fallback only).
scraper.py          Downloads candidate config list from GitHub raw.
merger.py           Merges outbound dicts into the full Xray config.
config.example.py   Template for config.py (never committed).
config.py           Runtime secrets (gitignored, chmod 600).
install.sh          One-line installer (systemd service).
update.sh           git pull + pip install + systemctl restart.
uninstall.sh        Stops and removes the service and all files.
docs/               This folder.
```

---

## Architecture

```
Telegram user
    │
    ▼
bot.py  (python-telegram-bot polling)
    │
    ├── scraper.py        → downloads V2RAY.txt from roosterkid/openproxylist
    ├── converter.py      → parses each link into an outbound dict
    ├── ipcheck.py        → starts Xray, connects through it, gets exit IP
    ├── healthcheck.py    → TCP connect fallback (when XRAY_CHECK_ENABLED=False)
    ├── merger.py         → inserts/replaces outbounds in the Xray config dict
    └── panel_client.py   → login + read + save via 3x-ui HTTP API
```

No database, no scheduler. Everything is triggered by a Telegram command.

---

## Candidate evaluation pipeline

For each candidate link from the source list:

1. **Parse** (`converter.py`) — extract protocol, address, port, settings into an outbound dict.
2. **Xray check** (`ipcheck.py`, when `XRAY_CHECK_ENABLED=True`):
   - Find a free local port.
   - Write a temp Xray config: SOCKS5 inbound on that port → candidate outbound.
   - Spawn `/usr/local/x-ui/bin/xray-linux-amd64 -c /tmp/tmpXXX.json`.
   - Wait `XRAY_STARTUP_WAIT_SEC` seconds.
   - Run `curl -s --max-time 8 --proxy socks5h://127.0.0.1:{port} https://api.ipify.org`.
   - If curl returns a valid IPv4 → candidate is alive; the IP is the **real origin server IP**.
   - Terminate xray subprocess and delete temp file.
3. **Dedup** — if the exit IP matches any existing slot or any earlier candidate in this run → skip.
4. **Accept** — add to `tag_to_outbound` map for this run.

Fallback (TCP mode, `XRAY_CHECK_ENABLED=False`):
- TCP connect to `address:port`, measure latency.
- Skip if latency > `MAX_LATENCY_MS` or address string already seen.

---

## Panel API (3x-ui v2.9.3)

All endpoints are relative to `PANEL_BASE_URL`.

| Endpoint | Method | Content-Type | Notes |
|---|---|---|---|
| `/login` | POST | form | Fields: `username`, `password` |
| `/panel/xray` | POST | — | Returns `{success, obj: "<json string>"}`. `obj` contains `{xraySetting: {...}, outboundTestUrl: "..."}` |
| `/panel/xray/update` | POST | `application/x-www-form-urlencoded` | Fields: `xraySetting=<json>`, `outboundTestUrl=<url>` |

**Critical:** Save endpoint must be `form-urlencoded`, NOT `application/json`. Sending JSON body causes a panel validation error (verified with DevTools).

**`outboundTestUrl`** must be included in every save request. `panel_client.py` caches it from the read response.

---

## Outbound settings format (per protocol)

Verified from `web/assets/js/model/outbound.js` in 3x-ui v2.9.3 source.

### VLESS — flat format (NOT vnext)
```json
{
  "protocol": "vless",
  "settings": {
    "address": "1.2.3.4",
    "port": 443,
    "id": "uuid-here",
    "flow": "",
    "encryption": "none"
  },
  "streamSettings": { ... },
  "tag": "out01"
}
```
> ⚠️ The Xray core spec uses `vnext`, but 3x-ui panel UI uses flat structure. Using `vnext` causes `undefined:undefined` in the panel form.

### VMess — vnext format
```json
{
  "protocol": "vmess",
  "settings": {
    "vnext": [{"address": "1.2.3.4", "port": 443,
      "users": [{"id": "uuid", "alterId": 0, "security": "auto"}]}]
  },
  "streamSettings": { ... },
  "tag": "out02"
}
```

### Trojan
```json
{
  "protocol": "trojan",
  "settings": {"servers": [{"address": "1.2.3.4", "port": 443, "password": "..."}]},
  "streamSettings": { ... },
  "tag": "out03"
}
```

### Shadowsocks
```json
{
  "protocol": "shadowsocks",
  "settings": {"servers": [{"address": "1.2.3.4", "port": 8388, "method": "aes-256-gcm", "password": "..."}]},
  "streamSettings": {"network": "tcp", "security": "none"},
  "tag": "out04"
}
```

---

## Xray 26.x compatibility

**`allowInsecure` was removed** from `tlsSettings` in Xray 26.x. Including it causes:
```
Failed to start: ... The feature "allowInsecure" has been removed
```
`converter.py` intentionally omits this field. Never add it back.

Xray binary path on this server: `/usr/local/x-ui/bin/xray-linux-amd64`

---

## Slot system

- Slots are outbounds whose tags match `^out\d{2}$` (e.g. `out01`…`out99`).
- Prefix/digits configurable via `SLOT_TAG_PREFIX` / `SLOT_TAG_DIGITS`.
- `merger.slot_tag(n)` generates the canonical tag string.
- `/fill N` — creates/replaces slots 1 through N.
- `/replace 1,5,8` — replaces only specified slot numbers.
- `/checkall` — reads existing slots, tests each via Xray, replaces failed ones.
- **Manually deleted slots are invisible to `/checkall`** — use `/fill` or `/replace` to recreate.

---

## Duplicate detection

**Xray mode (default):** dedup by **real exit IP**.
- For CDN-fronted configs (Cloudflare, etc.): the exit IP is the origin server IP, not the CDN edge. This is because traffic exits from the 3x-ui server through the CDN tunnel to the origin.
- Exit IPs seen in existing slots and earlier in the current run are both tracked.

**TCP mode (fallback):** dedup by **address string** — less accurate, can miss two different domains pointing to the same server.

---

## Config source

- URL: `https://raw.githubusercontent.com/roosterkid/openproxylist/main/V2RAY.txt`
- Updated hourly.
- Format: one link per line, prefixed with flag emoji and suffixed with latency/country.
- Regex: `((?:vless|vmess|trojan|ss)://\S+)`
- `CANDIDATES_TO_FETCH` (default 80) controls how many lines are read.

---

## Bot commands

| Command | Handler | Behaviour |
|---|---|---|
| `/start` | `cmd_start` | Shows help |
| `/help` | `cmd_help` | Shows help |
| `/status` | `cmd_status` | Lists all managed slots |
| `/fill N` | `cmd_fill` | Fills slots 1…N |
| `/replace 1,5` | `cmd_replace` | Replaces specific slots |
| `/checkall` | `cmd_checkall` | Checks all slots, replaces failed |
| `/setup` | `cmd_setup` (ConversationHandler) | URL → username → password → confirm |
| `/cancel` | `setup_cancel` | Aborts setup wizard |

- Unauthorised users are silently ignored (`_reject` logs a warning but sends no reply).
- `/setup` tests credentials by actually logging into the panel before saving.
- `/setup` writes new values to `config.py` on disk so they survive restart.
- Command menu registered on startup via `bot.set_my_commands()` — no BotFather needed.

---

## Performance

Xray mode: ~3–10s per candidate.
- `/fill 5` ≈ 30–60s
- `/fill 10` ≈ 1–2 min
- `/checkall` (10 slots) ≈ 2–3 min (checks all slots + fetches replacements for failed ones)

The Telegram bot stays responsive during these operations because they run inside the async handler (blocking the single handler thread but not the polling loop).

---

## Known issues / gotchas

- **Panel URL** must NOT include `/panel`. Installer and `/setup` both validate and warn.
- **VLESS flat format** — 3x-ui uses flat settings, not Xray core `vnext`. Using `vnext` breaks the panel UI.
- **`outboundTestUrl`** must accompany every save. `panel_client.py` handles this automatically.
- **`allowInsecure` removed in Xray 26.x** — never include in `tlsSettings`.
- **`CANDIDATES_TO_FETCH`** may need to be raised if many candidates fail the Xray check. With 80 candidates and 10 slots needed, there's usually enough margin.
- **Xray binary path** may differ on non-standard 3x-ui installs. Check with: `find / -name 'xray-linux-amd64' 2>/dev/null`

---

## Planned features

1. **Auto-schedule** — optional systemd timer to run `/checkall` nightly.
2. **Slot gap detection** — make `/checkall` detect and recreate manually deleted slots.
3. **Parallel candidate checking** — run multiple Xray processes in parallel to speed up `/fill` and `/checkall`.

---

## Development setup

```bash
git clone https://github.com/ali934h/tg-xui-manager
cd tg-xui-manager
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.py config.py
# fill in config.py, set XRAY_CHECK_ENABLED = False for local dev without xray
python bot.py
```

---

## Key files to read first

1. `config.example.py` — all available settings
2. `panel_client.py` — panel API details
3. `converter.py` — outbound dict structure per protocol + Xray 26.x notes
4. `ipcheck.py` — Xray subprocess flow
5. `merger.py` — how outbounds are inserted
6. `bot.py` — command handlers top to bottom
