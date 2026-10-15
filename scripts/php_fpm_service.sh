#!/usr/bin/env bash
# ==============================================================================
# PressPanel: php_fpm_service.sh - Quản lý Systemd Service độc lập cho từng User
# Đảm bảo mỗi User / Website sở hữu 1 Service PHP-FPM riêng chạy trong Slice riêng:
#   - Tự động sinh file cấu hình standalone chứa [global] + [pool]
#   - Service chạy trực tiếp dưới Slice=wpc.slice (hoặc user slice)
#   - Mọi tiến trình worker sinh ra (kể cả qua pm.dynamic) tự động 100% thuộc về Cgroup của User
# Cú pháp: php_fpm_service.sh <create|start|stop|restart|reload|delete> <username> <php_version>
# ==============================================================================

set -euo pipefail

ACTION="${1:-help}"
USERNAME="${2:-}"
PHP_VER="${3:-8.2}"

if [ -z "$USERNAME" ] || [ -z "$PHP_VER" ]; then
    echo "[-] Thiếu tham số: php_fpm_service.sh <create|start|stop|restart|reload|delete> <username> <php_version>"
    exit 1
fi

SVC_NAME="wpc-php${PHP_VER}-fpm@${USERNAME}.service"
PHP_CONF_DIR="/etc/php/${PHP_VER}/fpm"
USER_CONF_DIR="$PHP_CONF_DIR/users"
CONF_FILE="$USER_CONF_DIR/${USERNAME}.conf"
PID_FILE="/run/php/php${PHP_VER}-fpm-${USERNAME}.pid"
SOCK_FILE="/run/php/php${PHP_VER}-fpm-${USERNAME}.sock"

# Đảm bảo thư mục runtime tồn tại
mkdir -p /run/php /var/run/php
chown -R www-data:www-data /run/php /var/run/php 2>/dev/null || true

case "$ACTION" in
    create)
        mkdir -p "$USER_CONF_DIR"
        
        # 1. Sinh file cấu hình standalone hoàn chỉnh (gồm [global] và [pool])
        cat << EOF > "$CONF_FILE"
[global]
pid = $PID_FILE
error_log = /var/log/php${PHP_VER}-fpm-${USERNAME}.log
log_level = notice
daemonize = no

[${USERNAME}]
user = ${USERNAME}
group = ${USERNAME}

listen = $SOCK_FILE
listen.owner = www-data
listen.group = www-data
listen.mode = 0660

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

        # 2. Đảm bảo Systemd Template Service tồn tại: /etc/systemd/system/wpc-php${PHP_VER}-fpm@.service
        TEMPLATE_FILE="/etc/systemd/system/wpc-php${PHP_VER}-fpm@.service"
        if [ ! -f "$TEMPLATE_FILE" ]; then
            cat << 'EOF' > "$TEMPLATE_FILE"
[Unit]
Description=WPCloud PHP-FPM Service for user %i
After=network.target

[Service]
Type=notify
PIDFile=/run/php/php__PHP_VER__-fpm-%i.pid
ExecStart=/usr/sbin/php-fpm__PHP_VER__ --nodaemonize --fpm-config /etc/php/__PHP_VER__/fpm/users/%i.conf
ExecReload=/bin/kill -USR2 $MAINPID
TimeoutSec=15
Slice=wpc.slice

[Install]
WantedBy=multi-user.target
EOF
            sed -i "s/__PHP_VER__/${PHP_VER}/g" "$TEMPLATE_FILE"
            systemctl daemon-reload 2>/dev/null || true
        fi

        # 3. Kích hoạt và khởi động service riêng của user
        systemctl daemon-reload 2>/dev/null || true
        systemctl enable "$SVC_NAME" 2>/dev/null || true
        systemctl restart "$SVC_NAME" 2>/dev/null || true

        # 4. Gán PID master và workers vào Cgroup của User
        sleep 0.5
        pgrep -f "php-fpm: pool ${USERNAME}" 2>/dev/null | while read -r pid; do
            for cg in /sys/fs/cgroup/wpc/reseller_*/user_${USERNAME}/cgroup.procs /sys/fs/cgroup/wpc/user_${USERNAME}/cgroup.procs; do
                [ -f "$cg" ] && echo "$pid" > "$cg" 2>/dev/null || true
            done
        done
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
