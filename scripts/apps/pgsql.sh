#!/usr/bin/env bash
# WPCloud: pgsql.sh - Cài đặt PostgreSQL Database Server
set -euo pipefail

ACTION="${1:-install}"
VERSION="${2:-16}"

case "$ACTION" in
    install)
        echo "[+] Đang cài đặt PostgreSQL Database Server (v$VERSION)..."
        echo "[+] Đang cập nhật gói hệ điều hành AlmaLinux/Debian/Ubuntu trước khi cài đặt..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -y || true
            apt-get upgrade -y || true
            apt-get install -y postgresql postgresql-contrib
            systemctl enable postgresql || true
            systemctl restart postgresql || true
        elif command -v dnf &>/dev/null; then
            dnf upgrade -y || true
            dnf install -y postgresql-server postgresql-contrib
            postgresql-setup --initdb 2>/dev/null || true
            systemctl enable postgresql || true
            systemctl restart postgresql || true
        fi
        echo "[+] Cài đặt PostgreSQL Server thành công!"
        ;;
    remove)
        echo "[+] Đang gỡ bỏ PostgreSQL Server..."
        systemctl stop postgresql 2>/dev/null || true
        if command -v apt-get &>/dev/null; then
            apt-get remove -y postgresql postgresql-contrib 2>/dev/null || true
        fi
        echo "[+] Đã gỡ bỏ PostgreSQL Server thành công!"
        ;;
    *)
        exit 1
        ;;
esac
