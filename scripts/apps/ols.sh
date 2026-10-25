#!/usr/bin/env bash
# WPCloud: ols.sh - Cài đặt OpenLiteSpeed Web Server (hỗ trợ Debian, Ubuntu, AlmaLinux, RHEL, Rocky)
set -euo pipefail

ACTION="${1:-install}"

case "$ACTION" in
    install)
        echo "[+] Đang cài đặt OpenLiteSpeed Web Server..."
        echo "[+] Đang cập nhật gói hệ điều hành AlmaLinux/Debian/Ubuntu trước khi cài đặt..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -y || true
            apt-get upgrade -y || true
        elif command -v dnf &>/dev/null; then
            dnf upgrade -y || true
        elif command -v yum &>/dev/null; then
            yum update -y || true
        fi

        if command -v apt-get &>/dev/null; then
            # Tải repo script của LiteSpeed
            wget -qO - https://repo.litespeed.sh | bash || true
            chmod -R a+r /etc/apt/trusted.gpg.d/ 2>/dev/null || true
            apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 011AA62DEDA1F085 2>/dev/null || true
            chmod -R a+r /etc/apt/trusted.gpg.d/ 2>/dev/null || true
            
            DEBIAN_FRONTEND=noninteractive apt-get update -y || true
            echo "[+] Đang cài đặt OpenLiteSpeed và LSPHP 8.2..."
            DEBIAN_FRONTEND=noninteractive apt-get install -y openlitespeed lsphp82 lsphp82-common lsphp82-mysql lsphp82-opcache lsphp82-curl || \
            DEBIAN_FRONTEND=noninteractive apt-get install -y openlitespeed

            # Cài đặt thêm các extensions bổ sung an toàn (bỏ qua nếu không tồn tại trong repo)
            for ext in lsphp82-redis lsphp82-imagick lsphp82-intl; do
                DEBIAN_FRONTEND=noninteractive apt-get install -y "$ext" 2>/dev/null || true
            done
        elif command -v dnf &>/dev/null; then
            rpm -Uvh http://rpms.litespeedtech.com/centos/litespeed-repo-1.3-1.el8.noarch.rpm 2>/dev/null || \
            rpm -Uvh http://rpms.litespeedtech.com/centos/litespeed-repo-1.3-1.el9.noarch.rpm 2>/dev/null || true
            dnf install -y openlitespeed lsphp82 lsphp82-common lsphp82-mysqlnd lsphp82-opcache lsphp82-curl || \
            dnf install -y openlitespeed

            for ext in lsphp82-redis lsphp82-pecl-redis lsphp82-imagick lsphp82-intl; do
                dnf install -y "$ext" 2>/dev/null || true
            done
        fi

        # Cấu hình OpenLiteSpeed: Đổi port mặc định 8088 thành 80 cho HTTP listener
        OLS_CONF="/usr/local/lsws/conf/httpd_config.conf"
        if [ -f "$OLS_CONF" ]; then
            sed -i 's/address *:8088/address *:80/g' "$OLS_CONF" || true
            sed -i 's/listener Default {/listener HTTP_80 {/g' "$OLS_CONF" || true
            
            chmod 755 /usr/local/lsws/conf 2>/dev/null || true
            chmod 644 /usr/local/lsws/conf/httpd_config.conf 2>/dev/null || true
            if id "wpc" &>/dev/null; then
                usermod -a -G lsadm "wpc" 2>/dev/null || true
            fi
        fi

        systemctl enable lshttpd 2>/dev/null || systemctl enable lsws 2>/dev/null || true
        systemctl restart lshttpd 2>/dev/null || systemctl restart lsws 2>/dev/null || true

        echo "[+] Cài đặt OpenLiteSpeed hoàn tất!"
        ;;

    remove)
        echo "[+] Đang gỡ cài đặt OpenLiteSpeed..."
        systemctl stop lshttpd 2>/dev/null || true
        systemctl stop lsws 2>/dev/null || true
        pkill -9 -f "openlitespeed" 2>/dev/null || true
        pkill -9 -f "lshttpd" 2>/dev/null || true
        pkill -9 -f "lsphp" 2>/dev/null || true
        pkill -9 -f "lscgid" 2>/dev/null || true
        rm -rf /tmp/lshttpd/*.sock 2>/dev/null || true
        if command -v apt-get &>/dev/null; then
            apt-get remove -y openlitespeed 2>/dev/null || true
        elif command -v dnf &>/dev/null; then
            dnf remove -y openlitespeed 2>/dev/null || true
        fi
        echo "[+] Đã gỡ bỏ OpenLiteSpeed thành công!"
        ;;

    *)
        echo "[-] Lệnh không hợp lệ"
        exit 1
        ;;
esac
