#!/usr/bin/env bash
# WPCloud: mysql.sh - Cài đặt MySQL / MariaDB Server với nhiều phiên bản (hỗ trợ Debian, Ubuntu, AlmaLinux, RHEL, Rocky)
set -euo pipefail

ACTION="${1:-install}"
VERSION="${2:-mariadb-10.11}"

case "$ACTION" in
    install)
        echo "[+] Bắt đầu cấu hình cài đặt Database Server ($VERSION)..."
        
        # 1. Nhánh Debian / Ubuntu
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            echo "[+] Đang cập nhật gói hệ điều hành Debian/Ubuntu (apt update & apt upgrade)..."
            apt-get update -y || true
            apt-get upgrade -y || true

            case "$VERSION" in
                mariadb-11.4)
                    echo "[+] Thiết lập kho MariaDB 11.4 LTS..."
                    curl -LsS https://r.mariadb.com/downloads/mariadb_repo_setup 2>/dev/null | bash -s -- --mariadb-server-version="mariadb-11.4" || true
                    apt-get update -y || true
                    apt-get install -y mariadb-server mariadb-client || apt-get install -y mariadb-server
                    ;;
                mariadb-10.11|mariadb-10.6|mariadb-10.5|mariadb*)
                    echo "[+] Cài đặt MariaDB Database Server ($VERSION)..."
                    apt-get install -y mariadb-server mariadb-client || apt-get install -y mariadb-server
                    ;;
                mysql-8.4|mysql-8.0|mysql*)
                    echo "[+] Cài đặt Oracle MySQL Server ($VERSION)..."
                    apt-get install -y mysql-server mysql-client || apt-get install -y mysql-server
                    ;;
                percona*)
                    echo "[+] Cài đặt Percona Server for MySQL..."
                    apt-get install -y percona-server-server 2>/dev/null || apt-get install -y mariadb-server
                    ;;
                *)
                    apt-get install -y mariadb-server mariadb-client
                    ;;
            esac

        # 2. Nhánh RHEL / AlmaLinux / Rocky / CentOS
        elif command -v dnf &>/dev/null; then
            echo "[+] Đang cập nhật gói hệ điều hành AlmaLinux/RHEL (dnf upgrade)..."
            dnf upgrade -y || true

            case "$VERSION" in
                mariadb-11.4)
                    echo "[+] Cài đặt MariaDB 11.4 trên RHEL/AlmaLinux..."
                    curl -LsS https://r.mariadb.com/downloads/mariadb_repo_setup 2>/dev/null | bash -s -- --mariadb-server-version="mariadb-11.4" || true
                    dnf install -y MariaDB-server MariaDB-client 2>/dev/null || dnf install -y mariadb-server mariadb
                    ;;
                mariadb-10.11|mariadb-10.6|mariadb-10.5|mariadb*)
                    echo "[+] Cài đặt MariaDB Server ($VERSION) trên RHEL/AlmaLinux..."
                    dnf install -y mariadb-server mariadb
                    ;;
                mysql-8.4|mysql-8.0|mysql*)
                    echo "[+] Cài đặt MySQL Server ($VERSION) trên RHEL/AlmaLinux..."
                    dnf install -y mysql-server mysql
                    ;;
                percona*)
                    echo "[+] Cài đặt Percona / MariaDB trên RHEL/AlmaLinux..."
                    dnf install -y mariadb-server mariadb
                    ;;
                *)
                    dnf install -y mariadb-server mariadb
                    ;;
            esac
        fi

        # Kích hoạt và khởi động dịch vụ
        systemctl enable mariadb 2>/dev/null || systemctl enable mysqld 2>/dev/null || systemctl enable mysql 2>/dev/null || true
        systemctl restart mariadb 2>/dev/null || systemctl restart mysqld 2>/dev/null || systemctl restart mysql 2>/dev/null || true

        echo "[+] Cài đặt Database Server ($VERSION) thành công!"
        ;;

    remove)
        echo "[+] Đang gỡ bỏ dịch vụ Database Server..."
        systemctl stop mariadb 2>/dev/null || true
        systemctl stop mysqld 2>/dev/null || true
        systemctl stop mysql 2>/dev/null || true
        systemctl disable mariadb 2>/dev/null || true
        systemctl disable mysqld 2>/dev/null || true
        systemctl disable mysql 2>/dev/null || true
        if command -v apt-get &>/dev/null; then
            apt-get remove -y mariadb-server mariadb-client mysql-server mysql-client 2>/dev/null || true
        elif command -v dnf &>/dev/null; then
            dnf remove -y mariadb-server mariadb mysql-server mysql MariaDB-server MariaDB-client 2>/dev/null || true
        fi
        echo "[+] Đã gỡ bỏ Database Server thành công!"
        ;;

    *)
        echo "[-] Lệnh không hợp lệ"
        exit 1
        ;;
esac
