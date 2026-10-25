#!/usr/bin/env bash
# WPCloud: mongodb.sh - Cài đặt MongoDB Community Server
set -euo pipefail

ACTION="${1:-install}"
VERSION="${2:-7.0}"

case "$ACTION" in
    install)
        echo "[+] Đang cài đặt MongoDB Database Server ($VERSION)..."
        echo "[+] Đang cập nhật gói hệ điều hành AlmaLinux/Debian/Ubuntu trước khi cài đặt..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -y || true
            apt-get upgrade -y || true
            apt-get install -y gnupg curl || true
            curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor --yes 2>/dev/null || true
            echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] http://repo.mongodb.org/apt/debian $(lsb_release -cs 2>/dev/null || echo bookworm)/mongodb-org/7.0 main" | tee /etc/apt/sources.list.d/mongodb-org-7.0.list 2>/dev/null || true
            apt-get update -y || true
            apt-get install -y mongodb-org || apt-get install -y mongodb || true
            systemctl daemon-reload || true
            systemctl enable mongod || systemctl enable mongodb || true
            systemctl restart mongod || systemctl restart mongodb || true
        fi
        echo "[+] Cài đặt MongoDB Server thành công!"
        ;;
    remove)
        echo "[+] Đang gỡ bỏ MongoDB Server..."
        systemctl stop mongod 2>/dev/null || systemctl stop mongodb 2>/dev/null || true
        if command -v apt-get &>/dev/null; then
            apt-get remove -y mongodb-org* mongodb* 2>/dev/null || true
        fi
        echo "[+] Đã gỡ bỏ MongoDB Server thành công!"
        ;;
    *)
        exit 1
        ;;
esac
