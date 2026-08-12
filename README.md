# tg-xui-manager

Telegram bot to manage outbounds on a [3x-ui](https://github.com/MHSanaei/3x-ui) panel.
Replaces stale or failed outbounds with healthy free configs fetched from a public V2Ray list — all on demand, via Telegram commands. No scheduler, no cron, no automatic rotation.

> **Tested on:** 3x-ui v2.9.3 · Xray 26.4.25 · Ubuntu 22.04 / 24.04 · Python 3.10+

---

## Features

| Command          | Description                                                        |
| ----------------- | ------------------------------------------------------------------ |
| `/fill N`        | Fill slots `out01`…`out0N` with verified, non-duplicate candidates |
| `/replace 1,5,8` | Force-replace specific slot numbers                                |
| `/checkall`      | Check every managed slot; replace only the failed ones             |
| `/status`        | List all managed slots with address / port / protocol              |
| `/locations`     | List all managed slots with their source country                  |
| `/setup`         | Interactive wizard to change panel credentials                     |
| `/help`          | Show all commands                                                  |

Unauthorised users are **silently ignored** — no reply that would reveal the bot exists.

---

## How candidate verification works

Each candidate config is checked against existing slots and previous candidates in this run. A candidate is **rejected** if any of the following match an existing slot:

- **Address** (IP or domain)
- **Credential** (UUID for vless/vmess, password for trojan/shadowsocks)
- **Real exit IP** (obtained by connecting through the candidate via Xray)

Candidates that pass all checks are then tested for **real connectivity**:

1. The local Xray binary starts with a temporary SOCKS5 inbound.
2. An HTTP request is made through it to `https://api.ipify.org`.
3. The returned IP is the **real exit IP** of the candidate server.
4. All checks run in **parallel** (`XRAY_WORKERS`, default 5).

The exit IP is then filtered again before the candidate is accepted:

- **Cloudflare rejection** — if the exit IP falls inside Cloudflare's published IP ranges (`cf_ranges.py`), the candidate is rejected. This catches configs that are entirely served by a serverless/edge platform (e.g. a Cloudflare Worker) rather than by a real origin server — in that case the outbound connection never actually leaves Cloudflare's network, so the "exit IP" is just one of Cloudflare's own edge addresses.
- **Self-IP rejection** — if the exit IP matches this server's own public IP (`config.SERVER_IP`, auto-detected by `install.sh`), the candidate is rejected, since traffic would just loop back through this box instead of reaching a distinct server.

Fallback: set `XRAY_CHECK_ENABLED = False` for simple TCP latency check (faster, less accurate — Cloudflare/self-IP filtering only applies in Xray mode, since it needs the real exit IP).

---

## Requirements

- Ubuntu 22.04 / 24.04 VPS with root access
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your numeric Telegram user ID (ask [@userinfobot](https://t.me/userinfobot))
- The panel can be on **any server** (same or different) — only the URL matters

> The installer downloads its own Xray binary (v26.4.25) independently of any 3x-ui installation.

---

## Install

One-line install (run as root):

```
bash <(curl -fsSL https://raw.githubusercontent.com/ali934h/tg-xui-manager/main/install.sh)
```

The installer will:

1. Install `python3`, `python3-venv`, `git`, and `unzip`
2. Download Xray v26.4.25 to `/root/tg-xui-manager-xray/xray`
3. Clone this repo to `/root/tg-xui-manager`
4. Create an isolated Python venv and install dependencies
5. Prompt for panel URL, username, password, bot token, allowed user IDs, and this server's public IP (auto-detected — press Enter to accept, or type a different value)
6. Install and start a `systemd` service that auto-starts on reboot

### Panel URL format

```
✅ https://example.com:2053/mywebbasepath
❌ https://example.com:2053/mywebbasepath/panel
❌ https://example.com:2053/mywebbasepath/panel/xray
```

The installer validates this and will re-prompt if `/panel` is included.

---

## Daily commands

```
journalctl -u tg-xui-manager -f          # live logs (Ctrl+C to stop)
systemctl status tg-xui-manager           # service status
systemctl restart tg-xui-manager          # restart
systemctl stop tg-xui-manager             # stop

bash /root/tg-xui-manager/update.sh       # pull latest code + restart
bash /root/tg-xui-manager/uninstall.sh    # remove everything
```

> If you're updating an existing install from before the Cloudflare/self-IP/locations
> features, `config.py` won't have `SERVER_IP` yet — add it manually
> (`SERVER_IP = "your.server.ip"`, or leave it `""` to disable the self-IP check).

---

## Configuration

Credentials live in `/root/tg-xui-manager/config.py` (chmod 600, not tracked by git).
Use `/setup` in Telegram to change panel credentials at any time — the bot tests them before saving.

| Key                        | Default                          | Description                                                |
| --------------------------- | --------------------------------- | ----------------------------------------------------------- |
| `PANEL_BASE_URL`           | —                                | Panel URL (without `/panel`)                               |
| `PANEL_USERNAME`           | —                                | Panel login username                                       |
| `PANEL_PASSWORD`           | —                                | Panel login password                                       |
| `BOT_TOKEN`                | —                                | Telegram bot token                                          |
| `ALLOWED_USERS`            | —                                | List of numeric Telegram user IDs                           |
| `SERVER_IP`                | `""`                             | This server's own public IP; auto-detected by `install.sh`. Candidates whose exit IP matches this are rejected. Leave empty to disable. |
| `XRAY_CHECK_ENABLED`       | `True`                           | Use Xray binary for real connectivity check                 |
| `XRAY_BINARY`              | `/root/tg-xui-manager-xray/xray` | Path to Xray binary                                          |
| `XRAY_WORKERS`             | `5`                              | Number of parallel candidate checks                          |
| `XRAY_STARTUP_WAIT_SEC`    | `2.0`                            | Seconds to wait for Xray to start                            |
| `XRAY_REQUEST_TIMEOUT_SEC` | `8`                              | HTTP request timeout through SOCKS5                          |
| `CANDIDATES_TO_FETCH`      | `80`                             | How many candidates to pull from source per run              |
| `SLOT_TAG_PREFIX`          | `out`                            | Tag prefix — `out` → `out01`, `out02`, …                    |
| `SLOT_TAG_DIGITS`          | `2`                              | Zero-padding width                                            |
| `MAX_LATENCY_MS`           | `350`                            | TCP latency threshold (fallback mode only)                   |
| `TCP_CONNECT_TIMEOUT_SEC`  | `4`                              | TCP timeout (fallback mode only)                              |

---

## How slots work

- The bot manages outbounds whose tags match `out01`…`out99` (configurable prefix/digits).
- `/fill N` fills slots 1 through N — creates them if missing, replaces if present.
- `/checkall` only checks slots that **currently exist** on the panel. Manually deleted slots are not detected — use `/fill N` or `/replace N` to recreate them.
- Duplicate detection checks **address, credential, and exit IP** independently — if any one matches an existing slot, the candidate is rejected.
- Each slot's source **country code** is recorded in `slots_meta.json` (next to `config.py`) whenever the slot is filled or replaced. This is entirely separate from the panel's outbound object — outbound tags (`out01`, etc.) are never modified to carry this information. See `/locations` below.

---

## Performance

With `XRAY_WORKERS = 5` (default):

| Operation              | Time    |
| ------------------------ | ------- |
| `/fill 5`              | ~8–15s  |
| `/fill 10`             | ~15–25s |
| `/checkall` (10 slots) | ~10–20s |

With `XRAY_CHECK_ENABLED = False` (TCP fallback): all operations complete in under 5 seconds.

---

## Config source

Free configs are fetched from [`github.com/roosterkid/openproxylist`](https://github.com/roosterkid/openproxylist) — updated hourly.

Supported protocols: `vless`, `vmess`, `trojan`, `shadowsocks`.

Each source line also carries a country code (e.g. `NL`, `US`) and ISP name. The bot reads the country code directly from this list — no GeoIP database or external lookup is used — and stores it in `slots_meta.json` for `/locations`.

---

## Panel API compatibility

Verified against **3x-ui v2.9.3** and **Xray 26.4.25**.

| Endpoint                   | Method                  | Purpose                |
| ---------------------------- | ------------------------ | ------------------------ |
| `{base}/login`             | POST (form)             | Authenticate            |
| `{base}/panel/xray`        | POST                    | Read full Xray config    |
| `{base}/panel/xray/update` | POST (form-urlencoded)  | Save Xray config         |

VLESS outbound uses the **flat format** expected by 3x-ui (`address`, `port`, `id`, `flow`, `encryption`) — not Xray core `vnext`.

> **Xray 26.x note:** `allowInsecure` was removed from `tlsSettings`. The bot never includes this field.

---

## Security notes

- `config.py` contains secrets and is in `.gitignore` — never commit it.
- `slots_meta.json` is local runtime state (not code) and is also in `.gitignore`.
- Only user IDs in `ALLOWED_USERS` receive any response.
- The service runs as root. To use a dedicated user, adjust `User=` in the systemd unit.

---

## Project structure

```
bot.py              — Telegram bot (all command handlers + SlotKeys dedup logic)
panel_client.py     — 3x-ui panel API client (login, read, save)
converter.py        — subscription link parser (vless/vmess/trojan/ss)
ipcheck.py          — real connectivity check via Xray binary + exit IP; Cloudflare/self-IP rejection
cf_ranges.py         — hardcoded Cloudflare IP ranges + membership check
slots_meta.py        — JSON-backed per-slot country metadata (slots_meta.json)
healthcheck.py       — TCP latency check (fallback when XRAY_CHECK_ENABLED=False)
scraper.py            — fetch candidate configs + country codes from GitHub raw source
merger.py             — merge outbounds into Xray config dict
config.example.py    — template (copy to config.py and fill in values)
install.sh             — one-line installer (downloads Xray + systemd setup, detects SERVER_IP)
update.sh               — git pull + pip install + restart
uninstall.sh            — stop service, remove files
docs/                    — developer & AI handoff documentation
```

---

## For AI assistants

See [`docs/AI_HANDOFF.md`](https://github.com/ali934h/tg-xui-manager/blob/main/docs/AI_HANDOFF.md) for a full technical briefing including architecture, design decisions, known issues, and planned features.
