#!/usr/bin/env bash
# ==============================================================================
# WPCloud: phpmyadmin.sh - Cài đặt, Cấu hình & Gỡ bỏ phpMyAdmin
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

detect_webserver() {
    if systemctl is-active --quiet nginx 2>/dev/null || command -v nginx &>/dev/null; then
        echo "nginx"
    elif systemctl is-active --quiet lshttpd 2>/dev/null || systemctl is-active --quiet lsws 2>/dev/null || [ -d "/usr/local/lsws" ]; then
        echo "ols"
    else
        echo "nginx"
    fi
}

case "$ACTION" in
    install)
        echo "[+] Đang tải và cấu hình phpMyAdmin..."
        mkdir -p "$TARGET_DIR"
        mkdir -p "$TARGET_DIR/tmp"

        # 1. Tải và giải nén phpMyAdmin mới nhất
        cd /tmp
        rm -f pma.tar.gz
        if command -v wget &>/dev/null; then
            wget -q --timeout=30 https://www.phpmyadmin.net/downloads/phpMyAdmin-latest-all-languages.tar.gz -O pma.tar.gz || true
        elif command -v curl &>/dev/null; then
            curl -sSL --connect-timeout 30 https://www.phpmyadmin.net/downloads/phpMyAdmin-latest-all-languages.tar.gz -o pma.tar.gz || true
        fi

        if [ -s pma.tar.gz ]; then
            tar -xzf pma.tar.gz --strip-components=1 -C "$TARGET_DIR"
            rm -f pma.tar.gz
        fi

        # Fallback nếu tải trực tiếp lỗi hoặc đã có sẵn
        if [ ! -f "$TARGET_DIR/index.php" ]; then
            if [ -d "/usr/share/phpmyadmin" ] && [ -f "/usr/share/phpmyadmin/index.php" ]; then
                echo "[+] Phát hiện phpmyadmin từ hệ điều hành /usr/share/phpmyadmin, đồng bộ..."
                cp -rn /usr/share/phpmyadmin/* "$TARGET_DIR/" 2>/dev/null || true
            else
                echo "[+] Thử cài phpmyadmin qua gói hệ thống (apt-get)..."
                if command -v apt-get &>/dev/null; then
                    DEBIAN_FRONTEND=noninteractive apt-get update -y || true
                    DEBIAN_FRONTEND=noninteractive apt-get install -y phpmyadmin || true
                    if [ -d "/usr/share/phpmyadmin" ]; then
                        cp -rn /usr/share/phpmyadmin/* "$TARGET_DIR/" 2>/dev/null || true
                    fi
                fi
            fi
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
\$cfg['Servers'][\$i]['compress'] = false;
\$cfg['Servers'][\$i]['AllowNoPassword'] = false;

\$cfg['UploadDir'] = '';
\$cfg['SaveDir'] = '';
\$cfg['TempDir'] = '/tmp';
\$cfg['SendErrorReports'] = 'never';
\$cfg['ShowPhpInfo'] = true;
\$cfg['MaxRows'] = 50;
EOF

        chmod 644 "$TARGET_DIR/config.inc.php"
        mkdir -p "$TARGET_DIR/tmp"
        chmod 777 "$TARGET_DIR/tmp" 2>/dev/null || true
        # Cấp quyền đọc và thực thi toàn cục cho thư mục phpMyAdmin để mọi webserver (www-data, nobody, wpc) đều đọc được
        chmod -R a+rX "$TARGET_DIR" 2>/dev/null || true
        chown -R www-data:www-data "$TARGET_DIR" 2>/dev/null || chown -R nobody:nogroup "$TARGET_DIR" 2>/dev/null || chown -R wpc:wpc "$TARGET_DIR" 2>/dev/null || true
        # Đảm bảo các thư mục cha /opt, /opt/wpc có quyền +x cho other
        chmod a+x /opt /opt/wpc /opt/wpc/apps 2>/dev/null || true

        # 3. Cấu hình VirtualHost / Alias cho Webserver (Nginx & OpenLiteSpeed)
        ACTIVE_PHP=$(detect_php_version)
        PHP_NUM=$(echo "$ACTIVE_PHP" | tr -d '.')

        # Cấu hình Vhost Port 888 & Alias trên Nginx
        if [ -d "/etc/nginx/conf.d" ]; then
            PHP_FPM_SOCK="/run/php/php${ACTIVE_PHP}-fpm.sock"
            if [ ! -e "$PHP_FPM_SOCK" ]; then
                PHP_FPM_SOCK=$(ls /run/php/php*-fpm.sock 2>/dev/null | head -n 1 || echo "/run/php/php8.2-fpm.sock")
            fi

            cat <<EOF > "/etc/nginx/conf.d/00_phpmyadmin.conf"
server {
    listen $PMA_PORT default_server;
    server_name _;
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

        # Cấu hình OpenLiteSpeed
        OLS_MAIN_CONF="/usr/local/lsws/conf/httpd_config.conf"
        if [ -f "$OLS_MAIN_CONF" ]; then
            mkdir -p "/usr/local/lsws/conf/vhosts/phpmyadmin"
            cat <<EOF > "/usr/local/lsws/conf/vhosts/phpmyadmin/vhconf.conf"
docRoot                   $TARGET_DIR
enableGzip                1

index  {
  useServer               0
  indexFiles              index.php, index.html
  autoIndex               0
}

errorlog logs/pma_error.log {
  useServer               1
  logLevel                DEBUG
}

accesslog logs/pma_access.log {
  useServer               1
}

scripthandler {
  add                     lsapi:lsphp$PHP_NUM php
}
EOF
            chown -R lsadm:lsadm "/usr/local/lsws/conf/vhosts/phpmyadmin" 2>/dev/null || true

            if ! grep -q "virtualhost phpmyadmin" "$OLS_MAIN_CONF"; then
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
  binding                 *:$PMA_PORT
  secure                  0
  map                     phpmyadmin *
}
EOF
            fi
            /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lshttpd 2>/dev/null || true
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
            sed -i '/virtualhost phpmyadmin {/,/}/d' "$OLS_MAIN_CONF" 2>/dev/null || true
            sed -i '/listener PMA_888 {/,/}/d' "$OLS_MAIN_CONF" 2>/dev/null || true
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
