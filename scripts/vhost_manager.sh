#!/usr/bin/env bash
# ==============================================================================
# WPCloud: vhost_manager.sh - Quản lý Vhost OpenLiteSpeed / Nginx & Let's Encrypt
# Được thực thi an toàn bởi C Gatekeeper SUID: /opt/wpc/bin/vhost
# ==============================================================================

set -uo pipefail

ACTION="${1:-}"
USERNAME="${2:-}"
DOMAIN="${3:-}"
WEBSERVER="${4:-ols}"
PHP_VER="${5:-8.2}"
HOSTING_USER="${6:-$USERNAME}"

WPC_DIR="/opt/wpc"

# 1. Đảm bảo Linux user tồn tại trên hệ thống (Khóa SSH bằng nologin)
ensure_linux_user() {
    local u="$1"
    local nologin_shell="/usr/sbin/nologin"
    [ -x "$nologin_shell" ] || nologin_shell="/sbin/nologin"
    [ -x "$nologin_shell" ] || nologin_shell="/bin/false"

    if ! id "$u" &>/dev/null; then
        echo "[+] Tao Linux user $u cho website (khoa SSH: $nologin_shell)..."
        useradd -m -s "$nologin_shell" "$u" 2>/dev/null || true
    else
        # Dam bao user ton tai cung bi khoa SSH login
        usermod -s "$nologin_shell" "$u" 2>/dev/null || true
    fi
}

# Cấu hình PHP cho OpenLiteSpeed: Hỗ trợ linh hoạt cả Native LSPHP, FastCGI Bridge (cho PHP cũ như 5.6), và Fallback
setup_ols_vhost_php() {
    local domain="$1"
    local username="$2"
    local php_ver="$3"
    local hosting_user="$4"
    local vh_conf="$5"

    local php_num
    php_num=$(echo "$php_ver" | tr -d '.')
    local lsphp_bin="/usr/local/lsws/lsphp${php_num}/bin/lsphp"

    # 1. Nếu chưa có binary LSPHP, thử tự động cài đặt qua apt
    if [ ! -x "$lsphp_bin" ]; then
        if command -v apt-get &>/dev/null; then
            apt-get update -qq 2>/dev/null || true
            apt-get install -y "lsphp${php_num}" "lsphp${php_num}-common" "lsphp${php_num}-mysql" "lsphp${php_num}-opcache" "lsphp${php_num}-curl" 2>/dev/null || true
        fi
    fi

    # Xóa block extprocessor và scripthandler cũ trong vhconf.conf nếu đã có
    sed -i '/extprocessor[[:space:]]\+[^[:space:]]\+[[:space:]]*{/,/^[[:space:]]*}/d' "$vh_conf" 2>/dev/null || true
    sed -i '/scripthandler[[:space:]]*{/,/^[[:space:]]*}/d' "$vh_conf" 2>/dev/null || true

    # 2. Kiểm tra nếu có binary LSPHP native của OLS
    if [ -x "$lsphp_bin" ]; then
        echo "[+] Su dung Native LSPHP $php_num ($lsphp_bin) cho $domain"
    else
        # Nếu OLS không có gói LSPHP này (như PHP 5.6 trên Ubuntu mới), tìm phiên bản LSPHP có sẵn trên máy
        local fallback_num=""
        for fb in 74 80 81 82 83 84; do
            if [ -x "/usr/local/lsws/lsphp${fb}/bin/lsphp" ]; then
                fallback_num="$fb"
                break
            fi
        done
        if [ -z "$fallback_num" ]; then
            for fb in 84 83 82 81; do
                if [ -x "/usr/local/lsws/lsphp${fb}/bin/lsphp" ]; then
                    fallback_num="$fb"
                    break
                fi
            done
        fi
        [ -z "$fallback_num" ] && fallback_num="84"
        echo "[-] Canh bao: OpenLiteSpeed khong co lsphp$php_num. Tu dong fallback sang LSPHP $fallback_num de tranh loi 403..."
        php_num="$fallback_num"
        lsphp_bin="/usr/local/lsws/lsphp${php_num}/bin/lsphp"
    fi

    # Cấu hình chuẩn LSAPI cho OpenLiteSpeed
    cat <<EOF_LSPHP >> "$vh_conf"

extprocessor lsphp_${username} {
  type                    lsapi
  address                 uds://tmp/lshttpd/lsphp_${username}.sock
  maxConns                10
  env                     LSAPI_CHILDREN=10
  initTimeout             60
  retryTimeout            0
  persistConn             1
  respBuffer              0
  autoStart               1
  path                    $lsphp_bin
  backlog                 100
  instances               1
  runOn                   1
  extUser                 ${username}
  extGroup                ${username}
  priority                0
  memSoftLimit            1024M
  memHardLimit            2048M
  procSoftLimit           200
  procHardLimit           300
}

scripthandler {
  add                     lsapi:lsphp_${username} php
}
EOF_LSPHP

    # Dừng tiến trình lsphp cũ để nạp binary mới
    pkill -9 -u "$username" -f "lsphp" 2>/dev/null || true
    rm -f "/tmp/lshttpd/lsphp_${username}.sock" 2>/dev/null || true

    # Dọn dẹp các service PHP-FPM riêng của user khi chạy OLS
    for pver in 5.6 7.0 7.1 7.2 7.3 7.4 8.0 8.1 8.2 8.3 8.4 8.5; do
        systemctl stop "wpc-php${pver}-fpm@${username}.service" 2>/dev/null || true
        systemctl disable "wpc-php${pver}-fpm@${username}.service" 2>/dev/null || true
        rm -f "/etc/php/${pver}/fpm/users/${username}.conf" "/run/php/php${pver}-fpm-${username}.sock" "/run/php/php${pver}-fpm-${username}.pid" 2>/dev/null || true
    done
}

# 2. Xử lý các hành động
case "$ACTION" in
    add)
        echo "[+] Tao vhost cho $DOMAIN ($WEBSERVER, PHP $PHP_VER) cua user $USERNAME..."
        ensure_linux_user "$USERNAME"

        # Tạo cây thư mục chuẩn: /home/$USERNAME/domains/$DOMAIN/{public_html, logs, ssl, backups}
        DOMAIN_DIR="/home/$USERNAME/domains/$DOMAIN"
        DOC_ROOT="$DOMAIN_DIR/public_html"
        mkdir -p "$DOC_ROOT"
        mkdir -p "$DOMAIN_DIR/logs"
        mkdir -p "$DOMAIN_DIR/ssl"
        mkdir -p "$DOMAIN_DIR/backups"

        # Các thư mục hệ thống của user
        mkdir -p "/home/$USERNAME/logs"
        mkdir -p "/home/$USERNAME/ssl"
        mkdir -p "/home/$USERNAME/tmp"
        mkdir -p "/home/$USERNAME/backups"
        mkdir -p "/home/$USERNAME/.tmp"
        mkdir -p "/home/$USERNAME/.sessions"

        # Tạo symlink /home/$USERNAME/public_html trỏ tới domain chính (hoặc domain đầu tiên)
        if [ ! -L "/home/$USERNAME/public_html" ] && [ ! -d "/home/$USERNAME/public_html" ]; then
            ln -s "$DOC_ROOT" "/home/$USERNAME/public_html" 2>/dev/null || true
        elif [ -L "/home/$USERNAME/public_html" ]; then
            # Cập nhật symlink nếu chưa trỏ đúng
            ln -snf "$DOC_ROOT" "/home/$USERNAME/public_html" 2>/dev/null || true
        fi

        # Phân quyền an toàn: Thư mục thuộc sở hữu của Linux Web User
        if id "$USERNAME" &>/dev/null; then
            chown -R "$USERNAME:$USERNAME" "/home/$USERNAME" 2>/dev/null || true
            # Thêm wpc (panel), nobody (OLS), www-data (Nginx) vào group của Web User để có thể phục vụ web
            for svc_u in wpc nobody www-data; do
                if id "$svc_u" &>/dev/null; then
                    usermod -a -G "$USERNAME" "$svc_u" 2>/dev/null || true
                fi
            done
        else
            chown -R wpc:wpc "/home/$USERNAME" 2>/dev/null || true
        fi

        # PHÂN QUYỀN CHUẨN SHARED HOSTING (cPanel / CyberPanel / OLS suEXEC standard):
        # 1. /home và /home/$USERNAME: 0711 (drwx--x--x)
        #    Cho phép Webserver (nobody/www-data) traverse (tìm đường) tới public_html,
        #    nhưng TUYỆT ĐỐI NGĂN CHẶN các user khác đọc danh sách thư mục (không có 'r') hoặc sửa đổi (không có 'w').
        chmod 711 "/home" 2>/dev/null || true
        chmod 711 "/home/$USERNAME" 2>/dev/null || true
        chmod 711 "/home/$USERNAME/domains" 2>/dev/null || true
        chmod 711 "$DOMAIN_DIR" 2>/dev/null || true

        # 2. Thư mục web (public_html) phân quyền 755 (thư mục) và 644 (file), owner $USERNAME:$USERNAME
        chmod 755 "$DOC_ROOT" 2>/dev/null || true
        find "$DOC_ROOT" -type d -exec chmod 755 {} + 2>/dev/null || true
        find "$DOC_ROOT" -type f -exec chmod 644 {} + 2>/dev/null || true

        # 3. KHÓA CHẶT các thư mục nhạy cảm 0700: Chỉ duy nhất owner ($USERNAME) và root có quyền truy cập
        chmod 700 "/home/$USERNAME/logs" "/home/$USERNAME/ssl" "/home/$USERNAME/backups" "/home/$USERNAME/.tmp" "/home/$USERNAME/.sessions" 2>/dev/null || true
        [ -d "/home/$USERNAME/.ssh" ] && chmod 700 "/home/$USERNAME/.ssh" 2>/dev/null || true
        [ -d "$DOMAIN_DIR/ssl" ] && chmod 700 "$DOMAIN_DIR/ssl" 2>/dev/null || true
        [ -d "$DOMAIN_DIR/backups" ] && chmod 700 "$DOMAIN_DIR/backups" 2>/dev/null || true

        # 3.1 Khóa bảo vệ nghiêm ngặt file cấu hình nhạy cảm wp-config.php (0640)
        for wp_cfg in "$DOC_ROOT/wp-config.php" "/home/$USERNAME/domains/$DOMAIN/wp-config.php"; do
            if [ -f "$wp_cfg" ]; then
                chmod 640 "$wp_cfg" 2>/dev/null || true
                if id "$USERNAME" &>/dev/null; then
                    chown "$USERNAME:$USERNAME" "$wp_cfg" 2>/dev/null || true
                fi
            fi
        done

        # 3.2 Khóa chống thực thi PHP webshell trong thư mục Uploads
        if [ -d "$DOC_ROOT/wp-content/uploads" ] && [ ! -f "$DOC_ROOT/wp-content/uploads/.htaccess" ]; then
            cat << 'EOF_UPLOADS' > "$DOC_ROOT/wp-content/uploads/.htaccess"
# WPCloud Security Hardening: Chặn đứng thực thi mã độc PHP trong thư mục Uploads
<FilesMatch "\.(php|php[0-9]|phtml|phar|pl|py|jsp|asp|sh|cgi)$">
    <IfModule mod_authz_core.c>
        Require all denied
    </IfModule>
    <IfModule !mod_authz_core.c>
        Deny from all
    </IfModule>
</FilesMatch>
EOF_UPLOADS
            if id "$USERNAME" &>/dev/null; then
                chown "$USERNAME:$USERNAME" "$DOC_ROOT/wp-content/uploads/.htaccess" 2>/dev/null || true
            fi
            chmod 644 "$DOC_ROOT/wp-content/uploads/.htaccess" 2>/dev/null || true
        fi

        # 4. Đảm bảo thư mục runtime socket của OLS tồn tại và có quyền sticky bit 1777
        mkdir -p /tmp/lshttpd 2>/dev/null || true
        chmod 1777 /tmp/lshttpd 2>/dev/null || true

        # Tạo file index.html mẫu nếu thư mục đang trống
        if [ ! -f "$DOC_ROOT/index.html" ] && [ ! -f "$DOC_ROOT/index.php" ]; then
            cat <<EOF > "$DOC_ROOT/index.html"
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chào mừng tới $DOMAIN - WPCloud</title>
    <style>
        body { font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
        .card { background: #1e293b; padding: 2.5rem; border-radius: 1.25rem; border: 1px solid #334155; text-align: center; max-width: 520px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5); }
        h1 { color: #38bdf8; margin: 0.75rem 0; font-size: 1.75rem; }
        p { color: #94a3b8; font-size: 0.95rem; line-height: 1.6; }
        .badge { background: #0284c7; color: white; padding: 0.25rem 0.75rem; border-radius: 9999px; font-size: 0.75rem; font-weight: bold; text-transform: uppercase; }
        code { background: #0f172a; padding: 0.2rem 0.4rem; border-radius: 0.375rem; font-family: monospace; color: #a5f3fc; }
    </style>
</head>
<body>
    <div class="card">
        <span class="badge">WPCloud Powered</span>
        <h1>$DOMAIN</h1>
        <p>Website đã được khởi tạo thành công trên máy chủ Linux tối ưu WordPress.</p>
        <p>Tải mã nguồn lên thư mục: <code>/home/$USERNAME/domains/$DOMAIN/public_html</code> (hoặc <code>/home/$USERNAME/public_html</code>) để bắt đầu.</p>
    </div>
</body>
</html>
EOF
            if id "$USERNAME" &>/dev/null; then
                chown "$USERNAME:$USERNAME" "$DOC_ROOT/index.html" 2>/dev/null || true
            fi
            chmod 644 "$DOC_ROOT/index.html" 2>/dev/null || true
        fi

        # Cấu hình OpenLiteSpeed nếu chọn OLS hoặc all
        PHP_NUM=$(echo "$PHP_VER" | tr -d '.')
        if [ "$WEBSERVER" = "ols" ] || [ "$WEBSERVER" = "all" ]; then
            if [ -d "/usr/local/lsws/conf" ]; then
                VH_DIR="/usr/local/lsws/conf/vhosts/$DOMAIN"
                mkdir -p "$VH_DIR"
                if [ -f "$WPC_DIR/templates/vhost_ols/default.tpl" ]; then
                    sed -e "s/{{DOMAIN}}/$DOMAIN/g" \
                        -e "s/{{PHP_VERSION}}/$PHP_NUM/g" \
                        -e "s/{{USERNAME}}/$USERNAME/g" \
                        -e "s|{{DOCUMENT_ROOT}}|$DOC_ROOT|g" \
                        "$WPC_DIR/templates/vhost_ols/default.tpl" > "$VH_DIR/vhconf.conf"
                fi
                setup_ols_vhost_php "$DOMAIN" "$USERNAME" "$PHP_VER" "$HOSTING_USER" "$VH_DIR/vhconf.conf"
                
                # Đăng ký vhost vào httpd_config.conf nếu chưa có
                OLS_MAIN_CONF="/usr/local/lsws/conf/httpd_config.conf"
                if [ -f "$OLS_MAIN_CONF" ]; then
                    # Đổi port 8088 thành 80 nếu còn tồn tại
                    sed -i 's/address *:8088/address *:80/g' "$OLS_MAIN_CONF" 2>/dev/null || true
                    
                    # Khai báo virtualhost block trong httpd_config.conf nếu chưa có
                    if ! grep -q "virtualhost $DOMAIN" "$OLS_MAIN_CONF"; then
                        cat <<EOF >> "$OLS_MAIN_CONF"

virtualhost $DOMAIN {
  vhRoot                  /home/$USERNAME
  configFile              conf/vhosts/$DOMAIN/vhconf.conf
  allowSymbolLink         2
  enableScript            1
  restrained              0
  setUIDMode              2
}
EOF
                    else
                        # Đảm bảo restrained là 0 và có setUIDMode 2 nếu block đã tồn tại
                        sed -i "/virtualhost[[:space:]]\+$DOMAIN[[:space:]]*{/,/^[[:space:]]*}/ s/restrained[[:space:]]\+1/restrained              0/" "$OLS_MAIN_CONF" 2>/dev/null || true
                        sed -i "/virtualhost[[:space:]]\+$DOMAIN[[:space:]]*{/,/^[[:space:]]*}/ s/allowSymbolLink[[:space:]]\+[01]/allowSymbolLink         2/" "$OLS_MAIN_CONF" 2>/dev/null || true
                        if ! grep -A 8 "virtualhost $DOMAIN" "$OLS_MAIN_CONF" | grep -q "setUIDMode"; then
                            sed -i "/virtualhost[[:space:]]\+$DOMAIN[[:space:]]*{/,/^[[:space:]]*}/ s/restrained[[:space:]]\+0/restrained              0\n  setUIDMode              2/" "$OLS_MAIN_CONF" 2>/dev/null || true
                        fi
                    fi

                    # Map virtualhost vào listener HTTP (port 80)
                    # Dọn dẹp các dòng map bị chèn nhầm vào errorlog trước đó
                    sed -i '/errorlog logs\/error\.log {/,/}/ { /map[[:space:]]/d }' "$OLS_MAIN_CONF" 2>/dev/null || true

                    # Xóa mapping Example * mặc định cướp toàn bộ traffic
                    sed -i 's/map[[:space:]]\+Example[[:space:]]\+\*/# map Example */g' "$OLS_MAIN_CONF" 2>/dev/null || true

                    # Thêm map vào listener Default hoặc HTTP_80 nếu chưa có
                    if ! grep -q "map[[:space:]]\+$DOMAIN" "$OLS_MAIN_CONF"; then
                        # Dùng awk để chèn map an toàn ngay trước dấu ngoặc đóng } của listener HTTP_80 hoặc Default
                        awk -v d="$DOMAIN" '
                            /listener[[:space:]]+(Default|HTTP_80|HTTP)[[:space:]]*{/ { in_listener=1 }
                            in_listener && /}/ {
                                print "    map                      " d " " d ", *." d
                                in_listener=0
                            }
                            { print }
                        ' "$OLS_MAIN_CONF" > "$OLS_MAIN_CONF.tmp" && mv "$OLS_MAIN_CONF.tmp" "$OLS_MAIN_CONF"
                    fi
                    chmod 644 "$OLS_MAIN_CONF" 2>/dev/null || true
                fi

                /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lsws 2>/dev/null || systemctl restart lshttpd 2>/dev/null || true
                (sleep 1 && /opt/wpc/bin/cgroup sync "$USERNAME" 2>/dev/null || true
                for lscg in /usr/local/lsws/lsns/bin/lscgctl /usr/local/lsws/bin/lscgctl; do
                    [ -x "$lscg" ] && "$lscg" set "$USERNAME" --cpu=200 2>/dev/null || true
                done) &>/dev/null &
            fi
        fi

        # Cấu hình Nginx CHỈ KHI chọn Nginx hoặc all
        if [ "$WEBSERVER" = "nginx" ] || [ "$WEBSERVER" = "all" ]; then
            mkdir -p /etc/nginx/conf.d /etc/nginx/sites-available /etc/nginx/sites-enabled
            rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
            if [ -f "$WPC_DIR/templates/vhost_nginx/default.tpl" ]; then
                    # Tạo thư mục tạm và session riêng cho user nếu chưa có
                    mkdir -p "/home/$USERNAME/.tmp" "/home/$USERNAME/.sessions" "/home/$USERNAME/logs"
                    chown -R "$USERNAME:$USERNAME" "/home/$USERNAME/.tmp" "/home/$USERNAME/.sessions" 2>/dev/null || true
                    chmod 700 "/home/$USERNAME/.tmp" "/home/$USERNAME/.sessions" 2>/dev/null || true

                    # 1. Tạo file PHP-FPM Pool & Systemd Template Service độc lập riêng cho User này
                    USER_POOL_SOCK="/run/php/php${PHP_VER}-fpm-${USERNAME}.sock"
                    PHP_SOCK="$USER_POOL_SOCK"

                    if [ -f "$WPC_DIR/scripts/php_fpm_service.sh" ]; then
                        bash "$WPC_DIR/scripts/php_fpm_service.sh" create "$USERNAME" "$PHP_VER" "$HOSTING_USER" 2>/dev/null || true
                    fi

                    # Nếu pool sock chưa sinh ra kịp (fallback sang pool mặc định nếu cần)
                    if [ ! -e "$PHP_SOCK" ]; then
                        if [ -e "/run/php/php${PHP_VER}-fpm.sock" ]; then
                            PHP_SOCK="/run/php/php${PHP_VER}-fpm.sock"
                        elif [ -e "/var/run/php/php${PHP_VER}-fpm.sock" ]; then
                            PHP_SOCK="/var/run/php/php${PHP_VER}-fpm.sock"
                        fi
                    fi

                    sed -e "s/{{DOMAIN}}/$DOMAIN/g" \
                        -e "s/{{PHP_VERSION}}/$PHP_VER/g" \
                        -e "s/{{USERNAME}}/$USERNAME/g" \
                        -e "s|{{DOCUMENT_ROOT}}|$DOC_ROOT|g" \
                        -e "s|{{PHP_SOCK}}|$PHP_SOCK|g" \
                        "$WPC_DIR/templates/vhost_nginx/default.tpl" > "/etc/nginx/conf.d/$DOMAIN.conf"
                    nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null || true
            fi
        fi

        # Nếu webserver là OLS thì dọn dẹp cấu hình Nginx & dừng PHP-FPM user service
        if [ "$WEBSERVER" = "ols" ]; then
            rm -f "/etc/nginx/conf.d/$DOMAIN.conf" 2>/dev/null || true
            for pver in 5.6 7.0 7.1 7.2 7.3 7.4 8.0 8.1 8.2 8.3 8.4 8.5; do
                systemctl stop "wpc-php${pver}-fpm@${USERNAME}.service" 2>/dev/null || true
                systemctl disable "wpc-php${pver}-fpm@${USERNAME}.service" 2>/dev/null || true
                rm -f "/etc/php/${pver}/fpm/users/${USERNAME}.conf" "/run/php/php${pver}-fpm-${USERNAME}.sock" "/run/php/php${pver}-fpm-${USERNAME}.pid" 2>/dev/null || true
            done
        fi

        # Nếu webserver là Nginx thì dọn cấu hình OLS của domain này
        if [ "$WEBSERVER" = "nginx" ]; then
            rm -rf "/usr/local/lsws/conf/vhosts/$DOMAIN" 2>/dev/null || true
            if [ -f "/usr/local/lsws/conf/httpd_config.conf" ]; then
                sed -i "/virtualhost[[:space:]]\+$DOMAIN[[:space:]]*{/,/^[[:space:]]*}/d" /usr/local/lsws/conf/httpd_config.conf 2>/dev/null || true
                sed -i "/map[[:space:]]\+$DOMAIN/d" /usr/local/lsws/conf/httpd_config.conf 2>/dev/null || true
            fi
            # Dừng tiến trình lsphp riêng của user này nếu có
            pkill -9 -u "$USERNAME" -f "lsphp" 2>/dev/null || true
            rm -f "/tmp/lshttpd/lsphp_${USERNAME}.sock" 2>/dev/null || true
            /usr/local/lsws/bin/lswsctrl reload 2>/dev/null || systemctl reload lsws 2>/dev/null || true
        fi

        echo "[+] Khoi tao VirtualHost $DOMAIN thanh cong!"
        exit 0
        ;;

    remove)
        echo "[+] Xoa vhost $DOMAIN cua user $USERNAME..."
        rm -rf "/home/$USERNAME/domains/$DOMAIN" 2>/dev/null || true
        rm -rf "/home/$USERNAME/httpdocs/$DOMAIN" 2>/dev/null || true
        # Xóa symlink public_html nếu đang trỏ tới domain này
        if [ -L "/home/$USERNAME/public_html" ]; then
            TARGET_LNK=$(readlink "/home/$USERNAME/public_html" || true)
            if echo "$TARGET_LNK" | grep -q "$DOMAIN"; then
                rm -f "/home/$USERNAME/public_html" 2>/dev/null || true
            fi
        fi
        rm -rf "/usr/local/lsws/conf/vhosts/$DOMAIN" 2>/dev/null || true
        if [ -f "/usr/local/lsws/conf/httpd_config.conf" ]; then
            sed -i "/virtualhost[[:space:]]\+$DOMAIN[[:space:]]*{/,/^[[:space:]]*}/d" /usr/local/lsws/conf/httpd_config.conf 2>/dev/null || true
            sed -i "/map[[:space:]]\+$DOMAIN/d" /usr/local/lsws/conf/httpd_config.conf 2>/dev/null || true
        fi
        rm -f "/etc/nginx/conf.d/$DOMAIN.conf" 2>/dev/null || true
        # Xoá pool & dừng các standalone PHP-FPM service riêng của user trên tất cả các phiên bản PHP
        for pver in 5.6 7.0 7.1 7.2 7.3 7.4 8.0 8.1 8.2 8.3 8.4 8.5; do
            systemctl stop "wpc-php${pver}-fpm@${USERNAME}.service" 2>/dev/null || true
            systemctl disable "wpc-php${pver}-fpm@${USERNAME}.service" 2>/dev/null || true
            rm -f "/etc/php/${pver}/fpm/users/${USERNAME}.conf" "/run/php/php${pver}-fpm-${USERNAME}.pid" "/run/php/php${pver}-fpm-${USERNAME}.sock" 2>/dev/null || true
        done
        for pdir in /etc/php/*/fpm/pool.d; do
            [ -d "$pdir" ] && rm -f "$pdir/${USERNAME}.conf" 2>/dev/null || true
        done
        /usr/local/lsws/bin/lswsctrl reload 2>/dev/null || systemctl reload lsws 2>/dev/null || true
        nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null || true
        systemctl reload "php*-fpm" 2>/dev/null || true
        # Neu la user linux rieng biet cua domain (hoac user doc lap), xoa user va thu muc home neu can
        if [ "$USERNAME" != "root" ] && [ "$USERNAME" != "wpc" ] && id "$USERNAME" &>/dev/null; then
            userdel -r "$USERNAME" 2>/dev/null || true
            rm -rf "/home/$USERNAME" 2>/dev/null || true
        fi
        echo "[+] Da xoa VirtualHost $DOMAIN thanh cong!"
        exit 0
        ;;

    set-php)
        echo "[+] Chuyen PHP sang $PHP_VER cho $DOMAIN (Webserver: $WEBSERVER)..."
        PHP_NUM=$(echo "$PHP_VER" | tr -d '.')
        ensure_linux_user "$USERNAME"

        # 1. Đảm bảo phân quyền chuẩn 0711 Shared Hosting cho toàn bộ cây thư mục website
        DOMAIN_DIR="/home/$USERNAME/domains/$DOMAIN"
        DOC_ROOT="$DOMAIN_DIR/public_html"
        [ ! -d "$DOC_ROOT" ] && DOC_ROOT="/home/$USERNAME/httpdocs/$DOMAIN"

        chmod 711 "/home" 2>/dev/null || true
        chmod 711 "/home/$USERNAME" 2>/dev/null || true
        chmod 711 "/home/$USERNAME/domains" 2>/dev/null || true
        [ -d "$DOMAIN_DIR" ] && chmod 711 "$DOMAIN_DIR" 2>/dev/null || true
        if [ -d "$DOC_ROOT" ]; then
            chmod 755 "$DOC_ROOT" 2>/dev/null || true
            find "$DOC_ROOT" -type d -exec chmod 755 {} + 2>/dev/null || true
            find "$DOC_ROOT" -type f -exec chmod 644 {} + 2>/dev/null || true
        fi

        # Khóa chặt các thư mục nhạy cảm 0700
        mkdir -p "/home/$USERNAME/logs" "/home/$USERNAME/.tmp" "/home/$USERNAME/.sessions"
        chown -R "$USERNAME:$USERNAME" "/home/$USERNAME/logs" "/home/$USERNAME/.tmp" "/home/$USERNAME/.sessions" 2>/dev/null || true
        chmod 700 "/home/$USERNAME/logs" "/home/$USERNAME/ssl" "/home/$USERNAME/backups" "/home/$USERNAME/.tmp" "/home/$USERNAME/.sessions" 2>/dev/null || true
        [ -d "/home/$USERNAME/.ssh" ] && chmod 700 "/home/$USERNAME/.ssh" 2>/dev/null || true

        # Đảm bảo thư mục socket OLS có quyền 1777
        mkdir -p /tmp/lshttpd 2>/dev/null || true
        chmod 1777 /tmp/lshttpd 2>/dev/null || true

        # 2. Xử lý OpenLiteSpeed khi chọn OLS
        if [ "$WEBSERVER" = "ols" ] || ([ "$WEBSERVER" = "all" ] && [ -f "/usr/local/lsws/conf/vhosts/$DOMAIN/vhconf.conf" ]); then
            VH_CONF="/usr/local/lsws/conf/vhosts/$DOMAIN/vhconf.conf"
            if [ -f "$VH_CONF" ]; then
                # Đảm bảo có indexFiles nếu file cũ chưa có
                if ! grep -q "indexFiles" "$VH_CONF"; then
                    sed -i "1s/^/indexFiles                index.php, index.html, index.htm\n\n/" "$VH_CONF" 2>/dev/null || true
                fi

                # Đảm bảo có accessControl nếu file cũ chưa có
                if ! grep -q "accessControl" "$VH_CONF"; then
                    cat << 'EOF_AC' >> "$VH_CONF"

accessControl {
  allow                   *
}
EOF_AC
                fi

                # Đảm bảo có rewrite nếu file cũ chưa có
                if ! grep -q "autoLoadHtaccess" "$VH_CONF"; then
                    cat << 'EOF_RW' >> "$VH_CONF"

rewrite  {
  enable                  1
  autoLoadHtaccess        1
}
EOF_RW
                fi

                # Áp dụng cấu hình PHP chuyên sâu (Native LSPHP hoặc Bridge FastCGI nếu thiếu gói như PHP 5.6)
                setup_ols_vhost_php "$DOMAIN" "$USERNAME" "$PHP_VER" "$HOSTING_USER" "$VH_CONF"

                # Đảm bảo restrained 0 trong httpd_config.conf cho vhost này
                if [ -f "/usr/local/lsws/conf/httpd_config.conf" ]; then
                    sed -i "/virtualhost[[:space:]]\+$DOMAIN[[:space:]]*{/,/^[[:space:]]*}/ s/restrained[[:space:]]\+1/restrained              0/" /usr/local/lsws/conf/httpd_config.conf 2>/dev/null || true
                fi

                # Khởi động lại OLS để nhận cấu hình tức thì
                /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lsws 2>/dev/null || systemctl restart lshttpd 2>/dev/null || true
                (sleep 1 && /opt/wpc/bin/cgroup sync "$USERNAME" 2>/dev/null || true
                for lscg in /usr/local/lsws/lsns/bin/lscgctl /usr/local/lsws/bin/lscgctl; do
                    [ -x "$lscg" ] && "$lscg" set "$USERNAME" --cpu=200 2>/dev/null || true
                done) &>/dev/null &
            fi

            # Dọn dẹp vhost Nginx khi đang chạy OLS
            rm -f "/etc/nginx/conf.d/$DOMAIN.conf" 2>/dev/null || true
        fi

        # 3. Xử lý Nginx khi chọn Nginx
        if [ "$WEBSERVER" = "nginx" ] || ([ "$WEBSERVER" = "all" ] && [ -f "/etc/nginx/conf.d/$DOMAIN.conf" ]); then
            # Dừng tiến trình lsphp cũ của user nếu có
            pkill -9 -u "$USERNAME" -f "lsphp" 2>/dev/null || true
            rm -f "/tmp/lshttpd/lsphp_${USERNAME}.sock" 2>/dev/null || true

            # Xoá pool cũ ở các phiên bản khác
            for pver in 5.6 7.0 7.1 7.2 7.3 7.4 8.0 8.1 8.2 8.3 8.4 8.5; do
                if [ "$pver" != "$PHP_VER" ]; then
                    systemctl stop "wpc-php${pver}-fpm@${USERNAME}.service" 2>/dev/null || true
                    systemctl disable "wpc-php${pver}-fpm@${USERNAME}.service" 2>/dev/null || true
                    rm -f "/etc/php/${pver}/fpm/users/${USERNAME}.conf" "/run/php/php${pver}-fpm-${USERNAME}.sock" "/run/php/php${pver}-fpm-${USERNAME}.pid" 2>/dev/null || true
                fi
            done
            for pdir in /etc/php/*/fpm/pool.d; do
                [ -d "$pdir" ] && rm -f "$pdir/${USERNAME}.conf" 2>/dev/null || true
            done

            # Tạo và kích hoạt Systemd Template Service độc lập cho phiên bản PHP mới
            if [ -f "$WPC_DIR/scripts/php_fpm_service.sh" ]; then
                bash "$WPC_DIR/scripts/php_fpm_service.sh" create "$USERNAME" "$PHP_VER" "$HOSTING_USER" 2>/dev/null || true
            fi
            NEW_SOCK="/run/php/php${PHP_VER}-fpm-${USERNAME}.sock"
            if [ ! -e "$NEW_SOCK" ]; then
                if [ -e "/run/php/php${PHP_VER}-fpm.sock" ]; then
                    NEW_SOCK="/run/php/php${PHP_VER}-fpm.sock"
                elif [ -e "/var/run/php/php${PHP_VER}-fpm.sock" ]; then
                    NEW_SOCK="/var/run/php/php${PHP_VER}-fpm.sock"
                fi
            fi
            if [ -f "/etc/nginx/conf.d/$DOMAIN.conf" ]; then
                sed -i -E "s|fastcgi_pass unix:[^;]+;|fastcgi_pass unix:${NEW_SOCK};|" "/etc/nginx/conf.d/$DOMAIN.conf"
                nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null || true
            fi
        fi
        exit 0
        ;;

    ssl)
        echo "[+] Cap phat SSL Let's Encrypt cho $DOMAIN..."
        SSL_WEBROOT="/home/$USERNAME/domains/$DOMAIN/public_html"
        [ ! -d "$SSL_WEBROOT" ] && SSL_WEBROOT="/home/$USERNAME/httpdocs/$DOMAIN"
        if command -v certbot &>/dev/null; then
            certbot certonly --webroot -w "$SSL_WEBROOT" -d "$DOMAIN" --non-interactive --agree-tos -m "admin@$DOMAIN" || true
        fi
        exit 0
        ;;

    apply-php-ini)
        echo "[+] Ap dung cau hinh php.ini cho $DOMAIN (User: $USERNAME, Webserver: $WEBSERVER)..."
        DOC_ROOT="/home/$USERNAME/domains/$DOMAIN/public_html"
        [ ! -d "$DOC_ROOT" ] && DOC_ROOT="/home/$USERNAME/httpdocs/$DOMAIN"
        mkdir -p "$DOC_ROOT" 2>/dev/null || true

        # 1. Đưa tệp tạm /tmp/wpc_phpini_${DOMAIN}.ini vào $DOC_ROOT/.user.ini nếu có
        TMP_INI="/tmp/wpc_phpini_${DOMAIN}.ini"
        if [ -f "$TMP_INI" ]; then
            cp -f "$TMP_INI" "$DOC_ROOT/.user.ini" 2>/dev/null || true
            rm -f "$TMP_INI" 2>/dev/null || true
        fi

        # 2. Phân quyền chuẩn cho file .user.ini
        if [ -f "$DOC_ROOT/.user.ini" ]; then
            if id "$USERNAME" &>/dev/null; then
                chown "$USERNAME:$USERNAME" "$DOC_ROOT/.user.ini" 2>/dev/null || true
            fi
            chmod 644 "$DOC_ROOT/.user.ini" 2>/dev/null || true
            touch "$DOC_ROOT/.user.ini" 2>/dev/null || true
        fi

        # 3. Xử lý OpenLiteSpeed: Cập nhật phpIniOverride trong vhconf.conf
        if [ "$WEBSERVER" = "ols" ] || [ -f "/usr/local/lsws/conf/vhosts/$DOMAIN/vhconf.conf" ]; then
            VH_CONF="/usr/local/lsws/conf/vhosts/$DOMAIN/vhconf.conf"
            if [ -f "$VH_CONF" ] && [ -f "$DOC_ROOT/.user.ini" ]; then
                # Xóa block phpIniOverride cũ nếu có
                sed -i '/phpIniOverride[[:space:]]*{/,/^[[:space:]]*}/d' "$VH_CONF" 2>/dev/null || true

                # Tạo block phpIniOverride mới từ .user.ini theo đúng chuẩn OpenLiteSpeed:
                # php_value directive_name value
                # php_flag boolean_directive On|Off
                echo "" >> "$VH_CONF"
                echo "phpIniOverride  {" >> "$VH_CONF"
                while IFS='=' read -r key val || [ -n "$key" ]; do
                    clean_k=$(echo "$key" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
                    clean_v=$(echo "$val" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'"'"']//' -e 's/["'"'"']$//')
                    [ -z "$clean_k" ] && continue
                    case "$clean_k" in \#*|\;*) continue ;; esac
                    
                    if [ "$clean_k" = "display_errors" ]; then
                        echo "  php_flag display_errors $clean_v" >> "$VH_CONF"
                    else
                        echo "  php_value $clean_k $clean_v" >> "$VH_CONF"
                    fi
                done < "$DOC_ROOT/.user.ini"
                echo "}" >> "$VH_CONF"

                # QUAN TRỌNG: Kill toàn bộ tiến trình lsphp cũ của user này để giải phóng worker đang giữ cache trong bộ nhớ
                pkill -9 -u "$USERNAME" -f "lsphp" 2>/dev/null || true
                rm -f "/tmp/lshttpd/lsphp_${USERNAME}.sock" 2>/dev/null || true

                # Khởi động lại / reload OLS
                /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lsws 2>/dev/null || systemctl restart lshttpd 2>/dev/null || true
            fi
        fi

        # 4. Xử lý Nginx: Reload Nginx và các worker PHP-FPM của user
        if [ "$WEBSERVER" = "nginx" ] || [ -f "/etc/nginx/conf.d/$DOMAIN.conf" ]; then
            for pver in 5.6 7.0 7.1 7.2 7.3 7.4 8.0 8.1 8.2 8.3 8.4 8.5; do
                systemctl reload "wpc-php${pver}-fpm@${USERNAME}.service" 2>/dev/null || true
            done
            nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null || true
        fi

        echo "[+] Da ap dung cau hinh php.ini cho $DOMAIN thanh cong!"
        exit 0
        ;;

    rewrite-all)
        echo "[+] Rewrite toan bo cau hinh webserver: $WEBSERVER..."
        if [ "$WEBSERVER" = "ols" ]; then
            # 1. Dọn dẹp và dừng TOÀN BỘ tiến trình / service PHP-FPM riêng của user VÀ hệ thống
            echo "[+] Dang chuyen sang OLS: Tat cac service PHP-FPM nguoi dung va he thong..."
            systemctl stop "wpc-php*-fpm@*" 2>/dev/null || true
            systemctl disable "wpc-php*-fpm@*" 2>/dev/null || true
            for pver in 5.6 7.0 7.1 7.2 7.3 7.4 8.0 8.1 8.2 8.3 8.4 8.5; do
                systemctl stop "wpc-php${pver}-fpm@*.service" 2>/dev/null || true
                systemctl disable "wpc-php${pver}-fpm@*.service" 2>/dev/null || true
                systemctl stop "php${pver}-fpm" 2>/dev/null || true
                systemctl disable "php${pver}-fpm" 2>/dev/null || true
            done
            pkill -9 -f "php-fpm" 2>/dev/null || true
            # Xóa socket và pid của php-fpm
            rm -f /run/php/php*-fpm*.sock /run/php/php*-fpm*.pid /var/run/php/php*-fpm*.sock 2>/dev/null || true
            # Tắt và dừng Nginx để giải phóng port 80/443 cho OLS
            systemctl stop nginx 2>/dev/null || true
            systemctl disable nginx 2>/dev/null || true

            # 2. Đảm bảo thư mục runtime socket của OLS tồn tại và có quyền 1777
            mkdir -p /tmp/lshttpd 2>/dev/null || true
            chmod 1777 /tmp/lshttpd 2>/dev/null || true

            # 3. Chuẩn hóa phân quyền 711 toàn bộ /home để tránh lỗi 403 OLS
            chmod 711 /home 2>/dev/null || true
            for udir in /home/*; do
                if [ -d "$udir" ]; then
                    u=$(basename "$udir")
                    if [ "$u" != "wpc" ]; then
                        chmod 711 "$udir" 2>/dev/null || true
                        [ -d "$udir/domains" ] && chmod 711 "$udir/domains" 2>/dev/null || true
                        for ddir in "$udir/domains"/*; do
                            [ -d "$ddir" ] && chmod 711 "$ddir" 2>/dev/null || true
                            if [ -d "$ddir/public_html" ]; then
                                chmod 755 "$ddir/public_html" 2>/dev/null || true
                                find "$ddir/public_html" -type d -exec chmod 755 {} + 2>/dev/null || true
                                find "$ddir/public_html" -type f -exec chmod 644 {} + 2>/dev/null || true
                            fi
                        done
                    fi
                fi
            done

            # 4. Cấu hình OLS
            OLS_CONF="/usr/local/lsws/conf/httpd_config.conf"
            if [ -f "$OLS_CONF" ]; then
                # Sửa restrained 1 thành restrained 0 cho tất cả vhost
                sed -i 's/restrained[[:space:]]\+1/restrained              0/g' "$OLS_CONF" 2>/dev/null || true
                sed -i 's/allowSymbolLink[[:space:]]\+[01]/allowSymbolLink         2/g' "$OLS_CONF" 2>/dev/null || true

                # Bổ sung setUIDMode 2 (User Home UID) cho tất cả virtualhost chưa có
                awk '
                    /virtualhost[[:space:]]+[^{]+{/ { in_vh=1; has_uid=0 }
                    in_vh && /setUIDMode/ { has_uid=1 }
                    in_vh && /}/ {
                        if (!has_uid) print "  setUIDMode              2"
                        in_vh=0
                    }
                    { print }
                ' "$OLS_CONF" > "$OLS_CONF.tmp" 2>/dev/null && mv "$OLS_CONF.tmp" "$OLS_CONF" 2>/dev/null || true

                # Đổi port 8088 thành 80 bất kể định dạng có khoảng trắng
                sed -i -E 's/address[[:space:]]+(\*:|0\.0\.0\.0:|\[::\]:)?8088/address *:80/g' "$OLS_CONF" || true
                sed -i 's/address *:8088/address *:80/g' "$OLS_CONF" || true
                sed -i 's/:8088/:80/g' "$OLS_CONF" || true
                sed -i 's/listener Default {/listener HTTP_80 {/g' "$OLS_CONF" || true

                # Dọn sạch các dòng map bị chèn nhầm vào errorlog
                sed -i '/errorlog logs\/error\.log {/,/}/ { /map[[:space:]]/d }' "$OLS_CONF" || true
                # Vô hiệu hóa map Example * mặc định cướp toàn bộ domain
                sed -i 's/map[[:space:]]\+Example[[:space:]]\+\*/# map Example */g' "$OLS_CONF" || true
                
                # Phân quyền 644 để panel có thể đọc trực tiếp
                chmod 755 /usr/local/lsws/conf 2>/dev/null || true
                chmod 644 "$OLS_CONF" 2>/dev/null || true
                
                # Kích hoạt và khởi động lại lsws
                systemctl enable lsws 2>/dev/null || systemctl enable lshttpd 2>/dev/null || true
                /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lsws 2>/dev/null || systemctl restart lshttpd 2>/dev/null || true

                # Đảm bảo các vhost OLS đang dùng FastCGI Bridge (như PHP 5.6) có service PHP-FPM hoạt động
                for vh in /usr/local/lsws/conf/vhosts/*/vhconf.conf; do
                    if [ -f "$vh" ] && grep -q "type[[:space:]]\+fcgi" "$vh"; then
                        sock_line=$(grep -oE "address[[:space:]]+uds://run/php/php[0-9.]+-fpm-[^[:space:]]+\.sock" "$vh" | head -n 1 || true)
                        if [ -n "$sock_line" ]; then
                            p_ver=$(echo "$sock_line" | sed -nE 's/.*php([0-9.]+)-fpm-.*/\1/p')
                            p_user=$(echo "$sock_line" | sed -nE 's/.*-fpm-([^.]+)\.sock/\1/p')
                            if [ -n "$p_ver" ] && [ -n "$p_user" ]; then
                                systemctl enable "wpc-php${p_ver}-fpm@${p_user}.service" 2>/dev/null || true
                                systemctl start "wpc-php${p_ver}-fpm@${p_user}.service" 2>/dev/null || true
                                [ -S "/run/php/php${p_ver}-fpm-${p_user}.sock" ] && chmod 666 "/run/php/php${p_ver}-fpm-${p_user}.sock" 2>/dev/null || true
                            fi
                        fi
                    fi
                done
            fi
        elif [ "$WEBSERVER" = "nginx" ]; then
            # Tắt OLS và tiêu diệt triệt để toàn bộ tiến trình lshttpd / lsphp để giải phóng port & RAM
            echo "[+] Dang chuyen sang Nginx: Dung OLS va tat toan bo tien trinh lsphp..."
            /usr/local/lsws/bin/lswsctrl stop 2>/dev/null || systemctl stop lsws 2>/dev/null || systemctl stop lshttpd 2>/dev/null || true
            systemctl disable lsws 2>/dev/null || systemctl disable lshttpd 2>/dev/null || true
            pkill -9 -f "openlitespeed" 2>/dev/null || true
            pkill -9 -f "lshttpd" 2>/dev/null || true
            pkill -9 -f "lsphp" 2>/dev/null || true
            pkill -9 -f "lscgid" 2>/dev/null || true
            rm -rf /tmp/lshttpd/*.sock 2>/dev/null || true

            # Chuẩn bị cấu hình Nginx
            mkdir -p /etc/nginx/conf.d /etc/nginx/sites-available /etc/nginx/sites-enabled
            rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

            # Chuẩn hóa phân quyền 711 /home
            chmod 711 /home 2>/dev/null || true
            for udir in /home/*; do
                if [ -d "$udir" ]; then
                    u=$(basename "$udir")
                    if [ "$u" != "wpc" ]; then
                        chmod 711 "$udir" 2>/dev/null || true
                        [ -d "$udir/domains" ] && chmod 711 "$udir/domains" 2>/dev/null || true
                    fi
                fi
            done

            systemctl enable openresty 2>/dev/null || systemctl enable nginx 2>/dev/null || true
            for pver in 5.6 7.0 7.1 7.2 7.3 7.4 8.0 8.1 8.2 8.3 8.4 8.5; do
                if [ -x "/usr/sbin/php-fpm${pver}" ]; then
                    systemctl enable "php${pver}-fpm" 2>/dev/null || true
                    systemctl restart "php${pver}-fpm" 2>/dev/null || true
                fi
            done
            (systemctl reload openresty 2>/dev/null || systemctl reload nginx 2>/dev/null || systemctl restart openresty 2>/dev/null || systemctl restart nginx 2>/dev/null || true)
        fi
        exit 0
        ;;

    *)
        echo "[-] Lenh khong hop le: $ACTION"
        exit 1
        ;;
esac
