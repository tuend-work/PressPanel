#!/usr/bin/env bash
# ==============================================================================
# WPCloud: phpmyadmin.sh - Cài đặt, Cấu hình & Gỡ bỏ phpMyAdmin (Port 888)
# ==============================================================================
set -uo pipefail

ACTION="${1:-install}"
VERSION="${2:-latest}"
TARGET_DIR="/opt/wpc/apps/phpmyadmin"
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
        echo "[+] Đang chuẩn bị thư mục phpMyAdmin tại $TARGET_DIR..."
        mkdir -p "$TARGET_DIR"
        mkdir -p "$TARGET_DIR/tmp"
        mkdir -p "$TARGET_DIR/logs"

        # 1. Tải và giải nén phpMyAdmin nếu chưa có file index.php
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

        # 2. Sinh blowfish secret ngẫu nhiên và tạo config.inc.php
        BLOWFISH=$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32)
        [ -z "$BLOWFISH" ] && BLOWFISH="WPCloudPMASecretKey20261016Secure"

        cat <<EOF > "$TARGET_DIR/config.inc.php"
<?php
declare(strict_types=1);

\$cfg['blowfish_secret'] = '$BLOWFISH';

\$i = 0;
\$i++;
\$cfg['Servers'][\$i]['auth_type'] = 'cookie';
\$cfg['Servers'][\$i]['host'] = '127.0.0.1';
\$cfg['Servers'][\$i]['port'] = '3306';
\$cfg['Servers'][\$i]['connect_type'] = 'tcp';
\$cfg['Servers'][\$i]['compress'] = false;
\$cfg['Servers'][\$i]['AllowNoPassword'] = false;
\$cfg['Servers'][\$i]['AllowRoot'] = true;

\$cfg['UploadDir'] = '';
\$cfg['SaveDir'] = '';
\$cfg['TempDir'] = '$TARGET_DIR/tmp';
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

        # 3. Phân quyền toàn diện và gỡ bỏ các file .htaccess chặn truy cập
        rm -f "$TARGET_DIR/.htaccess" 2>/dev/null || true
        mkdir -p "$TARGET_DIR/tmp" "$TARGET_DIR/logs"
        chmod 777 "$TARGET_DIR/tmp" 2>/dev/null || true
        chmod 755 "$TARGET_DIR/logs" 2>/dev/null || true
        chmod 644 "$TARGET_DIR/config.inc.php" "$TARGET_DIR/index.php" 2>/dev/null || true

        # Đảm bảo cây thư mục cha /opt/wpc có quyền thực thi (755) cho webserver
        chmod 755 /opt /opt/wpc /opt/wpc/apps "$TARGET_DIR" 2>/dev/null || true

        # Thêm nobody và www-data vào nhóm wpc nếu cần
        if id "wpc" &>/dev/null; then
            id "nobody" &>/dev/null && usermod -a -G wpc nobody 2>/dev/null || true
            id "www-data" &>/dev/null && usermod -a -G wpc www-data 2>/dev/null || true
        fi

        find "$TARGET_DIR" -type d -exec chmod 755 {} + 2>/dev/null || true
        find "$TARGET_DIR" -type f -exec chmod 644 {} + 2>/dev/null || true
        chmod 777 "$TARGET_DIR/tmp" 2>/dev/null || true
        chmod -R a+rX "$TARGET_DIR" 2>/dev/null || true

        if [ -d "/usr/local/lsws" ]; then
            chown -R nobody:nogroup "$TARGET_DIR" 2>/dev/null || true
        else
            chown -R www-data:www-data "$TARGET_DIR" 2>/dev/null || true
        fi

        # Tạo symlink phpmyadmin để hỗ trợ cả URI / và /phpmyadmin/
        ln -snf "$TARGET_DIR" "$TARGET_DIR/phpmyadmin" 2>/dev/null || true
        ln -snf "$TARGET_DIR" "$TARGET_DIR/phpMyAdmin" 2>/dev/null || true

        # Cài đặt bổ sung các PHP module cần thiết cho phpMyAdmin
        if command -v apt-get &>/dev/null; then
            apt-get install -y lsphp82-mysql lsphp82-curl lsphp82-opcache lsphp82-mbstring lsphp82-xml php-mbstring php-xml php-mysql 2>/dev/null || true
        fi

        # 4. Mở cổng tường lửa nếu đang bật (UFW hoặc Firewalld)
        if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
            ufw allow "$PMA_PORT"/tcp 2>/dev/null || true
        fi
        if command -v firewall-cmd &>/dev/null && systemctl is-active --quiet firewalld 2>/dev/null; then
            firewall-cmd --add-port="$PMA_PORT"/tcp --permanent 2>/dev/null || true
            firewall-cmd --reload 2>/dev/null || true
        fi

        # 5. Cấu hình Vhost Port 888 trên Nginx
        if [ -d "/etc/nginx/conf.d" ]; then
            ACTIVE_PHP=$(detect_php_version)
            PHP_FPM_SOCK="/run/php/php${ACTIVE_PHP}-fpm.sock"
            if [ ! -e "$PHP_FPM_SOCK" ]; then
                PHP_FPM_SOCK=$(ls /run/php/php*-fpm.sock /var/run/php/php*-fpm.sock 2>/dev/null | head -n 1 || echo "/run/php/php8.2-fpm.sock")
            fi

            cat <<EOF > "/etc/nginx/conf.d/00_phpmyadmin.conf"
server {
    listen $PMA_PORT default_server;
    server_name localhost 127.0.0.1 _;
    root $TARGET_DIR;
    index index.php index.html index.htm;

    client_max_body_size 256M;

    location / {
        try_files \$uri \$uri/ /index.php?\$args;
    }

    location ~ \.php$ {
        try_files \$uri =404;
        fastcgi_split_path_info ^(.+\.php)(/.+)$;
        fastcgi_pass unix:$PHP_FPM_SOCK;
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
            nginx -t 2>/dev/null && (nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null || true)
        fi

        # 6. Cấu hình Vhost Port 888 trên OpenLiteSpeed
        OLS_MAIN_CONF="/usr/local/lsws/conf/httpd_config.conf"
        if [ -f "$OLS_MAIN_CONF" ]; then
            OLS_LSPHP=$(detect_ols_lsphp)
            echo "[+] Cấu hình OpenLiteSpeed cho phpMyAdmin (LSPHP: $OLS_LSPHP)..."

            mkdir -p "/usr/local/lsws/conf/vhosts/phpmyadmin"
            mkdir -p "/usr/local/lsws/logs"
            touch "/usr/local/lsws/logs/pma_error.log" "/usr/local/lsws/logs/pma_access.log" 2>/dev/null || true
            chmod 666 "/usr/local/lsws/logs/pma_error.log" "/usr/local/lsws/logs/pma_access.log" 2>/dev/null || true

            cat <<EOF > "/usr/local/lsws/conf/vhosts/phpmyadmin/vhconf.conf"
docRoot                   \$VH_ROOT/
vhDomain                  localhost
vhAliases                 127.0.0.1, *
adminEmails               admin@localhost
enableGzip                1
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

scripthandler {
  add                     lsapi:$OLS_LSPHP php
}
EOF
            chown -R lsadm:lsadm "/usr/local/lsws/conf/vhosts/phpmyadmin" 2>/dev/null || true
            chmod 755 "/usr/local/lsws/conf/vhosts/phpmyadmin" 2>/dev/null || true
            chmod 644 "/usr/local/lsws/conf/vhosts/phpmyadmin/vhconf.conf" 2>/dev/null || true

            # Làm sạch các khai báo virtualhost phpmyadmin và listener PMA_888 cũ nếu đã có
            sed -i '/virtualhost[[:space:]]\+phpmyadmin[[:space:]]*{/,/^[[:space:]]*}/d' "$OLS_MAIN_CONF" 2>/dev/null || true
            sed -i '/listener[[:space:]]\+PMA_888[[:space:]]*{/,/^[[:space:]]*}/d' "$OLS_MAIN_CONF" 2>/dev/null || true

            # Đảm bảo extprocessor cho OLS_LSPHP tồn tại trong httpd_config.conf
            if ! grep -q "extprocessor[[:space:]]\+$OLS_LSPHP" "$OLS_MAIN_CONF"; then
                cat <<EOF >> "$OLS_MAIN_CONF"

extprocessor $OLS_LSPHP {
  type                    lsapi
  address                 uds://tmp/lshttpd/$OLS_LSPHP.sock
  maxConns                35
  env                     PHP_LSAPI_CHILDREN=35
  initTimeout             60
  retryTimeout            0
  persistConn             1
  respBuffer              0
  autoStart               1
  path                    /usr/local/lsws/$OLS_LSPHP/bin/lsphp
  backlog                 100
  instances               1
  priority                0
  memSoftLimit            2047M
  memHardLimit            2047M
  procSoftLimit           400
  procHardLimit           400
}
EOF
            fi

            # Bổ sung virtualhost và listener chuẩn vào httpd_config.conf
            cat <<EOF >> "$OLS_MAIN_CONF"

virtualhost phpmyadmin {
  vhRoot                  $TARGET_DIR
  configFile              conf/vhosts/phpmyadmin/vhconf.conf
  allowSymbolLink         1
  enableScript            1
  restrained              0
}

listener PMA_888 {
  address                 *:$PMA_PORT
  secure                  0
  map                     phpmyadmin *
}
EOF
            chmod 644 "$OLS_MAIN_CONF" 2>/dev/null || true

            # Khởi động lại OpenLiteSpeed
            /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lshttpd 2>/dev/null || systemctl restart lsws 2>/dev/null || true
        fi

        # 7. Kiểm tra trạng thái HTTP cổng 888
        sleep 1
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 http://127.0.0.1:888/ 2>/dev/null || echo "000")
        echo "[+] Kiểm tra phản hồi HTTP cổng 888: Mã $HTTP_CODE"
        if [ "$HTTP_CODE" = "403" ]; then
            echo "[-] CẢNH BÁO: Vẫn nhận mã 403! Nhật ký lỗi Webserver:"
            [ -f "/usr/local/lsws/logs/error.log" ] && tail -n 15 /usr/local/lsws/logs/error.log 2>/dev/null || true
            [ -f "/var/log/nginx/error.log" ] && tail -n 15 /var/log/nginx/error.log 2>/dev/null || true
        fi

        echo "[+] Cài đặt và cấu hình phpMyAdmin thành công tại $TARGET_DIR (Port $PMA_PORT)!"
        ;;

    remove)
        echo "[+] Đang gỡ bỏ phpMyAdmin..."
        # 1. Xóa thư mục cài đặt
        rm -rf "$TARGET_DIR" 2>/dev/null || true
        rm -f "/usr/share/phpmyadmin/index.php" 2>/dev/null || true

        # 2. Xóa cấu hình Nginx
        if [ -f "/etc/nginx/conf.d/00_phpmyadmin.conf" ]; then
            rm -f "/etc/nginx/conf.d/00_phpmyadmin.conf"
            nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null || true
        fi

        # 3. Dọn dẹp cấu hình OpenLiteSpeed
        OLS_MAIN_CONF="/usr/local/lsws/conf/httpd_config.conf"
        if [ -f "$OLS_MAIN_CONF" ]; then
            sed -i '/virtualhost[[:space:]]\+phpmyadmin[[:space:]]*{/,/^[[:space:]]*}/d' "$OLS_MAIN_CONF" 2>/dev/null || true
            sed -i '/listener[[:space:]]\+PMA_888[[:space:]]*{/,/^[[:space:]]*}/d' "$OLS_MAIN_CONF" 2>/dev/null || true
            rm -rf "/usr/local/lsws/conf/vhosts/phpmyadmin" 2>/dev/null || true
            /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lshttpd 2>/dev/null || true
        fi

        echo "[+] Đã gỡ bỏ phpMyAdmin hoàn tất!"
        ;;

    *)
        echo "[-] Lệnh không hợp lệ. Sử dụng: phpmyadmin.sh <install|remove>"
        exit 1
        ;;
esac

