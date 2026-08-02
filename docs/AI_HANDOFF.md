# AI Handoff Document — tg-xui-manager

This document is written for an AI assistant (or developer) taking over this project.

---

## Project summary

`tg-xui-manager` is a **Telegram bot** that manages the outbound section of a [3x-ui](https://github.com/MHSanaei/3x-ui) Xray panel. It fetches free V2Ray/Trojan/SS configs from a public GitHub source, verifies each one in parallel by making a real HTTP request through a local Xray binary, and pushes verified, deduplicated configs into the panel — all triggered by Telegram commands.

**Stack:** Python 3.10+, `python-telegram-bot` v20+, `requests`, `PySocks`, `curl`, `systemd` on Ubuntu.
**Tested on:** 3x-ui v2.9.3, Xray 26.4.25, Ubuntu 22.04/24.04.

---

## Repository layout

```
bot.py              Entry point. All Telegram handlers + SlotKeys dedup class.
panel_client.py     HTTP client for the 3x-ui panel API.
converter.py        Parses vless/vmess/trojan/ss links into outbound dicts.
ipcheck.py          Real connectivity check via Xray binary; returns exit IP.
healthcheck.py      TCP connect latency check (fallback only).
scraper.py          Downloads candidate config list from GitHub raw.
merger.py           Merges outbound dicts into the full Xray config.
config.example.py   Template for config.py (never committed).
config.py           Runtime secrets (gitignored, chmod 600).
install.sh          One-line installer: downloads Xray + systemd setup.
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

**Step 1 — Pre-filter** (no I/O, instant):

For each candidate, build a `SlotKeys` object from existing panel slots containing:
- `addresses` — set of all addresses (IP/domain) in use
- `credentials` — set of all UUIDs and passwords in use
- `exit_ips` — set of real exit IPs (populated during `/checkall`)

A candidate is **rejected if ANY of these match**:
- Its address is in `slot_keys.addresses` or was accepted earlier this run
- Its credential (UUID/password) is in `slot_keys.credentials` or was accepted earlier this run

This is the key improvement over earlier versions that only checked `address:port:credential` as a combined fingerprint — now each field is checked independently.

**Step 2 — Parallel Xray check** (`_collect_candidates_parallel`):
- Use `ThreadPoolExecutor(max_workers=XRAY_WORKERS)` (default 5).
- Submit candidates in batches of `XRAY_WORKERS * 3`.
- Each worker calls `ipcheck.check_outbound(ob)`:
  - Find a free local TCP port.
  - Write a temp Xray config: SOCKS5 inbound → candidate outbound.
  - Spawn `xray-linux-amd64 -c /tmp/tmpXXX.json`.
  - Wait `XRAY_STARTUP_WAIT_SEC` (default 2s).
  - Run `curl --proxy socks5h://127.0.0.1:{port} https://api.ipify.org`.
  - If curl returns a valid IPv4 → candidate is alive, return that IP.
  - Terminate xray and delete temp file.
- Post-filter: reject if exit IP is in `slot_keys.exit_ips` or was seen this run.
- Stop collecting when `needed` candidates are found.

**Step 3 — Apply**: push accepted outbounds to panel.

### TCP fallback mode (`XRAY_CHECK_ENABLED=False`)

Same pre-filter (address/credential dedup), then serial TCP connect check.

---

## SlotKeys class

Defined in `bot.py`. Central to all dedup logic.

```python
class SlotKeys:
    addresses:   set[str]   # IPs and domains of existing slots
    credentials: set[str]   # UUIDs and passwords of existing slots
    exit_ips:    set[str]   # real exit IPs (populated by /checkall)

    def is_duplicate(self, ob) -> (bool, reason)  # check one candidate
    def add_from_ob(self, ob)                      # register a slot's keys
```

`_current_slot_keys(xray_cfg)` builds this from all managed slots on the panel.

---

## Panel API (3x-ui v2.9.3)

| Endpoint | Method | Content-Type | Notes |
|---|---|---|---|
| `/login` | POST | form | Fields: `username`, `password` |
| `/panel/xray` | POST | — | Returns `{success, obj: "<json>"}`. `obj` has `{xraySetting, outboundTestUrl}` |
| `/panel/xray/update` | POST | `application/x-www-form-urlencoded` | Fields: `xraySetting=<json>`, `outboundTestUrl=<url>` |

**Critical:** Save endpoint must be `form-urlencoded`, NOT `application/json`.
**`outboundTestUrl`** must be included. `panel_client.py` caches it automatically.

---

## Outbound settings format (per protocol)

Verified from 3x-ui v2.9.3 source (`web/assets/js/model/outbound.js`).

### VLESS — flat format (NOT vnext)
```json
{"protocol": "vless", "settings": {"address": "1.2.3.4", "port": 443, "id": "uuid", "flow": "", "encryption": "none"}, "tag": "out01"}
```
> ⚠️ Xray core uses `vnext` but 3x-ui panel UI expects flat. `vnext` shows `undefined:undefined`.

### VMess — vnext format
```json
{"protocol": "vmess", "settings": {"vnext": [{"address": "1.2.3.4", "port": 443, "users": [{"id": "uuid", "alterId": 0, "security": "auto"}]}]}, "tag": "out02"}
```

### Trojan
```json
{"protocol": "trojan", "settings": {"servers": [{"address": "1.2.3.4", "port": 443, "password": "..."}]}, "tag": "out03"}
```

### Shadowsocks
```json
{"protocol": "shadowsocks", "settings": {"servers": [{"address": "1.2.3.4", "port": 8388, "method": "aes-256-gcm", "password": "..."}]}, "tag": "out04"}
```

---

## Xray 26.x compatibility

**`allowInsecure` removed** from `tlsSettings`. Including it causes xray to refuse to start.
`converter.py` intentionally omits this field. Never add it back.

Xray binary installed by installer: `/root/tg-xui-manager-xray/xray` (v26.4.25).

---

## Slot system

- Tags matching `^out\d{2}$` are managed. Configurable via `SLOT_TAG_PREFIX` / `SLOT_TAG_DIGITS`.
- `/fill N` — creates/replaces slots 1…N.
- `/replace 1,5,8` — replaces specified slots.
- `/checkall` — tests all existing slots in parallel, replaces failed ones.
- **Manually deleted slots are invisible to `/checkall`** — use `/fill` or `/replace`.

---

## Duplicate detection (complete)

A candidate is rejected if **any** of these match an existing slot OR a candidate already accepted this run:

| Check | What it catches |
|---|---|
| Address match | Same IP or domain (e.g. `165.140.216.142`) |
| Credential match | Same UUID or password (different address, same server account) |
| Exit IP match | Same real origin server (catches CDN-fronted configs with different domains) |

All three checks run before the Xray test (address + credential) and after (exit IP).

---

## Config source

- URL: `https://raw.githubusercontent.com/roosterkid/openproxylist/main/V2RAY.txt`
- Updated hourly. `CANDIDATES_TO_FETCH` (default 80) = max lines read.

---

## Performance (measured)

| Operation | Workers=5 | TCP fallback |
|---|---|---|
| `/fill 5` | ~8–15s | <2s |
| `/fill 10` | ~15–25s | <5s |
| `/checkall` 10 slots | ~10–20s | <5s |

---

## Bot commands

| Command | Handler | Behaviour |
|---|---|---|
| `/start` `/help` | `cmd_start/help` | Shows help |
| `/status` | `cmd_status` | Lists all managed slots |
| `/fill N` | `cmd_fill` | Fills slots 1…N |
| `/replace 1,5` | `cmd_replace` | Replaces specific slots |
| `/checkall` | `cmd_checkall` | Checks all slots, replaces failed |
| `/setup` | `cmd_setup` (ConversationHandler) | URL → username → password → confirm |

---

## Known issues / gotchas

- **Panel URL** must NOT include `/panel`. Installer and `/setup` both validate.
- **VLESS flat format** — never use `vnext` for VLESS outbounds.
- **`outboundTestUrl`** required on every save. Handled automatically.
- **`allowInsecure` removed in Xray 26.x** — never include.
- **`CANDIDATES_TO_FETCH`** may need raising if source list has many failures. 80 is plenty for 10 slots normally.
- **Xray binary path** set automatically by installer. If changed manually, update `XRAY_BINARY` in `config.py`.

---

## Planned features

1. **Auto-schedule** — systemd timer to run `/checkall` nightly.
2. **Slot gap detection** — `/checkall` detects and recreates manually deleted slots.
3. **Lower `XRAY_STARTUP_WAIT_SEC`** — could be 1.0–1.5s on fast servers.

---

## Development setup

```bash
git clone https://github.com/ali934h/tg-xui-manager
cd tg-xui-manager
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp config.example.py config.py
# Set XRAY_CHECK_ENABLED = False for local dev (no xray needed)
python bot.py
```

---

## Key files to read first

1. `config.example.py` — all settings
2. `panel_client.py` — API details
3. `converter.py` — outbound format per protocol
4. `bot.py` — `SlotKeys` class, then `_collect_candidates_parallel`, then handlers
5. `ipcheck.py` — Xray subprocess flow
