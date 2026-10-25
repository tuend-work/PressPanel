#!/usr/bin/env bash
# ==============================================================================
# WPCloud: phpmyadmin.sh - Cài đặt, Cấu hình & Gỡ bỏ phpMyAdmin (Port 888)
# ==============================================================================
set -uo pipefail

ACTION="${1:-install}"
VERSION="${2:-latest}"
TARGET_WS="${3:-}"
PMA_USER="pma"
PMA_HOME="/home/$PMA_USER"
TARGET_DIR="$PMA_HOME/public_html"
LEGACY_DIR="/opt/wpc/apps/phpmyadmin"
PMA_PORT="888"

detect_php_version() {
    for ver in 8.3 8.2 8.4 8.1 8.0 7.4 8.5; do
        if command -v "php$ver" &>/dev/null || [ -e "/run/php/php$ver-fpm.sock" ] || [ -e "/var/run/php/php$ver-fpm.sock" ]; then
            echo "$ver"
            return 0
        fi
    done
    echo "8.2"
}

detect_ols_lsphp() {
    local main_conf="/usr/local/lsws/conf/httpd_config.conf"
    if [ -f "$main_conf" ]; then
        local detected
        detected=$(grep -oE "extprocessor[[:space:]]+lsphp[0-9]+" "$main_conf" 2>/dev/null | awk '{print $2}' | head -n 1 || echo "")
        if [ -n "$detected" ]; then
            echo "$detected"
            return 0
        fi
    fi
    for lsp in lsphp82 lsphp83 lsphp81 lsphp84 lsphp80 lsphp74; do
        if [ -x "/usr/local/lsws/$lsp/bin/lsphp" ]; then
            echo "$lsp"
            return 0
        fi
    done
    echo "lsphp82"
}

download_pma() {
    local target_file="/tmp/pma.tar.gz"
    rm -f "$target_file"

    local urls=(
        "https://files.phpmyadmin.net/phpMyAdmin/5.2.1/phpMyAdmin-5.2.1-all-languages.tar.gz"
        "https://github.com/phpmyadmin/phpmyadmin/releases/download/RELEASE_5_2_1/phpMyAdmin-5.2.1-all-languages.tar.gz"
        "https://files.phpmyadmin.net/phpMyAdmin/5.2.0/phpMyAdmin-5.2.0-all-languages.tar.gz"
        "https://www.phpmyadmin.net/downloads/phpMyAdmin-latest-all-languages.tar.gz"
    )

    for url in "${urls[@]}"; do
        echo "[+] Đang tải phpMyAdmin từ: $url..."
        if command -v curl &>/dev/null; then
            curl -sSL -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
                 --connect-timeout 15 --max-time 180 "$url" -o "$target_file" || true
        elif command -v wget &>/dev/null; then
            wget -q --timeout=30 --user-agent="Mozilla/5.0 (X11; Linux x86_64)" "$url" -O "$target_file" || true
        fi

        # Kiểm tra tính toàn vẹn của tệp tar.gz
        if [ -s "$target_file" ] && tar -tzf "$target_file" &>/dev/null; then
            echo "[+] Tải phpMyAdmin thành công từ: $url"
            return 0
        fi
        rm -f "$target_file"
    done

    return 1
}

case "$ACTION" in
    install)
        echo "[+] Chuẩn hóa Website User và thư mục cho phpMyAdmin tại $TARGET_DIR..."

        # 1. Tạo Website User Linux 'pma' là con của account 'admin'
        groupadd -r pma 2>/dev/null || true
        if ! id "$PMA_USER" &>/dev/null; then
            useradd -m -d "$PMA_HOME" -s /usr/sbin/nologin -g pma "$PMA_USER" 2>/dev/null || true
        else
            usermod -d "$PMA_HOME" "$PMA_USER" 2>/dev/null || true
        fi

        # Gắn user pma vào group của admin (cha) và webserver
        id "admin" &>/dev/null && usermod -a -G admin "$PMA_USER" 2>/dev/null || true
        id "nobody" &>/dev/null && usermod -a -G pma nobody 2>/dev/null || true
        id "www-data" &>/dev/null && usermod -a -G pma www-data 2>/dev/null || true
        usermod -a -G www-data "$PMA_USER" 2>/dev/null || true
        usermod -a -G nobody "$PMA_USER" 2>/dev/null || true

        # 2. Khởi tạo cấu trúc thư mục website chuẩn (/home/pma/public_html)
        mkdir -p "$PMA_HOME" "$TARGET_DIR" "$TARGET_DIR/tmp" "$PMA_HOME/logs"
        chmod 750 "$PMA_HOME" 2>/dev/null || true

        # Di chuyển source cũ từ legacy path nếu có
        if [ -d "$LEGACY_DIR" ] && [ ! -L "$LEGACY_DIR" ] && [ -f "$LEGACY_DIR/index.php" ] && [ ! -f "$TARGET_DIR/index.php" ]; then
            echo "[+] Chuyển mã nguồn phpMyAdmin từ $LEGACY_DIR sang $TARGET_DIR..."
            cp -rn "$LEGACY_DIR"/* "$TARGET_DIR/" 2>/dev/null || true
            rm -rf "$LEGACY_DIR" 2>/dev/null || true
        fi

        # Tạo symlink tương thích ngược: /opt/wpc/apps/phpmyadmin -> /home/pma/public_html
        mkdir -p /opt/wpc/apps 2>/dev/null || true
        ln -sfn "$TARGET_DIR" "$LEGACY_DIR" 2>/dev/null || true

        # 3. Tải và giải nén phpMyAdmin nếu chưa có file index.php
        if [ ! -f "$TARGET_DIR/index.php" ]; then
            if download_pma; then
                echo "[+] Đang giải nén phpMyAdmin..."
                tar -xzf /tmp/pma.tar.gz --strip-components=1 -C "$TARGET_DIR" 2>/dev/null || true
                rm -f /tmp/pma.tar.gz
            fi
        fi

        # Fallback nếu vẫn chưa có index.php
        if [ ! -f "$TARGET_DIR/index.php" ]; then
            if [ -d "/usr/share/phpmyadmin" ] && [ -f "/usr/share/phpmyadmin/index.php" ]; then
                echo "[+] Đồng bộ phpMyAdmin từ /usr/share/phpmyadmin..."
                cp -rn /usr/share/phpmyadmin/* "$TARGET_DIR/" 2>/dev/null || true
            else
                echo "[+] Cài đặt phpMyAdmin qua gói apt-get hệ thống..."
                if command -v apt-get &>/dev/null; then
                    echo "phpmyadmin phpmyadmin/dbconfig-install boolean false" | debconf-set-selections 2>/dev/null || true
                    echo "phpmyadmin phpmyadmin/reconfigure-webserver multiselect none" | debconf-set-selections 2>/dev/null || true
                    DEBIAN_FRONTEND=noninteractive apt-get update -y || true
                    DEBIAN_FRONTEND=noninteractive apt-get install -y phpmyadmin || true
                    if [ -d "/usr/share/phpmyadmin" ]; then
                        cp -rn /usr/share/phpmyadmin/* "$TARGET_DIR/" 2>/dev/null || true
                    fi
                fi
            fi
        fi

        # Kiểm tra xác nhận file index.php tồn tại
        if [ ! -f "$TARGET_DIR/index.php" ]; then
            echo "[-] CẢNH BÁO: Chưa tìm thấy $TARGET_DIR/index.php! Vui lòng kiểm tra lại kết nối mạng."
        else
            echo "[+] Xác nhận $TARGET_DIR/index.php tồn tại hợp lệ."
        fi

        # 4. Sinh blowfish secret ngẫu nhiên và tạo config.inc.php
        BLOWFISH=$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32)
        [ -z "$BLOWFISH" ] && BLOWFISH="WPCloudPMASecretKey20261016Secure"

        cat <<EOF > "$TARGET_DIR/config.inc.php"
<?php
declare(strict_types=1);

// Đảm bảo session save path và upload tmp dir luôn có quyền ghi tránh lỗi HTTP 500
\$tmpDir = __DIR__ . '/tmp';
if (!is_dir(\$tmpDir)) {
    @mkdir(\$tmpDir, 0777, true);
}
@chmod(\$tmpDir, 0777);
@ini_set('session.save_path', \$tmpDir);
@ini_set('upload_tmp_dir', \$tmpDir);
@ini_set('display_errors', '0');
@ini_set('log_errors', '1');
if (!is_dir(__DIR__ . '/logs')) {
    @mkdir(__DIR__ . '/logs', 0755, true);
}
@ini_set('error_log', __DIR__ . '/logs/php_error.log');

\$cfg['blowfish_secret'] = '$BLOWFISH';

\$i = 0;
\$i++;
\$cfg['Servers'][\$i]['auth_type'] = 'cookie';
\$cfg['Servers'][\$i]['host'] = '127.0.0.1';
\$cfg['Servers'][\$i]['port'] = '3306';
\$cfg['Servers'][\$i]['connect_type'] = 'tcp';
\$cfg['Servers'][\$i]['extension'] = 'mysqli';
\$cfg['Servers'][\$i]['compress'] = false;
\$cfg['Servers'][\$i]['AllowNoPassword'] = false;
\$cfg['Servers'][\$i]['AllowRoot'] = true;

\$cfg['UploadDir'] = '';
\$cfg['SaveDir'] = '';
\$cfg['TempDir'] = \$tmpDir;
\$cfg['SendErrorReports'] = 'never';
\$cfg['ShowPhpInfo'] = true;
\$cfg['MaxRows'] = 50;
EOF

        # Nếu index.php vẫn chưa có, tạo trang chẩn đoán tạm thời để tránh 403 Directory Index Forbidden
        if [ ! -f "$TARGET_DIR/index.php" ]; then
            echo "[-] Tạo file index.php chẩn đoán tạm thời..."
            cat << 'EOF' > "$TARGET_DIR/index.php"
<?php
echo "<div style='font-family:sans-serif;text-align:center;padding:50px;'>";
echo "<h2>🐬 WPCloud phpMyAdmin</h2>";
echo "<p>phpMyAdmin đang đồng bộ. Vui lòng bấm <b>🔄 Cài lại</b> trên giao diện hoặc kiểm tra kết nối mạng máy chủ.</p>";
echo "</div>";
EOF
        fi

        # 5. Phân quyền toàn diện chuẩn Website User
        rm -f "$TARGET_DIR/.htaccess" 2>/dev/null || true
        mkdir -p "$TARGET_DIR/tmp" "$PMA_HOME/.sessions" "$PMA_HOME/.tmp" "$PMA_HOME/logs"
        chmod 1777 "$TARGET_DIR/tmp" "$PMA_HOME/.sessions" "$PMA_HOME/.tmp" 2>/dev/null || true
        chmod 755 "$PMA_HOME/logs" 2>/dev/null || true
        chmod 644 "$TARGET_DIR/config.inc.php" "$TARGET_DIR/index.php" 2>/dev/null || true

        chmod 711 /home 2>/dev/null || true
        chmod 755 "$PMA_HOME" "$TARGET_DIR" 2>/dev/null || true
        chown -R pma:pma "$PMA_HOME" 2>/dev/null || true
        find "$TARGET_DIR" -type d -exec chmod 755 {} + 2>/dev/null || true
        find "$TARGET_DIR" -type f -exec chmod 644 {} + 2>/dev/null || true
        chmod 1777 "$TARGET_DIR/tmp" "$PMA_HOME/.sessions" "$PMA_HOME/.tmp" 2>/dev/null || true

        # Thêm nobody (OLS) và www-data (Nginx) vào group pma để truy cập static files
        id "nobody" &>/dev/null && usermod -a -G pma nobody 2>/dev/null || true
        id "www-data" &>/dev/null && usermod -a -G pma www-data 2>/dev/null || true
        usermod -a -G www-data "$PMA_USER" 2>/dev/null || true
        usermod -a -G nobody "$PMA_USER" 2>/dev/null || true

        # Tạo symlink phpmyadmin để hỗ trợ cả URI / và /phpmyadmin/
        ln -snf "$TARGET_DIR" "$TARGET_DIR/phpmyadmin" 2>/dev/null || true
        ln -snf "$TARGET_DIR" "$TARGET_DIR/phpMyAdmin" 2>/dev/null || true

        # Cài đặt bổ sung module PHP cần thiết (mysqli, mbstring) cho cả PHP-FPM và OpenLiteSpeed LSPHP
        ACTIVE_PHP=$(detect_php_version)
        OLS_LSPHP=$(detect_ols_lsphp)
        echo "[+] Đảm bảo các module PHP thiết yếu (mysqli, mbstring) cho PHP $ACTIVE_PHP và OLS $OLS_LSPHP..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get install -y "php${ACTIVE_PHP}-mysql" "php${ACTIVE_PHP}-mbstring" "php${ACTIVE_PHP}-xml" "php${ACTIVE_PHP}-curl" "php${ACTIVE_PHP}-zip" 2>/dev/null || true
            command -v phpenmod &>/dev/null && phpenmod -v "$ACTIVE_PHP" mysqlnd mysqli pdo_mysql mbstring xml curl zip 2>/dev/null || true
            if [ -d "/usr/local/lsws" ]; then
                for lsp_name in "$OLS_LSPHP" lsphp83 lsphp82 lsphp81 lsphp84; do
                    if [ -d "/usr/local/lsws/$lsp_name" ] || [ "$lsp_name" = "$OLS_LSPHP" ]; then
                        apt-get install -y "${lsp_name}-mysql" "${lsp_name}-mbstring" "${lsp_name}-curl" "${lsp_name}-zip" "${lsp_name}-xml" "${lsp_name}-json" 2>/dev/null || true
                    fi
                done
            fi
        elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
            PKG_MGR="dnf"
            command -v dnf &>/dev/null || PKG_MGR="yum"
            $PKG_MGR install -y php-mysqlnd php-mysqli php-mbstring php-xml php-curl php-zip 2>/dev/null || true
            if [ -d "/usr/local/lsws" ]; then
                for lsp_name in "$OLS_LSPHP" lsphp83 lsphp82 lsphp81 lsphp84; do
                    if [ -d "/usr/local/lsws/$lsp_name" ] || [ "$lsp_name" = "$OLS_LSPHP" ]; then
                        $PKG_MGR install -y "${lsp_name}-mysqlnd" "${lsp_name}-mbstring" "${lsp_name}-curl" "${lsp_name}-zip" "${lsp_name}-xml" 2>/dev/null || true
                    fi
                done
            fi
        fi

        # 6. Mở cổng tường lửa nếu đang bật (UFW hoặc Firewalld)
        if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
            ufw allow "$PMA_PORT"/tcp 2>/dev/null || true
        fi
        if command -v firewall-cmd &>/dev/null && systemctl is-active --quiet firewalld 2>/dev/null; then
            firewall-cmd --add-port="$PMA_PORT"/tcp --permanent 2>/dev/null || true
            firewall-cmd --reload 2>/dev/null || true
        fi

        # 7. Cấu hình Cgroups v2 phân cấp cho user 'pma' (con của admin)
        echo "[+] Khởi tạo cgroup v2 cho user pma..."
        mkdir -p /sys/fs/cgroup/wpc/user_admin/site_pma 2>/dev/null || true
        mkdir -p /sys/fs/cgroup/wpc/user_pma 2>/dev/null || true
        echo "50000 100000" > /sys/fs/cgroup/wpc/user_admin/site_pma/cpu.max 2>/dev/null || true
        echo "536870912" > /sys/fs/cgroup/wpc/user_admin/site_pma/memory.max 2>/dev/null || true
        echo "50000 100000" > /sys/fs/cgroup/wpc/user_pma/cpu.max 2>/dev/null || true
        echo "536870912" > /sys/fs/cgroup/wpc/user_pma/memory.max 2>/dev/null || true

        # 8. Phát hiện Web Server đang hoạt động thực tế (Nginx hoặc OpenLiteSpeed)
        ACTIVE_WS="nginx"
        if [ "$TARGET_WS" = "ols" ] || [ "$TARGET_WS" = "openlitespeed" ] || [ "$TARGET_WS" = "lsws" ]; then
            ACTIVE_WS="ols"
        elif [ "$TARGET_WS" = "nginx" ]; then
            ACTIVE_WS="nginx"
        elif command -v systemctl &>/dev/null; then
            if systemctl is-active --quiet lshttpd 2>/dev/null || systemctl is-active --quiet lsws 2>/dev/null; then
                ACTIVE_WS="ols"
            elif systemctl is-active --quiet nginx 2>/dev/null || systemctl is-active --quiet openresty 2>/dev/null; then
                ACTIVE_WS="nginx"
            elif pgrep -f "litespeed|openlitespeed|lshttpd" &>/dev/null; then
                ACTIVE_WS="ols"
            elif pgrep -x nginx &>/dev/null; then
                ACTIVE_WS="nginx"
            elif [ -d "/usr/local/lsws" ] && [ -f "/usr/local/lsws/conf/httpd_config.conf" ] && ! systemctl is-active --quiet nginx 2>/dev/null; then
                ACTIVE_WS="ols"
            elif [ -d "/etc/nginx/conf.d" ]; then
                ACTIVE_WS="nginx"
            elif [ -d "/usr/local/lsws" ]; then
                ACTIVE_WS="ols"
            fi
        else
            if pgrep -f "litespeed|openlitespeed|lshttpd" &>/dev/null; then
                ACTIVE_WS="ols"
            elif pgrep -x nginx &>/dev/null; then
                ACTIVE_WS="nginx"
            elif [ -d "/usr/local/lsws" ]; then
                ACTIVE_WS="ols"
            elif [ -d "/etc/nginx/conf.d" ]; then
                ACTIVE_WS="nginx"
            fi
        fi

        echo "[+] Phát hiện Web Server hoạt động thực tế: $ACTIVE_WS"
        OLS_MAIN_CONF="/usr/local/lsws/conf/httpd_config.conf"

        if [ "$ACTIVE_WS" = "nginx" ]; then
            # Nếu chuyển sang Nginx: dọn dẹp listener OLS PMA_888 để tránh xung đột cổng
            if [ -f "$OLS_MAIN_CONF" ]; then
                sed -i '/virtualhost[[:space:]]\+phpmyadmin[[:space:]]*{/,/^[[:space:]]*}/d' "$OLS_MAIN_CONF" 2>/dev/null || true
                sed -i '/listener[[:space:]]\+PMA_888[[:space:]]*{/,/^[[:space:]]*}/d' "$OLS_MAIN_CONF" 2>/dev/null || true
                rm -rf "/usr/local/lsws/conf/vhosts/phpmyadmin" 2>/dev/null || true
                /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lshttpd 2>/dev/null || systemctl restart lsws 2>/dev/null || true
            fi
            fuser -k "${PMA_PORT}/tcp" 2>/dev/null || true

            mkdir -p /etc/nginx/conf.d /etc/nginx/sites-available /etc/nginx/sites-enabled
            ACTIVE_PHP=$(detect_php_version)
            WPC_BASE="/opt/wpc"
            [ ! -d "$WPC_BASE" ] && WPC_BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

            # Tạo Systemd Service PHP-FPM riêng cho user pma
            if [ -f "$WPC_BASE/scripts/php_fpm_service.sh" ]; then
                bash "$WPC_BASE/scripts/php_fpm_service.sh" create pma "$ACTIVE_PHP" 2>/dev/null || true
            fi

            # Đảm bảo conf.d riêng của pma có đầy đủ 10-mysqlnd.ini và 20-mysqli.ini
            PMA_CONF_D="/etc/php/${ACTIVE_PHP}/fpm/users/pma/conf.d"
            mkdir -p "$PMA_CONF_D" 2>/dev/null || true
            rm -f "$PMA_CONF_D/mysqli.ini" "$PMA_CONF_D/mysqlnd.ini" "$PMA_CONF_D/pdo_mysql.ini" 2>/dev/null || true
            if [ -d "/etc/php/${ACTIVE_PHP}/fpm/conf.d" ]; then
                for inifile in "/etc/php/${ACTIVE_PHP}/fpm/conf.d"/*.ini; do
                    [ -f "$inifile" ] || continue
                    ln -sf "$inifile" "$PMA_CONF_D/$(basename "$inifile")" 2>/dev/null || true
                done
            fi
            for mdir in "/etc/php/${ACTIVE_PHP}/fpm/conf.d" "/etc/php/${ACTIVE_PHP}/mods-available" "/etc/php/${ACTIVE_PHP}/cli/conf.d"; do
                [ -f "$mdir/mysqlnd.ini" ] && ln -sf "$mdir/mysqlnd.ini" "$PMA_CONF_D/10-mysqlnd.ini" 2>/dev/null || true
                [ -f "$mdir/10-mysqlnd.ini" ] && ln -sf "$mdir/10-mysqlnd.ini" "$PMA_CONF_D/10-mysqlnd.ini" 2>/dev/null || true
                [ -f "$mdir/mysqli.ini" ] && ln -sf "$mdir/mysqli.ini" "$PMA_CONF_D/20-mysqli.ini" 2>/dev/null || true
                [ -f "$mdir/20-mysqli.ini" ] && ln -sf "$mdir/20-mysqli.ini" "$PMA_CONF_D/20-mysqli.ini" 2>/dev/null || true
                [ -f "$mdir/pdo_mysql.ini" ] && ln -sf "$mdir/pdo_mysql.ini" "$PMA_CONF_D/20-pdo_mysql.ini" 2>/dev/null || true
                [ -f "$mdir/20-pdo_mysql.ini" ] && ln -sf "$mdir/20-pdo_mysql.ini" "$PMA_CONF_D/20-pdo_mysql.ini" 2>/dev/null || true
                [ -f "$mdir/mbstring.ini" ] && ln -sf "$mdir/mbstring.ini" "$PMA_CONF_D/20-mbstring.ini" 2>/dev/null || true
            done
            systemctl restart "wpc-php${ACTIVE_PHP}-fpm@pma" 2>/dev/null || true

            PMA_FPM_SOCK="/run/php/php${ACTIVE_PHP}-fpm-pma.sock"
            if [ ! -e "$PMA_FPM_SOCK" ]; then
                PMA_FPM_SOCK=$(ls /run/php/php*-fpm-pma.sock 2>/dev/null | head -n 1 || echo "/run/php/php${ACTIVE_PHP}-fpm.sock")
            fi
            echo "[+] Khởi tạo PHP-FPM riêng cho pma trên Nginx (Service: wpc-php${ACTIVE_PHP}-fpm@pma, Sock: $PMA_FPM_SOCK)"

            # Đảm bảo quyền truy cập cho webserver vào thư mục /home/pma
            chmod 711 /home 2>/dev/null || true
            chmod 755 "$PMA_HOME" "$TARGET_DIR" 2>/dev/null || true
            mkdir -p "$TARGET_DIR/tmp" "$PMA_HOME/.sessions" "$PMA_HOME/.tmp" "$PMA_HOME/logs" 2>/dev/null || true
            chown -R pma:pma "$PMA_HOME" 2>/dev/null || true
            chmod 1777 "$TARGET_DIR/tmp" "$PMA_HOME/.sessions" "$PMA_HOME/.tmp" 2>/dev/null || true

            cat <<EOF > "/etc/nginx/conf.d/00_phpmyadmin.conf"
server {
    listen $PMA_PORT;
    server_name localhost 127.0.0.1 _;
    root $TARGET_DIR;
    index index.php index.html index.htm;

    client_max_body_size 256M;

    location / {
        try_files \$uri \$uri/ /index.php?\$args;
    }

    location ~ \.php\$ {
        try_files \$uri =404;
        fastcgi_split_path_info ^(.+\.php)(/.+)\$;
        fastcgi_pass unix:$PMA_FPM_SOCK;
        fastcgi_index index.php;
        fastcgi_param SCRIPT_FILENAME \$document_root\$fastcgi_script_name;
        include fastcgi_params;
        fastcgi_read_timeout 600;
        fastcgi_buffer_size 128k;
        fastcgi_buffers 256 16k;
    }

    location ~ /\.ht {
        deny all;
    }
}
EOF
            ln -sf "/etc/nginx/conf.d/00_phpmyadmin.conf" "/etc/nginx/sites-enabled/00_phpmyadmin.conf" 2>/dev/null || true

            # Khởi động lại Nginx để bind cổng mới 888
            if nginx -t 2>/dev/null; then
                systemctl restart nginx 2>/dev/null || systemctl reload nginx 2>/dev/null || nginx -s reload 2>/dev/null || true
                echo "[+] Nginx đã nạp cấu hình phpMyAdmin (Port $PMA_PORT) thành công!"
            else
                echo "[-] Cảnh báo: Nginx kiểm tra cấu hình thất bại:"
                nginx -t || true
            fi

        elif [ -d "/usr/local/lsws" ] && [ -f "$OLS_MAIN_CONF" ]; then
            # Tránh Nginx chiếm cổng 888 gây xung đột cổng
            if [ -f "/etc/nginx/conf.d/00_phpmyadmin.conf" ] || [ -f "/etc/nginx/sites-enabled/00_phpmyadmin.conf" ]; then
                rm -f "/etc/nginx/conf.d/00_phpmyadmin.conf" "/etc/nginx/sites-enabled/00_phpmyadmin.conf" 2>/dev/null || true
                if command -v nginx &>/dev/null && (systemctl is-active --quiet nginx 2>/dev/null || pgrep -x nginx &>/dev/null); then
                    nginx -t 2>/dev/null && nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null || true
                fi
            fi
            # Giải phóng cổng 888 nếu bị process khác chiếm giữ
            fuser -k "${PMA_PORT}/tcp" 2>/dev/null || true

            OLS_LSPHP=$(detect_ols_lsphp)
            OLS_LSPHP_BIN="/usr/local/lsws/$OLS_LSPHP/bin/lsphp"
            if [ ! -x "$OLS_LSPHP_BIN" ]; then
                OLS_LSPHP_BIN=$(find /usr/local/lsws/ -name "lsphp" -type f -executable 2>/dev/null | head -n 1 || echo "/usr/local/lsws/lsphp82/bin/lsphp")
            fi
            echo "[+] Cấu hình OpenLiteSpeed cho phpMyAdmin (LSPHP: $OLS_LSPHP, Bin: $OLS_LSPHP_BIN, User: pma)..."

            mkdir -p "/usr/local/lsws/conf/vhosts/phpmyadmin"
            mkdir -p "/usr/local/lsws/logs"
            mkdir -p "/tmp/lshttpd"
            chmod 1777 /tmp/lshttpd 2>/dev/null || true
            touch "/usr/local/lsws/logs/pma_error.log" "/usr/local/lsws/logs/pma_access.log" 2>/dev/null || true
            chmod 666 "/usr/local/lsws/logs/pma_error.log" "/usr/local/lsws/logs/pma_access.log" 2>/dev/null || true

            cat <<EOF > "/usr/local/lsws/conf/vhosts/phpmyadmin/vhconf.conf"
docRoot                   \$VH_ROOT/public_html
vhDomain                  localhost
vhAliases                 127.0.0.1, *
adminEmails               admin@localhost
enableGzip                1
enableBr                  1
indexFiles                index.php, index.html

accessControl {
  allow                   *
}

errorlog \$SERVER_ROOT/logs/pma_error.log {
  useServer               0
  logLevel                DEBUG
  rollingSize             10M
}

accesslog \$SERVER_ROOT/logs/pma_access.log {
  useServer               0
  rollingSize             10M
  keepDays                30
}

context / {
  type                    null
  location                \$DOC_ROOT/
  allow                   *
  addDefaultCharset       off
}

rewrite  {
  enable                  1
  autoLoadHtaccess        1
}

extprocessor pma_lsphp {
  type                    lsapi
  address                 uds://tmp/lshttpd/pma_lsphp.sock
  maxConns                20
  env                     LSAPI_CHILDREN=20
  initTimeout             60
  retryTimeout            0
  persistConn             1
  respBuffer              0
  autoStart               1
  path                    $OLS_LSPHP_BIN
  backlog                 100
  instances               1
  runOn                   1
  priority                0
  memSoftLimit            1024M
  memHardLimit            2048M
  procSoftLimit           200
  procHardLimit           300
  extUser                 pma
  extGroup                pma
}

scripthandler {
  add                     lsapi:pma_lsphp php
}

phpIniOverride {
  php_value session.save_path "$TARGET_DIR/tmp"
  php_value upload_tmp_dir "$TARGET_DIR/tmp"
  php_value memory_limit "512M"
  php_value upload_max_filesize "256M"
  php_value post_max_size "256M"
  php_value max_execution_time 300
}
EOF
            chown -R lsadm:lsadm "/usr/local/lsws/conf/vhosts/phpmyadmin" 2>/dev/null || true
            chmod 755 "/usr/local/lsws/conf/vhosts/phpmyadmin" 2>/dev/null || true
            chmod 644 "/usr/local/lsws/conf/vhosts/phpmyadmin/vhconf.conf" 2>/dev/null || true

            # Làm sạch các khai báo virtualhost phpmyadmin và listener PMA_888 cũ nếu đã có
            sed -i '/virtualhost[[:space:]]\+phpmyadmin[[:space:]]*{/,/^[[:space:]]*}/d' "$OLS_MAIN_CONF" 2>/dev/null || true
            sed -i '/listener[[:space:]]\+PMA_888[[:space:]]*{/,/^[[:space:]]*}/d' "$OLS_MAIN_CONF" 2>/dev/null || true

            # Bổ sung virtualhost và listener chuẩn vào httpd_config.conf
            cat <<EOF >> "$OLS_MAIN_CONF"

virtualhost phpmyadmin {
  vhRoot                  $PMA_HOME
  configFile              conf/vhosts/phpmyadmin/vhconf.conf
  allowSymbolLink         2
  enableScript            1
  restrained              0
  setUIDMode              2
}

listener PMA_888 {
  address                 *:$PMA_PORT
  secure                  0
  map                     phpmyadmin *
}
EOF
            chmod 644 "$OLS_MAIN_CONF" 2>/dev/null || true

            # Xoá socket cũ, dừng process cũ và restart OpenLiteSpeed
            rm -f "/tmp/lshttpd/pma_lsphp.sock" 2>/dev/null || true
            pkill -9 -f "pma_lsphp" 2>/dev/null || true
            /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lshttpd 2>/dev/null || systemctl restart lsws 2>/dev/null || true
            echo "[+] OpenLiteSpeed đã nạp cấu hình phpMyAdmin (Port $PMA_PORT, LSPHP: $OLS_LSPHP) thành công!"
        fi

        # 9. Gắn tiến trình pma vào Cgroups v2
        if [ -f "/opt/wpc/scripts/cgroup_attach.sh" ]; then
            /bin/bash /opt/wpc/scripts/cgroup_attach.sh once 2>/dev/null || true
        fi

        # 10. Đăng ký thông tin Website App hệ thống vào cơ sở dữ liệu panel.db (nếu có python)
        if command -v python3 &>/dev/null; then
            python3 - "$ACTIVE_WS" << 'PY_REGISTER' 2>/dev/null || true
import sqlite3, os, sys
active_ws = sys.argv[1] if len(sys.argv) > 1 else "nginx"
candidates = ["/opt/wpc/data/panel.db", "/opt/wpc/panel.db", os.path.expanduser("~/PressPanel/panel.db")]
db_path = None
for c in candidates:
    if os.path.isfile(c):
        db_path = c
        break
if db_path:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # Tìm admin user_id
    cur.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1")
    admin_row = cur.fetchone()
    admin_id = admin_row[0] if admin_row else 1
    
    cur.execute("SELECT id FROM domains WHERE domain_name IN ('phpmyadmin.sys', 'phpmyadmin')")
    existing = cur.fetchone()
    if existing:
        cur.execute("""
            UPDATE domains 
            SET document_root = '/home/pma/public_html', webserver = ?, is_system = 1, status = 'active'
            WHERE id = ?
        """, (active_ws, existing[0]))
    else:
        cur.execute("""
            INSERT INTO domains (user_id, domain_name, document_root, webserver, php_version, is_system, status)
            VALUES (?, 'phpmyadmin.sys', '/home/pma/public_html', ?, '8.2', 1, 'active')
        """, (admin_id, active_ws))
    conn.commit()
    conn.close()
PY_REGISTER
        fi

        # 11. Kiểm tra trạng thái HTTP cổng 888
        sleep 1
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 http://127.0.0.1:888/ 2>/dev/null || echo "000")
        echo "[+] Kiểm tra phản hồi HTTP cổng 888: Mã $HTTP_CODE"
        if [ "$HTTP_CODE" = "403" ]; then
            echo "[-] CẢNH BÁO: Vẫn nhận mã 403! Nhật ký lỗi Webserver:"
            [ -f "/usr/local/lsws/logs/error.log" ] && tail -n 15 /usr/local/lsws/logs/error.log 2>/dev/null || true
            [ -f "/var/log/nginx/error.log" ] && tail -n 15 /var/log/nginx/error.log 2>/dev/null || true
        fi

        echo "[+] Cài đặt và cấu hình phpMyAdmin thành công cho Website User 'pma' tại $TARGET_DIR (Port $PMA_PORT)!"
        ;;

    start|stop|restart|reload)
        echo "[+] Đang thực hiện $ACTION dịch vụ phpMyAdmin..."
        # 1. Điều khiển PHP-FPM Service riêng nếu chạy Nginx
        ACTIVE_PHP=$(detect_php_version)
        for pver in 8.5 8.4 8.3 8.2 8.1 8.0 7.4; do
            systemctl "$ACTION" "wpc-php${pver}-fpm@pma" 2>/dev/null || true
            systemctl "$ACTION" "wpc-php${pver}-fpm@phpmyadmin" 2>/dev/null || true
        done
        # 2. Điều khiển OpenLiteSpeed LSPHP nếu chạy OpenLiteSpeed
        if [ -d "/usr/local/lsws" ]; then
            pkill -9 -f "pma_lsphp" 2>/dev/null || true
            /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lshttpd 2>/dev/null || systemctl restart lsws 2>/dev/null || true
        fi
        # 3. Nạp lại / Khởi động lại Nginx
        if [ -f "/etc/nginx/conf.d/00_phpmyadmin.conf" ]; then
            systemctl restart nginx 2>/dev/null || nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null || true
        fi
        echo "[+] Thao tác $ACTION dịch vụ phpMyAdmin hoàn tất!"
        ;;

    status)
        ACTIVE_PHP=$(detect_php_version)
        PMA_SVC="wpc-php${ACTIVE_PHP}-fpm@pma"
        if systemctl is-active --quiet "$PMA_SVC" 2>/dev/null || systemctl is-active --quiet "wpc-php${ACTIVE_PHP}-fpm@phpmyadmin" 2>/dev/null; then
            echo "active (Nginx PHP-FPM: $PMA_SVC)"
        elif [ -d "/usr/local/lsws" ] && (systemctl is-active --quiet lsws 2>/dev/null || systemctl is-active --quiet lshttpd 2>/dev/null); then
            echo "active (OpenLiteSpeed LSPHP: $(detect_ols_lsphp))"
        else
            echo "inactive"
        fi
        ;;

    remove)
        echo "[+] Đang gỡ bỏ phpMyAdmin..."
        # 1. Dừng và xoá PHP-FPM Service riêng
        for pver in 8.5 8.4 8.3 8.2 8.1 8.0 7.4; do
            systemctl stop "wpc-php${pver}-fpm@pma" 2>/dev/null || true
            systemctl disable "wpc-php${pver}-fpm@pma" 2>/dev/null || true
            systemctl stop "wpc-php${pver}-fpm@phpmyadmin" 2>/dev/null || true
            systemctl disable "wpc-php${pver}-fpm@phpmyadmin" 2>/dev/null || true
            rm -f "/etc/php/${pver}/fpm/users/pma.conf" "/run/php/php${pver}-fpm-pma.sock" "/run/php/php${pver}-fpm-pma.pid" 2>/dev/null || true
            rm -f "/etc/php/${pver}/fpm/users/phpmyadmin.conf" "/run/php/php${pver}-fpm-phpmyadmin.sock" "/run/php/php${pver}-fpm-phpmyadmin.pid" 2>/dev/null || true
        done

        # 2. Xóa thư mục cài đặt & symlink
        rm -rf "$TARGET_DIR" 2>/dev/null || true
        [ -L "$LEGACY_DIR" ] && rm -f "$LEGACY_DIR" 2>/dev/null || true
        rm -f "/usr/share/phpmyadmin/index.php" 2>/dev/null || true

        # 3. Xóa cấu hình Nginx
        if [ -f "/etc/nginx/conf.d/00_phpmyadmin.conf" ]; then
            rm -f "/etc/nginx/conf.d/00_phpmyadmin.conf"
            nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null || true
        fi

        # 4. Dọn dẹp cấu hình OpenLiteSpeed
        OLS_MAIN_CONF="/usr/local/lsws/conf/httpd_config.conf"
        if [ -f "$OLS_MAIN_CONF" ]; then
            sed -i '/virtualhost[[:space:]]\+phpmyadmin[[:space:]]*{/,/^[[:space:]]*}/d' "$OLS_MAIN_CONF" 2>/dev/null || true
            sed -i '/listener[[:space:]]\+PMA_888[[:space:]]*{/,/^[[:space:]]*}/d' "$OLS_MAIN_CONF" 2>/dev/null || true
            rm -rf "/usr/local/lsws/conf/vhosts/phpmyadmin" 2>/dev/null || true
            /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lshttpd 2>/dev/null || true
        fi

        # 5. Cập nhật cơ sở dữ liệu panel.db
        if command -v python3 &>/dev/null; then
            python3 - << 'PY_UNREGISTER' 2>/dev/null || true
import sqlite3, os
candidates = ["/opt/wpc/data/panel.db", "/opt/wpc/panel.db", os.path.expanduser("~/PressPanel/panel.db")]
for c in candidates:
    if os.path.isfile(c):
        try:
            conn = sqlite3.connect(c)
            cur = conn.cursor()
            cur.execute("DELETE FROM domains WHERE domain_name IN ('phpmyadmin.sys', 'phpmyadmin')")
            conn.commit()
            conn.close()
        except Exception:
            pass
PY_UNREGISTER
        fi

        echo "[+] Đã gỡ bỏ phpMyAdmin hoàn tất!"
        ;;

    *)
        echo "[-] Lệnh không hợp lệ. Sử dụng: phpmyadmin.sh <install|remove|start|stop|restart|reload|status>"
        exit 1
        ;;
esac

