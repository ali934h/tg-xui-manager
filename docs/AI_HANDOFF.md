# AI Handoff Document — tg-xui-manager

This document is written for an AI assistant (or developer) taking over this project. It covers everything needed to understand the codebase, make changes safely, and continue development.

---

## Project summary

`tg-xui-manager` is a **Telegram bot** that manages the outbound section of a [3x-ui](https://github.com/MHSanaei/3x-ui) Xray panel. It fetches free V2Ray/Trojan/SS configs from a public GitHub source, verifies each one in parallel by making a real HTTP request through the local Xray binary, and pushes verified, deduplicated configs into the panel — all triggered by Telegram commands.

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
    ├── converter.py      → parses all candidate links into outbound dicts
    ├── ipcheck.py        → starts Xray, connects through it, gets exit IP
    ├── healthcheck.py    → TCP connect fallback (when XRAY_CHECK_ENABLED=False)
    ├── merger.py         → inserts/replaces outbounds in the Xray config dict
    └── panel_client.py   → login + read + save via 3x-ui HTTP API
```

No database, no scheduler. Everything is triggered by a Telegram command.

---

## Candidate evaluation pipeline

### Xray mode (default, `XRAY_CHECK_ENABLED=True`)

1. **Parse all candidates** (`converter.py`) — extract protocol, address, port, settings.
2. **Parallel check** (`_collect_candidates_parallel` in `bot.py`):
   - Use `ThreadPoolExecutor(max_workers=XRAY_WORKERS)` (default 5).
   - Submit candidates in batches of `XRAY_WORKERS * 3`.
   - Each worker calls `ipcheck.check_outbound(ob)`:
     - Find a free local TCP port.
     - Write a temp Xray config: SOCKS5 inbound → candidate outbound.
     - Spawn `xray-linux-amd64 -c /tmp/tmpXXX.json`.
     - Wait `XRAY_STARTUP_WAIT_SEC` (default 2s).
     - Run `curl --proxy socks5h://127.0.0.1:{port} https://api.ipify.org`.
     - If curl returns a valid IPv4 → return that IP as exit IP.
     - Terminate xray and delete temp file.
   - Collect results as futures complete (`as_completed`).
   - **Dedup**: skip if exit IP already in `existing_keys` (current slots) or `seen_ips` (this run).
   - Stop when `needed` good candidates are found.
3. **Apply** — push accepted outbounds to panel via `panel_client`.

### TCP fallback mode (`XRAY_CHECK_ENABLED=False`)

- Serial loop: parse → TCP connect → dedup by address string.
- Much faster but less accurate (can't detect dead protocols or same-origin CDN configs).

---

## Parallelism details

- `ThreadPoolExecutor` is used (not `asyncio`) because `ipcheck.check_outbound` is blocking (subprocess + curl).
- The thread pool runs inside the async Telegram handler, blocking that handler's thread. The Telegram polling loop itself is not blocked (it runs in a separate asyncio task).
- `seen_ips` is protected by a `threading.Lock()` to prevent race conditions when multiple workers finish simultaneously.
- `existing_keys` is read-only in workers (built before the pool starts) — no lock needed.
- Batch size = `XRAY_WORKERS * 3` to keep the pool fed without submitting all 80 candidates at once.

---

## Panel API (3x-ui v2.9.3)

All endpoints are relative to `PANEL_BASE_URL`.

| Endpoint | Method | Content-Type | Notes |
|---|---|---|---|
| `/login` | POST | form | Fields: `username`, `password` |
| `/panel/xray` | POST | — | Returns `{success, obj: "<json string>"}`. `obj` contains `{xraySetting, outboundTestUrl}` |
| `/panel/xray/update` | POST | `application/x-www-form-urlencoded` | Fields: `xraySetting=<json>`, `outboundTestUrl=<url>` |

**Critical:** Save endpoint must be `form-urlencoded`, NOT `application/json`. Verified with DevTools raw capture.

**`outboundTestUrl`** must be included in every save. `panel_client.py` caches it from the read response.

---

## Outbound settings format (per protocol)

Verified from `web/assets/js/model/outbound.js` in 3x-ui v2.9.3 source.

### VLESS — flat format (NOT vnext)
```json
{
  "protocol": "vless",
  "settings": {"address": "1.2.3.4", "port": 443, "id": "uuid", "flow": "", "encryption": "none"},
  "streamSettings": { ... },
  "tag": "out01"
}
```
> ⚠️ Using Xray core `vnext` format causes `undefined:undefined` in the 3x-ui panel UI.

### VMess — vnext format
```json
{
  "protocol": "vmess",
  "settings": {"vnext": [{"address": "1.2.3.4", "port": 443,
    "users": [{"id": "uuid", "alterId": 0, "security": "auto"}]}]},
  "streamSettings": { ... },
  "tag": "out02"
}
```

### Trojan
```json
{
  "protocol": "trojan",
  "settings": {"servers": [{"address": "1.2.3.4", "port": 443, "password": "..."}]},
  "streamSettings": { ... }, "tag": "out03"
}
```

### Shadowsocks
```json
{
  "protocol": "shadowsocks",
  "settings": {"servers": [{"address": "1.2.3.4", "port": 8388, "method": "aes-256-gcm", "password": "..."}]},
  "streamSettings": {"network": "tcp", "security": "none"}, "tag": "out04"
}
```

---

## Xray 26.x compatibility

**`allowInsecure` was removed** from `tlsSettings` in Xray 26.x:
```
Failed to start: ... The feature "allowInsecure" has been removed
```
`converter.py` intentionally omits this field. Never add it back.

Xray binary path: `/usr/local/x-ui/bin/xray-linux-amd64`

---

## Slot system

- Tags matching `^out\d{2}$` (e.g. `out01`…`out99`) are "managed slots".
- Prefix/digits configurable: `SLOT_TAG_PREFIX` / `SLOT_TAG_DIGITS`.
- `merger.slot_tag(n)` generates the canonical tag.
- `/fill N` — creates/replaces slots 1 through N.
- `/replace 1,5,8` — replaces only specified slot numbers.
- `/checkall` — reads existing slots, tests in parallel, replaces failed ones.
- **Manually deleted slots are invisible to `/checkall`** — use `/fill` or `/replace` to recreate.

---

## Duplicate detection

**Xray mode:** dedup by **real exit IP**.
- CDN-fronted configs: exit IP = origin server IP (traffic exits from the 3x-ui server).
- Both existing slots and candidates found earlier in this run are tracked.
- Lock protects `seen_ips` set during parallel collection.

**TCP fallback:** dedup by **address string** — less accurate.

---

## Config source

- URL: `https://raw.githubusercontent.com/roosterkid/openproxylist/main/V2RAY.txt`
- Updated hourly. One link per line, prefixed with flag emoji.
- Regex: `((?:vless|vmess|trojan|ss)://\S+)`
- `CANDIDATES_TO_FETCH` (default 80) = max lines read.

---

## Performance (measured)

| Operation | Workers=5 | TCP fallback |
|---|---|---|
| `/fill 5` | ~8–15s | <2s |
| `/fill 10` | ~15–25s | <5s |
| `/checkall` 10 slots | ~10–20s | <5s |

Bottleneck: `XRAY_STARTUP_WAIT_SEC` (2s) per worker — lowering it to 1.5s may help on fast servers.

---

## Bot commands

| Command | Handler | Behaviour |
|---|---|---|
| `/start` | `cmd_start` | Shows help |
| `/help` | `cmd_help` | Shows help |
| `/status` | `cmd_status` | Lists all managed slots |
| `/fill N` | `cmd_fill` | Fills slots 1…N (parallel) |
| `/replace 1,5` | `cmd_replace` | Replaces specific slots (parallel) |
| `/checkall` | `cmd_checkall` | Checks all slots in parallel, replaces failed |
| `/setup` | `cmd_setup` (ConversationHandler) | URL → username → password → confirm |
| `/cancel` | `setup_cancel` | Aborts setup wizard |

- Unauthorised users silently ignored.
- `/setup` tests credentials before saving, writes to `config.py` on disk.
- Command menu auto-registered via `bot.set_my_commands()` on startup.

---

## Known issues / gotchas

- **Panel URL** must NOT include `/panel`. Both installer and `/setup` validate this.
- **VLESS flat format** — use flat settings, not `vnext`. `vnext` breaks panel UI.
- **`outboundTestUrl`** must accompany every save. Handled automatically.
- **`allowInsecure` removed in Xray 26.x** — never include in `tlsSettings`.
- **`CANDIDATES_TO_FETCH`** may need raising if many candidates fail. 80 is usually plenty for 10 slots.
- **Xray binary path** may differ. Find it with: `find / -name 'xray-linux-amd64' 2>/dev/null`
- **Parallel workers share port space**: each worker picks a random free port for its SOCKS5 inbound. Port collisions are extremely unlikely but theoretically possible on very busy servers.

---

## Planned features

1. **Auto-schedule** — systemd timer to run `/checkall` nightly.
2. **Slot gap detection** — `/checkall` detects and recreates manually deleted slots.
3. **Tunable `XRAY_STARTUP_WAIT_SEC`** — already in config, could be lowered to 1.0–1.5s for faster servers.

---

## Development setup

```bash
git clone https://github.com/ali934h/tg-xui-manager
cd tg-xui-manager
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.py config.py
# Set XRAY_CHECK_ENABLED = False for local dev (no xray binary needed)
python bot.py
```

---

## Key files to read first

1. `config.example.py` — all available settings and their defaults
2. `panel_client.py` — panel API details (critical: form-urlencoded, outboundTestUrl)
3. `converter.py` — outbound dict structure per protocol + Xray 26.x notes
4. `ipcheck.py` — Xray subprocess + curl flow
5. `merger.py` — how outbounds are inserted/replaced
6. `bot.py` — `_collect_candidates_parallel`, then command handlers
