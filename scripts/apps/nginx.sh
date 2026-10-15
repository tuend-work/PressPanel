#!/usr/bin/env bash
# PressPanel: nginx.sh - Cài đặt Nginx Web Server
set -euo pipefail

ACTION="${1:-install}"

case "$ACTION" in
    install)
        echo "[+] Đang cài đặt Nginx Web Server..."
        DEBIAN_FRONTEND=noninteractive apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y nginx
        systemctl enable nginx
        systemctl start nginx
        echo "[+] Cài đặt Nginx thành công!"
        ;;
    remove)
        systemctl stop nginx 2>/dev/null || true
        apt-get remove -y nginx 2>/dev/null || true
        ;;
    *)
        exit 1
        ;;
esac
