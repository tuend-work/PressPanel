#!/usr/bin/env bash
# PressPanel: vhost_manager.sh - Quản lý Vhost OpenLiteSpeed / Nginx & Let's Encrypt

set -euo pipefail

ACTION="${1:-}"
USERNAME="${2:-}"
DOMAIN="${3:-}"
WEBSERVER="${4:-ols}"
PHP_VER="${5:-8.2}"

case "$ACTION" in
    add)
        echo "[+] Tao vhost cho $DOMAIN ($WEBSERVER, PHP $PHP_VER) cua user $USERNAME..."
        mkdir -p "/home/$USERNAME/httpdocs/$DOMAIN"
        chown -R "$USERNAME:$USERNAME" "/home/$USERNAME/httpdocs/$DOMAIN"
        chmod 755 "/home/$USERNAME/httpdocs/$DOMAIN"
        ;;
    remove)
        echo "[+] Xoa vhost $DOMAIN cua user $USERNAME..."
        ;;
    set-php)
        echo "[+] Chuyen PHP sang $PHP_VER cho $DOMAIN..."
        ;;
    ssl)
        echo "[+] Cap phat SSL Let's Encrypt cho $DOMAIN..."
        certbot certonly --webroot -w "/home/$USERNAME/httpdocs/$DOMAIN" -d "$DOMAIN" --non-interactive --agree-tos -m "admin@$DOMAIN" || true
        ;;
    *)
        echo "[-] Lenh khong hop le: $ACTION"
        exit 1
        ;;
esac

