# tg-xui-manager

Telegram bot to manage outbounds on a [3x-ui](https://github.com/MHSanaei/3x-ui) panel.
Replaces stale or failed outbounds with healthy free configs fetched from a public V2Ray list — all on demand, via Telegram commands. No scheduler, no cron, no automatic rotation.

> **Tested on:** 3x-ui v2.9.3 · Ubuntu 22.04 / 24.04 · Python 3.10+

---

## Features

| Command | Description |
|---|---|
| `/fill N` | Fill slots `out01`…`out0N` with healthy, non-duplicate candidates |
| `/replace 1,5,8` | Force-replace specific slot numbers (skips health check) |
| `/checkall` | TCP-health-check every managed slot; replace only the failed ones |
| `/status` | List all managed slots with address / port / protocol |
| `/setup` | Interactive wizard to change panel credentials |
| `/help` | Show all commands |

Unauthorised users are **silently ignored** — no reply that would reveal the bot exists.

---

## Requirements

- Ubuntu 22.04 / 24.04 VPS with root access
- 3x-ui panel already installed on the **same server**
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your numeric Telegram user ID (ask [@userinfobot](https://t.me/userinfobot))

> Xray binary is available automatically since 3x-ui installs it.

---

## Install

One-line install (run as root):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/ali934h/tg-xui-manager/main/install.sh)
```

The installer will:
1. Install `python3`, `python3-venv`, and `git`
2. Clone this repo to `/root/tg-xui-manager`
3. Create an isolated Python venv and install dependencies
4. Prompt for panel URL, username, password, bot token, and allowed user IDs
5. Install and start a `systemd` service that auto-starts on reboot

### Panel URL format

```
✅ https://example.com:2053/mywebbasepath
❌ https://example.com:2053/mywebbasepath/panel
❌ https://example.com:2053/mywebbasepath/panel/xray
```

The installer validates this and will re-prompt if `/panel` is included.

---

## Daily commands

```bash
journalctl -u tg-xui-manager -f          # live logs (Ctrl+C to stop)
systemctl status tg-xui-manager           # service status
systemctl restart tg-xui-manager          # restart
systemctl stop tg-xui-manager             # stop

bash /root/tg-xui-manager/update.sh       # pull latest code + restart
bash /root/tg-xui-manager/uninstall.sh    # remove everything
```

---

## Configuration

Credentials live in `/root/tg-xui-manager/config.py` (chmod 600, not tracked by git).
Use `/setup` in Telegram to change panel credentials at any time — the bot tests them before saving.

| Key | Default | Description |
|---|---|---|
| `PANEL_BASE_URL` | — | Panel URL (without `/panel`) |
| `PANEL_USERNAME` | — | Panel login username |
| `PANEL_PASSWORD` | — | Panel login password |
| `BOT_TOKEN` | — | Telegram bot token |
| `ALLOWED_USERS` | — | List of numeric Telegram user IDs |
| `MAX_LATENCY_MS` | `350` | TCP latency threshold for health checks |
| `TCP_CONNECT_TIMEOUT_SEC` | `4` | Connection timeout per check |
| `CANDIDATES_TO_FETCH` | `80` | How many candidates to pull from source per run |
| `SLOT_TAG_PREFIX` | `out` | Tag prefix — `out` → `out01`, `out02`, … |
| `SLOT_TAG_DIGITS` | `2` | Zero-padding width |

---

## How slots work

- The bot manages outbounds whose tags match `out01`…`out99` (configurable prefix/digits).
- `/fill N` fills slots 1 through N — creates them if missing, replaces if present.
- `/checkall` only checks slots that **currently exist** on the panel. Manually deleted slots are not detected — use `/fill N` or `/replace N` to recreate them.
- Duplicate detection is based on `address` string match within a single run.

---

## Config source

Free configs are fetched from
[`github.com/roosterkid/openproxylist`](https://github.com/roosterkid/openproxylist) — updated hourly.

Supported protocols: `vless`, `vmess`, `trojan`, `shadowsocks`.

---

## Panel API compatibility

Verified against **3x-ui v2.9.3**. The bot uses:

| Endpoint | Method | Purpose |
|---|---|---|
| `{base}/login` | POST (form) | Authenticate |
| `{base}/panel/xray` | POST | Read full Xray config |
| `{base}/panel/xray/update` | POST (form-urlencoded) | Save Xray config |

The VLESS outbound settings use the **flat format** expected by the 3x-ui panel (`address`, `port`, `id`, `flow`, `encryption`) — not the Xray core `vnext` format.

---

## Security notes

- `config.py` contains secrets and is in `.gitignore` — never commit it.
- Only user IDs in `ALLOWED_USERS` receive any response.
- The service runs as root. To use a dedicated user, adjust `User=` in the systemd unit.

---

## Project structure

```
bot.py              — Telegram bot (all command handlers)
panel_client.py     — 3x-ui panel API client (login, read, save)
converter.py        — subscription link parser (vless/vmess/trojan/ss)
healthcheck.py      — TCP latency health check
scraper.py          — fetch candidate configs from GitHub raw source
merger.py           — merge outbounds into Xray config dict
config.example.py   — template (copy to config.py and fill in values)
install.sh          — one-line installer (systemd)
update.sh           — git pull + pip install + restart
uninstall.sh        — stop service, remove files
docs/               — developer & AI handoff documentation
```

---

## For AI assistants

See [`docs/AI_HANDOFF.md`](docs/AI_HANDOFF.md) for a full technical briefing including architecture, design decisions, known issues, and planned features.
