# tg-xui-manager

Telegram bot to manage outbounds on a [3x-ui](https://github.com/MHSanaei/3x-ui) panel.  
Replaces stale or failed outbounds with healthy free configs fetched from a public V2Ray list — all on demand, via Telegram commands. No scheduler, no cron, no automatic rotation.

---

## Features

- `/fill N` — fill slots `out01`…`out0N` with healthy, non-duplicate candidates
- `/replace 1,5,8` — replace specific slots directly (no health check, your call)
- `/checkall` — TCP-health-check every managed slot and replace only the failed ones
- `/status` — list all managed slots (address / port / protocol)
- `/setup` — interactive wizard to change panel credentials without touching the server

Unauthorised users are silently ignored (no reply that would reveal the bot exists).

---

## Requirements

- Ubuntu 22.04 / 24.04 VPS with root access
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your numeric Telegram user ID (ask [@userinfobot](https://t.me/userinfobot))

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
4. Prompt you for panel URL, username, password, bot token, and allowed user IDs
5. Install and start a `systemd` service (`tg-xui-manager`) that auto-starts on reboot

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
Use `/setup` in the Telegram chat to change panel credentials at any time — the bot will test them before saving.

Key settings you can tune in `config.py`:

| Key | Default | Description |
|---|---|---|
| `MAX_LATENCY_MS` | `350` | TCP latency threshold for health checks |
| `TCP_CONNECT_TIMEOUT_SEC` | `4` | Connection timeout per check |
| `CANDIDATES_TO_FETCH` | `80` | How many candidates to pull from source per run |
| `SLOT_TAG_PREFIX` | `out` | Tag prefix (out01, out02, …) |
| `SLOT_TAG_DIGITS` | `2` | Zero-padding width |

---

## Config source

Free configs are fetched from  
[`github.com/roosterkid/openproxylist`](https://github.com/roosterkid/openproxylist) — updated hourly.

---

## Security notes

- `config.py` contains secrets and is in `.gitignore` — never commit it.
- Only `chat_id`s listed in `ALLOWED_USERS` receive any response.
- The systemd service runs as root inside `/root/tg-xui-manager`. If you prefer a dedicated user, adjust `User=` in the service unit and clone to a different path.

---

## Project structure

```
bot.py              — Telegram bot (command handlers)
panel_client.py     — 3x-ui panel API client
converter.py        — vless/vmess/trojan/ss link parser
healthcheck.py      — TCP latency check
scraper.py          — fetch candidate configs from GitHub raw
merger.py           — merge outbounds into Xray config
config.example.py   — template (copy to config.py and fill in)
install.sh          — one-line installer
update.sh           — git pull + restart
uninstall.sh        — remove service + files
```
