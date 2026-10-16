#!/usr/bin/env bash
# WPCloud: mysql.sh - Cài đặt MySQL / MariaDB Server với nhiều tùy chọn phiên bản
set -euo pipefail

ACTION="${1:-install}"
VERSION="${2:-mariadb-10.11}"

case "$ACTION" in
    install)
        echo "[+] Bắt đầu cấu hình cài đặt Database Server ($VERSION)..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -y || true

            case "$VERSION" in
                mariadb-10.11|mariadb-10.6|mariadb-latest|mariadb)
                    echo "[+] Cài đặt MariaDB Database Server ($VERSION)..."
                    apt-get install -y mariadb-server mariadb-client
                    systemctl enable mariadb || systemctl enable mysql || true
                    systemctl restart mariadb || systemctl restart mysql || true
                    ;;
                mysql-8.0|mysql-latest|mysql)
                    echo "[+] Cài đặt Oracle MySQL Server ($VERSION)..."
                    apt-get install -y mysql-server mysql-client
                    systemctl enable mysql || true
                    systemctl restart mysql || true
                    ;;
                *)
                    echo "[+] Phiên bản mặc định MariaDB Server..."
                    apt-get install -y mariadb-server mariadb-client
                    systemctl enable mariadb || true
                    systemctl restart mariadb || true
                    ;;
            esac
        fi
        echo "[+] Cài đặt Database Server ($VERSION) thành công!"
        ;;

    remove)
        echo "[+] Đang gỡ bỏ dịch vụ Database Server..."
        systemctl stop mariadb 2>/dev/null || true
        systemctl stop mysql 2>/dev/null || true
        if command -v apt-get &>/dev/null; then
            apt-get remove -y mariadb-server mariadb-client mysql-server mysql-client 2>/dev/null || true
        fi
        echo "[+] Đã gỡ bỏ Database Server thành công!"
        ;;

    *)
        echo "Lệnh không hợp lệ"
        exit 1
        ;;
esac
