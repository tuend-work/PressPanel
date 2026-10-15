#!/usr/bin/env bash
# ==============================================================================
# PressPanel: vhost_manager.sh - Quản lý Vhost OpenLiteSpeed / Nginx & Let's Encrypt
# Được thực thi an toàn bởi C Gatekeeper SUID: /opt/presspanel/bin/vhost
# ==============================================================================

set -uo pipefail

ACTION="${1:-}"
USERNAME="${2:-}"
DOMAIN="${3:-}"
WEBSERVER="${4:-ols}"
PHP_VER="${5:-8.2}"

PRESSPANEL_DIR="/opt/presspanel"

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

        # Tạo cây thư mục chuẩn
        DOC_ROOT="/home/$USERNAME/httpdocs/$DOMAIN"
        mkdir -p "$DOC_ROOT"
        mkdir -p "/home/$USERNAME/logs"
        mkdir -p "/home/$USERNAME/ssl"
        mkdir -p "/home/$USERNAME/tmp"
        mkdir -p "/home/$USERNAME/backups"

        # Phân quyền an toàn
        if id "$USERNAME" &>/dev/null; then
            chown -R "$USERNAME:$USERNAME" "/home/$USERNAME" 2>/dev/null || true
        else
            chown -R presspanel:presspanel "/home/$USERNAME" 2>/dev/null || true
        fi
        chmod 755 "/home/$USERNAME" "/home/$USERNAME/httpdocs" "$DOC_ROOT" 2>/dev/null || true

        # Tạo file index.html mẫu nếu thư mục đang trống
        if [ ! -f "$DOC_ROOT/index.html" ] && [ ! -f "$DOC_ROOT/index.php" ]; then
            cat <<EOF > "$DOC_ROOT/index.html"
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chào mừng tới $DOMAIN - PressPanel</title>
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
        <span class="badge">PressPanel Powered</span>
        <h1>$DOMAIN</h1>
        <p>Website đã được khởi tạo thành công trên máy chủ Linux tối ưu WordPress.</p>
        <p>Tải mã nguồn lên thư mục: <code>/home/$USERNAME/httpdocs/$DOMAIN</code> hoặc kích hoạt <b>WordPress Toolkit</b> để bắt đầu.</p>
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
            if [ -f "$PRESSPANEL_DIR/templates/vhost_ols/default.tpl" ]; then
                sed -e "s/{{DOMAIN}}/$DOMAIN/g" \
                    -e "s/{{PHP_VERSION}}/$PHP_NUM/g" \
                    -e "s/{{USERNAME}}/$USERNAME/g" \
                    -e "s|{{DOCUMENT_ROOT}}|$DOC_ROOT|g" \
                    "$PRESSPANEL_DIR/templates/vhost_ols/default.tpl" > "$VH_DIR/vhconf.conf"
            fi
            /usr/local/lsws/bin/lswsctrl reload 2>/dev/null || systemctl reload lsws 2>/dev/null || true
        fi

        # Cấu hình Nginx nếu có Nginx
        if [ -d "/etc/nginx/conf.d" ]; then
            if [ -f "$PRESSPANEL_DIR/templates/vhost_nginx/default.tpl" ]; then
                PHP_SOCK="/run/php/php${PHP_VER}-fpm.sock"
                [ ! -e "$PHP_SOCK" ] && PHP_SOCK="/var/run/php/php${PHP_VER}-fpm.sock"
                sed -e "s/{{DOMAIN}}/$DOMAIN/g" \
                    -e "s/{{PHP_VERSION}}/$PHP_VER/g" \
                    -e "s/{{USERNAME}}/$USERNAME/g" \
                    -e "s|{{DOCUMENT_ROOT}}|$DOC_ROOT|g" \
                    -e "s|{{PHP_SOCK}}|$PHP_SOCK|g" \
                    "$PRESSPANEL_DIR/templates/vhost_nginx/default.tpl" > "/etc/nginx/conf.d/$DOMAIN.conf"
                nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null || true
            fi
        fi

        echo "[+] Khoi tao VirtualHost $DOMAIN thanh cong!"
        exit 0
        ;;

    remove)
        echo "[+] Xoa vhost $DOMAIN cua user $USERNAME..."
        rm -rf "/home/$USERNAME/httpdocs/$DOMAIN" 2>/dev/null || true
        rm -rf "/usr/local/lsws/conf/vhosts/$DOMAIN" 2>/dev/null || true
        rm -f "/etc/nginx/conf.d/$DOMAIN.conf" 2>/dev/null || true
        /usr/local/lsws/bin/lswsctrl reload 2>/dev/null || systemctl reload lsws 2>/dev/null || true
        nginx -s reload 2>/dev/null || systemctl reload nginx 2>/dev/null || true
        echo "[+] Da xoa VirtualHost $DOMAIN thanh cong!"
        exit 0
        ;;

    set-php)
        echo "[+] Chuyen PHP sang $PHP_VER cho $DOMAIN..."
        PHP_NUM=$(echo "$PHP_VER" | tr -d '.')
        if [ -f "/usr/local/lsws/conf/vhosts/$DOMAIN/vhconf.conf" ]; then
            sed -i -E "s/lsapi:lsphp[0-9]+/lsapi:lsphp$PHP_NUM/" "/usr/local/lsws/conf/vhosts/$DOMAIN/vhconf.conf"
            /usr/local/lsws/bin/lswsctrl reload 2>/dev/null || systemctl reload lsws 2>/dev/null || true
        fi
        exit 0
        ;;

    ssl)
        echo "[+] Cap phat SSL Let's Encrypt cho $DOMAIN..."
        if command -v certbot &>/dev/null; then
            certbot certonly --webroot -w "/home/$USERNAME/httpdocs/$DOMAIN" -d "$DOMAIN" --non-interactive --agree-tos -m "admin@$DOMAIN" || true
        fi
        exit 0
        ;;

    *)
        echo "[-] Lenh khong hop le: $ACTION"
        exit 1
        ;;
esac
