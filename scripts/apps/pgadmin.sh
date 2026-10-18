#!/usr/bin/env bash
# WPCloud: pgadmin.sh - Cài đặt pgAdmin 4 Web GUI
set -euo pipefail

ACTION="${1:-install}"

case "$ACTION" in
    install)
        echo "[+] Đang cài đặt pgAdmin 4 Web Interface..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -y || true
            apt-get install -y curl gnupg || true
            curl -fsS https://www.pgadmin.org/static/packages_pgadmin_org.pub | gpg --dearmor -o /usr/share/keyrings/packages-pgadmin-org.gpg --yes 2>/dev/null || true
            echo "deb [signed-by=/usr/share/keyrings/packages-pgadmin-org.gpg] https://ftp.postgresql.org/pub/pgadmin/pgadmin4/apt/$(lsb_release -cs 2>/dev/null || echo bookworm) pgadmin4 main" | tee /etc/apt/sources.list.d/pgadmin4.list 2>/dev/null || true
            apt-get update -y || true
            apt-get install -y pgadmin4-web || true
        fi
        echo "[+] Cài đặt pgAdmin 4 thành công!"
        ;;
    remove)
        echo "[+] Đang gỡ bỏ pgAdmin 4..."
        if command -v apt-get &>/dev/null; then
            apt-get remove -y pgadmin4-web 2>/dev/null || true
        fi
        echo "[+] Đã gỡ bỏ pgAdmin 4 thành công!"
        ;;
    *)
        exit 1
        ;;
esac
