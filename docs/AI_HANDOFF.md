# AI Handoff Document — tg-xui-manager

This document is written for an AI assistant (or developer) taking over this project.

---

## Project summary

`tg-xui-manager` is a **Telegram bot** that manages the outbound section of a [3x-ui](https://github.com/MHSanaei/3x-ui) Xray panel. It fetches free V2Ray/Trojan/SS configs from a public GitHub source, verifies each one in parallel by making a real HTTP request through a local Xray binary, filters out candidates that resolve to Cloudflare's edge network or to this server's own IP, and pushes verified, deduplicated configs into the panel — all triggered by Telegram commands.

**Stack:** Python 3.10+, `python-telegram-bot` v20+, `requests`, `PySocks`, `curl`, `systemd` on Ubuntu. **Tested on:** 3x-ui v2.9.3, Xray 26.4.25, Ubuntu 22.04/24.04.

---

## Repository layout

```
bot.py              Entry point. All Telegram handlers + SlotKeys dedup class.
panel_client.py     HTTP client for the 3x-ui panel API.
converter.py        Parses vless/vmess/trojan/ss links into outbound dicts.
ipcheck.py          Real connectivity check via Xray binary; returns exit IP.
                     Also rejects Cloudflare-range exit IPs and this server's own IP.
cf_ranges.py         Hardcoded Cloudflare IPv4/IPv6 ranges + is_cloudflare_ip().
slots_meta.py         JSON-backed store (slots_meta.json) for per-slot country
                       codes. Deliberately separate from the panel outbound object —
                       outbound tags are never touched to carry this data.
healthcheck.py        TCP connect latency check (fallback only).
scraper.py             Downloads candidate config list from GitHub raw, and extracts
                        each candidate's source country code from the same line.
merger.py               Merges outbound dicts into the full Xray config.
config.example.py      Template for config.py (never committed).
config.py                Runtime secrets (gitignored, chmod 600). Includes SERVER_IP.
install.sh                One-line installer: downloads Xray + systemd setup;
                           auto-detects and confirms SERVER_IP.
update.sh                  git pull + pip install + systemctl restart.
uninstall.sh                Stops and removes the service and all files.
docs/                        This folder.
```

---

## Architecture

```
Telegram user
    │
    ▼
bot.py  (python-telegram-bot polling)
    │
    ├── scraper.py        → downloads V2RAY.txt from roosterkid/openproxylist,
    │                         extracts (link, country_code) per line
    ├── converter.py      → parses all candidate links into outbound dicts
    ├── ipcheck.py        → starts Xray, connects through it, gets exit IP;
    │                         rejects Cloudflare ranges (cf_ranges.py) and
    │                         this server's own IP (config.SERVER_IP)
    ├── healthcheck.py    → TCP connect fallback (when XRAY_CHECK_ENABLED=False)
    ├── merger.py         → inserts/replaces outbounds in the Xray config dict
    │                         (strips the internal _meta key, including country,
    │                         before the config reaches the panel)
    ├── slots_meta.py     → persists {tag: country} after a successful save
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
  * Find a free local TCP port.
  * Write a temp Xray config: SOCKS5 inbound → candidate outbound.
  * Spawn `xray-linux-amd64 -c /tmp/tmpXXX.json`.
  * Wait `XRAY_STARTUP_WAIT_SEC` (default 2s).
  * Run `curl --proxy socks5h://127.0.0.1:{port} https://api.ipify.org`.
  * If curl returns a valid IPv4 → check the exit IP against two more filters:
    - `cf_ranges.is_cloudflare_ip(exit_ip)` — reject if inside a published
      Cloudflare range. Catches configs that are entirely served by a
      serverless/edge platform, where the outbound connection never leaves
      Cloudflare's network in the first place.
    - `exit_ip == config.SERVER_IP` — reject if it matches this server's own
      public IP (traffic would loop back through this box).
  * If it passes both, candidate is alive — return the exit IP.
  * Terminate xray and delete temp file.
- Post-filter: reject if exit IP is in `slot_keys.exit_ips` or was seen this run.
- Stop collecting when `needed` candidates are found.

**Step 3 — Apply**: push accepted outbounds to panel, then persist each
assigned slot's country code via `slots_meta.set_country(tag, country)`
(`_record_countries()` in `bot.py`).

### TCP fallback mode (`XRAY_CHECK_ENABLED=False`)

Same pre-filter (address/credential dedup), then serial TCP connect check.
**Cloudflare/self-IP filtering does NOT apply in this mode** — it needs the
real exit IP, which the TCP fallback never obtains.

---

## SlotKeys class

Defined in `bot.py`. Central to all dedup logic.

```
class SlotKeys:
    addresses:   set[str]   # IPs and domains of existing slots
    credentials: set[str]   # UUIDs and passwords of existing slots
    exit_ips:    set[str]   # real exit IPs (populated by /checkall)

    def is_duplicate(self, ob) -> (bool, reason)  # check one candidate
    def add_from_ob(self, ob)                      # register a slot's keys
```

`_current_slot_keys(xray_cfg)` builds this from all managed slots on the panel.

---

## Cloudflare / self-IP rejection (`ipcheck.py`, `cf_ranges.py`)

- `cf_ranges.py` hardcodes Cloudflare's published IPv4/IPv6 ranges (from
  `cloudflare.com/ips-v4` / `ips-v6`) and exposes `is_cloudflare_ip(ip) -> bool`
  via the stdlib `ipaddress` module. No runtime network dependency — refresh
  the list manually if Cloudflare adds new ranges.
- `config.SERVER_IP` holds this server's own public IP, filled in by
  `install.sh` (auto-detected via `api.ipify.org`, user confirms/overrides).
  Leave it `""` to disable the self-IP check.
- Both checks happen inside `ipcheck.check_outbound()`, right after a valid
  exit IP is obtained and before it's returned — so a rejected candidate
  behaves exactly like a failed connectivity check (returns `None`) from the
  caller's point of view.
- Deliberately scoped to *Cloudflare specifically*, not "any cloud provider":
  legitimate VPS candidates on AWS/GCP/DigitalOcean/Hetzner/etc. are real
  servers and must not be rejected. Only edge/serverless platforms that never
  actually run the candidate's own server process should be filtered this way.

---

## Country / location tracking (`scraper.py`, `slots_meta.py`, `/locations`)

- The source list (`V2RAY.txt` from `roosterkid/openproxylist`) already
  includes a country code on each line — no GeoIP database or external API
  lookup is used. Format per line:
  `<flag emoji> <config_link> <latency>ms <COUNTRY_CODE> [<ISP>]`
  `scraper.LOCATION_RE` extracts the 2-letter code; it can be absent (some
  lines carry no country tag), in which case an empty string is returned.
- `scraper.collect_candidate_configs()` now returns `list[tuple[link, country]]`
  instead of `list[str]` — this changed its public signature; both
  `_collect_candidates_parallel` and `_collect_candidates_serial` in `bot.py`
  were updated to unpack the tuple and attach `country` to the parsed
  outbound's `_meta` dict.
- `merger.replace_outbounds()` strips `_meta` (including `country`) before
  the outbound is sent to the panel, exactly as it always did — the panel
  outbound object is never touched by this feature.
- On a successful `/fill`, `/replace`, or `/checkall` save, `bot._record_countries()`
  writes `{tag: country}` into `slots_meta.json` via `slots_meta.set_country()`.
- `/locations` reads the current managed slot tags from the panel and looks
  up each one's stored country in `slots_meta.json` (`slots_meta.all_countries()`),
  reporting `"unknown"` for any tag with no recorded country (e.g. a slot
  that predates this feature, or was created outside the bot).
- `slots_meta.json` lives next to `config.py`, is gitignored, and is pure
  runtime state — safe to delete (countries just show as "unknown" until the
  next fill/replace/checkall).

---

## Panel API (3x-ui v2.9.3)

| Endpoint             | Method | Content-Type                        | Notes                                                                          |
| --------------------- | ------ | ------------------------------------- | --------------------------------------------------------------------------------- |
| `/login`             | POST   | form                                  | Fields: `username`, `password`                                                    |
| `/panel/xray`        | POST   | —                                     | Returns `{success, obj: "<json>"}`. `obj` has `{xraySetting, outboundTestUrl}`     |
| `/panel/xray/update` | POST   | `application/x-www-form-urlencoded`   | Fields: `xraySetting=<json>`, `outboundTestUrl=<url>`                              |

**Critical:** Save endpoint must be `form-urlencoded`, NOT `application/json`. **`outboundTestUrl`** must be included. `panel_client.py` caches it automatically.

---

## Outbound settings format (per protocol)

Verified from 3x-ui v2.9.3 source (`web/assets/js/model/outbound.js`).

### VLESS — flat format (NOT vnext)

```
{"protocol": "vless", "settings": {"address": "1.2.3.4", "port": 443, "id": "uuid", "flow": "", "encryption": "none"}, "tag": "out01"}
```
> ⚠️ Xray core uses `vnext` but 3x-ui panel UI expects flat. `vnext` shows `undefined:undefined`.

### VMess — vnext format

```
{"protocol": "vmess", "settings": {"vnext": [{"address": "1.2.3.4", "port": 443, "users": [{"id": "uuid", "alterId": 0, "security": "auto"}]}]}, "tag": "out02"}
```

### Trojan

```
{"protocol": "trojan", "settings": {"servers": [{"address": "1.2.3.4", "port": 443, "password": "..."}]}, "tag": "out03"}
```

### Shadowsocks

```
{"protocol": "shadowsocks", "settings": {"servers": [{"address": "1.2.3.4", "port": 8388, "method": "aes-256-gcm", "password": "..."}]}, "tag": "out04"}
```

---

## Xray 26.x compatibility

**`allowInsecure` removed** from `tlsSettings`. Including it causes xray to refuse to start. `converter.py` intentionally omits this field. Never add it back.

Xray binary installed by installer: `/root/tg-xui-manager-xray/xray` (v26.4.25).

---

## Slot system

- Tags matching `^out\d{2}$` are managed. Configurable via `SLOT_TAG_PREFIX` / `SLOT_TAG_DIGITS`.
- `/fill N` — creates/replaces slots 1…N.
- `/replace 1,5,8` — replaces specified slots.
- `/checkall` — tests all existing slots in parallel, replaces failed ones.
- `/locations` — reports each managed slot's recorded source country
  (`slots_meta.json`), read-only, no panel writes.
- **Manually deleted slots are invisible to `/checkall`** — use `/fill` or `/replace`.

---

## Duplicate detection (complete)

A candidate is rejected if **any** of these match an existing slot OR a candidate already accepted this run:

| Check            | What it catches                                                              |
| ------------------ | -------------------------------------------------------------------------------- |
| Address match     | Same IP or domain (e.g. `165.140.216.142`)                                       |
| Credential match  | Same UUID or password (different address, same server account)                    |
| Exit IP match     | Same real origin server (catches CDN-fronted configs with different domains)       |

All three checks run before the Xray test (address + credential) and after (exit IP).

In Xray mode, two more filters run on the exit IP **before** it's returned as
valid (see "Cloudflare / self-IP rejection" above): Cloudflare-range match,
and this server's own IP match.

---

## Config source

- URL: `https://raw.githubusercontent.com/roosterkid/openproxylist/main/V2RAY.txt`
- Updated hourly. `CANDIDATES_TO_FETCH` (default 80) = max lines read.
- Each line also carries a country code and ISP name; the country code is
  extracted by `scraper.py` and used for `/locations` (see above).

---

## Performance (measured)

| Operation            | Workers=5 | TCP fallback |
| ----------------------- | --------- | -------------- |
| `/fill 5`              | ~8–15s    | <2s            |
| `/fill 10`             | ~15–25s   | <5s            |
| `/checkall` 10 slots   | ~10–20s   | <5s            |

---

## Bot commands

| Command          | Handler                           | Behaviour                            |
| ----------------- | ------------------------------------ | ---------------------------------------- |
| `/start` `/help` | `cmd_start/help`                    | Shows help                                |
| `/status`        | `cmd_status`                        | Lists all managed slots                    |
| `/locations`     | `cmd_locations`                     | Lists all managed slots with their country |
| `/fill N`        | `cmd_fill`                          | Fills slots 1…N                            |
| `/replace 1,5`   | `cmd_replace`                       | Replaces specific slots                     |
| `/checkall`      | `cmd_checkall`                      | Checks all slots, replaces failed            |
| `/setup`         | `cmd_setup` (ConversationHandler)   | URL → username → password → confirm          |

---

## Known issues / gotchas

- **Panel URL** must NOT include `/panel`. Installer and `/setup` both validate.
- **VLESS flat format** — never use `vnext` for VLESS outbounds.
- **`outboundTestUrl`** required on every save. Handled automatically.
- **`allowInsecure` removed in Xray 26.x** — never include.
- **`CANDIDATES_TO_FETCH`** may need raising if source list has many failures. 80 is plenty for 10 slots normally.
- **Xray binary path** set automatically by installer. If changed manually, update `XRAY_BINARY` in `config.py`.
- **`cf_ranges.py` is a static snapshot** of Cloudflare's published ranges — if Cloudflare
  adds new ranges and candidates start slipping through, refresh the list from
  `cloudflare.com/ips-v4` / `ips-v6` and redeploy. No runtime fetch by design.
- **`SERVER_IP` missing on upgraded installs** — `config.py` files created before
  this feature won't have the key. `install.sh` appends it automatically on
  re-run; for a manual upgrade, add `SERVER_IP = "..."` to `config.py` yourself
  (or leave it unset — `getattr(config, "SERVER_IP", "")` defaults to `""`,
  which simply disables the self-IP check).
- **Cloudflare/self-IP filtering only applies in Xray mode** — the TCP fallback
  (`XRAY_CHECK_ENABLED = False`) never obtains a real exit IP, so it can't
  apply these filters.

---

## Planned features

1. **Auto-schedule** — systemd timer to run `/checkall` nightly.
2. **Slot gap detection** — `/checkall` detects and recreates manually deleted slots.
3. **Lower `XRAY_STARTUP_WAIT_SEC`** — could be 1.0–1.5s on fast servers.
4. **Broader edge-platform filtering** — consider extending `cf_ranges.py`-style
   filtering to other well-known serverless/edge networks (e.g. Fastly Compute,
   Vercel Edge) if they turn out to be common in the candidate source.

---

## Development setup

```
git clone https://github.com/ali934h/tg-xui-manager
cd tg-xui-manager
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp config.example.py config.py
# Set XRAY_CHECK_ENABLED = False for local dev (no xray needed)
# Cloudflare/self-IP filtering and country tracking are inert in this mode
python bot.py
```

---

## Key files to read first

1. `config.example.py` — all settings, including `SERVER_IP`
2. `panel_client.py` — API details
3. `converter.py` — outbound format per protocol
4. `bot.py` — `SlotKeys` class, then `_collect_candidates_parallel`, then handlers, then `cmd_locations`
5. `ipcheck.py` — Xray subprocess flow, then Cloudflare/self-IP rejection
6. `cf_ranges.py` / `slots_meta.py` — the two small new modules backing the above
