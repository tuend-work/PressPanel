#!/usr/bin/env bash
# PressPanel: redis.sh - Cài đặt Redis Object Cache
set -euo pipefail

ACTION="${1:-install}"

case "$ACTION" in
    install)
        echo "[+] Đang cài đặt Redis Server..."
        DEBIAN_FRONTEND=noninteractive apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y redis-server
        systemctl enable redis-server || systemctl enable redis || true
        systemctl start redis-server || systemctl start redis || true
        echo "[+] Cài đặt Redis Server thành công!"
        ;;
    remove)
        systemctl stop redis-server 2>/dev/null || true
        apt-get remove -y redis-server 2>/dev/null || true
        ;;
    *)
        exit 1
        ;;
esac
