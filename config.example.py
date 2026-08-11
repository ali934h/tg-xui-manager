# Copy this file to config.py and fill in your values.
# NEVER commit config.py to git — it is listed in .gitignore.

# ---------- 3x-ui Panel ----------
# URL up to (but not including) /panel/xray
# Example: https://example.com:2053/mypath
PANEL_BASE_URL = "https://your-panel-url:port/webbasepath"

PANEL_USERNAME = "your_username"
PANEL_PASSWORD = "your_password"

# Set to False if your panel uses a self-signed SSL certificate
VERIFY_SSL = True

# ---------- Telegram Bot ----------
BOT_TOKEN = "123456:ABC-your-bot-token"

# List of numeric Telegram chat IDs allowed to use the bot.
# Get yours from @userinfobot.
ALLOWED_USERS = [123456789]

# ---------- Outbound Slot Tags ----------
SLOT_TAG_PREFIX = "out"    # -> out01, out02, ...
SLOT_TAG_DIGITS = 2

# ---------- Candidate Check ----------
# XRAY_CHECK_ENABLED: use the local Xray binary to make a real HTTP request
# through each candidate and verify connectivity + get the real exit IP.
# Requires 3x-ui to be installed on the same server (provides the binary).
# Set to False to fall back to TCP latency check only (faster but less accurate).
XRAY_CHECK_ENABLED = True

# Path to Xray binary (installed by 3x-ui)
XRAY_BINARY = "/usr/local/x-ui/bin/xray-linux-amd64"

# How many candidates to test in parallel.
# Higher = faster, but uses more RAM and CPU.
# Recommended: 3-5 for servers with 1GB RAM, up to 10 for 2GB+.
XRAY_WORKERS = 5

# Seconds to wait for Xray process to start before sending traffic
XRAY_STARTUP_WAIT_SEC = 2.0

# HTTP request timeout (seconds) when checking through SOCKS5
XRAY_REQUEST_TIMEOUT_SEC = 8

# ---------- Post-save Xray restart ----------
# save_xray_config() only hot-reloads the running Xray process. That does
# NOT clear stale connection pools, DNS cache, or mux session state built up
# over long uptime -- which can make external health checkers (e.g. a
# 9Router proxy pool test) intermittently report failures (ECONNRESET /
# timeouts) on outbounds that are actually fine. When enabled, the bot
# forces a full Xray-core process restart after every config save.
RESTART_XRAY_AFTER_SAVE = True

# Seconds to wait after triggering a restart before the command finishes,
# so Xray is fully back up before you (or an external checker) test again.
XRAY_RESTART_WAIT_SEC = 3

# ---------- Health Check (TCP fallback) ----------
# Used only when XRAY_CHECK_ENABLED = False
MAX_LATENCY_MS = 350
TCP_CONNECT_TIMEOUT_SEC = 4

# ---------- Config Source ----------
RAW_SOURCE_URL = "https://raw.githubusercontent.com/roosterkid/openproxylist/main/V2RAY.txt"
CANDIDATES_TO_FETCH = 80

# ---------- Network ----------
REQUEST_TIMEOUT_SEC = 15
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
