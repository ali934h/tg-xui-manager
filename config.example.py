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

# ---------- Health Check ----------
MAX_LATENCY_MS = 350
TCP_CONNECT_TIMEOUT_SEC = 4

# ---------- Config Source ----------
RAW_SOURCE_URL = "https://raw.githubusercontent.com/roosterkid/openproxylist/main/V2RAY.txt"
CANDIDATES_TO_FETCH = 80

# ---------- Network ----------
REQUEST_TIMEOUT_SEC = 15
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
