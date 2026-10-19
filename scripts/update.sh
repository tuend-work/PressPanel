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

# Cập nhật CLI tool /usr/local/bin/wpc
if [ -f "$TMP_DIR/scripts/wpc" ]; then
    cp "$TMP_DIR/scripts/wpc" /usr/local/bin/wpc
    chmod +x /usr/local/bin/wpc
fi
rm -f /usr/local/bin/presspanel 2>/dev/null || true

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
fi
systemctl daemon-reload 2>/dev/null || true
systemctl enable wpc 2>/dev/null || true
systemctl restart wpc 2>/dev/null || true

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

# Restart Webserver để nạp lại danh sách supplementary groups cho nobody & www-data
if systemctl is-active --quiet lsws 2>/dev/null; then
    systemctl restart lsws 2>/dev/null || true
elif [ -x "/usr/local/lsws/bin/lswsctrl" ]; then
    /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || true
fi

if systemctl is-active --quiet nginx 2>/dev/null; then
    systemctl restart nginx 2>/dev/null || true
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

rm -rf "$TMP_DIR"

NEW_VER=$(cat "$INSTALL_DIR/VERSION" 2>/dev/null || echo "latest")
PANEL_PORT=$(grep -E "^port=" "$INSTALL_DIR/conf/wpc.conf" 2>/dev/null | cut -d'=' -f2 | tr -d ' \r\n' || echo "8888")

echo "=========================================================="
echo "   CẬP NHẬT THÀNH CÔNG LÊN PHIÊN BẢN: $NEW_VER           "
echo "   Trạng thái dịch vụ: $(systemctl is-active wpc 2>/dev/null) "
echo "   Giao diện quản trị: http://<IP_MAY_CHU>:$PANEL_PORT   "
echo "=========================================================="
