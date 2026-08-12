#!/usr/bin/env bash
# tg-xui-manager installer
# Run as root: bash <(curl -fsSL https://raw.githubusercontent.com/ali934h/tg-xui-manager/main/install.sh)

set -e

REPO="https://github.com/ali934h/tg-xui-manager.git"
INSTALL_DIR="/root/tg-xui-manager"
SERVICE_NAME="tg-xui-manager"
PYTHON="python3"

# Preferred Xray version (tested and confirmed working with this project).
# If this release is unavailable on GitHub, the installer falls back to latest.
XRAY_PREFERRED_VERSION="v26.4.25"
XRAY_INSTALL_DIR="/root/tg-xui-manager-xray"
XRAY_BINARY="$XRAY_INSTALL_DIR/xray"

echo "=== tg-xui-manager installer ==="

# ---------- system dependencies ----------
echo "[1/6] Installing system dependencies…"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl unzip

# ---------- Xray binary ----------
echo "[2/6] Downloading Xray binary…"
mkdir -p "$XRAY_INSTALL_DIR"

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64) XRAY_ARCH="64" ;;
    aarch64) XRAY_ARCH="arm64-v8a" ;;
    armv7l) XRAY_ARCH="arm32-v7a" ;;
    *) XRAY_ARCH="64" ;;
esac

PREFERRED_URL="https://github.com/XTLS/Xray-core/releases/download/${XRAY_PREFERRED_VERSION}/Xray-linux-${XRAY_ARCH}.zip"
LATEST_URL="https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-${XRAY_ARCH}.zip"

echo "  Trying preferred version ${XRAY_PREFERRED_VERSION}…"
if curl -fsSL --head "$PREFERRED_URL" -o /dev/null 2>/dev/null; then
    DOWNLOAD_URL="$PREFERRED_URL"
    echo "  ✅ Preferred version available."
else
    echo "  ⚠️  Preferred version not available — falling back to latest."
    DOWNLOAD_URL="$LATEST_URL"
fi

TMP_ZIP="$(mktemp /tmp/xray-XXXXXX.zip)"
curl -fsSL "$DOWNLOAD_URL" -o "$TMP_ZIP"
unzip -o -q "$TMP_ZIP" -d "$XRAY_INSTALL_DIR"
rm -f "$TMP_ZIP"
chmod +x "$XRAY_BINARY"

XRAY_VER="$("$XRAY_BINARY" version 2>/dev/null | head -1 || echo 'unknown')"
echo "  Xray installed: $XRAY_VER"

# ---------- clone / update ----------
echo "[3/6] Cloning repository…"
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  Directory exists — pulling latest…"
    git -C "$INSTALL_DIR" pull --ff-only
else
    git clone "$REPO" "$INSTALL_DIR"
fi

# ---------- venv ----------
echo "[4/6] Setting up Python virtual environment…"
$PYTHON -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

# ---------- config ----------
echo "[5/6] Configuration…"
CONFIG_FILE="$INSTALL_DIR/config.py"
if [ ! -f "$CONFIG_FILE" ]; then
    cp "$INSTALL_DIR/config.example.py" "$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
fi

echo ""
echo "  Panel base URL — the URL up to (but NOT including) /panel"
echo "  Example: https://example.com:2053/mywebbasepath"
echo "  If your panel login page is https://example.com:2053/mywebbasepath/panel/xray"
echo "  then enter only: https://example.com:2053/mywebbasepath"
echo "  Note: the panel can be on a different server — just use its full URL."
echo ""

while true; do
    read -rp "  Panel base URL: " PANEL_URL
    if echo "$PANEL_URL" | grep -q "/panel"; then
        echo "  ⚠️  URL should not include /panel — please re-enter."
    else
        break
    fi
done

read -rp "  Panel username: " PANEL_USER
read -rsp "  Panel password: " PANEL_PASS
echo ""
read -rp "  Bot token (from @BotFather): " BOT_TOKEN
read -rp "  Allowed user IDs (comma-separated, e.g. 123456789,987654321): " ALLOWED_RAW

# Build a Python list from the comma-separated IDs
ALLOWED_LIST="[$(echo "$ALLOWED_RAW" | tr ',' '\n' | sed 's/[^0-9]//g' | tr '\n' ',' | sed 's/,$//')]"

echo ""
echo "  Detecting this server's public IP…"
echo "  (used to reject candidates that would just loop back through this box)"
DETECTED_IP="$(curl -fsSL --max-time 5 https://api.ipify.org || true)"
if [ -n "$DETECTED_IP" ]; then
    read -rp "  This server's public IP [$DETECTED_IP]: " SERVER_IP
    SERVER_IP="${SERVER_IP:-$DETECTED_IP}"
else
    echo "  ⚠️  Could not auto-detect public IP."
    read -rp "  Enter this server's public IP (leave blank to skip this check): " SERVER_IP
fi

sed -i "s|PANEL_BASE_URL = .*|PANEL_BASE_URL = \"$PANEL_URL\"|" "$CONFIG_FILE"
sed -i "s|PANEL_USERNAME = .*|PANEL_USERNAME = \"$PANEL_USER\"|" "$CONFIG_FILE"
sed -i "s|PANEL_PASSWORD = .*|PANEL_PASSWORD = \"$PANEL_PASS\"|" "$CONFIG_FILE"
sed -i "s|BOT_TOKEN = .*|BOT_TOKEN = \"$BOT_TOKEN\"|" "$CONFIG_FILE"
sed -i "s|ALLOWED_USERS = .*|ALLOWED_USERS = $ALLOWED_LIST|" "$CONFIG_FILE"
sed -i "s|XRAY_BINARY = .*|XRAY_BINARY = \"$XRAY_BINARY\"|" "$CONFIG_FILE"

# SERVER_IP — add the key if this is an older config.py that predates it
grep -q "SERVER_IP" "$CONFIG_FILE" || cat >> "$CONFIG_FILE" << 'EOFCFG'

# ---------- Server Identity ----------
SERVER_IP = ""
EOFCFG
sed -i "s|SERVER_IP = .*|SERVER_IP = \"$SERVER_IP\"|" "$CONFIG_FILE"

# Append extra settings if not present
grep -q "XRAY_WORKERS" "$CONFIG_FILE" || cat >> "$CONFIG_FILE" << 'EOFCFG'

# ---------- Parallel Workers ----------
XRAY_WORKERS = 5
EOFCFG

grep -q "XRAY_CHECK_ENABLED" "$CONFIG_FILE" || cat >> "$CONFIG_FILE" << 'EOFCFG'

# ---------- Xray Real Connectivity Check ----------
XRAY_CHECK_ENABLED = True
XRAY_STARTUP_WAIT_SEC = 2.0
XRAY_REQUEST_TIMEOUT_SEC = 8
EOFCFG

# ---------- systemd service ----------
echo "[6/6] Installing systemd service…"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=tg-xui-manager Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo ""
echo "=== Installation complete ==="
echo ""
echo "  Xray binary : $XRAY_BINARY"
echo "  Install dir : $INSTALL_DIR"
echo "  Panel URL   : $PANEL_URL"
echo "  Server IP   : ${SERVER_IP:-<not set>}"
echo ""
echo "Useful commands:"
echo "  journalctl -u $SERVICE_NAME -f      # live logs"
echo "  systemctl status $SERVICE_NAME       # service status"
echo "  systemctl restart $SERVICE_NAME      # restart"
echo "  systemctl stop $SERVICE_NAME         # stop"
echo "  bash $INSTALL_DIR/update.sh          # update to latest version"
echo "  bash $INSTALL_DIR/uninstall.sh       # remove everything"
