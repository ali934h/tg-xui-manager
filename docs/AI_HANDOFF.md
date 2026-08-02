# AI Handoff Document — tg-xui-manager

This document is written for an AI assistant (or developer) taking over this project. It covers everything needed to understand the codebase, make changes safely, and continue development.

---

## Project summary

`tg-xui-manager` is a **Telegram bot** that manages the outbound section of a [3x-ui](https://github.com/MHSanaei/3x-ui) Xray panel. It fetches free V2Ray/Trojan/SS configs from a public GitHub source, health-checks them via TCP connect, and pushes healthy ones into the panel's Xray config — all triggered by Telegram commands.

**Stack:** Python 3.10+, `python-telegram-bot` v20+, `requests`, `systemd` on Ubuntu.

---

## Repository layout

```
bot.py              Entry point. All Telegram command handlers live here.
panel_client.py     HTTP client for the 3x-ui panel API.
converter.py        Parses vless/vmess/trojan/ss subscription links into outbound dicts.
healthcheck.py      TCP connect latency check.
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
    ├── healthcheck.py    → TCP connect to address:port, measures latency
    ├── merger.py         → inserts/replaces outbounds in the Xray config dict
    └── panel_client.py   → login + read + save via 3x-ui HTTP API
```

No database, no scheduler. Everything is triggered by a Telegram command and runs synchronously inside the async handler.

---

## Panel API (3x-ui v2.9.3)

Verified with DevTools on a real panel. All endpoints are relative to `PANEL_BASE_URL`.

| Endpoint | Method | Content-Type | Notes |
|---|---|---|---|
| `/login` | POST | form | Fields: `username`, `password` |
| `/panel/xray` | POST | — | Returns `{success, obj: "<json string>"}`. `obj` is a JSON string containing `{xraySetting: {...}, outboundTestUrl: "..."}` |
| `/panel/xray/update` | POST | `application/x-www-form-urlencoded` | Fields: `xraySetting=<json string>`, `outboundTestUrl=<url>` |

**Critical:** The save endpoint is `form-urlencoded`, NOT `application/json`. This was verified with raw DevTools capture. Sending JSON body causes a panel validation error.

**`outboundTestUrl`** must be included in the save request or the panel rejects it. `panel_client.py` caches it from the read response.

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
> ⚠️ The Xray core spec uses `vnext`, but 3x-ui's panel UI uses a flat structure. Using `vnext` causes the panel form to show `undefined:undefined` for address/port.

### VMess — vnext format
```json
{
  "protocol": "vmess",
  "settings": {
    "vnext": [{
      "address": "1.2.3.4",
      "port": 443,
      "users": [{"id": "uuid", "alterId": 0, "security": "auto"}]
    }]
  },
  "streamSettings": { ... },
  "tag": "out02"
}
```

### Trojan
```json
{
  "protocol": "trojan",
  "settings": {
    "servers": [{"address": "1.2.3.4", "port": 443, "password": "..."}]
  },
  "streamSettings": { ... },
  "tag": "out03"
}
```

### Shadowsocks
```json
{
  "protocol": "shadowsocks",
  "settings": {
    "servers": [{"address": "1.2.3.4", "port": 8388, "method": "aes-256-gcm", "password": "..."}]
  },
  "streamSettings": {"network": "tcp", "security": "none"},
  "tag": "out04"
}
```

---

## Slot system

- Slots are outbounds whose tags match the regex `^out\d{2}$` (e.g. `out01`…`out99`).
- Prefix and digit count are configurable via `SLOT_TAG_PREFIX` and `SLOT_TAG_DIGITS` in `config.py`.
- `merger.slot_tag(n)` generates the canonical tag string.
- `/fill N` creates/replaces slots 1 through N.
- `/replace 1,5,8` replaces only the specified slot numbers.
- `/checkall` reads existing slots from the panel, TCP-checks each one, replaces failed ones.
- **Manually deleted slots** are invisible to `/checkall` — use `/fill` or `/replace` to recreate.

---

## Duplicate detection

Currently based on **address string match** within a single run:
- Before accepting a candidate, the bot checks if its `address` is already used by an existing managed slot OR was already picked in the current run.
- This works for direct IPs and non-CDN domains.
- For Cloudflare-proxied domains: the `address` in the link is the Cloudflare entry point, not the origin IP. Address-string dedup still avoids exact duplicates but cannot detect different domains pointing to the same origin.

**Planned improvement:** Use the Xray binary (already installed by 3x-ui) to actually connect through each candidate and fetch the exit IP from `https://api.ipify.org`. This gives the real origin IP and enables true dedup.

---

## Config source

- URL: `https://raw.githubusercontent.com/roosterkid/openproxylist/main/V2RAY.txt`
- Updated hourly by the source repo.
- Format: one config link per line, prefixed with a flag emoji and suffixed with latency/country info.
- Regex extracts the link: `((?:vless|vmess|trojan|ss)://\S+)`
- `CANDIDATES_TO_FETCH` (default 80) controls how many lines are read before stopping.

---

## Health check

- Simple TCP `connect()` to `address:port`.
- Threshold: `MAX_LATENCY_MS` (default 350ms).
- Timeout: `TCP_CONNECT_TIMEOUT_SEC` (default 4s).
- The check uses `_meta.address` and `_meta.port` which `converter.py` attaches to every outbound dict.
- `merger.py` strips `_meta` before sending to the panel.

---

## Bot commands

| Command | Handler | Behaviour |
|---|---|---|
| `/start` | `cmd_start` | Shows help text |
| `/help` | `cmd_help` | Shows help text |
| `/status` | `cmd_status` | Reads panel, lists all managed slots |
| `/fill N` | `cmd_fill` | Fills slots 1…N with healthy candidates |
| `/replace 1,5` | `cmd_replace` | Replaces specific slots (health-checked) |
| `/checkall` | `cmd_checkall` | Checks existing slots, replaces failed ones |
| `/setup` | `cmd_setup` (ConversationHandler) | 3-step wizard: URL → username → password → confirm |
| `/cancel` | `setup_cancel` | Aborts setup wizard |

- All handlers start with `if not _allowed(update): return await _reject(update)` — unauthorised users get no response.
- `/setup` validates credentials by actually logging into the panel before saving.
- `/setup` writes the new values to `config.py` on disk (regex replace) so they survive restart.
- Command menu is registered with Telegram on startup via `bot.set_my_commands()` (no BotFather needed).

---

## Planned features (not yet implemented)

1. **Real IP dedup via Xray binary**
   - Spin up a temporary Xray process with SOCKS5 inbound on a random port.
   - Make an HTTP request through it to `https://api.ipify.org`.
   - Use the returned IP for dedup instead of the address string.
   - Xray binary path: `/usr/local/x-ui/bin/xray` (installed by 3x-ui).
   - Decision pending: run after TCP health check (more accurate, slower) or replace it.

2. **Auto-schedule** — optional cron/systemd timer to run `/checkall` nightly.

3. **Slot gap detection** — make `/checkall` detect and recreate manually deleted slots.

---

## Known issues / gotchas

- **Panel URL** must NOT include `/panel` or `/panel/xray`. The installer now validates this, and `/setup` also warns if `/panel` is in the URL.
- **VLESS settings** use a flat format in 3x-ui (not `vnext`). This differs from the Xray core spec and was the cause of `undefined:undefined` showing in the panel UI before the fix.
- **`outboundTestUrl`** must be sent with every save request. `panel_client.py` caches it automatically from the last read.
- **Large `CANDIDATES_TO_FETCH`** values slow down `/fill` and `/checkall` significantly since each candidate is TCP-checked.

---

## Development setup

```bash
git clone https://github.com/ali934h/tg-xui-manager
cd tg-xui-manager
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.py config.py
# fill in config.py
python bot.py
```

---

## Key files to read first

If you're picking this up for the first time, read in this order:

1. `config.example.py` — understand all settings
2. `panel_client.py` — how the panel API works
3. `converter.py` — outbound dict structure per protocol
4. `merger.py` — how outbounds are inserted into the config
5. `bot.py` — command flow from top to bottom
