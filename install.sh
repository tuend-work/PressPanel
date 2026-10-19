#!/usr/bin/env bash
# WPCloud (WPC) Installer Script
# Hỗ trợ: Debian 11/12, Ubuntu 22.04/24.04, AlmaLinux 9, Rocky Linux 9
# Cài đặt 1 dòng: curl -sSL https://raw.githubusercontent.com/tuend-work/PressPanel/main/install.sh | sudo bash

set -euo pipefail

INSTALL_DIR="/opt/wpc"
PANEL_USER="wpc"
PANEL_GROUP="wpc"
REPO_URL="https://github.com/tuend-work/PressPanel.git"
TMP_DIR=$(mktemp -d /tmp/wpc-install-XXXXXX 2>/dev/null || echo "/tmp/wpc-install-$$")

echo "=========================================================="
echo "    CÀI ĐẶT WPCLOUD CONTROL PANEL - TỐI ƯU CHO WORDPRESS  "
echo "=========================================================="

if [ "$EUID" -ne 0 ]; then
    echo "[-] Vui lòng chạy script này với quyền root: sudo bash install.sh"
    exit 1
fi

# 1. Phát hiện hệ điều hành
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_ID=$ID
    OS_VERSION_ID=$VERSION_ID
else
    echo "[-] Không thể xác định hệ điều hành!"
    exit 1
fi

echo "[+] Phát hiện hệ điều hành: $OS_ID $OS_VERSION_ID"

# 2. Cài đặt các gói phụ thuộc cơ bản
echo "[+] Đang cài đặt các gói công cụ nền tảng..."
if [[ "$OS_ID" =~ ^(debian|ubuntu)$ ]]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y build-essential gcc make python3 python3-venv python3-pip \
                       sqlite3 libsqlite3-dev curl wget git quota certbot || \
    apt-get install -y build-essential gcc make python3 python3-venv python3-pip \
                       sqlite3 libsqlite3-dev curl wget git quota
elif [[ "$OS_ID" =~ ^(almalinux|rocky|rhel|centos)$ ]]; then
    # 1. Kích hoạt EPEL repository (bắt buộc cho certbot và các gói tiện ích trên RHEL/AlmaLinux)
    echo "[+] Kích hoạt repository EPEL..."
    dnf install -y epel-release || true

    # 2. Kích hoạt CodeReady Builder (CRB) hoặc PowerTools
    echo "[+] Kích hoạt repository CRB / PowerTools..."
    dnf config-manager --set-enabled crb 2>/dev/null || \
    dnf config-manager --set-enabled powertools 2>/dev/null || \
    dnf config-manager --set-enabled "PowerTools" 2>/dev/null || true

    # 3. Cài đặt Development Tools và các công cụ biên dịch
    dnf groupinstall -y "Development Tools" || dnf install -y gcc gcc-c++ make || true
    dnf install -y gcc make sqlite-devel curl wget git quota tar || true

    # 4. Cài đặt Python (ưu tiên Python 3.11 hoặc 3.9 nếu trên EL8 mặc định là Python 3.6)
    dnf install -y python3.11 python3.11-pip python3.11-devel 2>/dev/null || \
    dnf install -y python39 python39-pip python39-devel 2>/dev/null || \
    dnf install -y python3 python3-pip python3-devel 2>/dev/null || true

    # 5. Cài đặt certbot từ EPEL (không dừng script nếu mirror chưa đồng bộ)
    echo "[+] Cài đặt certbot..."
    dnf install -y certbot || true
fi

# 3. Chuẩn bị mã nguồn dự án (nếu chạy qua curl | bash)
SRC_DIR="$(pwd)"
if [ ! -d "$SRC_DIR/gatekeepers" ] || [ ! -d "$SRC_DIR/app" ]; then
    echo "[+] Đang tải mã nguồn WPCloud từ GitHub..."
    git clone --depth 1 "$REPO_URL" "$TMP_DIR"
    SRC_DIR="$TMP_DIR"
fi
cd "$SRC_DIR"

# 4. Tạo tài khoản hệ thống cho WPCloud
NOLOGIN_SHELL="/usr/sbin/nologin"
[ -x "$NOLOGIN_SHELL" ] || NOLOGIN_SHELL="/sbin/nologin"
[ -x "$NOLOGIN_SHELL" ] || NOLOGIN_SHELL="/bin/false"

if ! id -u "$PANEL_USER" >/dev/null 2>&1; then
    echo "[+] Tạo user hệ thống: $PANEL_USER..."
    useradd -r -s "$NOLOGIN_SHELL" -d "$INSTALL_DIR" "$PANEL_USER"
fi

# Cấu hình sudoers cho wpc thực hiện update, apps, service management và Web Terminal root
echo "$PANEL_USER ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/wpc 2>/dev/null || true
chmod 440 /etc/sudoers.d/wpc 2>/dev/null || true
rm -f /etc/sudoers.d/presspanel 2>/dev/null || true

# 5. Tạo cây thư mục triển khai
echo "[+] Khởi tạo cấu trúc thư mục tại $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"/{bin,app,conf,static,data,logs/tasks,scripts/apps,server,templates}
chown -R "$PANEL_USER":"$PANEL_GROUP" "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR"

# 6. Sao chép và cấu hình cổng tự động (Tránh xung đột cổng với dịch vụ khác)
PANEL_PORT=8888
if [ -f "$INSTALL_DIR/conf/wpc.conf" ]; then
    PANEL_PORT=$(grep -E "^port=" "$INSTALL_DIR/conf/wpc.conf" | cut -d'=' -f2 | tr -d ' \r\n' || echo "8888")
else
    # Kiểm tra cổng 8888 có bị chiếm dụng không
    CHECK_PORT=8888
    while true; do
        IS_BUSY=0
        if command -v ss >/dev/null 2>&1; then
            if ss -tulpn 2>/dev/null | grep -q ":$CHECK_PORT "; then IS_BUSY=1; fi
        elif command -v netstat >/dev/null 2>&1; then
            if netstat -tulpn 2>/dev/null | grep -q ":$CHECK_PORT "; then IS_BUSY=1; fi
        fi

        if [ "$IS_BUSY" -eq 1 ]; then
            echo "[!] Phát hiện cổng $CHECK_PORT đang bị tiến trình khác chiếm dụng. Đang thử cổng tiếp theo..."
            CHECK_PORT=$((CHECK_PORT + 1))
        else
            PANEL_PORT=$CHECK_PORT
            break
        fi
    done

    echo "[+] Khởi tạo file cấu hình trung tâm: $INSTALL_DIR/conf/wpc.conf (Gán cổng an toàn: $PANEL_PORT)..."
    if [ -f "$SRC_DIR/conf/wpc.conf" ]; then
        cp "$SRC_DIR/conf/wpc.conf" "$INSTALL_DIR/conf/wpc.conf"
    fi
    sed -i "s/^port=.*/port=$PANEL_PORT/" "$INSTALL_DIR/conf/wpc.conf"
    
    # Tự động sinh Secret Key 64 hex ngẫu nhiên bảo vệ an toàn cho JWT
    RANDOM_SECRET_KEY=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || echo "wpc_sec_$(date +%s%N)_$RANDOM")
    sed -i "s/^secret_key=.*/secret_key=$RANDOM_SECRET_KEY/" "$INSTALL_DIR/conf/wpc.conf"
    chmod 640 "$INSTALL_DIR/conf/wpc.conf"
    chown "$PANEL_USER":"$PANEL_GROUP" "$INSTALL_DIR/conf/wpc.conf"
fi

# 7. Biên dịch 8 C Gatekeeper
echo "[+] Biên dịch các module C Gatekeeper..."
cd "$SRC_DIR/gatekeepers"
make clean
make all
make install PREFIX="$INSTALL_DIR"
cd "$SRC_DIR"

# 7.1 Cấu hình bảo mật procfs (hidepid=2) cách ly tiến trình giữa các user
echo "[+] Thiết lập cách ly tiến trình /proc (hidepid=2) cho user thường..."
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

echo "[+] Phân quyền bảo mật 0711 cho /home/* để cô lập người dùng và cho phép webserver phục vụ..."
mkdir -p /tmp/lshttpd 2>/dev/null || true
chmod 1777 /tmp/lshttpd 2>/dev/null || true
for user_home in /home/*; do
    [ -d "$user_home" ] || continue
    u_name=$(basename "$user_home")
    if id "$u_name" &>/dev/null; then
        chown -R "$u_name:$u_name" "$user_home" 2>/dev/null || true
        for svc_u in "$PANEL_USER" nobody www-data; do
            id "$svc_u" &>/dev/null && usermod -a -G "$u_name" "$svc_u" 2>/dev/null || true
        done
        chmod 711 "$user_home" 2>/dev/null || true
        [ -d "$user_home/domains" ] && chmod 711 "$user_home/domains" 2>/dev/null || true
        [ -d "$user_home/public_html" ] && chmod 755 "$user_home/public_html" 2>/dev/null || true
        for priv_dir in "$user_home/logs" "$user_home/ssl" "$user_home/backups" "$user_home/.tmp" "$user_home/.sessions" "$user_home/.ssh"; do
            [ -d "$priv_dir" ] && chmod 700 "$priv_dir" 2>/dev/null || true
        done
        if [ -d "$user_home/domains" ]; then
            find "$user_home/domains" -type d -exec chmod 755 {} + 2>/dev/null || true
            find "$user_home/domains" -type f -exec chmod 644 {} + 2>/dev/null || true
            find "$user_home/domains" -type d \( -name "logs" -o -name "ssl" -o -name "backups" \) -exec chmod 700 {} + 2>/dev/null || true
        fi
        if [ -d "$user_home/public_html" ]; then
            find "$user_home/public_html" -type d -exec chmod 755 {} + 2>/dev/null || true
            find "$user_home/public_html" -type f -exec chmod 644 {} + 2>/dev/null || true
        fi
    fi
done

# 8. Thiết lập môi trường Python venv
echo "[+] Thiết lập Python Virtualenv..."
# Ưu tiên tìm binary Python >= 3.9 hiện đại
PYTHON_BIN=""
for py in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$py" &>/dev/null; then
        PY_VER=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 9 ]; then
            PYTHON_BIN=$(command -v "$py")
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN=$(command -v python3 || echo "python3")
fi

echo "[+] Sử dụng: $($PYTHON_BIN --version 2>&1)"
"$PYTHON_BIN" -m venv "$INSTALL_DIR/venv" || virtualenv -p "$PYTHON_BIN" "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r requirements.txt

# 9. Sao chép mã nguồn ứng dụng & scripts
echo "[+] Cài đặt mã nguồn ứng dụng..."
mkdir -p "$INSTALL_DIR/app"
if [ -d "$INSTALL_DIR/app/app" ]; then
    cp -r "$INSTALL_DIR/app/app"/* "$INSTALL_DIR/app/" 2>/dev/null || true
    rm -rf "$INSTALL_DIR/app/app"
fi
cp -r app/* "$INSTALL_DIR/app/"
cp -r scripts/* "$INSTALL_DIR/scripts/" 2>/dev/null || true
cp -r templates/* "$INSTALL_DIR/templates/" 2>/dev/null || true
mkdir -p "$INSTALL_DIR/static"
cp -r static/* "$INSTALL_DIR/static/" 2>/dev/null || true
cp VERSION "$INSTALL_DIR/" 2>/dev/null || true
chmod +x "$INSTALL_DIR"/scripts/*.sh "$INSTALL_DIR"/scripts/apps/*.sh "$INSTALL_DIR"/scripts/wpc 2>/dev/null || true
chown -R "$PANEL_USER":"$PANEL_GROUP" "$INSTALL_DIR"/app "$INSTALL_DIR"/conf "$INSTALL_DIR"/data "$INSTALL_DIR"/logs "$INSTALL_DIR"/static

# Cài đặt lệnh CLI wpc nhị phân (Binary ELF) toàn hệ thống (bảo vệ mã nguồn, chống đọc trần)
if [ -f "$SRC_DIR/gatekeepers/wpc.c" ]; then
    echo "[+] Biên dịch wpc CLI thành file thực thi nhị phân (ELF Binary)..."
    gcc -O2 -fPIE -pie "$SRC_DIR/gatekeepers/wpc.c" -o /usr/local/bin/wpc 2>/dev/null || cp "$INSTALL_DIR/scripts/wpc" /usr/local/bin/wpc
    strip /usr/local/bin/wpc 2>/dev/null || true
    chmod 755 /usr/local/bin/wpc
    ln -sf /usr/local/bin/wpc /usr/bin/wpc 2>/dev/null || true
elif [ -f "$INSTALL_DIR/scripts/wpc" ]; then
    cp "$INSTALL_DIR/scripts/wpc" /usr/local/bin/wpc
    chmod +x /usr/local/bin/wpc
    ln -sf /usr/local/bin/wpc /usr/bin/wpc 2>/dev/null || true
fi

# Biên dịch trước mã nguồn Python sang bytecode (.pyc)
echo "[+] Tối ưu hóa & biên dịch trước bytecode Python (.pyc)..."
"$INSTALL_DIR/venv/bin/python" -m compileall -q "$INSTALL_DIR/app" 2>/dev/null || true

# 10. Khởi tạo Database SQLite
echo "[+] Khởi tạo cơ sở dữ liệu SQLite..."
cd "$INSTALL_DIR"
"$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/app/db/init_db.py"

# Đảm bảo quyền sở hữu wpc và quyền ghi 775/664 cho SQLite (tránh lỗi readonly database)
chown -R "$PANEL_USER":"$PANEL_GROUP" "$INSTALL_DIR"/data
chmod 775 "$INSTALL_DIR"/data
chmod 664 "$INSTALL_DIR"/data/panel.db* 2>/dev/null || true

# 11. Cài đặt Systemd Service
echo "[+] Đăng ký dịch vụ systemd wpc..."
if [ -f "$SRC_DIR/systemd/wpc.service" ]; then
    cp "$SRC_DIR/systemd/wpc.service" /etc/systemd/system/wpc.service
elif [ -f "$INSTALL_DIR/systemd/wpc.service" ]; then
    cp "$INSTALL_DIR/systemd/wpc.service" /etc/systemd/system/wpc.service
fi

if [ -f /etc/systemd/system/wpc.service ]; then
    sed -i "s/--port 8888/--port $PANEL_PORT/g" /etc/systemd/system/wpc.service 2>/dev/null || true
fi

# Dọn dẹp presspanel cũ nếu có để tránh xung đột
systemctl stop presspanel 2>/dev/null || true
systemctl disable presspanel 2>/dev/null || true
rm -f /etc/systemd/system/presspanel.service 2>/dev/null || true

systemctl daemon-reload
systemctl enable wpc
systemctl restart wpc

# 12. Cấu hình bảo mật hệ thống: hidepid=2 và khóa quyền cô lập /home
echo "[+] Cấu hình bảo mật cách ly tiến trình (/proc hidepid=2)..."
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

echo "[+] Khóa quyền bảo mật 0711 cho /home và tối ưu phân quyền phục vụ website..."
chmod 711 /home 2>/dev/null || true
chmod 711 /home/* 2>/dev/null || true

# 13. Khóa quyền Linux SSH cho Hosting User và Account Linux Admin
echo "[+] Khóa quyền Linux SSH cho tất cả Hosting User và tài khoản admin..."
NOLOGIN_SHELL="/usr/sbin/nologin"
[ -x "$NOLOGIN_SHELL" ] || NOLOGIN_SHELL="/sbin/nologin"
[ -x "$NOLOGIN_SHELL" ] || NOLOGIN_SHELL="/bin/false"

# Khóa Linux SSH cho tất cả hosting users trong /home
for user_home in /home/*; do
    [ -d "$user_home" ] || continue
    u_name=$(basename "$user_home")
    if id "$u_name" &>/dev/null && [ "$u_name" != "root" ]; then
        usermod -s "$NOLOGIN_SHELL" "$u_name" 2>/dev/null || true
    fi
done

# Khóa quyền SSH của tài khoản linux admin nếu tồn tại
if id admin &>/dev/null; then
    echo "[+] Khóa SSH tài khoản linux admin..."
    usermod -s "$NOLOGIN_SHELL" admin 2>/dev/null || true
    passwd -l admin 2>/dev/null || true
fi

# Chặn truy cập SSH cho admin trong OpenSSH Server Daemon
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

# Dọn dẹp thư mục tạm nếu có
if [ -d "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
fi

echo "=========================================================="
echo "   WPCLOUD CONTROL PANEL ĐÃ CÀI ĐẶT THÀNH CÔNG!          "
echo "   Giao diện quản trị: http://<IP_MAY_CHU>:$PANEL_PORT   "
echo "   Tài liệu Swagger API: http://<IP_MAY_CHU>:$PANEL_PORT/docs "
echo "   File cấu hình: /opt/wpc/conf/wpc.conf   "
echo "   Lệnh quản trị hệ thống CLI: wpc                       "
echo "   Tài khoản mặc định: admin / admin123456               "
echo "=========================================================="
