#!/usr/bin/env bash
# WPCloud: ols.sh - Cài đặt OpenLiteSpeed Web Server (hỗ trợ Debian, Ubuntu, AlmaLinux, RHEL, Rocky)
set -euo pipefail

ACTION="${1:-install}"

case "$ACTION" in
    install|switch|reinstall|update)
        echo "[+] Đang thực thi $ACTION OpenLiteSpeed Web Server kèm gói tăng tốc WordPress tối đa..."
        echo "[+] Đang cập nhật gói hệ điều hành AlmaLinux/Debian/Ubuntu..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -y || true
        elif command -v dnf &>/dev/null; then
            dnf check-update -y 2>/dev/null || true
        elif command -v yum &>/dev/null; then
            yum check-update -y 2>/dev/null || true
        fi

        if command -v apt-get &>/dev/null; then
            # Tải repo script của LiteSpeed chính thức
            wget -qO - https://repo.litespeed.sh | bash || true
            chmod -R a+r /etc/apt/trusted.gpg.d/ 2>/dev/null || true
            apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 011AA62DEDA1F085 2>/dev/null || true
            chmod -R a+r /etc/apt/trusted.gpg.d/ 2>/dev/null || true
            
            DEBIAN_FRONTEND=noninteractive apt-get update -y || true
            echo "[+] Đang cài đặt OpenLiteSpeed và trọn bộ LSPHP WordPress Accelerators..."
            DEBIAN_FRONTEND=noninteractive apt-get install -y openlitespeed \
                lsphp82 lsphp82-common lsphp82-mysql lsphp82-opcache lsphp82-curl \
                lsphp82-redis lsphp82-imagick lsphp82-intl lsphp82-memcached \
                lsphp82-apcu lsphp82-mbstring lsphp82-xml lsphp82-zip lsphp82-bcmath || \
            DEBIAN_FRONTEND=noninteractive apt-get install -y openlitespeed

            # Cài đặt thêm các extensions bổ sung cho các bản PHP khác nếu đã có trên máy
            for pver in 84 83 81 80 74; do
                if [ -d "/usr/local/lsws/lsphp${pver}" ]; then
                    DEBIAN_FRONTEND=noninteractive apt-get install -y \
                        "lsphp${pver}-opcache" "lsphp${pver}-redis" "lsphp${pver}-imagick" \
                        "lsphp${pver}-intl" "lsphp${pver}-memcached" "lsphp${pver}-curl" \
                        "lsphp${pver}-mysql" 2>/dev/null || true
                fi
            done
        elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
            rpm -Uvh http://rpms.litespeedtech.com/centos/litespeed-repo-1.3-1.el8.noarch.rpm 2>/dev/null || \
            rpm -Uvh http://rpms.litespeedtech.com/centos/litespeed-repo-1.3-1.el9.noarch.rpm 2>/dev/null || true
            dnf install -y epel-release 2>/dev/null || true
            dnf install -y openlitespeed \
                lsphp82 lsphp82-common lsphp82-mysqlnd lsphp82-opcache lsphp82-curl \
                lsphp82-redis lsphp82-pecl-redis lsphp82-imagick lsphp82-intl \
                lsphp82-pecl-memcached lsphp82-pecl-apcu lsphp82-mbstring lsphp82-xml lsphp82-zip || \
            dnf install -y openlitespeed

            for pver in 84 83 81 80 74; do
                if [ -d "/usr/local/lsws/lsphp${pver}" ]; then
                    dnf install -y "lsphp${pver}-opcache" "lsphp${pver}-redis" "lsphp${pver}-imagick" "lsphp${pver}-intl" 2>/dev/null || true
                fi
            done
        fi

        # 1. THIẾT LẬP THƯ MỤC NATIVE LSCACHE & SWAP STORAGE
        echo "[+] Cấu hình thư mục bộ nhớ đệm LSCache siêu tốc (/tmp/lshttpd/swap)..."
        mkdir -p /tmp/lshttpd /tmp/lshttpd/swap /tmp/lshttpd/cache
        chmod 1777 /tmp/lshttpd /tmp/lshttpd/swap /tmp/lshttpd/cache 2>/dev/null || true

        # 2. TỐI ƯU HÓA ZEND OPCACHE & JIT COMPILER CHO WORDPRESS
        echo "[+] Tối ưu hóa Zend OPcache và JIT Compiler cho WordPress..."
        for pver in 82 83 84 81 80 74; do
            OPC_CONF=""
            for cand in "/usr/local/lsws/lsphp${pver}/etc/php/${pver:0:1}.${pver:1:1}/mods-available/99-wpcloud-opcache.ini" \
                        "/usr/local/lsws/lsphp${pver}/etc/php.d/99-wpcloud-opcache.ini" \
                        "/etc/opt/remi/php${pver}/php.d/99-wpcloud-opcache.ini"; do
                cand_dir=$(dirname "$cand")
                if [ -d "$cand_dir" ]; then
                    OPC_CONF="$cand"
                    break
                fi
            done
            if [ -n "$OPC_CONF" ]; then
                cat << 'EOF_OPC' > "$OPC_CONF"
; WPCloud High-Performance Zend OPcache & JIT Tuning for WordPress
opcache.enable=1
opcache.enable_cli=1
opcache.memory_consumption=256
opcache.interned_strings_buffer=32
opcache.max_accelerated_files=50000
opcache.revalidate_freq=10
opcache.validate_timestamps=1
opcache.save_comments=1
opcache.fast_shutdown=1
opcache.jit=tracing
opcache.jit_buffer_size=128M
EOF_OPC
            fi
        done

        # 3. CẤU HÌNH HTTP LISTENER & BROTLI & GZIP TRONG HTTPD_CONFIG.CONF
        OLS_CONF="/usr/local/lsws/conf/httpd_config.conf"
        if [ -f "$OLS_CONF" ]; then
            sed -i 's/address *:8088/address *:80/g' "$OLS_CONF" || true
            sed -i 's/listener Default {/listener HTTP_80 {/g' "$OLS_CONF" || true
            
            # Đảm bảo bật Gzip và Brotli toàn cục trong OLS
            grep -q "enableGzip" "$OLS_CONF" && sed -i 's/enableGzip.*/enableGzip                1/' "$OLS_CONF" || echo "enableGzip                1" >> "$OLS_CONF"
            grep -q "enableBr" "$OLS_CONF" && sed -i 's/enableBr.*/enableBr                  1/' "$OLS_CONF" || echo "enableBr                  1" >> "$OLS_CONF"

            chmod 755 /usr/local/lsws/conf 2>/dev/null || true
            chmod 644 /usr/local/lsws/conf/httpd_config.conf 2>/dev/null || true
            if id "wpc" &>/dev/null; then
                usermod -a -G lsadm "wpc" 2>/dev/null || true
            fi
        fi

        systemctl enable lshttpd 2>/dev/null || systemctl enable lsws 2>/dev/null || true
        systemctl restart lshttpd 2>/dev/null || systemctl restart lsws 2>/dev/null || /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || true

        echo "[+] $ACTION OpenLiteSpeed kèm gói tăng tốc WordPress tối đa hoàn tất thành công!"
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
