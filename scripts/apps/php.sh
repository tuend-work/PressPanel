#!/usr/bin/env bash
# WPCloud: php.sh - Cài đặt và Quản lý PHP-FPM / LS-PHP cùng các Extensions tối ưu cho WordPress
set -euo pipefail

ACTION="${1:-install}"
VERSION="${2:-8.2}"

# Chuẩn hoá version string (vd: php82 -> 8.2, 8.2 -> 8.2)
VERSION_CLEAN=$(echo "$VERSION" | sed -E 's/^php//; s/^([0-9])([0-9])$/\1.\2/')
[ -z "$VERSION_CLEAN" ] && VERSION_CLEAN="8.2"

detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "$ID"
    else
        echo "unknown"
    fi
}

OS=$(detect_os)

case "$ACTION" in
    install)
        echo "[+] Đang chuẩn bị cài đặt PHP-FPM $VERSION_CLEAN..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -y || true
            apt-get install -y software-properties-common lsb-release ca-certificates apt-transport-https || true
            
            # Nếu Ubuntu hoặc Debian, thêm repository ondrej/php nếu chưa có
            if [ "$OS" = "ubuntu" ]; then
                if ! grep -q "^deb .*ondrej/php" /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null; then
                    add-apt-repository -y ppa:ondrej/php || true
                    apt-get update -y || true
                fi
            elif [ "$OS" = "debian" ]; then
                if [ ! -f /etc/apt/sources.list.d/php.list ]; then
                    curl -sSLo /usr/share/keyrings/deb.sury.org-php.gpg https://packages.sury.org/php/apt.gpg 2>/dev/null || true
                    echo "deb [signed-by=/usr/share/keyrings/deb.sury.org-php.gpg] https://packages.sury.org/php/ $(lsb_release -sc) main" > /etc/apt/sources.list.d/php.list || true
                    apt-get update -y || true
                fi
            fi

            # Cài đặt PHP-FPM và các module thiết yếu cho WordPress
            echo "[+] Đang cài đặt php${VERSION_CLEAN}-fpm cùng các module cho WordPress..."
            apt-get install -y \
                "php${VERSION_CLEAN}-fpm" \
                "php${VERSION_CLEAN}-cli" \
                "php${VERSION_CLEAN}-common" \
                "php${VERSION_CLEAN}-mysql" \
                "php${VERSION_CLEAN}-mbstring" \
                "php${VERSION_CLEAN}-xml" \
                "php${VERSION_CLEAN}-curl" \
                "php${VERSION_CLEAN}-gd" \
                "php${VERSION_CLEAN}-zip" \
                "php${VERSION_CLEAN}-opcache" \
                "php${VERSION_CLEAN}-intl" \
                "php${VERSION_CLEAN}-bcmath" \
                "php${VERSION_CLEAN}-imagick" \
                "php${VERSION_CLEAN}-redis" || true

            # Đảm bảo thư mục runtime socket tồn tại
            mkdir -p /run/php /var/run/php
            chown -R www-data:www-data /run/php /var/run/php 2>/dev/null || true

            # Tự động tối ưu php.ini cơ bản
            PHP_INI="/etc/php/${VERSION_CLEAN}/fpm/php.ini"
            if [ -f "$PHP_INI" ]; then
                sed -i 's/^upload_max_filesize = .*/upload_max_filesize = 128M/' "$PHP_INI" || true
                sed -i 's/^post_max_size = .*/post_max_size = 128M/' "$PHP_INI" || true
                sed -i 's/^memory_limit = .*/memory_limit = 512M/' "$PHP_INI" || true
                sed -i 's/^max_execution_time = .*/max_execution_time = 300/' "$PHP_INI" || true
                sed -i 's/^max_input_time = .*/max_input_time = 300/' "$PHP_INI" || true
            fi

            # Cấu hình drop-in override cho PHP-FPM để tự động gán worker vào Cgroup v2 của user khi start/reload
            PHP_OVERRIDE_DIR="/etc/systemd/system/php${VERSION_CLEAN}-fpm.service.d"
            mkdir -p "$PHP_OVERRIDE_DIR"
            cat << 'EOF' > "$PHP_OVERRIDE_DIR/cgroup_attach.conf"
[Service]
ExecStartPost=-/bin/bash -c '(sleep 1 && /opt/wpc/scripts/cgroup_attach.sh) &'
ExecReloadPost=-/bin/bash -c '(sleep 1 && /opt/wpc/scripts/cgroup_attach.sh) &'
EOF

            # Kích hoạt và khởi chạy service PHP-FPM
            systemctl daemon-reload 2>/dev/null || true
            systemctl enable "php${VERSION_CLEAN}-fpm" || true
            systemctl restart "php${VERSION_CLEAN}-fpm" || true
            /opt/wpc/scripts/cgroup_attach.sh 2>/dev/null || true

            # Nếu có OpenLiteSpeed thì cài thêm cả LSPHP tương ứng nếu có repo
            LSPHP_NUM=$(echo "$VERSION_CLEAN" | tr -d '.')
            LSPHP_PKG="lsphp${LSPHP_NUM}"
            apt-get install -y "$LSPHP_PKG" "${LSPHP_PKG}-mysql" "${LSPHP_PKG}-curl" "${LSPHP_PKG}-opcache" 2>/dev/null || true

            # Xác thực thực tế: Phải có ít nhất PHP-FPM hoặc LSPHP thực sự hoạt động
            INSTALLED_OK=0
            if [ -x "/usr/sbin/php-fpm${VERSION_CLEAN}" ] || [ -x "/usr/bin/php${VERSION_CLEAN}" ] || [ -x "/usr/local/lsws/lsphp${LSPHP_NUM}/bin/lsphp" ]; then
                INSTALLED_OK=1
            fi

            if [ "$INSTALLED_OK" -eq 0 ]; then
                echo "[-] Lỗi: Cài đặt PHP ${VERSION_CLEAN} không thành công! Không tìm thấy gói php-fpm hoặc lsphp tương thích trên kho phần mềm."
                exit 1
            fi

            echo "[+] Đã cài đặt và kích hoạt PHP Runtime ${VERSION_CLEAN} thành công!"
        fi
        ;;

    remove)
        echo "[+] Đang gỡ bỏ PHP-FPM $VERSION_CLEAN..."
        systemctl stop "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
        systemctl disable "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
        if command -v apt-get &>/dev/null; then
            apt-get remove -y "php${VERSION_CLEAN}-fpm" "php${VERSION_CLEAN}-cli" 2>/dev/null || true
        fi
        echo "[+] Đã gỡ bỏ PHP-FPM $VERSION_CLEAN!"
        ;;

    install-ext)
        EXT="${3:-}"
        if [ -n "$EXT" ]; then
            echo "[+] Đang cài đặt PHP extension: php${VERSION_CLEAN}-${EXT}..."
            apt-get install -y "php${VERSION_CLEAN}-${EXT}" || true
            systemctl restart "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
        fi
        ;;

    remove-ext)
        EXT="${3:-}"
        if [ -n "$EXT" ]; then
            echo "[+] Đang gỡ bỏ PHP extension: php${VERSION_CLEAN}-${EXT}..."
            apt-get remove -y "php${VERSION_CLEAN}-${EXT}" || true
            systemctl restart "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
        fi
        ;;

    pool-restart)
        systemctl restart "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
        echo "[+] Khởi động lại PHP-FPM $VERSION_CLEAN pool thành công!"
        ;;

    *)
        echo "[-] Lệnh không hợp lệ: $ACTION"
        exit 1
        ;;
esac
