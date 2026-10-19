#!/usr/bin/env bash
# WPCloud: nginx.sh - Cài đặt Nginx Web Server / OpenResty (hỗ trợ Debian, Ubuntu, AlmaLinux, RHEL, Rocky)
set -euo pipefail

ACTION="${1:-install}"
VERSION="${2:-1.26.x}"

case "$ACTION" in
    install)
        echo "[+] Đang cài đặt Nginx / OpenResty ($VERSION)..."
        if [ "$VERSION" = "openresty" ]; then
            if command -v apt-get &>/dev/null; then
                export DEBIAN_FRONTEND=noninteractive
                apt-get update -y || true
                apt-get install -y --no-install-recommends wget gnupg ca-certificates lsb-release || true
                wget -O - https://openresty.org/package/pubkey.gpg | gpg --dearmor -o /etc/apt/trusted.gpg.d/openresty.gpg 2>/dev/null || true
                CODENAME=$(lsb_release -sc 2>/dev/null || echo "jammy")
                echo "deb http://openresty.org/package/ubuntu $CODENAME main" > /etc/apt/sources.list.d/openresty.list 2>/dev/null || true
                apt-get update -y || true
                apt-get install -y openresty || apt-get install -y nginx
            elif command -v dnf &>/dev/null; then
                dnf install -y yum-utils || true
                yum-config-manager --add-repo https://openresty.org/package/centos/openresty.repo 2>/dev/null || true
                dnf install -y openresty || dnf install -y nginx
            fi
            # Tạo symlink chuẩn /usr/sbin/nginx nếu dùng openresty
            if [ -x "/usr/local/openresty/nginx/sbin/nginx" ]; then
                ln -sf /usr/local/openresty/nginx/sbin/nginx /usr/sbin/nginx 2>/dev/null || true
            fi
            systemctl enable openresty 2>/dev/null || systemctl enable nginx 2>/dev/null || true
            systemctl restart openresty 2>/dev/null || systemctl restart nginx 2>/dev/null || true
        else
            if command -v apt-get &>/dev/null; then
                export DEBIAN_FRONTEND=noninteractive
                apt-get update -y || true
                apt-get install -y nginx
            elif command -v dnf &>/dev/null; then
                dnf install -y nginx
            fi
            systemctl enable nginx 2>/dev/null || true
            systemctl restart nginx 2>/dev/null || true
        fi
        echo "[+] Cài đặt Nginx ($VERSION) hoàn tất thành công!"
        ;;

    remove)
        echo "[+] Đang gỡ bỏ Nginx / OpenResty..."
        systemctl stop openresty 2>/dev/null || true
        systemctl stop nginx 2>/dev/null || true
        systemctl disable openresty 2>/dev/null || true
        systemctl disable nginx 2>/dev/null || true
        if command -v apt-get &>/dev/null; then
            apt-get remove -y nginx openresty 2>/dev/null || true
        elif command -v dnf &>/dev/null; then
            dnf remove -y nginx openresty 2>/dev/null || true
        fi
        echo "[+] Đã gỡ bỏ Nginx thành công!"
        ;;

    *)
        echo "[-] Lệnh không hợp lệ"
        exit 1
        ;;
esac
