#!/usr/bin/env bash
# tg-xui-manager installer
# Run as root: bash <(curl -fsSL https://raw.githubusercontent.com/ali934h/tg-xui-manager/main/install.sh)
set -e

REPO="https://github.com/ali934h/tg-xui-manager.git"
INSTALL_DIR="/root/tg-xui-manager"
SERVICE_NAME="tg-xui-manager"
PYTHON="python3"

echo "=== tg-xui-manager installer ==="

# ---------- dependencies ----------
echo "[1/5] Installing system dependencies…"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl

# ---------- clone / update ----------
echo "[2/5] Cloning repository…"
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  Directory exists — pulling latest…"
    git -C "$INSTALL_DIR" pull --ff-only
else
    git clone "$REPO" "$INSTALL_DIR"
fi

# ---------- venv ----------
echo "[3/5] Setting up Python virtual environment…"
$PYTHON -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

# ---------- config ----------
echo "[4/5] Configuration…"
CONFIG_FILE="$INSTALL_DIR/config.py"

if [ ! -f "$CONFIG_FILE" ]; then
    cp "$INSTALL_DIR/config.example.py" "$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
fi

read -rp "  Panel base URL (e.g. https://host:port/path): " PANEL_URL
read -rp "  Panel username: " PANEL_USER
read -rsp "  Panel password: " PANEL_PASS
echo ""
read -rp "  Bot token (from @BotFather): " BOT_TOKEN
read -rp "  Allowed user IDs (comma-separated, e.g. 123456789,987654321): " ALLOWED_RAW

# Build a Python list from the comma-separated IDs
ALLOWED_LIST="[$(echo "$ALLOWED_RAW" | tr ',' '\n' | sed 's/[^0-9]//g' | tr '\n' ',' | sed 's/,$//')]"

sed -i "s|PANEL_BASE_URL = .*|PANEL_BASE_URL = \"$PANEL_URL\"|" "$CONFIG_FILE"
sed -i "s|PANEL_USERNAME = .*|PANEL_USERNAME = \"$PANEL_USER\"|" "$CONFIG_FILE"
sed -i "s|PANEL_PASSWORD = .*|PANEL_PASSWORD = \"$PANEL_PASS\"|" "$CONFIG_FILE"
sed -i "s|BOT_TOKEN = .*|BOT_TOKEN = \"$BOT_TOKEN\"|" "$CONFIG_FILE"
sed -i "s|ALLOWED_USERS = .*|ALLOWED_USERS = $ALLOWED_LIST|" "$CONFIG_FILE"

# ---------- systemd service ----------
echo "[5/5] Installing systemd service…"

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
echo "Useful commands:"
echo "  journalctl -u $SERVICE_NAME -f       # live logs"
echo "  systemctl status $SERVICE_NAME        # service status"
echo "  systemctl restart $SERVICE_NAME       # restart"
echo "  systemctl stop $SERVICE_NAME          # stop"
echo "  bash $INSTALL_DIR/update.sh           # update to latest version"
echo "  bash $INSTALL_DIR/uninstall.sh        # remove everything"
