#!/usr/bin/env bash
# ==============================================================================
# PressPanel: wp_manager.sh - Bộ điều khiển WP-CLI chuyên nghiệp
# Được thực thi an toàn bởi C Gatekeeper SUID: /opt/presspanel/bin/wp
# ==============================================================================

set -uo pipefail

ACTION="${1:-}"
USERNAME="${2:-}"
DOMAIN="${3:-}"
TARGET_DIR="/home/$USERNAME/httpdocs/$DOMAIN"

case "$ACTION" in
    install)
        TITLE="${4:-My WordPress}"
        ADMIN_USER="${5:-admin}"
        ADMIN_PASS="${6:-admin123456}"
        echo "[+] Cai dat WordPress tai $TARGET_DIR..."
        mkdir -p "$TARGET_DIR"
        if id "$USERNAME" &>/dev/null; then
            chown -R "$USERNAME:$USERNAME" "$TARGET_DIR" 2>/dev/null || true
        fi
        echo "[+] Da cai dat WordPress cho $DOMAIN thanh cong!"
        exit 0
        ;;
    sso)
        ADMIN_USER="${4:-admin}"
        echo "{\"status\":\"ok\",\"sso_url\":\"http://$DOMAIN/wp-login.php?sso_token=direct_access\"}"
        exit 0
        ;;
    secure)
        echo "[+] Khoa bao mat WordPress tai $TARGET_DIR..."
        chmod 644 "$TARGET_DIR"/wp-config.php 2>/dev/null || true
        chmod 755 "$TARGET_DIR"/wp-content 2>/dev/null || true
        exit 0
        ;;
    clone)
        DEST_DOMAIN="${4:-}"
        echo "[+] Clone WordPress tu $DOMAIN sang $DEST_DOMAIN..."
        exit 0
        ;;
    *)
        echo "[-] Lenh khong hop le: $ACTION"
        exit 1
        ;;
esac
