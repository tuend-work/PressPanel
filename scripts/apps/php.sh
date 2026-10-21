#!/usr/bin/env bash
# WPCloud: php.sh - Cài đặt và Quản lý PHP-FPM / LS-PHP cùng các Extensions tối ưu cho WordPress
set -euo pipefail

ACTION="${1:-install}"
VERSION="${2:-8.2}"

# Chuẩn hoá version string (vd: php84 -> 8.4, 8.4 -> 8.4)
VERSION_CLEAN=$(echo "$VERSION" | sed -E 's/^php//; s/^([0-9])([0-9])$/\1.\2/')
[ -z "$VERSION_CLEAN" ] && VERSION_CLEAN="8.2"

LSPHP_NUM=$(echo "$VERSION_CLEAN" | tr -d '.')
PHP_PKG_PREFIX="php${LSPHP_NUM}"

detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "$ID"
    else
        echo "unknown"
    fi
}

detect_el_maj() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "${VERSION_ID%%.*}"
    else
        echo "9"
    fi
}

OS=$(detect_os)
EL_MAJ=$(detect_el_maj)

ensure_repos() {
    if command -v apt-get &>/dev/null; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -y || true
        apt-get install -y software-properties-common lsb-release ca-certificates apt-transport-https || true

        # Kích hoạt Ondřej PPA (ppa:ondrej/php) cho Ubuntu hoặc sury cho Debian
        if [ "$OS" = "ubuntu" ]; then
            if ! grep -rq "ondrej/php" /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null; then
                echo "[+] Kích hoạt Ondřej PPA (ppa:ondrej/php) trên Ubuntu..."
                LC_ALL=C.UTF-8 add-apt-repository -y ppa:ondrej/php || true
                apt-get update -y || true
            fi
        elif [ "$OS" = "debian" ]; then
            if [ ! -f /etc/apt/sources.list.d/php.list ] && ! grep -rq "deb.sury.org" /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null; then
                echo "[+] Kích hoạt Ondřej Sury repository trên Debian..."
                curl -sSLo /usr/share/keyrings/deb.sury.org-php.gpg https://packages.sury.org/php/apt.gpg 2>/dev/null || true
                echo "deb [signed-by=/usr/share/keyrings/deb.sury.org-php.gpg] https://packages.sury.org/php/ $(lsb_release -sc) main" > /etc/apt/sources.list.d/php.list || true
                apt-get update -y || true
            fi
        fi
    elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
        PKG_MGR="dnf"
        command -v dnf &>/dev/null || PKG_MGR="yum"

        # Kích hoạt Remi và EPEL repository trên RHEL/AlmaLinux theo phiên bản EL
        if ! rpm -q remi-release &>/dev/null; then
            echo "[+] Cấu hình Remi repository trên RHEL/AlmaLinux ${EL_MAJ}..."
            $PKG_MGR install -y epel-release || true
            $PKG_MGR install -y "https://rpms.remirepo.net/enterprise/remi-release-${EL_MAJ}.rpm" 2>/dev/null || \
            $PKG_MGR install -y "https://rpms.remirepo.net/enterprise/remi-release-9.rpm" 2>/dev/null || \
            $PKG_MGR install -y "https://rpms.remirepo.net/enterprise/remi-release-8.rpm" 2>/dev/null || true
        fi
        $PKG_MGR config-manager --set-enabled remi 2>/dev/null || dnf config-manager --enable remi 2>/dev/null || true
    fi
}

case "$ACTION" in
    install)
        echo "[+] Đang chuẩn bị cài đặt PHP-FPM $VERSION_CLEAN..."
        ensure_repos
        if command -v apt-get &>/dev/null; then

            # Cài đặt PHP-FPM và các module thiết yếu cho WordPress
            echo "[+] Đang cài đặt php${VERSION_CLEAN}-fpm cùng các module cho WordPress trên Debian/Ubuntu..."
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

            # Không bật service FPM toàn cục để tiết kiệm RAM; FPM được quản lý độc lập theo từng website/user
            systemctl stop "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
            systemctl disable "php${VERSION_CLEAN}-fpm" 2>/dev/null || true

            # Nếu có OpenLiteSpeed thì cài thêm cả LSPHP tương ứng nếu có repo
            LSPHP_PKG="lsphp${LSPHP_NUM}"
            apt-get install -y "$LSPHP_PKG" "${LSPHP_PKG}-mysql" "${LSPHP_PKG}-curl" "${LSPHP_PKG}-opcache" "${LSPHP_PKG}-redis" "${LSPHP_PKG}-imagick" "${LSPHP_PKG}-intl" "${LSPHP_PKG}-soap" "${LSPHP_PKG}-bcmath" 2>/dev/null || true

            # Xác thực thực tế
            INSTALLED_OK=0
            if [ -x "/usr/sbin/php-fpm${VERSION_CLEAN}" ] || [ -x "/usr/bin/php${VERSION_CLEAN}" ] || [ -x "/usr/local/lsws/lsphp${LSPHP_NUM}/bin/lsphp" ]; then
                INSTALLED_OK=1
            fi

            if [ "$INSTALLED_OK" -eq 0 ]; then
                echo "[-] Lỗi: Cài đặt PHP ${VERSION_CLEAN} không thành công! Không tìm thấy gói php-fpm hoặc lsphp tương thích trên kho phần mềm."
                exit 1
            fi

            echo "[+] Đã cài đặt và kích hoạt PHP ${VERSION_CLEAN} thành công!"

        elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
            PKG_MGR="dnf"
            command -v dnf &>/dev/null || PKG_MGR="yum"

            # 1. Kích hoạt Remi và EPEL repository nếu chưa có
            if ! rpm -q remi-release &>/dev/null; then
                echo "[+] Cấu hình Remi repository trên RHEL/AlmaLinux..."
                $PKG_MGR install -y epel-release || true
                $PKG_MGR install -y "https://rpms.remirepo.net/enterprise/remi-release-8.rpm" 2>/dev/null || \
                $PKG_MGR install -y "https://rpms.remirepo.net/enterprise/remi-release-9.rpm" 2>/dev/null || true
            fi

            # 2. Cài đặt các gói PHP SCL độc lập từ Remi (hỗ trợ nhiều phiên bản song song không xung đột)
            echo "[+] Đang cài đặt ${PHP_PKG_PREFIX}-php-fpm cùng các module cho WordPress trên RHEL/AlmaLinux..."
            $PKG_MGR install -y \
                "${PHP_PKG_PREFIX}-php-fpm" \
                "${PHP_PKG_PREFIX}-php-cli" \
                "${PHP_PKG_PREFIX}-php-common" \
                "${PHP_PKG_PREFIX}-php-mysqlnd" \
                "${PHP_PKG_PREFIX}-php-mbstring" \
                "${PHP_PKG_PREFIX}-php-xml" \
                "${PHP_PKG_PREFIX}-php-curl" \
                "${PHP_PKG_PREFIX}-php-gd" \
                "${PHP_PKG_PREFIX}-php-zip" \
                "${PHP_PKG_PREFIX}-php-opcache" \
                "${PHP_PKG_PREFIX}-php-intl" \
                "${PHP_PKG_PREFIX}-php-bcmath" \
                "${PHP_PKG_PREFIX}-php-pecl-imagick" \
                "${PHP_PKG_PREFIX}-php-pecl-redis5" \
                "${PHP_PKG_PREFIX}-php-soap" 2>/dev/null || \
            $PKG_MGR install -y "${PHP_PKG_PREFIX}-php-fpm" "${PHP_PKG_PREFIX}-php-cli" "${PHP_PKG_PREFIX}-php-mysqlnd" || true

            # Cài đặt LSPHP nếu máy chủ đang có OpenLiteSpeed repo
            $PKG_MGR install -y "lsphp${LSPHP_NUM}" "lsphp${LSPHP_NUM}-mysqlnd" "lsphp${LSPHP_NUM}-curl" "lsphp${LSPHP_NUM}-opcache" "lsphp${LSPHP_NUM}-gd" "lsphp${LSPHP_NUM}-zip" "lsphp${LSPHP_NUM}-mbstring" 2>/dev/null || true

            # 3. Tạo Symlink chuẩn hoá cho hệ thống
            if [ -x "/opt/remi/${PHP_PKG_PREFIX}/root/usr/sbin/php-fpm" ]; then
                ln -sf "/opt/remi/${PHP_PKG_PREFIX}/root/usr/sbin/php-fpm" "/usr/sbin/php-fpm${VERSION_CLEAN}" 2>/dev/null || true
            fi
            if [ -x "/usr/bin/${PHP_PKG_PREFIX}" ]; then
                ln -sf "/usr/bin/${PHP_PKG_PREFIX}" "/usr/bin/php${VERSION_CLEAN}" 2>/dev/null || true
            fi

            # 4. Tạo Systemd Service Alias
            if [ -f "/usr/lib/systemd/system/${PHP_PKG_PREFIX}-php-fpm.service" ]; then
                ln -sf "/usr/lib/systemd/system/${PHP_PKG_PREFIX}-php-fpm.service" "/etc/systemd/system/php${VERSION_CLEAN}-fpm.service" 2>/dev/null || true
            fi

            # 5. Cấu hình thư mục Socket và phân quyền
            mkdir -p /run/php /var/run/php "/var/opt/remi/${PHP_PKG_PREFIX}/run/php-fpm"
            chown -R nginx:nginx /run/php /var/run/php 2>/dev/null || \
            chown -R www-data:www-data /run/php /var/run/php 2>/dev/null || \
            chown -R nobody:nobody /run/php /var/run/php 2>/dev/null || true

            # 6. Tối ưu php.ini Remi
            REMI_INI="/etc/opt/remi/${PHP_PKG_PREFIX}/php.ini"
            if [ -f "$REMI_INI" ]; then
                sed -i 's/^upload_max_filesize = .*/upload_max_filesize = 128M/' "$REMI_INI" || true
                sed -i 's/^post_max_size = .*/post_max_size = 128M/' "$REMI_INI" || true
                sed -i 's/^memory_limit = .*/memory_limit = 512M/' "$REMI_INI" || true
                sed -i 's/^max_execution_time = .*/max_execution_time = 300/' "$REMI_INI" || true
                sed -i 's/^max_input_time = .*/max_input_time = 300/' "$REMI_INI" || true
            fi

            # 7. Cấu hình cgroup drop-in override
            PHP_OVERRIDE_DIR="/etc/systemd/system/${PHP_PKG_PREFIX}-php-fpm.service.d"
            mkdir -p "$PHP_OVERRIDE_DIR"
            cat << 'EOF' > "$PHP_OVERRIDE_DIR/cgroup_attach.conf"
[Service]
ExecStartPost=-/bin/bash -c '(sleep 1 && /opt/wpc/scripts/cgroup_attach.sh) &'
ExecReloadPost=-/bin/bash -c '(sleep 1 && /opt/wpc/scripts/cgroup_attach.sh) &'
EOF

            systemctl daemon-reload 2>/dev/null || true
            systemctl enable "${PHP_PKG_PREFIX}-php-fpm" 2>/dev/null || systemctl enable "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
            systemctl restart "${PHP_PKG_PREFIX}-php-fpm" 2>/dev/null || systemctl restart "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
            /opt/wpc/scripts/cgroup_attach.sh 2>/dev/null || true

            # 8. Xác thực cài đặt thực tế
            INSTALLED_OK=0
            if [ -x "/usr/sbin/php-fpm${VERSION_CLEAN}" ] || \
               [ -x "/opt/remi/${PHP_PKG_PREFIX}/root/usr/sbin/php-fpm" ] || \
               [ -x "/usr/bin/${PHP_PKG_PREFIX}" ] || \
               [ -x "/usr/bin/php${VERSION_CLEAN}" ] || \
               [ -x "/usr/local/lsws/lsphp${LSPHP_NUM}/bin/lsphp" ]; then
                INSTALLED_OK=1
            fi

            if [ "$INSTALLED_OK" -eq 0 ]; then
                echo "[-] Lỗi: Cài đặt PHP ${VERSION_CLEAN} trên RHEL/AlmaLinux không thành công! Không tìm thấy nhị phân PHP được cài đặt."
                exit 1
            fi

            echo "[+] Đã cài đặt và kích hoạt PHP ${VERSION_CLEAN} (${PHP_PKG_PREFIX}) thành công!"
        else
            echo "[-] Hệ điều hành không hỗ trợ apt-get hoặc dnf/yum."
            exit 1
        fi
        ;;

    remove)
        echo "[+] Đang gỡ bỏ PHP $VERSION_CLEAN..."
        systemctl stop "${PHP_PKG_PREFIX}-php-fpm" "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
        systemctl disable "${PHP_PKG_PREFIX}-php-fpm" "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
        if command -v apt-get &>/dev/null; then
            apt-get purge -y "php${VERSION_CLEAN}*" "lsphp${LSPHP_NUM}*" 2>/dev/null || apt-get remove -y "php${VERSION_CLEAN}-fpm" "php${VERSION_CLEAN}-cli" 2>/dev/null || true
        elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
            PKG_MGR="dnf"
            command -v dnf &>/dev/null || PKG_MGR="yum"
            $PKG_MGR remove -y "${PHP_PKG_PREFIX}*" "lsphp${LSPHP_NUM}*" 2>/dev/null || true
        fi
        rm -rf "/etc/php/${VERSION_CLEAN}" "/usr/local/lsws/lsphp${LSPHP_NUM}" "/opt/remi/${PHP_PKG_PREFIX}" "/etc/opt/remi/${PHP_PKG_PREFIX}" "/var/opt/remi/${PHP_PKG_PREFIX}" 2>/dev/null || true
        rm -f "/usr/sbin/php-fpm${VERSION_CLEAN}" "/usr/bin/php${VERSION_CLEAN}" "/usr/bin/${PHP_PKG_PREFIX}" "/usr/sbin/${PHP_PKG_PREFIX}-fpm" "/etc/systemd/system/php${VERSION_CLEAN}-fpm.service" "/etc/systemd/system/${PHP_PKG_PREFIX}-php-fpm.service" 2>/dev/null || true
        if command -v alternatives &>/dev/null; then
            alternatives --remove php "/usr/bin/php${VERSION_CLEAN}" 2>/dev/null || true
            alternatives --remove php "/usr/bin/${PHP_PKG_PREFIX}" 2>/dev/null || true
        fi
        echo "[+] Đã gỡ bỏ PHP $VERSION_CLEAN và xoá triệt để tệp nhị phân, thư mục cấu hình hoàn tất!"
        ;;

    set-cli)
        echo "[+] Đang thiết lập PHP $VERSION_CLEAN làm PHP CLI mặc định hệ thống (/usr/bin/php)..."
        if command -v update-alternatives &>/dev/null && [ -x "/usr/bin/php${VERSION_CLEAN}" ]; then
            update-alternatives --set php "/usr/bin/php${VERSION_CLEAN}" 2>/dev/null || true
        fi
        if [ -x "/usr/bin/php${VERSION_CLEAN}" ]; then
            ln -sf "/usr/bin/php${VERSION_CLEAN}" /usr/bin/php
        elif [ -x "/usr/bin/${PHP_PKG_PREFIX}" ]; then
            ln -sf "/usr/bin/${PHP_PKG_PREFIX}" /usr/bin/php
        elif [ -x "/usr/local/lsws/lsphp${LSPHP_NUM}/bin/lsphp" ]; then
            ln -sf "/usr/local/lsws/lsphp${LSPHP_NUM}/bin/lsphp" /usr/bin/php
        fi
        echo "[+] PHP CLI hệ thống hiện tại:"
        /usr/bin/php -v 2>/dev/null | head -n 1 || true
        ;;

    install-ext)
        EXT="${3:-}"
        if [ -n "$EXT" ]; then
            echo "[+] Đang cài đặt PHP extension: $EXT cho PHP ${VERSION_CLEAN} / LSPHP ${LSPHP_NUM}..."
            if command -v apt-get &>/dev/null; then
                apt-get install -y "php${VERSION_CLEAN}-${EXT}" "lsphp${LSPHP_NUM}-${EXT}" 2>/dev/null || true
            elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
                PKG_MGR="dnf"
                command -v dnf &>/dev/null || PKG_MGR="yum"
                $PKG_MGR install -y "${PHP_PKG_PREFIX}-php-${EXT}" "${PHP_PKG_PREFIX}-php-pecl-${EXT}" "lsphp${LSPHP_NUM}-${EXT}" "lsphp${LSPHP_NUM}-pecl-${EXT}" 2>/dev/null || true
            fi
            systemctl restart "${PHP_PKG_PREFIX}-php-fpm" "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
            pkill -9 -f "lsphp${LSPHP_NUM}" 2>/dev/null || true
            /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lsws 2>/dev/null || true
        fi
        ;;

    remove-ext)
        EXT="${3:-}"
        if [ -n "$EXT" ]; then
            echo "[+] Đang gỡ bỏ PHP extension: $EXT cho PHP ${VERSION_CLEAN} / LSPHP ${LSPHP_NUM}..."
            if command -v apt-get &>/dev/null; then
                apt-get remove -y "php${VERSION_CLEAN}-${EXT}" "lsphp${LSPHP_NUM}-${EXT}" 2>/dev/null || true
            elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
                PKG_MGR="dnf"
                command -v dnf &>/dev/null || PKG_MGR="yum"
                $PKG_MGR remove -y "${PHP_PKG_PREFIX}-php-${EXT}" "${PHP_PKG_PREFIX}-php-pecl-${EXT}" "lsphp${LSPHP_NUM}-${EXT}" "lsphp${LSPHP_NUM}-pecl-${EXT}" 2>/dev/null || true
            fi
            systemctl restart "${PHP_PKG_PREFIX}-php-fpm" "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
            pkill -9 -f "lsphp${LSPHP_NUM}" 2>/dev/null || true
            /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lsws 2>/dev/null || true
        fi
        ;;

    pool-restart)
        systemctl restart "${PHP_PKG_PREFIX}-php-fpm" 2>/dev/null || \
        systemctl restart "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
        echo "[+] Khởi động lại PHP-FPM $VERSION_CLEAN pool thành công!"
        ;;

    check-update)
        # Kiểm tra phiên bản hiện tại và phiên bản candidate mới nhất trên repo (Remi / Ondrej)
        echo "[+] Đang kiểm tra bản cập nhật cho PHP ${VERSION_CLEAN}..."
        ensure_repos >/dev/null 2>&1 || true

        CURRENT_VER="none"
        LATEST_VER="none"
        HAS_UPDATE=0
        REPO_NAME="standard"

        if command -v apt-get &>/dev/null; then
            REPO_NAME="Ondřej PPA (ppa:ondrej/php)"
            PKG_NAME="php${VERSION_CLEAN}-fpm"
            if ! dpkg -s "$PKG_NAME" &>/dev/null; then
                PKG_NAME="php${VERSION_CLEAN}-cli"
            fi
            
            if dpkg -s "$PKG_NAME" &>/dev/null; then
                POLICY=$(apt-cache policy "$PKG_NAME" 2>/dev/null || true)
                CURRENT_VER=$(echo "$POLICY" | awk '/Installed:/ {print $2}')
                LATEST_VER=$(echo "$POLICY" | awk '/Candidate:/ {print $2}')
                if [ -n "$CURRENT_VER" ] && [ -n "$LATEST_VER" ] && [ "$CURRENT_VER" != "(none)" ] && [ "$LATEST_VER" != "(none)" ] && [ "$CURRENT_VER" != "$LATEST_VER" ]; then
                    HAS_UPDATE=1
                fi
            fi
        elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
            REPO_NAME="Remi RPM Repository"
            PKG_NAME="${PHP_PKG_PREFIX}-php-fpm"
            if ! rpm -q "$PKG_NAME" &>/dev/null; then
                PKG_NAME="${PHP_PKG_PREFIX}-php-cli"
            fi

            if rpm -q "$PKG_NAME" &>/dev/null; then
                CURRENT_VER=$(rpm -q --qf '%{VERSION}-%{RELEASE}' "$PKG_NAME" 2>/dev/null || echo "installed")
                PKG_MGR="dnf"
                command -v dnf &>/dev/null || PKG_MGR="yum"
                $PKG_MGR config-manager --set-enabled remi 2>/dev/null || true
                UPD_CHECK=$($PKG_MGR check-update "$PKG_NAME" 2>/dev/null || true)
                if echo "$UPD_CHECK" | grep -q "$PKG_NAME"; then
                    HAS_UPDATE=1
                    LATEST_VER=$(echo "$UPD_CHECK" | grep "$PKG_NAME" | awk '{print $2}' | head -n 1)
                else
                    LATEST_VER="$CURRENT_VER"
                fi
            fi
        fi

        # Lấy chính xác PHP semantic version
        SEMANTIC_VER=""
        if [ -x "/usr/bin/php${VERSION_CLEAN}" ]; then
            SEMANTIC_VER=$(/usr/bin/php${VERSION_CLEAN} -r 'echo PHP_VERSION;' 2>/dev/null || true)
        elif [ -x "/opt/remi/${PHP_PKG_PREFIX}/root/usr/bin/php" ]; then
            SEMANTIC_VER=$(/opt/remi/${PHP_PKG_PREFIX}/root/usr/bin/php -r 'echo PHP_VERSION;' 2>/dev/null || true)
        elif [ -x "/usr/local/lsws/lsphp${LSPHP_NUM}/bin/lsphp" ]; then
            SEMANTIC_VER=$(/usr/local/lsws/lsphp${LSPHP_NUM}/bin/lsphp -r 'echo PHP_VERSION;' 2>/dev/null || true)
        fi

        CURRENT_SHOW="${SEMANTIC_VER:-$CURRENT_VER}"
        LATEST_SHOW="${LATEST_VER}"
        if [ "$HAS_UPDATE" -eq 0 ] && [ "$LATEST_SHOW" = "none" -o "$LATEST_SHOW" = "$CURRENT_VER" ]; then
            LATEST_SHOW="$CURRENT_SHOW"
        fi

        echo "PHP_VERSION: $VERSION_CLEAN"
        echo "CURRENT_VERSION: $CURRENT_SHOW"
        echo "LATEST_VERSION: $LATEST_SHOW"
        echo "HAS_UPDATE: $HAS_UPDATE"
        echo "REPO_NAME: $REPO_NAME"
        echo "CHECK_RESULT_JSON: {\"has_update\": $([ "$HAS_UPDATE" -eq 1 ] && echo "true" || echo "false"), \"current_version\": \"$CURRENT_SHOW\", \"latest_version\": \"$LATEST_SHOW\", \"repo\": \"$REPO_NAME\"}"
        ;;

    update|upgrade)
        echo "[+] Đang cập nhật PHP ${VERSION_CLEAN} từ kho lưu trữ chính thức..."
        ensure_repos
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -y || true
            echo "[+] Nâng cấp các gói php${VERSION_CLEAN} và lsphp${LSPHP_NUM} qua Ondřej PPA..."
            apt-get --only-upgrade install -y "php${VERSION_CLEAN}*" "lsphp${LSPHP_NUM}*" 2>/dev/null || \
            apt-get install -y --only-upgrade "php${VERSION_CLEAN}-fpm" "php${VERSION_CLEAN}-cli" "php${VERSION_CLEAN}-common" || true
            
            systemctl restart "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
            systemctl restart "wpc-php${VERSION_CLEAN}-fpm@*" 2>/dev/null || true
            pkill -9 -f "lsphp${LSPHP_NUM}" 2>/dev/null || true
            /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lsws 2>/dev/null || true
        elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
            PKG_MGR="dnf"
            command -v dnf &>/dev/null || PKG_MGR="yum"
            $PKG_MGR config-manager --set-enabled remi 2>/dev/null || true
            echo "[+] Nâng cấp các gói ${PHP_PKG_PREFIX} và lsphp${LSPHP_NUM} từ Remi RPM repository..."
            $PKG_MGR upgrade -y "${PHP_PKG_PREFIX}*" "lsphp${LSPHP_NUM}*" 2>/dev/null || true
            
            systemctl restart "${PHP_PKG_PREFIX}-php-fpm" 2>/dev/null || true
            systemctl restart "php${VERSION_CLEAN}-fpm" 2>/dev/null || true
            systemctl restart "wpc-php${VERSION_CLEAN}-fpm@*" 2>/dev/null || true
            pkill -9 -f "lsphp${LSPHP_NUM}" 2>/dev/null || true
            /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lsws 2>/dev/null || true
        fi
        
        /opt/wpc/scripts/cgroup_attach.sh 2>/dev/null || true
        echo "[+] Đã cập nhật PHP ${VERSION_CLEAN} lên phiên bản mới nhất hoàn tất!"
        ;;

    *)
        echo "[-] Lệnh không hợp lệ: $ACTION"
        exit 1
        ;;
esac
