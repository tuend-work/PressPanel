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

# 1. Đảm bảo Linux user tồn tại trên hệ thống
ensure_linux_user() {
    local u="$1"
    if ! id "$u" &>/dev/null; then
        echo "[+] Tao Linux user $u cho website..."
        useradd -m -s /bin/bash "$u" 2>/dev/null || true
    fi
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
        # KHÓA CHẶT BẢO MẬT 0750: Tuyệt đối ngăn chặn user khác xem trộm file qua Terminal/SSH/PHP
        chmod 750 "/home/$USERNAME" "/home/$USERNAME/domains" "$DOMAIN_DIR" "$DOC_ROOT" 2>/dev/null || true
        chmod 700 "/home/$USERNAME/logs" "/home/$USERNAME/ssl" "/home/$USERNAME/backups" "/home/$USERNAME/.tmp" "/home/$USERNAME/.sessions" 2>/dev/null || true

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

        # Cấu hình OpenLiteSpeed nếu có OLS
        PHP_NUM=$(echo "$PHP_VER" | tr -d '.')
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
  allowSymbolLink         1
  enableScript            1
  restrained              1
}
EOF
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

            /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lsws 2>/dev/null || true
            (sleep 1 && pgrep -u "$USERNAME" | while read -r pid; do
                for cg_proc in /sys/fs/cgroup/wpc/reseller_*/user_${USERNAME}/cgroup.procs /sys/fs/cgroup/wpc/user_${USERNAME}/cgroup.procs; do
                    [ -f "$cg_proc" ] && echo "$pid" > "$cg_proc" 2>/dev/null || true
                done
            done) &>/dev/null &
        fi

        # Cấu hình Nginx nếu có Nginx
        if [ -d "/etc/nginx/conf.d" ]; then
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
        echo "[+] Chuyen PHP sang $PHP_VER cho $DOMAIN..."
        PHP_NUM=$(echo "$PHP_VER" | tr -d '.')
        ensure_linux_user "$USERNAME"

        # Đảm bảo các thư mục cần thiết của user tồn tại và đúng quyền trước khi kích hoạt pool
        mkdir -p "/home/$USERNAME/logs" "/home/$USERNAME/.tmp" "/home/$USERNAME/.sessions"
        chown -R "$USERNAME:$USERNAME" "/home/$USERNAME/logs" "/home/$USERNAME/.tmp" "/home/$USERNAME/.sessions" 2>/dev/null || true
        chmod 700 "/home/$USERNAME/.tmp" "/home/$USERNAME/.sessions" 2>/dev/null || true

        # OpenLiteSpeed
        if [ -f "/usr/local/lsws/conf/vhosts/$DOMAIN/vhconf.conf" ]; then
            sed -i -E "s/lsapi:lsphp[0-9]+/lsapi:lsphp$PHP_NUM/" "/usr/local/lsws/conf/vhosts/$DOMAIN/vhconf.conf"
            /usr/local/lsws/bin/lswsctrl reload 2>/dev/null || systemctl reload lsws 2>/dev/null || true
            (sleep 1 && pgrep -u "$USERNAME" | while read -r pid; do
                for cg_proc in /sys/fs/cgroup/wpc/reseller_*/user_${USERNAME}/cgroup.procs /sys/fs/cgroup/wpc/user_${USERNAME}/cgroup.procs; do
                    [ -f "$cg_proc" ] && echo "$pid" > "$cg_proc" 2>/dev/null || true
                done
            done) &>/dev/null &
        fi
        # Nginx
        if [ -f "/etc/nginx/conf.d/$DOMAIN.conf" ]; then
            # Xoá pool cũ ở các phiên bản khác nếu có
            # Xoá service / pool cũ ở các phiên bản khác
            for pdir in /etc/php/*/fpm/users; do
                [ -d "$pdir" ] && rm -f "$pdir/${USERNAME}.conf" 2>/dev/null || true
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
            sed -i -E "s|fastcgi_pass unix:[^;]+;|fastcgi_pass unix:${NEW_SOCK};|" "/etc/nginx/conf.d/$DOMAIN.conf"
            nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null || true
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

    rewrite-all)
        echo "[+] Rewrite toan bo cau hinh webserver: $WEBSERVER..."
        if [ "$WEBSERVER" = "ols" ]; then
            OLS_CONF="/usr/local/lsws/conf/httpd_config.conf"
            if [ -f "$OLS_CONF" ]; then
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
                
                # Khởi động lại lsws
                /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lsws 2>/dev/null || systemctl restart lshttpd 2>/dev/null || true
            fi
        elif [ "$WEBSERVER" = "nginx" ]; then
            # Kiểm tra cú pháp nginx và reload
            if command -v nginx &>/dev/null; then
                nginx -t 2>/dev/null && nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null || true
            fi
        fi
        exit 0
        ;;

    *)
        echo "[-] Lenh khong hop le: $ACTION"
        exit 1
        ;;
esac
