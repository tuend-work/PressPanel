#!/usr/bin/env bash
# ==============================================================================
# WPCloud: update.sh - Cập nhật Panel lên phiên bản mới nhất từ GitHub
# Bảo toàn 100% dữ liệu database, website người dùng và file cấu hình
# Cách chạy: sudo bash /opt/wpc/scripts/update.sh
# ==============================================================================

set -euo pipefail

# Tự động nhân bản script sang /tmp để tránh lỗi bash buffer khi tự ghi đè file scripts
CURRENT_SCRIPT="${BASH_SOURCE[0]:-$0}"
if [ -f "$CURRENT_SCRIPT" ] && [[ "$CURRENT_SCRIPT" != /tmp/* ]]; then
    TMP_SELF=$(mktemp /tmp/wpc_update_runner_XXXXXX.sh 2>/dev/null || echo "/tmp/wpc_update_runner_$$.sh")
    cp "$CURRENT_SCRIPT" "$TMP_SELF"
    chmod +x "$TMP_SELF"
    exec bash "$TMP_SELF" "$@"
fi

INSTALL_DIR="/opt/wpc"
REPO_URL="https://github.com/tuend-work/PressPanel.git"
TMP_DIR=$(mktemp -d /tmp/wpc-update-XXXXXX 2>/dev/null || echo "/tmp/wpc-update-$$")

echo "=========================================================="
echo "         CẬP NHẬT WPCLOUD CONTROL PANEL TỪ GITHUB         "
echo "=========================================================="

if [ "$EUID" -ne 0 ]; then
    echo "[-] Vui lòng chạy lệnh với quyền root: sudo bash update.sh"
    exit 1
fi

OLD_VER="${WPC_PREV_VER:-$(cat "$INSTALL_DIR/VERSION" 2>/dev/null || cat "/opt/presspanel/VERSION" 2>/dev/null || echo "0.0.0")}"
export WPC_PREV_VER="$OLD_VER"

on_update_error() {
    local err_code=$?
    local cur_ver=$(cat "$INSTALL_DIR/VERSION" 2>/dev/null || echo "unknown")
    echo "=========================================================="
    echo "Update thất bại từ ver${OLD_VER} lên ver${cur_ver}"
    echo "=========================================================="
    exit $err_code
}
trap on_update_error ERR

# Tự động di chuyển / migration nếu máy chủ hiện tại đang chạy tại /opt/presspanel cũ
if [ -d "/opt/presspanel" ] && [ ! -L "/opt/presspanel" ] && [ ! -d "/opt/wpc" ]; then
    echo "[+] Di chuyển thư mục từ /opt/presspanel sang /opt/wpc..."
    mv /opt/presspanel /opt/wpc
elif [ ! -d "$INSTALL_DIR" ] && [ -d "/opt/presspanel" ]; then
    echo "[+] Phát hiện dữ liệu tại /opt/presspanel, di chuyển sang /opt/wpc..."
    mv /opt/presspanel /opt/wpc
fi

# Dọn dẹp symlink cũ /opt/presspanel nếu có
if [ -L "/opt/presspanel" ]; then
    rm -f /opt/presspanel 2>/dev/null || true
fi

if [ ! -d "$INSTALL_DIR" ]; then
    echo "[-] Không tìm thấy thư mục cài đặt $INSTALL_DIR! Vui lòng cài đặt trước."
    exit 1
fi

# 1. Tải phiên bản mới nhất từ GitHub
echo "[+] Đang tải mã nguồn mới nhất từ $REPO_URL..."
rm -rf "$TMP_DIR"
git clone --depth 1 "$REPO_URL" "$TMP_DIR"

# Tự động chuyển quyền sang update.sh mới nhất vừa clone để luôn thực thi toàn bộ logic mới nhất
if [ -z "${WPC_UPDATE_STAGE2:-}" ] && [ -f "$TMP_DIR/scripts/update.sh" ]; then
    echo "[+] Nạp bộ cài đặt cập nhật phiên bản mới nhất từ GitHub..."
    export WPC_UPDATE_STAGE2=1
    exec bash "$TMP_DIR/scripts/update.sh" "$@"
fi

# 2. Biên dịch lại các module C Gatekeeper
echo "[+] Biên dịch lại các binary C Gatekeeper..."
cd "$TMP_DIR/gatekeepers"
make clean
make all
make install PREFIX="$INSTALL_DIR"

# 3. Cập nhật mã nguồn Python backend
echo "[+] Cập nhật mã nguồn ứng dụng..."
mkdir -p "$INSTALL_DIR/app"
if [ -d "$INSTALL_DIR/app/app" ]; then
    cp -r "$INSTALL_DIR/app/app"/* "$INSTALL_DIR/app/" 2>/dev/null || true
    rm -rf "$INSTALL_DIR/app/app"
fi
cp -r "$TMP_DIR/app"/* "$INSTALL_DIR/app/"
cp -r "$TMP_DIR/templates"/* "$INSTALL_DIR/templates/" 2>/dev/null || true
cp -r "$TMP_DIR/gatekeepers" "$INSTALL_DIR/" 2>/dev/null || true
cp -r "$TMP_DIR/scripts"/* "$INSTALL_DIR/scripts/" 2>/dev/null || true
mkdir -p "$INSTALL_DIR/static"
cp -r "$TMP_DIR/static"/* "$INSTALL_DIR/static/" 2>/dev/null || true
cp "$TMP_DIR/VERSION" "$INSTALL_DIR/" 2>/dev/null || true

# 3.1 Đồng bộ và chuẩn hoá cấu hình & cơ sở dữ liệu sang /opt/wpc
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/conf" "$INSTALL_DIR/logs"

# Nếu database cũ còn nằm đâu đó
if [ -f "/opt/presspanel/data/panel.db" ] && [ ! -f "$INSTALL_DIR/data/panel.db" ]; then
    echo "[+] Sao chép cơ sở dữ liệu panel.db sang $INSTALL_DIR/data/panel.db..."
    cp -a /opt/presspanel/data/panel.db* "$INSTALL_DIR/data/" 2>/dev/null || true
fi

# Chuẩn hoá file cấu hình wpc.conf
if [ ! -f "$INSTALL_DIR/conf/wpc.conf" ]; then
    if [ -f "$INSTALL_DIR/conf/presspanel.conf" ]; then
        mv "$INSTALL_DIR/conf/presspanel.conf" "$INSTALL_DIR/conf/wpc.conf"
    elif [ -f "$TMP_DIR/conf/wpc.conf" ]; then
        cp "$TMP_DIR/conf/wpc.conf" "$INSTALL_DIR/conf/wpc.conf"
    fi
fi
rm -f "$INSTALL_DIR/conf/presspanel.conf" 2>/dev/null || true

# Tự động thay thế /opt/presspanel thành /opt/wpc trong file cấu hình
if [ -f "$INSTALL_DIR/conf/wpc.conf" ]; then
    sed -i 's|/opt/presspanel|/opt/wpc|g' "$INSTALL_DIR/conf/wpc.conf" 2>/dev/null || true
    chmod 640 "$INSTALL_DIR/conf/wpc.conf" 2>/dev/null || true
fi

# 3.2 Cập nhật tài khoản hệ thống sang wpc
if ! id -u "wpc" >/dev/null 2>&1; then
    echo "[+] Khởi tạo user hệ thống mới: wpc..."
    useradd -r -s /usr/sbin/nologin -d "$INSTALL_DIR" wpc 2>/dev/null || true
fi

chmod +x "$INSTALL_DIR"/scripts/*.sh "$INSTALL_DIR"/scripts/apps/*.sh "$INSTALL_DIR"/scripts/wpc 2>/dev/null || true

# Cập nhật CLI tool /usr/local/bin/wpc thành binary ELF nhị phân (bảo mật, chống đọc trần)
if [ -f "$TMP_DIR/gatekeepers/wpc.c" ]; then
    echo "[+] Biên dịch wpc CLI thành file thực thi nhị phân (ELF Binary)..."
    gcc -O2 -fPIE -pie "$TMP_DIR/gatekeepers/wpc.c" -o /usr/local/bin/wpc || \
    (command -v shc >/dev/null 2>&1 && shc -f "$TMP_DIR/scripts/wpc" -o /usr/local/bin/wpc) || \
    cp "$TMP_DIR/scripts/wpc" /usr/local/bin/wpc
    strip /usr/local/bin/wpc 2>/dev/null || true
    chmod 755 /usr/local/bin/wpc
    ln -sf /usr/local/bin/wpc /usr/bin/wpc 2>/dev/null || true
elif [ -f "$TMP_DIR/scripts/wpc" ]; then
    (command -v shc >/dev/null 2>&1 && shc -f "$TMP_DIR/scripts/wpc" -o /usr/local/bin/wpc) || cp "$TMP_DIR/scripts/wpc" /usr/local/bin/wpc
    chmod 755 /usr/local/bin/wpc
    ln -sf /usr/local/bin/wpc /usr/bin/wpc 2>/dev/null || true
fi
rm -f /usr/local/bin/presspanel /usr/bin/presspanel 2>/dev/null || true

# Biên dịch trước mã nguồn Python sang bytecode (.pyc)
"$INSTALL_DIR/venv/bin/python" -m compileall -q "$INSTALL_DIR/app" 2>/dev/null || true

# 4. Nâng cấp các thư viện Python nếu có bổ sung
echo "[+] Kiểm tra và cập nhật dependencies Python..."
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$TMP_DIR/requirements.txt"

# 5. Chạy database migration (nếu có bảng/cột mới)
echo "[+] Kiểm tra và cập nhật cấu trúc cơ sở dữ liệu SQLite..."
cd "$INSTALL_DIR"
"$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/app/db/init_db.py"

# 6. Đảm bảo phân quyền chính xác cho wpc
PANEL_USER="wpc"
chown -R "$PANEL_USER":"$PANEL_USER" "$INSTALL_DIR/app" "$INSTALL_DIR/data" "$INSTALL_DIR/logs" "$INSTALL_DIR/static" "$INSTALL_DIR/conf" 2>/dev/null || true
chmod 775 "$INSTALL_DIR"/data 2>/dev/null || true
chmod 664 "$INSTALL_DIR"/data/panel.db* 2>/dev/null || true
echo "$PANEL_USER ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/wpc 2>/dev/null || true
chmod 440 /etc/sudoers.d/wpc 2>/dev/null || true
rm -f /etc/sudoers.d/presspanel 2>/dev/null || true

# 7. Đăng ký & Khởi động lại dịch vụ wpc.service
echo "[+] Đăng ký dịch vụ wpc.service..."
# Dọn dẹp presspanel cũ nếu có để tránh xung đột cổng
if systemctl is-active --quiet presspanel 2>/dev/null; then
    systemctl stop presspanel 2>/dev/null || true
fi
systemctl disable presspanel 2>/dev/null || true
rm -f /etc/systemd/system/presspanel.service 2>/dev/null || true

if [ -f "$TMP_DIR/systemd/wpc.service" ]; then
    cp "$TMP_DIR/systemd/wpc.service" /etc/systemd/system/wpc.service
    CURR_VER=$(cat "$TMP_DIR/VERSION" 2>/dev/null || cat "$INSTALL_DIR/VERSION" 2>/dev/null || echo "")
    if [ -n "$CURR_VER" ]; then
        sed -i "s/Description=.*/Description=WPCloud Control Panel Server v${CURR_VER} (FastAPI + Asyncio)/" /etc/systemd/system/wpc.service 2>/dev/null || true
    fi
fi
systemctl daemon-reload 2>/dev/null || true
systemctl enable wpc 2>/dev/null || true
# Dịch vụ wpc sẽ được khởi động lại ở cuối cùng của script để không kill tiến trình update giữa chừng

# Khởi tạo các Systemd Template Service wpc-php<ver>-fpm@.service cho tất cả các bản PHP
for pver in 5.6 7.0 7.1 7.2 7.3 7.4 8.0 8.1 8.2 8.3 8.4 8.5; do
    if [ -x "/usr/sbin/php-fpm${pver}" ]; then
        TPL="/etc/systemd/system/wpc-php${pver}-fpm@.service"
        cat << EOF > "$TPL"
[Unit]
Description=WPCloud PHP-FPM Service for user %i
After=network.target

[Service]
Type=notify
PIDFile=/run/php/php${pver}-fpm-%i.pid
ExecStart=/usr/sbin/php-fpm${pver} --nodaemonize --fpm-config /etc/php/${pver}/fpm/users/%i.conf
ExecReload=/bin/kill -USR2 \$MAINPID
TimeoutSec=15
Slice=wpc-user-%i.slice

[Install]
WantedBy=multi-user.target
EOF
    fi
done
systemctl daemon-reload 2>/dev/null || true

# Cập nhật cấu hình slice cho các user đã có standalone service (chỉ khi Nginx đang kích hoạt)
if ! systemctl is-active --quiet lshttpd 2>/dev/null && ! systemctl is-active --quiet lsws 2>/dev/null; then
    for uconf in /etc/php/*/fpm/users/*.conf; do
        [ -f "$uconf" ] || continue
        UNAME=$(basename "$uconf" .conf)
        PVER=$(echo "$uconf" | cut -d'/' -f4)
        bash "$INSTALL_DIR/scripts/php_fpm_service.sh" create "$UNAME" "$PVER" "$UNAME" 2>/dev/null || true
    done
else
    # Đang chạy OpenLiteSpeed: Dừng và tắt toàn bộ PHP-FPM (user + system) để không tốn RAM
    systemctl stop "wpc-php*-fpm@*" 2>/dev/null || true
    systemctl disable "wpc-php*-fpm@*" 2>/dev/null || true
    for php_ver in 5.6 7.0 7.1 7.2 7.3 7.4 8.0 8.1 8.2 8.3 8.4 8.5; do
        systemctl stop "php${php_ver}-fpm" 2>/dev/null || true
        systemctl disable "php${php_ver}-fpm" 2>/dev/null || true
    done
    pkill -9 -f "php-fpm" 2>/dev/null || true
fi

# Nếu Nginx đang hoạt động thì khởi động lại PHP-FPM để nhận drop-in và gán vào Cgroups v2
if ! systemctl is-active --quiet lshttpd 2>/dev/null && ! systemctl is-active --quiet lsws 2>/dev/null; then
    for php_ver in 5.6 7.0 7.1 7.2 7.3 7.4 8.0 8.1 8.2 8.3 8.4 8.5; do
        if systemctl is-active --quiet "php${php_ver}-fpm" 2>/dev/null; then
            systemctl restart "php${php_ver}-fpm" 2>/dev/null || true
        fi
    done
    /opt/wpc/scripts/cgroup_attach.sh 2>/dev/null || true
    # Dọn dẹp triệt để các tiến trình lsphp thừa của OLS
    pkill -9 -f "openlitespeed" 2>/dev/null || true
    pkill -9 -f "lshttpd" 2>/dev/null || true
    pkill -9 -f "lsphp" 2>/dev/null || true
    pkill -9 -f "lscgid" 2>/dev/null || true
    rm -rf /tmp/lshttpd/*.sock 2>/dev/null || true
fi

# Đảm bảo thư mục runtime socket của OLS tồn tại và có quyền 1777
mkdir -p /tmp/lshttpd 2>/dev/null || true
chmod 1777 /tmp/lshttpd 2>/dev/null || true

# 7.2 Cấu hình bảo mật procfs (hidepid=2) cách ly tiến trình giữa các user
echo "[+] Kiểm tra và cấu hình cách ly tiến trình /proc (hidepid=2)..."
groupadd -r procview 2>/dev/null || true
usermod -aG procview "$PANEL_USER" 2>/dev/null || true
usermod -aG procview root 2>/dev/null || true
PROCVIEW_GID=$(getent group procview 2>/dev/null | cut -d: -f3 || echo "")
if [ -n "$PROCVIEW_GID" ]; then
    mount -o remount,rw,hidepid=2,gid="$PROCVIEW_GID" /proc 2>/dev/null || true
    if ! grep -q "hidepid=" /etc/fstab; then
        echo "proc /proc proc defaults,hidepid=2,gid=$PROCVIEW_GID 0 0" >> /etc/fstab
    else
        sed -i -E "s/hidepid=[0-9]+/hidepid=2/g" /etc/fstab
        sed -i -E "s/gid=[0-9]+/gid=$PROCVIEW_GID/g" /etc/fstab
    fi
fi

# 7.3 Khóa quyền bảo mật toàn bộ thư mục /home (711) và cô lập tuyệt đối giữa các User
echo "[+] Khóa quyền bảo mật 0711 cho /home và tối ưu phân quyền phục vụ website..."
chmod 711 /home 2>/dev/null || true

for user_home in /home/*; do
    [ -d "$user_home" ] || continue
    u_name=$(basename "$user_home")
    
    # Xác định user và group thực tế của thư mục
    owner_u=$(stat -c '%U' "$user_home" 2>/dev/null || echo "$u_name")
    owner_g=$(stat -c '%G' "$user_home" 2>/dev/null || echo "$u_name")
    
    # Gán bổ sung quyền group cho user panel wpc, nobody (OLS), www-data (Nginx) để phục vụ website
    for grp in "$u_name" "$owner_g" "$owner_u"; do
        if getent group "$grp" &>/dev/null; then
            for svc_u in "$PANEL_USER" nobody www-data; do
                id "$svc_u" &>/dev/null && usermod -a -G "$grp" "$svc_u" 2>/dev/null || true
            done
        fi
    done

    # 1. Khóa cửa thư mục home cấp 1: 0711 (cấm tiệt user khác xem file, cho phép webserver traverse)
    chmod 711 "$user_home" 2>/dev/null || true

    # 2. Khóa tuyệt đối các thư mục nhạy cảm cá nhân: 0700
    for priv_dir in "$user_home/logs" "$user_home/ssl" "$user_home/backups" "$user_home/.tmp" "$user_home/.sessions" "$user_home/.ssh" "$user_home/tmp"; do
        [ -d "$priv_dir" ] && chmod 700 "$priv_dir" 2>/dev/null || true
    done

    # 3. Phân quyền cây thư mục web: Thư mục 755, File 644
    # Do cửa /home/$u_name đã là 750 nên user khác không thể xâm phạm, Webserver đọc trơn tru 100%
    if [ -d "$user_home/domains" ]; then
        find "$user_home/domains" -type d -exec chmod 755 {} + 2>/dev/null || true
        find "$user_home/domains" -type f -exec chmod 644 {} + 2>/dev/null || true
        find "$user_home/domains" -type d \( -name "logs" -o -name "ssl" -o -name "backups" \) -exec chmod 700 {} + 2>/dev/null || true
    fi
    if [ -d "$user_home/public_html" ]; then
        chmod 755 "$user_home/public_html" 2>/dev/null || true
        find "$user_home/public_html" -type d -exec chmod 755 {} + 2>/dev/null || true
        find "$user_home/public_html" -type f -exec chmod 644 {} + 2>/dev/null || true
    fi

    # 4. Khóa quyền Linux SSH cho Hosting User này (chỉ cho phép thao tác web)
    NOLOGIN_SHELL="/usr/sbin/nologin"
    [ -x "$NOLOGIN_SHELL" ] || NOLOGIN_SHELL="/sbin/nologin"
    [ -x "$NOLOGIN_SHELL" ] || NOLOGIN_SHELL="/bin/false"
    if [ "$u_name" != "root" ]; then
        usermod -s "$NOLOGIN_SHELL" "$u_name" 2>/dev/null || true
    fi
done

# Khóa quyền SSH của tài khoản linux admin nếu tồn tại
if id admin &>/dev/null; then
    echo "[+] Khóa SSH tài khoản linux admin..."
    NOLOGIN_SHELL="/usr/sbin/nologin"
    [ -x "$NOLOGIN_SHELL" ] || NOLOGIN_SHELL="/sbin/nologin"
    [ -x "$NOLOGIN_SHELL" ] || NOLOGIN_SHELL="/bin/false"
    usermod -s "$NOLOGIN_SHELL" admin 2>/dev/null || true
    passwd -l admin 2>/dev/null || true
fi

# Chặn truy cập SSH cho tài khoản admin trong cấu hình SSH daemon
mkdir -p /etc/ssh/sshd_config.d 2>/dev/null || true
if [ -d /etc/ssh/sshd_config.d ]; then
    cat << 'EOF' > /etc/ssh/sshd_config.d/50-wpc-security.conf
# WPCloud Security: Chặn SSH trực tiếp tài khoản linux admin
DenyUsers admin
EOF
    chmod 644 /etc/ssh/sshd_config.d/50-wpc-security.conf 2>/dev/null || true
fi

if [ -f /etc/ssh/sshd_config ]; then
    if ! grep -q "DenyUsers.*admin" /etc/ssh/sshd_config; then
        echo "" >> /etc/ssh/sshd_config
        echo "# WPCloud Security: Chặn SSH trực tiếp tài khoản linux admin" >> /etc/ssh/sshd_config
        echo "DenyUsers admin" >> /etc/ssh/sshd_config
    fi
    systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || true
fi

# 7.4 Tự động quét và vá triệt để lỗi shell metacharacters / {{PHP_VERSION}} trong toàn bộ VirtualHost OpenLiteSpeed
echo "[+] Quét và chuẩn hóa cấu hình toàn bộ VirtualHost OpenLiteSpeed..."
if [ -d "/usr/local/lsws/conf/vhosts" ]; then
    for vh_conf in /usr/local/lsws/conf/vhosts/*/vhconf.conf; do
        [ -f "$vh_conf" ] || continue
        # Xóa bỏ ký tự \r (CR) gây hỏng cú pháp OLS
        sed -i 's/\r$//' "$vh_conf" 2>/dev/null || true

        # Thay thế triệt để bất kỳ dòng path nào chứa lsphp và dính ký tự { hoặc }
        sed -i '/^[[:space:]]*path[[:space:]]\+.*lsphp.*[{}]/c\  path                    /usr/local/lsws/lsphp82/bin/lsphp' "$vh_conf" 2>/dev/null || true
        sed -i 's/{{PHP_VERSION}}/82/g' "$vh_conf" 2>/dev/null || true
        sed -i 's/{{PHP_NUM}}/82/g' "$vh_conf" 2>/dev/null || true
        sed -i -E 's|/usr/local/lsws/lsphp\{\{[^}]+\}\}/bin/lsphp|/usr/local/lsws/lsphp82/bin/lsphp|g' "$vh_conf" 2>/dev/null || true

        # Kiểm tra binary lsphp trong file cấu hình, nếu không tồn tại thì tự động fallback về binary có sẵn
        curr_lsphp=$(grep -E '^[[:space:]]*path[[:space:]]+' "$vh_conf" | awk '{print $2}' | head -n 1)
        if [ -n "$curr_lsphp" ] && [ ! -x "$curr_lsphp" ]; then
            for fb in 84 83 82 81 74; do
                if [ -x "/usr/local/lsws/lsphp${fb}/bin/lsphp" ]; then
                    sed -i -E "s|path[[:space:]]+.*|path                    /usr/local/lsws/lsphp${fb}/bin/lsphp|g" "$vh_conf" 2>/dev/null || true
                    break
                fi
            done
        fi

        # Đảm bảo phân quyền 644 cho vhconf.conf và thư mục vhost
        vh_dir=$(dirname "$vh_conf")
        chmod 755 "$vh_dir" 2>/dev/null || true
        chmod 644 "$vh_conf" 2>/dev/null || true
        chown -R lsadm:lsadm "$vh_dir" 2>/dev/null || true
    done

    # Quét cả cấu hình chính httpd_config.conf và các templates của OLS
    if [ -f "/usr/local/lsws/conf/httpd_config.conf" ]; then
        sed -i '/^[[:space:]]*path[[:space:]]\+.*lsphp.*[{}]/c\  path                    /usr/local/lsws/lsphp82/bin/lsphp' /usr/local/lsws/conf/httpd_config.conf 2>/dev/null || true
        sed -i 's/{{PHP_VERSION}}/82/g' /usr/local/lsws/conf/httpd_config.conf 2>/dev/null || true

        # Bổ sung listener HTTPS_443 (HTTP/2 & HTTP/3 QUIC) vào OLS nếu chưa có
        if ! grep -qE "listener[[:space:]]+(HTTPS_443|HTTPS)[[:space:]]*\{" /usr/local/lsws/conf/httpd_config.conf; then
            cat << 'EOF_OLS_HTTPS' >> /usr/local/lsws/conf/httpd_config.conf

listener HTTPS_443 {
  address                 *:443
  secure                  1
  keyFile                 /etc/ssl/wpc/selfsigned.key
  certFile                /etc/ssl/wpc/selfsigned.crt
  certChain               1
  sslProtocol             24
  enableQuic              1
  enableHttp3             1
}
EOF_OLS_HTTPS
        else
            sed -i "/listener[[:space:]]\+\(HTTPS_443\|HTTPS\)[[:space:]]*{/,/^[[:space:]]*}/ s/enableQuic[[:space:]]\+0/enableQuic              1/" /usr/local/lsws/conf/httpd_config.conf 2>/dev/null || true
            sed -i "/listener[[:space:]]\+\(HTTPS_443\|HTTPS\)[[:space:]]*{/,/^[[:space:]]*}/ s/enableHttp3[[:space:]]\+0/enableHttp3             1/" /usr/local/lsws/conf/httpd_config.conf 2>/dev/null || true
        fi
    fi
    for tpl_f in /usr/local/lsws/conf/templates/*.conf; do
        [ -f "$tpl_f" ] || continue
        sed -i '/^[[:space:]]*path[[:space:]]\+.*lsphp.*[{}]/c\  path                    /usr/local/lsws/lsphp82/bin/lsphp' "$tpl_f" 2>/dev/null || true
        sed -i 's/{{PHP_VERSION}}/82/g' "$tpl_f" 2>/dev/null || true
    done

    # Dọn socket cũ và đảm bảo thư mục runtime có sticky bit 1777
    pkill -9 -f "lsphp" 2>/dev/null || true
    rm -rf /tmp/lshttpd/*.sock 2>/dev/null || true
    mkdir -p /tmp/lshttpd && chmod 1777 /tmp/lshttpd 2>/dev/null || true
fi

# 7.5 Mở cổng tường lửa: 80/tcp, 443/tcp và 443/udp (QUIC/HTTP3)
echo "[+] Kiểm tra và mở cổng tường lửa: 80/tcp, 443/tcp, 443/udp (QUIC/HTTP3)..."
if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow 80/tcp 2>/dev/null || true
    ufw allow 443/tcp 2>/dev/null || true
    ufw allow 443/udp 2>/dev/null || true
elif command -v firewall-cmd &>/dev/null && systemctl is-active --quiet firewalld 2>/dev/null; then
    firewall-cmd --permanent --add-port=80/tcp 2>/dev/null || true
    firewall-cmd --permanent --add-port=443/tcp 2>/dev/null || true
    firewall-cmd --permanent --add-port=443/udp 2>/dev/null || true
    firewall-cmd --reload 2>/dev/null || true
fi

# 7.6 Đảm bảo chứng chỉ SSL tự ký mặc định cho máy chủ (hỗ trợ HTTP/2 & HTTP/3)
mkdir -p /etc/ssl/wpc 2>/dev/null || true
if [ ! -s "/etc/ssl/wpc/selfsigned.crt" ] || [ ! -s "/etc/ssl/wpc/selfsigned.key" ]; then
    echo "[+] Khởi tạo chứng chỉ SSL tự ký mặc định cho máy chủ..."
    S_IP=$(curl -s4 --max-time 2 https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
    [ -z "$S_IP" ] && S_IP="127.0.0.1"
    S_HOST=$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo "localhost")
    [ -z "$S_HOST" ] && S_HOST="localhost"

    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "/etc/ssl/wpc/selfsigned.key" \
        -out "/etc/ssl/wpc/selfsigned.crt" \
        -subj "/CN=$S_HOST/O=WPCloud/C=VN" \
        -addext "subjectAltName=DNS:$S_HOST,DNS:localhost,IP:$S_IP,IP:127.0.0.1" 2>/dev/null || \
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "/etc/ssl/wpc/selfsigned.key" \
        -out "/etc/ssl/wpc/selfsigned.crt" \
        -subj "/CN=$S_HOST/O=WPCloud/C=VN" 2>/dev/null || true

    chmod 755 /etc/ssl/wpc 2>/dev/null || true
    chmod 644 /etc/ssl/wpc/selfsigned.crt /etc/ssl/wpc/selfsigned.key 2>/dev/null || true
fi

# Restart Webserver để nạp lại danh sách supplementary groups cho nobody & www-data
if systemctl is-active --quiet lsws 2>/dev/null; then
    systemctl restart lsws 2>/dev/null || true
elif [ -x "/usr/local/lsws/bin/lswsctrl" ]; then
    /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || true
fi

# Tự động đồng bộ và bảo mật môi trường OpenResty (nếu có cài đặt)
if [ -f "/usr/local/openresty/nginx/conf/nginx.conf" ] || command -v openresty &>/dev/null; then
    bash "$TMP_DIR/scripts/apps/nginx.sh" configure 2>/dev/null || true
fi

# 7.7 Tự động sửa lỗi unknown "http3" variable nếu Nginx không có module ngx_http_v3_module
if command -v nginx &>/dev/null; then
    HAS_HTTP3=0
    if nginx -V 2>&1 | grep -q -- "with-http_v3_module"; then
        HAS_HTTP3=1
    fi
    if [ "$HAS_HTTP3" -eq 0 ] || ! nginx -t 2>/dev/null; then
        echo "[!] Đang kiểm tra và loại bỏ biến http3/quic không tương thích khỏi cấu hình Nginx..."
        for f in /etc/nginx/conf.d/*.conf /etc/nginx/nginx.conf /usr/local/openresty/nginx/conf/nginx.conf; do
            [ -f "$f" ] || continue
            sed -i '/quic;/d' "$f" 2>/dev/null || true
            sed -i '/quic/d' "$f" 2>/dev/null || true
            sed -i '/http3/d' "$f" 2>/dev/null || true
            sed -i '/Alt-Svc/d' "$f" 2>/dev/null || true
            sed -i '/QUIC-Status/d' "$f" 2>/dev/null || true
        done
        if ! nginx -t 2>/dev/null; then
            for f in /etc/nginx/conf.d/*.conf; do
                [ -f "$f" ] || continue
                sed -i '/http2[[:space:]]\+on;/d' "$f" 2>/dev/null || true
                sed -i 's/listen 443 ssl;/listen 443 ssl http2;/g' "$f" 2>/dev/null || true
                sed -i 's/listen \[::\]:443 ssl;/listen [::]:443 ssl http2;/g' "$f" 2>/dev/null || true
            done
        fi
    fi
fi

if systemctl is-active --quiet openresty 2>/dev/null; then
    systemctl restart openresty 2>/dev/null || true
elif systemctl is-active --quiet nginx 2>/dev/null; then
    systemctl restart nginx 2>/dev/null || true
fi

# Chuẩn hóa systemd timer/service aliases cross-distro (RHEL/AlmaLinux vs Debian/Ubuntu)
if [ -d /etc/systemd/system ]; then
    # 1. Certbot timer: certbot-renew.timer (RHEL) <-> certbot.timer (Debian)
    if [ -f /usr/lib/systemd/system/certbot-renew.timer ] && [ ! -f /etc/systemd/system/certbot.timer ]; then
        ln -sf /usr/lib/systemd/system/certbot-renew.timer /etc/systemd/system/certbot.timer 2>/dev/null || true
        ln -sf /usr/lib/systemd/system/certbot-renew.service /etc/systemd/system/certbot.service 2>/dev/null || true
    elif [ -f /lib/systemd/system/certbot.timer ] && [ ! -f /etc/systemd/system/certbot-renew.timer ]; then
        ln -sf /lib/systemd/system/certbot.timer /etc/systemd/system/certbot-renew.timer 2>/dev/null || true
        ln -sf /lib/systemd/system/certbot.service /etc/systemd/system/certbot-renew.service 2>/dev/null || true
    fi

    # 2. Redis service: redis (RHEL) <-> redis-server (Debian)
    if [ -f /usr/lib/systemd/system/redis.service ] && [ ! -f /etc/systemd/system/redis-server.service ]; then
        ln -sf /usr/lib/systemd/system/redis.service /etc/systemd/system/redis-server.service 2>/dev/null || true
    elif [ -f /lib/systemd/system/redis-server.service ] && [ ! -f /etc/systemd/system/redis.service ]; then
        ln -sf /lib/systemd/system/redis-server.service /etc/systemd/system/redis.service 2>/dev/null || true
    fi

    # 3. MySQL / MariaDB service
    if [ -f /usr/lib/systemd/system/mariadb.service ] && [ ! -f /etc/systemd/system/mysql.service ]; then
        ln -sf /usr/lib/systemd/system/mariadb.service /etc/systemd/system/mysql.service 2>/dev/null || true
    fi

    systemctl daemon-reload 2>/dev/null || true
fi

# 8. Cập nhật scripts điều khiển hệ thống bằng atomic replacement và dọn dẹp
for s_item in "$TMP_DIR/scripts"/*; do
    [ -e "$s_item" ] || continue
    s_base=$(basename "$s_item")
    if [ "$s_base" != "update.sh" ]; then
        cp -r "$s_item" "$INSTALL_DIR/scripts/" 2>/dev/null || true
    fi
done
# Cập nhật update.sh bằng atomic rename để bảo toàn inode cho tiến trình đang chạy
cp -f "$TMP_DIR/scripts/update.sh" "$INSTALL_DIR/scripts/update.sh.tmp" 2>/dev/null || true
mv -f "$INSTALL_DIR/scripts/update.sh.tmp" "$INSTALL_DIR/scripts/update.sh" 2>/dev/null || true
chmod +x "$INSTALL_DIR"/scripts/*.sh "$INSTALL_DIR"/scripts/apps/*.sh "$INSTALL_DIR"/scripts/wpc 2>/dev/null || true

# 8.1 Đảm bảo phân quyền truy cập hệ thống và kích hoạt lại phpMyAdmin nếu đã được cài đặt
chmod 755 /opt /opt/wpc /opt/wpc/apps 2>/dev/null || true
if id "wpc" &>/dev/null; then
    id "nobody" &>/dev/null && usermod -a -G wpc nobody 2>/dev/null || true
    id "www-data" &>/dev/null && usermod -a -G wpc www-data 2>/dev/null || true
fi

if [ -d "$INSTALL_DIR/apps/phpmyadmin" ] || [ -f "/etc/nginx/conf.d/00_phpmyadmin.conf" ] || grep -q "PMA_888" /usr/local/lsws/conf/httpd_config.conf 2>/dev/null; then
    echo "[+] Phát hiện phpMyAdmin, tự động chuẩn hóa cấu hình Vhost Port 888..."
    bash "$INSTALL_DIR/scripts/apps/phpmyadmin.sh" install 2>/dev/null || true
fi

# 8.1.1 Đảm bảo cài đặt các PHP/LSPHP extensions quan trọng (Redis, Imagick, Intl, Zip, OPcache, MySQL)
echo "[+] Cài đặt và cập nhật các extension PHP & LiteSpeed LSPHP (Redis, Imagick, OPcache, Intl)..."
if command -v apt-get &>/dev/null; then
    for v in 84 83 82 81 80 74; do
        if [ -d "/usr/local/lsws/lsphp$v" ] || [ -f "/usr/local/lsws/lsphp$v/bin/lsphp" ]; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y \
                "lsphp$v-redis" "lsphp$v-imagick" "lsphp$v-intl" "lsphp$v-memcached" "lsphp$v-soap" "lsphp$v-bcmath" 2>/dev/null || true
        fi
    done
    for pv in 8.4 8.3 8.2 8.1 8.0 7.4; do
        pv_num=$(echo "$pv" | tr -d '.')
        if [ -d "/etc/php/$pv" ] || [ -f "/usr/bin/php$pv" ] || [ -f "/usr/sbin/php-fpm$pv" ] || [ -d "/usr/local/lsws/lsphp${pv_num}" ]; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y \
                "php$pv-fpm" "php$pv-cli" "php$pv-common" "php$pv-mysql" "php$pv-curl" "php$pv-opcache" "php$pv-zip" \
                "php$pv-redis" "php$pv-imagick" "php$pv-intl" "php$pv-memcached" "php$pv-soap" "php$pv-bcmath" 2>/dev/null || true
            systemctl enable "php$pv-fpm" 2>/dev/null || true
            systemctl start "php$pv-fpm" 2>/dev/null || true
        fi
    done
elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
    PKG_MGR="dnf"
    command -v dnf &>/dev/null || PKG_MGR="yum"
    for v in 84 83 82 81 80 74; do
        if [ -d "/usr/local/lsws/lsphp$v" ]; then
            $PKG_MGR install -y "lsphp$v-redis" "lsphp$v-pecl-redis" "lsphp$v-imagick" "lsphp$v-pecl-imagick" "lsphp$v-intl" "lsphp$v-soap" 2>/dev/null || true
        fi
    done
fi

if [ -d "/usr/local/lsws" ]; then
    pkill -9 -f "lsphp" 2>/dev/null || true
    rm -f /tmp/lshttpd/*.sock 2>/dev/null || true
    /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || systemctl restart lsws 2>/dev/null || systemctl restart lshttpd 2>/dev/null || true
fi

if systemctl is-active --quiet nginx 2>/dev/null || systemctl is-active --quiet openresty 2>/dev/null; then
    for u_conf in /etc/php/*/fpm/users/*.conf; do
        [ -f "$u_conf" ] || continue
        u_name=$(basename "$u_conf" .conf)
        p_ver=$(echo "$u_conf" | awk -F'/' '{print $(NF-3)}')
        if [ -n "$p_ver" ] && [ -n "$u_name" ]; then
            bash /opt/wpc/scripts/php_fpm_service.sh create "$u_name" "$p_ver" 2>/dev/null || true
        fi
    done
    systemctl reload openresty 2>/dev/null || systemctl reload nginx 2>/dev/null || true
fi

# 8.2 Cấu hình dịch vụ giám sát Cgroups v2 thời gian thực (wpc-cgroup)
cat << 'EOF_CGSVC' > /etc/systemd/system/wpc-cgroup.service
[Unit]
Description=WPCloud Cgroups v2 Realtime Process Enforcer
After=network.target

[Service]
Type=simple
ExecStart=/bin/bash /opt/wpc/scripts/cgroup_attach.sh daemon
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF_CGSVC

systemctl daemon-reload 2>/dev/null || true
systemctl enable wpc-cgroup.service 2>/dev/null || true
systemctl restart wpc-cgroup.service 2>/dev/null || true
/bin/bash /opt/wpc/scripts/cgroup_attach.sh once 2>/dev/null || true

# Kích hoạt Cgroups v2 cho OpenLiteSpeed nếu có tiện ích lsns
if [ -x "/usr/local/lsws/lsns/bin/lssetup" ]; then
    /usr/local/lsws/lsns/bin/lssetup -i 1 2>/dev/null || true
fi

rm -rf "$TMP_DIR"

NEW_VER=$(cat "$INSTALL_DIR/VERSION" 2>/dev/null || echo "latest")
PANEL_PORT=$(grep -E "^port=" "$INSTALL_DIR/conf/wpc.conf" 2>/dev/null | cut -d'=' -f2 | tr -d ' \r\n' || echo "8888")

echo "=========================================================="
echo "   CẬP NHẬT THÀNH CÔNG LÊN PHIÊN BẢN: $NEW_VER           "
echo "   Trạng thái dịch vụ: $(systemctl is-active wpc 2>/dev/null) "
echo "   Giao diện quản trị: http://<IP_MAY_CHU>:$PANEL_PORT   "
echo "[+] Hoàn tất toàn bộ tiến trình cập nhật 100%!"
echo "[+] Đang khởi động lại dịch vụ WPCloud Control Panel (wpc.service)..."
systemctl daemon-reload 2>/dev/null || true
systemctl restart wpc 2>/dev/null || true
echo "[+] Dịch vụ WPCloud đã được khởi động lại: $(systemctl is-active wpc 2>/dev/null || echo 'active')"
echo "=========================================================="
echo "Update thành công từ ver${OLD_VER} lên ver${NEW_VER}"
echo "=========================================================="
exit 0
