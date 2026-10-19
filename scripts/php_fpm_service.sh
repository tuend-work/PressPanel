#!/usr/bin/env bash
# ==============================================================================
# WPCloud: php_fpm_service.sh - Quản lý Systemd Service độc lập cho từng User
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

        # 2. Xoá file pool cũ trong pool.d để tránh service php-fpm gốc của hệ thống cũng listen cùng socket
        rm -f "$PHP_CONF_DIR/pool.d/${USERNAME}.conf" 2>/dev/null || true
        # Xóa file socket cũ nếu còn tồn tại
        rm -f "$SOCK_FILE" 2>/dev/null || true
        # Reload service php-fpm gốc để nhả socket nếu nó đang chiếm giữ
        systemctl reload "php${PHP_VER}-fpm" 2>/dev/null || true

        # 3. Đảm bảo Systemd Template Service tồn tại: /etc/systemd/system/wpc-php${PHP_VER}-fpm@.service
        TEMPLATE_FILE="/etc/systemd/system/wpc-php${PHP_VER}-fpm@.service"
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
Slice=wpc-user-%i.slice

[Install]
WantedBy=multi-user.target
EOF
        sed -i "s/__PHP_VER__/${PHP_VER}/g" "$TEMPLATE_FILE"

        # 4. Tạo Drop-in Override để gán service này vào Slice của Hosting User cha (nếu có hosting user)
        # Hệ thống phân cấp Systemd Slice: wpc-hosting_<hosting_user>.slice
        # Khi tất cả website của Hosting User cùng chạy dưới wpc-hosting_<hosting_user>.slice,
        # tổng CPU và RAM của tất cả website con KHÔNG BAO GIỜ vượt quá trần của Hosting User!
        TARGET_SLICE="wpc-user-${USERNAME}.slice"
        if [ -n "$HOSTING_USER" ]; then
            TARGET_SLICE="wpc-hosting_${HOSTING_USER}.slice"
        fi

        SVC_DROPIN_DIR="/etc/systemd/system/wpc-php${PHP_VER}-fpm@${USERNAME}.service.d"
        mkdir -p "$SVC_DROPIN_DIR"
        cat << EOF > "$SVC_DROPIN_DIR/slice.conf"
[Service]
Slice=${TARGET_SLICE}
EOF

        # 5. Kích hoạt và nạp lại daemon
        systemctl daemon-reload 2>/dev/null || true
        systemctl enable "$SVC_NAME" 2>/dev/null || true

        # Đọc hạn mức từ file cgroup của hosting user hoặc user để set-property cho slice này
        LOOKUP_USER="${HOSTING_USER:-$USERNAME}"
        USER_CG_PATH="/sys/fs/cgroup/wpc/reseller_1/user_${LOOKUP_USER}"
        [ -d "$USER_CG_PATH" ] || USER_CG_PATH="/sys/fs/cgroup/wpc/user_${LOOKUP_USER}"
        if [ -f "$USER_CG_PATH/cpu.max" ]; then
            CPU_MAX_VAL=$(awk '{print $1}' "$USER_CG_PATH/cpu.max" 2>/dev/null || echo "100000")
            if [ "$CPU_MAX_VAL" != "max" ] && [ "$CPU_MAX_VAL" -gt 0 ]; then
                CPU_PCT=$((CPU_MAX_VAL / 1000))
                systemctl set-property "${TARGET_SLICE}" CPUQuota="${CPU_PCT}%" 2>/dev/null || true
            fi
        fi
        if [ -f "$USER_CG_PATH/memory.max" ]; then
            MEM_MAX_VAL=$(cat "$USER_CG_PATH/memory.max" 2>/dev/null || echo "max")
            if [ "$MEM_MAX_VAL" != "max" ] && [ "$MEM_MAX_VAL" -gt 0 ]; then
                MEM_MB=$((MEM_MAX_VAL / 1024 / 1024))
                systemctl set-property "${TARGET_SLICE}" MemoryMax="${MEM_MB}M" 2>/dev/null || true
            fi
        fi

        systemctl restart "$SVC_NAME" 2>/dev/null || true
        mkdir -p /tmp/lshttpd /run/php /var/run/php 2>/dev/null || true
        chmod 755 /run/php /var/run/php 2>/dev/null || true
        chmod 1777 /tmp/lshttpd 2>/dev/null || true
        for i in 1 2 3; do
            if [ -S "$SOCK_FILE" ]; then
                chmod 666 "$SOCK_FILE" 2>/dev/null || true
                ln -sf "$SOCK_FILE" "/tmp/lshttpd/php${PHP_VER}-fpm-${USERNAME}.sock" 2>/dev/null || true
                break
            fi
            sleep 0.2
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
