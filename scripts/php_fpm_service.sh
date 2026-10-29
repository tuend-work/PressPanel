#!/usr/bin/env bash
# ==============================================================================
# WPCloud: php_fpm_service.sh - Quản lý Systemd Service độc lập cho từng User
# Đảm bảo mỗi User / Website sở hữu 1 Service PHP-FPM riêng chạy trong Slice riêng:
#   - Tự động sinh file cấu hình standalone chứa [global] + [pool]
#   - Service chạy trực tiếp dưới Slice=wpc-user-%i.slice
#   - Tự động fallback về pool mặc định nếu pool riêng gặp sự cố để chống lỗi 502
# Cú pháp: php_fpm_service.sh <create|start|stop|restart|reload|delete|set-disable-functions> <username> <php_version> [hosting_user] [disable_functions]
# ==============================================================================

set -uo pipefail

ACTION="${1:-help}"
USERNAME="${2:-}"
PHP_VER="${3:-8.2}"
HOSTING_USER="${4:-$USERNAME}"
CUSTOM_DISABLE_FUNCS="${5:-}"

if [ -z "$USERNAME" ] || [ -z "$PHP_VER" ]; then
    echo "[-] Thiếu tham số: php_fpm_service.sh <create|start|stop|restart|reload|delete|set-disable-functions> <username> <php_version> [hosting_user] [disable_functions]"
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

        # 1. Sinh file cấu hình standalone hoàn chỉnh từ template php_fpm_pool.tpl
        TPL_FILE="/opt/wpc/templates/vhost_nginx/php_fpm_pool.tpl"
        [ ! -f "$TPL_FILE" ] && TPL_FILE="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)/templates/vhost_nginx/php_fpm_pool.tpl"

        # Quyết định danh sách disable_functions:
        # - Ưu tiên 1: Tham số truyền vào ($CUSTOM_DISABLE_FUNCS) hoặc biến môi trường DISABLE_FUNCTIONS
        # - Ưu tiên 2: Nếu file $CONF_FILE đã tồn tại trước đó, GIỮ NGUYÊN cấu hình php_admin_value[disable_functions]
        # - Ưu tiên 3: Mặc định an toàn hệ thống
        DEFAULT_DISABLE_FUNCS="exec,passthru,shell_exec,system,proc_open,popen,parse_ini_file,show_source"
        if [ "$USERNAME" = "pma" ] || [ "$USERNAME" = "admin" ]; then
            DEFAULT_DISABLE_FUNCS="exec,passthru,shell_exec,system,proc_open,popen"
        fi

        FINAL_DISABLE_FUNCS=""
        if [ -n "$CUSTOM_DISABLE_FUNCS" ]; then
            FINAL_DISABLE_FUNCS="$CUSTOM_DISABLE_FUNCS"
        elif [ -n "${DISABLE_FUNCTIONS:-}" ]; then
            FINAL_DISABLE_FUNCS="$DISABLE_FUNCTIONS"
        elif [ -f "$CONF_FILE" ]; then
            EXISTING_VAL=$(grep -E '^[;[:space:]]*php_admin_value\[disable_functions\]' "$CONF_FILE" 2>/dev/null | tail -n1 | sed -E 's/^[;[:space:]]*php_admin_value\[disable_functions\][[:space:]]*=[[:space:]]*//' | tr -d '"' | tr -d "'")
            if [ -n "$EXISTING_VAL" ]; then
                FINAL_DISABLE_FUNCS="$EXISTING_VAL"
            fi
        fi

        [ -z "$FINAL_DISABLE_FUNCS" ] && FINAL_DISABLE_FUNCS="$DEFAULT_DISABLE_FUNCS"
        if [ "$FINAL_DISABLE_FUNCS" = "none" ] || [ "$FINAL_DISABLE_FUNCS" = "off" ] || [ "$FINAL_DISABLE_FUNCS" = "empty" ]; then
            FINAL_DISABLE_FUNCS=""
        fi

        if [ -f "$TPL_FILE" ]; then
            sed \
                -e "s|{{PID_FILE}}|$PID_FILE|g" \
                -e "s|{{ERROR_LOG}}|/var/log/php${PHP_VER}-fpm-${USERNAME}.log|g" \
                -e "s|{{LOG_LEVEL}}|notice|g" \
                -e "s|{{PHP_VER}}|$PHP_VER|g" \
                -e "s|{{USERNAME}}|$USERNAME|g" \
                -e "s|{{FPM_GRP}}|$FPM_GRP|g" \
                -e "s|{{SOCK_FILE}}|$SOCK_FILE|g" \
                -e "s|{{WORKER_USER}}|$WORKER_USER|g" \
                -e "s|{{WORKER_GRP}}|$WORKER_GRP|g" \
                -e "s|{{LISTEN_MODE}}|0666|g" \
                -e "s|{{LISTEN_BACKLOG}}|511|g" \
                -e "s|{{PM_DIRECTIVES}}|pm = dynamic\npm.max_children = 20\npm.start_servers = 4\npm.min_spare_servers = 2\npm.max_spare_servers = 6\npm.process_idle_timeout = 60s\npm.max_requests = 2000|g" \
                -e "s|{{REQUEST_TERMINATE_TIMEOUT}}|0s|g" \
                -e "s|{{REQUEST_SLOWLOG_TIMEOUT}}|0s|g" \
                -e "s|{{SLOWLOG}}|/home/${USERNAME}/logs/php-fpm.slow.log|g" \
                -e "s|{{CATCH_WORKERS_OUTPUT}}|yes|g" \
                -e "s|{{DECORATE_WORKERS_OUTPUT}}|no|g" \
                -e "s|{{CLEAR_ENV}}|no|g" \
                -e "s|{{PM_STATUS_PATH}}|/status-${USERNAME}|g" \
                -e "s|{{PING_PATH}}|/ping-${USERNAME}|g" \
                -e "s|{{PING_RESPONSE}}|pong|g" \
                -e "s|{{RLIMIT_FILES}}|65535|g" \
                -e "s|{{RLIMIT_CORE}}|0|g" \
                -e "s|{{PHP_OPEN_BASEDIR}}|/home/${USERNAME}/:/tmp/:/var/tmp/|g" \
                -e "s|{{PHP_DISABLE_FUNCTIONS}}|$FINAL_DISABLE_FUNCS|g" \
                -e "s|{{CUSTOM_DIRECTIVES}}||g" \
                -e "s|{{EXT_CONFIG}}||g" \
                "$TPL_FILE" > "$CONF_FILE"
        else
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

php_admin_value[open_basedir] = /home/${USERNAME}/:/tmp/:/var/tmp/
php_admin_value[upload_tmp_dir] = /home/${USERNAME}/.tmp
php_admin_value[session.save_path] = /home/${USERNAME}/.sessions
php_admin_value[disable_functions] = ${FINAL_DISABLE_FUNCS}
php_admin_flag[log_errors] = on
php_admin_value[error_log] = /home/${USERNAME}/logs/php-fpm.error.log
EOF
        fi

        # 1.1 Tạo thư mục conf.d riêng cho User để kiểm soát nạp Extension qua PHP_INI_SCAN_DIR (giảm tải RAM)
        USER_CONF_D="$USER_CONF_DIR/${USERNAME}/conf.d"
        USER_ENV_FILE="$USER_CONF_DIR/${USERNAME}.env"
        mkdir -p "$USER_CONF_D" 2>/dev/null || true
        chmod 755 "$USER_CONF_D" 2>/dev/null || true
        echo "PHP_INI_SCAN_DIR=$USER_CONF_D" > "$USER_ENV_FILE"
        chmod 644 "$USER_ENV_FILE" 2>/dev/null || true

        # Khởi tạo symlink mặc định (ưu tiên conf.d gốc đã có tiền tố số để đảm bảo thứ tự nạp module)
        SRC_DIR=""
        for cdir in "/etc/php/${PHP_VER}/fpm/conf.d" "/etc/php/${PHP_VER}/cli/conf.d" "/etc/php/${PHP_VER}/mods-available" "/etc/opt/remi/php$(echo "$PHP_VER" | tr -d '.')/php.d" "/etc/php.d"; do
            if [ -d "$cdir" ] && [ -n "$(ls -A "$cdir"/*.ini 2>/dev/null)" ]; then
                SRC_DIR="$cdir"
                break
            fi
        done
        if [ -n "$SRC_DIR" ]; then
            for inifile in "$SRC_DIR"/*.ini; do
                [ -f "$inifile" ] || continue
                [ ! -e "$USER_CONF_D/$(basename "$inifile")" ] && ln -sf "$inifile" "$USER_CONF_D/$(basename "$inifile")" 2>/dev/null || true
            done
        fi

        # Đảm bảo mysqlnd load trước mysqli (tránh lỗi undefined symbol và lỗi thiếu phần mở rộng mysqli)
        rm -f "$USER_CONF_D/mysqli.ini" "$USER_CONF_D/mysqlnd.ini" "$USER_CONF_D/pdo_mysql.ini" 2>/dev/null || true
        for mdir in "/etc/php/${PHP_VER}/fpm/conf.d" "/etc/php/${PHP_VER}/mods-available" "/etc/php/${PHP_VER}/cli/conf.d"; do
            [ -f "$mdir/mysqlnd.ini" ] && ln -sf "$mdir/mysqlnd.ini" "$USER_CONF_D/10-mysqlnd.ini" 2>/dev/null || true
            [ -f "$mdir/10-mysqlnd.ini" ] && ln -sf "$mdir/10-mysqlnd.ini" "$USER_CONF_D/10-mysqlnd.ini" 2>/dev/null || true
            [ -f "$mdir/mysqli.ini" ] && ln -sf "$mdir/mysqli.ini" "$USER_CONF_D/20-mysqli.ini" 2>/dev/null || true
            [ -f "$mdir/20-mysqli.ini" ] && ln -sf "$mdir/20-mysqli.ini" "$USER_CONF_D/20-mysqli.ini" 2>/dev/null || true
            [ -f "$mdir/pdo_mysql.ini" ] && ln -sf "$mdir/pdo_mysql.ini" "$USER_CONF_D/20-pdo_mysql.ini" 2>/dev/null || true
            [ -f "$mdir/20-pdo_mysql.ini" ] && ln -sf "$mdir/20-pdo_mysql.ini" "$USER_CONF_D/20-pdo_mysql.ini" 2>/dev/null || true
            [ -f "$mdir/mbstring.ini" ] && ln -sf "$mdir/mbstring.ini" "$USER_CONF_D/20-mbstring.ini" 2>/dev/null || true
        done

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
EnvironmentFile=-/etc/php/${PHP_VER}/fpm/users/%i.env
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
        rm -f "$CONF_FILE" "$PID_FILE" "$SOCK_FILE" "$USER_CONF_DIR/${USERNAME}.env" 2>/dev/null || true
        rm -rf "$USER_CONF_DIR/${USERNAME}" 2>/dev/null || true
        systemctl daemon-reload 2>/dev/null || true
        ;;

    set-disable-functions)
        NEW_FUNCS="${CUSTOM_DISABLE_FUNCS:-${4:-}}"
        [ "$NEW_FUNCS" = "none" ] || [ "$NEW_FUNCS" = "off" ] && NEW_FUNCS=""
        if [ -f "$CONF_FILE" ]; then
            if grep -q "php_admin_value\[disable_functions\]" "$CONF_FILE"; then
                sed -i -E "s|^[;[:space:]]*php_admin_value\[disable_functions\].*|php_admin_value[disable_functions] = ${NEW_FUNCS}|g" "$CONF_FILE"
            else
                echo "php_admin_value[disable_functions] = ${NEW_FUNCS}" >> "$CONF_FILE"
            fi
            systemctl reload "$SVC_NAME" 2>/dev/null || systemctl restart "$SVC_NAME" 2>/dev/null || true
            echo "[+] Đã cập nhật disable_functions cho $USERNAME ($PHP_VER) thành công."
        else
            echo "[-] Không tìm thấy file cấu hình: $CONF_FILE"
            exit 1
        fi
        ;;

    *)
        echo "[-] Lệnh không hợp lệ: $ACTION"
        exit 1
        ;;
esac

exit 0
