# tg-xui-manager

Telegram bot to manage outbounds on a [3x-ui](https://github.com/MHSanaei/3x-ui) panel.
Replaces stale or failed outbounds with healthy free configs fetched from a public V2Ray list — all on demand, via Telegram commands. No scheduler, no cron, no automatic rotation.

> **Tested on:** 3x-ui v2.9.3 · Xray 26.4.25 · Ubuntu 22.04 / 24.04 · Python 3.10+

---

## Features

| Command | Description |
|---|---|
| `/fill N` | Fill slots `out01`…`out0N` with verified, non-duplicate candidates |
| `/replace 1,5,8` | Force-replace specific slot numbers |
| `/checkall` | Check every managed slot; replace only the failed ones |
| `/status` | List all managed slots with address / port / protocol |
| `/setup` | Interactive wizard to change panel credentials |
| `/help` | Show all commands |

Unauthorised users are **silently ignored** — no reply that would reveal the bot exists.

---

## How candidate verification works

Each candidate config goes through a **real connectivity test** before being accepted:

1. The local Xray binary (installed by 3x-ui at `/usr/local/x-ui/bin/xray-linux-amd64`) is started with a temporary SOCKS5 inbound.
2. An HTTP request is made through that SOCKS5 proxy to `https://api.ipify.org`.
3. The returned IP is the **real exit IP** of the candidate server — even for Cloudflare-fronted configs, this reveals the true origin server IP.
4. If the same exit IP was already seen (in existing slots or earlier in this run), the candidate is skipped.

This means:
- Only candidates that **actually work** are accepted
- No two slots will share the same origin server IP
- The check is equivalent to what the 3x-ui panel's own test button does

Fallback: set `XRAY_CHECK_ENABLED = False` in `config.py` to revert to a simple TCP latency check.

---

## Requirements

- Ubuntu 22.04 / 24.04 VPS with root access
- **3x-ui panel already installed on the same server** (provides the Xray binary)
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
| `XRAY_CHECK_ENABLED` | `True` | Use Xray binary for real connectivity check |
| `XRAY_BINARY` | `/usr/local/x-ui/bin/xray-linux-amd64` | Path to Xray binary |
| `XRAY_STARTUP_WAIT_SEC` | `2.0` | Seconds to wait for Xray to start |
| `XRAY_REQUEST_TIMEOUT_SEC` | `8` | HTTP request timeout through SOCKS5 |
| `CANDIDATES_TO_FETCH` | `80` | How many candidates to pull from source per run |
| `SLOT_TAG_PREFIX` | `out` | Tag prefix — `out` → `out01`, `out02`, … |
| `SLOT_TAG_DIGITS` | `2` | Zero-padding width |
| `MAX_LATENCY_MS` | `350` | TCP latency threshold (fallback mode only) |
| `TCP_CONNECT_TIMEOUT_SEC` | `4` | TCP timeout (fallback mode only) |

---

## How slots work

- The bot manages outbounds whose tags match `out01`…`out99` (configurable prefix/digits).
- `/fill N` fills slots 1 through N — creates them if missing, replaces if present.
- `/checkall` only checks slots that **currently exist** on the panel. Manually deleted slots are not detected — use `/fill N` or `/replace N` to recreate them.
- Duplicate detection is based on **real exit IP** (Xray mode) or address string (fallback mode).

---

## Performance

With `XRAY_CHECK_ENABLED = True`, each candidate takes ~3–10 seconds to test.
Typical times: `/fill 5` ≈ 30–60s, `/fill 10` ≈ 1–2 min, `/checkall` (10 slots) ≈ 2–3 min.

If speed matters more than accuracy, set `XRAY_CHECK_ENABLED = False` for instant TCP-only checks.

---

## Config source

Free configs are fetched from
[`github.com/roosterkid/openproxylist`](https://github.com/roosterkid/openproxylist) — updated hourly.

Supported protocols: `vless`, `vmess`, `trojan`, `shadowsocks`.

---

## Panel API compatibility

Verified against **3x-ui v2.9.3** and **Xray 26.4.25**. The bot uses:

| Endpoint | Method | Purpose |
|---|---|---|
| `{base}/login` | POST (form) | Authenticate |
| `{base}/panel/xray` | POST | Read full Xray config |
| `{base}/panel/xray/update` | POST (form-urlencoded) | Save Xray config |

The VLESS outbound settings use the **flat format** expected by the 3x-ui panel (`address`, `port`, `id`, `flow`, `encryption`) — not the Xray core `vnext` format.

> **Xray 26.x note:** `allowInsecure` was removed from `tlsSettings` in Xray 26.x. The bot never includes this field in generated configs.

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
ipcheck.py          — real connectivity check via Xray binary + exit IP dedup
healthcheck.py      — TCP latency check (fallback when XRAY_CHECK_ENABLED=False)
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
