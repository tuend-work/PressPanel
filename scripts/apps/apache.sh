#!/usr/bin/env bash
# WPCloud: apache.sh - Cài đặt & Quản lý Apache HTTP Server (Debian/Ubuntu/AlmaLinux/RHEL)
set -euo pipefail

ACTION="${1:-install}"
VERSION="${2:-2.4}"

# Xác định tên service theo HĐH
SERVICE_NAME="apache2"
if [ -f "/etc/redhat-release" ] || command -v dnf &>/dev/null; then
    SERVICE_NAME="httpd"
fi

case "$ACTION" in
    install|switch)
        echo "[+] Bắt đầu cài đặt Apache Web Server (Phiên bản $VERSION)..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -y || true
            apt-get install -y apache2 apache2-utils libapache2-mod-fcgid ssl-cert || true

            # Kích hoạt các module tối quan trọng cho WordPress & PHP-FPM
            echo "[+] Kích hoạt các Apache modules cần thiết (rewrite, proxy_fcgi, ssl, http2)..."
            a2enmod rewrite 2>/dev/null || true
            a2enmod proxy 2>/dev/null || true
            a2enmod proxy_fcgi 2>/dev/null || true
            a2enmod proxy_http 2>/dev/null || true
            a2enmod ssl 2>/dev/null || true
            a2enmod headers 2>/dev/null || true
            a2enmod http2 2>/dev/null || true
            a2enmod env 2>/dev/null || true
            a2enmod dir 2>/dev/null || true
            a2enmod mime 2>/dev/null || true
            a2enmod remoteip 2>/dev/null || true

            # Tắt default virtual host nếu cần
            a2dissite 000-default.conf 2>/dev/null || true
        elif command -v dnf &>/dev/null; then
            dnf install -y httpd httpd-tools mod_ssl mod_http2 2>/dev/null || true
        elif command -v yum &>/dev/null; then
            yum install -y httpd httpd-tools mod_ssl 2>/dev/null || true
        fi

        # Cấu hình an toàn và khởi động dịch vụ
        systemctl enable "$SERVICE_NAME" 2>/dev/null || true
        systemctl restart "$SERVICE_NAME" 2>/dev/null || true

        echo "[+] Cài đặt Apache Web Server hoàn tất! Service: $SERVICE_NAME"
        ;;

    remove|uninstall)
        echo "[+] Đang gỡ cài đặt Apache Web Server..."
        systemctl stop "$SERVICE_NAME" 2>/dev/null || true
        systemctl disable "$SERVICE_NAME" 2>/dev/null || true
        pkill -9 -f "apache2" 2>/dev/null || true
        pkill -9 -f "httpd" 2>/dev/null || true

        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get remove --purge -y apache2 apache2-utils libapache2-mod-fcgid 2>/dev/null || true
            apt-get autoremove -y 2>/dev/null || true
        elif command -v dnf &>/dev/null; then
            dnf remove -y httpd httpd-tools mod_ssl 2>/dev/null || true
        elif command -v yum &>/dev/null; then
            yum remove -y httpd httpd-tools mod_ssl 2>/dev/null || true
        fi
        echo "[+] Gỡ cài đặt Apache hoàn tất!"
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
        echo "Cú pháp: apache.sh <install|remove|start|stop|restart|reload|status> [version]"
        exit 1
        ;;
esac

exit 0
