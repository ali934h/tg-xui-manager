#!/usr/bin/env bash
# Completely remove tg-xui-manager from this server.
set -e

INSTALL_DIR="/root/tg-xui-manager"
SERVICE_NAME="tg-xui-manager"

read -rp "This will stop the bot and delete ALL files in $INSTALL_DIR. Continue? [y/N] " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Aborted."
    exit 0
fi

echo "Stopping and disabling service…"
systemctl stop "$SERVICE_NAME"  2>/dev/null || true
systemctl disable "$SERVICE_NAME" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload

echo "Removing files…"
rm -rf "$INSTALL_DIR"

echo "Done. tg-xui-manager has been removed."
