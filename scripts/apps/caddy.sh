#!/usr/bin/env bash
# WPCloud: caddy.sh - Cài đặt & Quản lý Caddy Web Server (Tự động HTTPS, HTTP/3, Zero-config)
set -euo pipefail

ACTION="${1:-install}"
VERSION="${2:-latest}"

SERVICE_NAME="caddy"

case "$ACTION" in
    install|switch)
        echo "[+] Bắt đầu cài đặt Caddy Web Server..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -y || true
            apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg || true
            
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null || true
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list 2>/dev/null || true
            
            apt-get update -y || true
            apt-get install -y caddy || true
        elif command -v dnf &>/dev/null; then
            dnf install -y 'dnf-command(copr)' 2>/dev/null || true
            dnf copr enable -y @caddy/caddy 2>/dev/null || true
            dnf install -y caddy 2>/dev/null || true
        elif command -v yum &>/dev/null; then
            yum install -y yum-plugin-copr 2>/dev/null || true
            yum copr enable -y @caddy/caddy 2>/dev/null || true
            yum install -y caddy 2>/dev/null || true
        fi

        # Nếu không cài được qua package manager, fallback binary chính thức
        if ! command -v caddy &>/dev/null; then
            echo "[+] Cài đặt Caddy Binary trực tiếp từ Github Releases..."
            curl -fsSL https://getcaddy.com | bash 2>/dev/null || true
        fi

        systemctl enable "$SERVICE_NAME" 2>/dev/null || true
        systemctl restart "$SERVICE_NAME" 2>/dev/null || true

        echo "[+] Cài đặt Caddy Web Server hoàn tất! Service: $SERVICE_NAME"
        ;;

    remove|uninstall)
        echo "[+] Đang gỡ cài đặt Caddy Web Server..."
        systemctl stop "$SERVICE_NAME" 2>/dev/null || true
        systemctl disable "$SERVICE_NAME" 2>/dev/null || true
        pkill -9 -f "caddy" 2>/dev/null || true

        if command -v apt-get &>/dev/null; then
            apt-get remove --purge -y caddy 2>/dev/null || true
            rm -f /etc/apt/sources.list.d/caddy-stable.list /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        elif command -v dnf &>/dev/null; then
            dnf remove -y caddy 2>/dev/null || true
        fi
        echo "[+] Gỡ cài đặt Caddy hoàn tất!"
        ;;

    start)
        systemctl start "$SERVICE_NAME" 2>/dev/null || true
        ;;

    stop)
        systemctl stop "$SERVICE_NAME" 2>/dev/null || true
        ;;

    restart)
        systemctl restart "$SERVICE_NAME" 2>/dev/null || true
        ;;

    reload)
        systemctl reload "$SERVICE_NAME" 2>/dev/null || systemctl restart "$SERVICE_NAME" 2>/dev/null || true
        ;;

    status)
        systemctl status "$SERVICE_NAME" --no-pager 2>/dev/null || true
        ;;

    *)
        echo "Cú pháp: caddy.sh <install|remove|start|stop|restart|reload|status> [version]"
        exit 1
        ;;
esac

exit 0
