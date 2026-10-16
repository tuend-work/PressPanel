#!/usr/bin/env bash
# ==============================================================================
# PressPanel: update.sh - Cập nhật Panel lên phiên bản mới nhất từ GitHub
# Bảo toàn 100% dữ liệu database, website người dùng và file cấu hình
# Cách chạy: sudo bash /opt/wpc/scripts/update.sh
# ==============================================================================

set -euo pipefail

INSTALL_DIR="/opt/wpc"
REPO_URL="https://github.com/tuend-work/PressPanel.git"
TMP_DIR="/tmp/presspanel-update-$$"

echo "=========================================================="
echo "         CẬP NHẬT PRESSPANEL TỪ GITHUB                   "
echo "=========================================================="

if [ "$EUID" -ne 0 ]; then
    echo "[-] Vui lòng chạy lệnh với quyền root: sudo bash update.sh"
    exit 1
fi

# Tự động chuyển giao / migration nếu máy chủ hiện tại đang chạy tại /opt/presspanel
if [ ! -d "$INSTALL_DIR" ] && [ -d "/opt/presspanel" ]; then
    echo "[+] Phát hiện dữ liệu cũ tại /opt/presspanel, tự động tạo liên kết symlink sang /opt/wpc..."
    ln -sfn /opt/presspanel /opt/wpc
fi

if [ ! -d "$INSTALL_DIR" ]; then
    echo "[-] Không tìm thấy thư mục cài đặt $INSTALL_DIR! Vui lòng cài đặt trước."
    exit 1
fi

# 1. Tải phiên bản mới nhất từ GitHub
echo "[+] Đang tải mã nguồn mới nhất từ $REPO_URL..."
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
# Đảm bảo xử lý triệt để nếu trước đó bị copy lồng app/app
if [ -d "$INSTALL_DIR/app/app" ]; then
    cp -r "$INSTALL_DIR/app/app"/* "$INSTALL_DIR/app/" 2>/dev/null || true
    rm -rf "$INSTALL_DIR/app/app"
fi
cp -r "$TMP_DIR/app"/* "$INSTALL_DIR/app/"
cp -r "$TMP_DIR/scripts"/* "$INSTALL_DIR/scripts/" 2>/dev/null || true
cp -r "$TMP_DIR/templates"/* "$INSTALL_DIR/templates/" 2>/dev/null || true
mkdir -p "$INSTALL_DIR/static"
cp -r "$TMP_DIR/static"/* "$INSTALL_DIR/static/" 2>/dev/null || true
cp "$TMP_DIR/VERSION" "$INSTALL_DIR/" 2>/dev/null || true
# 3.1 Cập nhật tài khoản hệ thống sang wpc
if ! id -u "wpc" >/dev/null 2>&1; then
    echo "[+] Khởi tạo user hệ thống mới: wpc..."
    useradd -r -s /usr/sbin/nologin -d "$INSTALL_DIR" wpc 2>/dev/null || true
    usermod -aG presspanel wpc 2>/dev/null || true
fi

chmod +x "$INSTALL_DIR"/scripts/*.sh "$INSTALL_DIR"/scripts/apps/*.sh "$INSTALL_DIR"/scripts/wpc "$INSTALL_DIR"/scripts/presspanel 2>/dev/null || true

# Cập nhật CLI tool /usr/local/bin/wpc và /usr/local/bin/presspanel
if [ -f "$TMP_DIR/scripts/wpc" ]; then
    cp "$TMP_DIR/scripts/wpc" /usr/local/bin/wpc
    chmod +x /usr/local/bin/wpc
fi
if [ -f "$TMP_DIR/scripts/presspanel" ]; then
    cp "$TMP_DIR/scripts/presspanel" /usr/local/bin/presspanel
    chmod +x /usr/local/bin/presspanel
else
    ln -sf /usr/local/bin/wpc /usr/local/bin/presspanel 2>/dev/null || true
fi

# 4. Nâng cấp các thư viện Python nếu có bổ sung
echo "[+] Kiểm tra và cập nhật dependencies Python..."
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$TMP_DIR/requirements.txt"

# 5. Chạy database migration (nếu có bảng/cột mới)
echo "[+] Kiểm tra và cập nhật cấu trúc cơ sở dữ liệu SQLite..."
cd "$INSTALL_DIR"
"$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/app/db/init_db.py"

# 6. Đảm bảo phân quyền chính xác cho wpc và presspanel
PANEL_USER="wpc"
if ! id -u "$PANEL_USER" >/dev/null 2>&1; then
    PANEL_USER="presspanel"
fi

chown -R "$PANEL_USER":"$PANEL_USER" "$INSTALL_DIR/app" "$INSTALL_DIR/data" "$INSTALL_DIR/logs" "$INSTALL_DIR/static" "$INSTALL_DIR/conf" 2>/dev/null || true
echo "$PANEL_USER ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/wpc 2>/dev/null || true
chmod 440 /etc/sudoers.d/wpc 2>/dev/null || true
if [ -f /etc/sudoers.d/presspanel ]; then
    cp /etc/sudoers.d/wpc /etc/sudoers.d/presspanel 2>/dev/null || true
    chmod 440 /etc/sudoers.d/presspanel 2>/dev/null || true
fi

# 7. Đăng ký & Khởi động lại dịch vụ
echo "[+] Đăng ký dịch vụ wpc.service và dọn dẹp presspanel.service cũ..."
# Dừng và vô hiệu hóa triệt để presspanel.service cũ để tránh xung đột cổng
if systemctl is-active --quiet presspanel 2>/dev/null; then
    echo "[+] Dừng dịch vụ presspanel.service cũ..."
    systemctl stop presspanel 2>/dev/null || true
fi
systemctl disable presspanel 2>/dev/null || true
rm -f /etc/systemd/system/presspanel.service 2>/dev/null || true

if [ -f "$TMP_DIR/systemd/wpc.service" ]; then
    cp "$TMP_DIR/systemd/wpc.service" /etc/systemd/system/wpc.service
elif [ -f "$TMP_DIR/systemd/presspanel.service" ]; then
    cp "$TMP_DIR/systemd/presspanel.service" /etc/systemd/system/wpc.service
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

# Tự động di chuyển các pool hiện có (như presspanellocal) sang Standalone Service
for pdir in /etc/php/*/fpm/pool.d; do
    [ -d "$pdir" ] || continue
    PVER=$(echo "$pdir" | cut -d'/' -f4)
    for conf in "$pdir"/*.conf; do
        [ -f "$conf" ] || continue
        UNAME=$(basename "$conf" .conf)
        [ "$UNAME" != "www" ] || continue
        echo "[+] Di chuyển pool user $UNAME (PHP $PVER) sang Systemd Template Service..."
        # Xóa file cấu hình cũ trong pool.d và giải phóng socket cũ
        rm -f "$conf" "/run/php/php${PVER}-fpm-${UNAME}.sock" 2>/dev/null || true
        systemctl reload "php${PVER}-fpm" 2>/dev/null || true
        bash "$INSTALL_DIR/scripts/php_fpm_service.sh" create "$UNAME" "$PVER" "$UNAME" 2>/dev/null || true
    done
done

# Cập nhật cấu hình slice cho các user đã có standalone service
for uconf in /etc/php/*/fpm/users/*.conf; do
    [ -f "$uconf" ] || continue
    UNAME=$(basename "$uconf" .conf)
    PVER=$(echo "$uconf" | cut -d'/' -f4)
    bash "$INSTALL_DIR/scripts/php_fpm_service.sh" create "$UNAME" "$PVER" "$UNAME" 2>/dev/null || true
done

# Khởi động lại các PHP-FPM để nhận drop-in và gán ngay PID vào Cgroups v2
for php_ver in 5.6 7.0 7.1 7.2 7.3 7.4 8.0 8.1 8.2 8.3 8.4 8.5; do
    if systemctl is-active --quiet "php${php_ver}-fpm" 2>/dev/null; then
        systemctl restart "php${php_ver}-fpm" 2>/dev/null || true
    fi
done
/opt/wpc/scripts/cgroup_attach.sh 2>/dev/null || true

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

# 8. Dọn dẹp thư mục tạm
rm -rf "$TMP_DIR"

NEW_VER=$(cat "$INSTALL_DIR/VERSION" 2>/dev/null || echo "latest")
PANEL_PORT=$(grep -E "^port=" "$INSTALL_DIR/conf/presspanel.conf" 2>/dev/null | cut -d'=' -f2 | tr -d ' \r\n' || echo "8888")

echo "=========================================================="
echo "   CẬP NHẬT THÀNH CÔNG LÊN PHIÊN BẢN: $NEW_VER           "
echo "   Trạng thái dịch vụ: $(systemctl is-active wpc 2>/dev/null || systemctl is-active presspanel) "
echo "   Giao diện quản trị: http://<IP_MAY_CHU>:$PANEL_PORT   "
echo "=========================================================="
