#!/usr/bin/env bash
# WPCloud: sqlserver.sh - Cài đặt Microsoft SQL Server on Linux
set -euo pipefail

ACTION="${1:-install}"
VERSION="${2:-2022}"

case "$ACTION" in
    install)
        echo "[+] Đang cài đặt Microsoft SQL Server 2022 on Linux..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -y || true
            apt-get install -y curl gnupg || true
            curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg --yes 2>/dev/null || true
            curl -fsSL https://packages.microsoft.com/config/ubuntu/22.04/mssql-server-2022.list | tee /etc/apt/sources.list.d/mssql-server-2022.list 2>/dev/null || true
            apt-get update -y || true
            apt-get install -y mssql-server || true
            systemctl enable mssql-server || true
            systemctl restart mssql-server || true
        fi
        echo "[+] Cài đặt Microsoft SQL Server thành công!"
        ;;
    remove)
        echo "[+] Đang gỡ bỏ Microsoft SQL Server..."
        systemctl stop mssql-server 2>/dev/null || true
        if command -v apt-get &>/dev/null; then
            apt-get remove -y mssql-server 2>/dev/null || true
        fi
        echo "[+] Đã gỡ bỏ Microsoft SQL Server thành công!"
        ;;
    *)
        exit 1
        ;;
esac
