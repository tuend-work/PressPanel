#!/usr/bin/env bash
# ==============================================================================
# WPCloud: php_fpm_service.sh - Quản lý Systemd Service độc lập cho từng User
# Đảm bảo mỗi User / Website sở hữu 1 Service PHP-FPM riêng chạy trong Slice riêng:
#   - Tự động sinh file cấu hình standalone chứa [global] + [pool]
#   - Service chạy trực tiếp dưới Slice=wpc-user-%i.slice
#   - Tự động fallback về pool mặc định nếu pool riêng gặp sự cố để chống lỗi 502
# Cú pháp: php_fpm_service.sh <create|start|stop|restart|reload|delete> <username> <php_version> [hosting_user]
# ==============================================================================

set -uo pipefail

ACTION="${1:-help}"
USERNAME="${2:-}"
PHP_VER="${3:-8.2}"
HOSTING_USER="${4:-$USERNAME}"

if [ -z "$USERNAME" ] || [ -z "$PHP_VER" ]; then
    echo "[-] Thiếu tham số: php_fpm_service.sh <create|start|stop|restart|reload|delete> <username> <php_version> [hosting_user]"
    exit 1
fi

SVC_NAME="wpc-php${PHP_VER}-fpm@${USERNAME}.service"
PHP_CONF_DIR="/etc/php/${PHP_VER}/fpm"
USER_CONF_DIR="$PHP_CONF_DIR/users"
CONF_FILE="$USER_CONF_DIR/${USERNAME}.conf"
PID_FILE="/run/php/php${PHP_VER}-fpm-${USERNAME}.pid"
SOCK_FILE="/run/php/php${PHP_VER}-fpm-${USERNAME}.sock"

# Xác định an toàn user worker Webserver (tránh lỗi cannot get uid for user www-data trên RHEL/AlmaLinux)
WORKER_USER="nobody"
if id "www-data" &>/dev/null; then
    WORKER_USER="www-data"
elif id "nginx" &>/dev/null; then
    WORKER_USER="nginx"
elif id "apache" &>/dev/null; then
    WORKER_USER="apache"
elif id "$USERNAME" &>/dev/null; then
    WORKER_USER="$USERNAME"
fi

WORKER_GRP="$WORKER_USER"
if ! getent group "$WORKER_USER" &>/dev/null; then
    if getent group "$USERNAME" &>/dev/null; then
        WORKER_GRP="$USERNAME"
    else
        WORKER_GRP="nobody"
    fi
fi

# Đảm bảo thư mục runtime tồn tại
mkdir -p /run/php /var/run/php /tmp/lshttpd 2>/dev/null || true
chmod 755 /run/php /var/run/php 2>/dev/null || true
chmod 1777 /tmp/lshttpd 2>/dev/null || true
chown -R ${WORKER_USER}:${WORKER_GRP} /run/php /var/run/php 2>/dev/null || true

case "$ACTION" in
    create)
        mkdir -p "$USER_CONF_DIR" 2>/dev/null || true
        
        # 0. Kiểm tra binary PHP-FPM có sẵn trên hệ thống hay không
        FPM_BIN=""
        if [ -x "/usr/sbin/php-fpm${PHP_VER}" ]; then
            FPM_BIN="/usr/sbin/php-fpm${PHP_VER}"
        elif [ -x "/usr/sbin/php-fpm" ]; then
            FPM_BIN="/usr/sbin/php-fpm"
        elif [ -x "/usr/bin/php-fpm${PHP_VER}" ]; then
            FPM_BIN="/usr/bin/php-fpm${PHP_VER}"
        fi

        # Nếu chưa có php-fpm trên VPS, tự động cài đặt package tương ứng
        if [ -z "$FPM_BIN" ]; then
            echo "[!] Không tìm thấy PHP-FPM ${PHP_VER}, tự động cài đặt gói hệ thống..."
            if command -v apt-get &>/dev/null; then
                export DEBIAN_FRONTEND=noninteractive
                apt-get update -y 2>/dev/null || true
                apt-get install -y "php${PHP_VER}-fpm" "php${PHP_VER}-cli" "php${PHP_VER}-common" "php${PHP_VER}-mysql" "php${PHP_VER}-curl" 2>/dev/null || true
            elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
                PKG_MGR="dnf"
                command -v dnf &>/dev/null || PKG_MGR="yum"
                PHP_NUM=$(echo "$PHP_VER" | tr -d '.')
                $PKG_MGR install -y "php${PHP_NUM}-php-fpm" "php-fpm" 2>/dev/null || true
            fi
            if [ -x "/usr/sbin/php-fpm${PHP_VER}" ]; then
                FPM_BIN="/usr/sbin/php-fpm${PHP_VER}"
            elif [ -x "/usr/sbin/php-fpm" ]; then
                FPM_BIN="/usr/sbin/php-fpm"
            fi
        fi

        # Đảm bảo user và group tồn tại
        if ! id "$USERNAME" &>/dev/null; then
            useradd -m -s /bin/bash "$USERNAME" 2>/dev/null || true
        fi
        FPM_GRP="$USERNAME"
        if ! getent group "$USERNAME" &>/dev/null; then
            groupadd "$USERNAME" 2>/dev/null || true
            if ! getent group "$USERNAME" &>/dev/null; then
                FPM_GRP="$WORKER_USER"
            fi
        fi

        # Đảm bảo thư mục log, tmp, session của user tồn tại và thuộc quyền sở hữu của user
        mkdir -p "/home/${USERNAME}/logs" "/home/${USERNAME}/.tmp" "/home/${USERNAME}/.sessions" 2>/dev/null || true
        chown -R "${USERNAME}:${FPM_GRP}" "/home/${USERNAME}/logs" "/home/${USERNAME}/.tmp" "/home/${USERNAME}/.sessions" 2>/dev/null || true
        chmod 755 "/home/${USERNAME}/logs" "/home/${USERNAME}/.tmp" "/home/${USERNAME}/.sessions" 2>/dev/null || true

        # 1. Sinh file cấu hình standalone hoàn chỉnh (gồm [global] và [pool])
        cat << EOF > "$CONF_FILE"
[global]
pid = $PID_FILE
error_log = /var/log/php${PHP_VER}-fpm-${USERNAME}.log
log_level = notice
daemonize = no

[${USERNAME}]
user = ${USERNAME}
group = ${FPM_GRP}

listen = $SOCK_FILE
listen.owner = ${WORKER_USER}
listen.group = ${WORKER_GRP}
listen.mode = 0666

pm = dynamic
pm.max_children = 10
pm.start_servers = 2
pm.min_spare_servers = 1
pm.max_spare_servers = 3
pm.max_requests = 500

pm.status_path = /status-${USERNAME}
ping.path = /ping-${USERNAME}

; Tách biệt môi trường và bảo vệ thư mục gốc của user
php_admin_value[open_basedir] = /home/${USERNAME}/:/tmp/:/var/tmp/
php_admin_value[upload_tmp_dir] = /home/${USERNAME}/.tmp
php_admin_value[session.save_path] = /home/${USERNAME}/.sessions
php_admin_value[disable_functions] = exec,passthru,shell_exec,system,proc_open,popen,curl_multi_exec,parse_ini_file,show_source
php_admin_flag[log_errors] = on
php_admin_value[error_log] = /home/${USERNAME}/logs/php-fpm.error.log
EOF

        # 2. Xoá file pool cũ trong pool.d để tránh trùng lặp
        rm -f "$PHP_CONF_DIR/pool.d/${USERNAME}.conf" 2>/dev/null || true
        rm -f "$SOCK_FILE" 2>/dev/null || true

        # 3. Đảm bảo Systemd Template Service tồn tại: /etc/systemd/system/wpc-php${PHP_VER}-fpm@.service
        ACTUAL_BIN="${FPM_BIN:-/usr/sbin/php-fpm${PHP_VER}}"
        TEMPLATE_FILE="/etc/systemd/system/wpc-php${PHP_VER}-fpm@.service"
        cat << EOF > "$TEMPLATE_FILE"
[Unit]
Description=WPCloud PHP-FPM Service for user %i
After=network.target

[Service]
Type=notify
PIDFile=/run/php/php${PHP_VER}-fpm-%i.pid
ExecStart=${ACTUAL_BIN} --nodaemonize --fpm-config /etc/php/${PHP_VER}/fpm/users/%i.conf
ExecReload=/bin/kill -USR2 \$MAINPID
TimeoutSec=15
Slice=wpc-user-%i.slice

[Install]
WantedBy=multi-user.target
EOF

        # 4. Kích hoạt và nạp lại daemon
        systemctl daemon-reload 2>/dev/null || true
        systemctl enable "$SVC_NAME" 2>/dev/null || true
        systemctl restart "$SVC_NAME" 2>/dev/null || true

        # 5. Kiểm tra socket riêng có được sinh ra thành công hay không
        SOCKET_READY=0
        for i in {1..15}; do
            if [ -S "$SOCK_FILE" ]; then
                chmod 666 "$SOCK_FILE" 2>/dev/null || true
                ln -sf "$SOCK_FILE" "/tmp/lshttpd/php${PHP_VER}-fpm-${USERNAME}.sock" 2>/dev/null || true
                SOCKET_READY=1
                break
            fi
            sleep 0.2
        done

        # 6. FALLBACK CỨU NGUY (BẢO VỆ CHỐNG LỖI 502):
        # Nếu service riêng chưa lên socket, tự khởi động service PHP-FPM mặc định và symlink sang $SOCK_FILE
        if [ "$SOCKET_READY" -eq 0 ]; then
            echo "[!] Socket riêng $SOCK_FILE chưa khởi tạo, kích hoạt fallback từ pool mặc định..."
            systemctl start "php${PHP_VER}-fpm" 2>/dev/null || true
            for def_sock in "/run/php/php${PHP_VER}-fpm.sock" "/var/run/php/php${PHP_VER}-fpm.sock" "/run/php/php-fpm.sock"; do
                if [ -S "$def_sock" ]; then
                    chmod 666 "$def_sock" 2>/dev/null || true
                    ln -sf "$def_sock" "$SOCK_FILE" 2>/dev/null || true
                    SOCKET_READY=1
                    break
                fi
            done
        fi
        ;;

    start)
        systemctl start "$SVC_NAME" 2>/dev/null || true
        ;;

    stop)
        systemctl stop "$SVC_NAME" 2>/dev/null || true
        ;;

    restart)
        systemctl restart "$SVC_NAME" 2>/dev/null || true
        ;;

    reload)
        systemctl reload "$SVC_NAME" 2>/dev/null || systemctl restart "$SVC_NAME" 2>/dev/null || true
        ;;

    delete)
        systemctl stop "$SVC_NAME" 2>/dev/null || true
        systemctl disable "$SVC_NAME" 2>/dev/null || true
        rm -f "$CONF_FILE" "$PID_FILE" "$SOCK_FILE" 2>/dev/null || true
        systemctl daemon-reload 2>/dev/null || true
        ;;

    *)
        echo "[-] Lệnh không hợp lệ: $ACTION"
        exit 1
        ;;
esac

exit 0
