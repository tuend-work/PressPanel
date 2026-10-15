#!/usr/bin/env bash
# PressPanel: mariadb.sh - Cài đặt MariaDB Database Server
set -euo pipefail

ACTION="${1:-install}"

case "$ACTION" in
    install)
        echo "[+] Đang cài đặt MariaDB Server..."
        if command -v apt-get &>/dev/null; then
            DEBIAN_FRONTEND=noninteractive apt-get update
            DEBIAN_FRONTEND=noninteractive apt-get install -y mariadb-server mariadb-client
            systemctl enable mariadb
            systemctl start mariadb
        fi
        echo "[+] Cài đặt MariaDB thành công!"
        ;;
    remove)
        echo "[+] Gỡ cài đặt MariaDB..."
        systemctl stop mariadb 2>/dev/null || true
        apt-get remove -y mariadb-server mariadb-client 2>/dev/null || true
        ;;
    *)
        echo "Lệnh không hợp lệ"
        exit 1
        ;;
esac
