#!/usr/bin/env bash
# PressPanel: ols.sh - Cài đặt OpenLiteSpeed Web Server
set -euo pipefail

ACTION="${1:-install}"

case "$ACTION" in
    install)
        echo "[+] Đang cài đặt OpenLiteSpeed Web Server..."
        if command -v apt-get &>/dev/null; then
            wget -O - https://repo.litespeed.sh | bash || true
            DEBIAN_FRONTEND=noninteractive apt-get install -y openlitespeed lsphp82 lsphp82-common lsphp82-mysql lsphp82-opcache lsphp82-curl || true
            systemctl enable lshttpd || systemctl enable lsws || true
            systemctl start lshttpd || systemctl start lsws || true
        fi
        echo "[+] Cài đặt OpenLiteSpeed thành công!"
        ;;
    remove)
        echo "[+] Đang gỡ cài đặt OpenLiteSpeed..."
        systemctl stop lshttpd 2>/dev/null || true
        apt-get remove -y openlitespeed 2>/dev/null || true
        ;;
    *)
        echo "Lệnh không hợp lệ"
        exit 1
        ;;
esac
