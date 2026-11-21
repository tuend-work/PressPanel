#!/usr/bin/env bash
# PressPanel Installer Script
# Hỗ trợ: Debian 11/12, Ubuntu 22.04/24.04, AlmaLinux 8/9, Rocky Linux 8/9
# Cài đặt 1 dòng: curl -sSL https://raw.githubusercontent.com/tuend-work/PressPanel/main/install.sh | sudo bash

set -euo pipefail

INSTALL_DIR="/opt/presspanel"
PANEL_USER="presspanel"
PANEL_GROUP="presspanel"
REPO_URL="https://github.com/tuend-work/PressPanel.git"
TMP_DIR=$(mktemp -d /tmp/presspanel-install-XXXXXX 2>/dev/null || echo "/tmp/presspanel-install-$$")

echo "=========================================================="
echo "    CÀI ĐẶT PRESSPANEL CONTROL PANEL - TỐI ƯU WORDPRESS   "
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

# Tự động chuyển SELinux sang Permissive để tránh systemd và PHP-FPM bị chặn quyền thực thi (chuẩn hosting cPanel, aaPanel)
if command -v getenforce &>/dev/null; then
    if [ "$(getenforce)" = "Enforcing" ]; then
        echo "[+] Phát hiện SELinux đang ở chế độ Enforcing, chuyển sang Permissive để tránh xung đột quyền systemd..."
        setenforce 0 2>/dev/null || true
        if [ -f /etc/selinux/config ]; then
            sed -i 's/^SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config 2>/dev/null || true
        fi
    fi
fi

# 2. Cài đặt các gói phụ thuộc cơ bản
echo "[+] Đang kiểm tra và cập nhật các gói hệ điều hành..."
if [[ "$OS_ID" =~ ^(debian|ubuntu)$ ]]; then
    export DEBIAN_FRONTEND=noninteractive
    echo "[+] Đang cập nhật toàn diện các gói hệ điều hành Debian/Ubuntu (apt update && apt upgrade)..."
    apt-get update -y
    apt-get upgrade -y || true
    echo "[+] Đang cài đặt các gói công cụ nền tảng..."
    apt-get install -y build-essential gcc make python3 python3-venv python3-pip \
                       sqlite3 libsqlite3-dev curl wget git quota certbot ipset fail2ban \
                       software-properties-common lsb-release ca-certificates apt-transport-https || \
    apt-get install -y build-essential gcc make python3 python3-venv python3-pip \
                       sqlite3 libsqlite3-dev curl wget git quota ipset fail2ban software-properties-common

    # Cấu hình Ondřej PPA cho Ubuntu hoặc Sury cho Debian để sẵn sàng cài đặt mọi phiên bản PHP
    if [ "$OS_ID" = "ubuntu" ]; then
        if ! grep -rq "ondrej/php" /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null; then
            echo "[+] Kích hoạt Ondřej PPA (ppa:ondrej/php) trên Ubuntu..."
            LC_ALL=C.UTF-8 add-apt-repository -y ppa:ondrej/php || true
            apt-get update -y || true
        fi
    elif [ "$OS_ID" = "debian" ]; then
        if [ ! -f /etc/apt/sources.list.d/php.list ] && ! grep -rq "deb.sury.org" /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null; then
            echo "[+] Kích hoạt Ondřej Sury repository trên Debian..."
            curl -sSLo /usr/share/keyrings/deb.sury.org-php.gpg https://packages.sury.org/php/apt.gpg 2>/dev/null || true
            echo "deb [signed-by=/usr/share/keyrings/deb.sury.org-php.gpg] https://packages.sury.org/php/ $(lsb_release -sc) main" > /etc/apt/sources.list.d/php.list || true
            apt-get update -y || true
        fi
    fi
elif [[ "$OS_ID" =~ ^(almalinux|rocky|rhel|centos)$ ]]; then
    echo "[+] Đang cập nhật toàn diện các gói hệ điều hành AlmaLinux/RHEL/Rocky (dnf upgrade)..."
    dnf upgrade -y || true

    # 1. Kích hoạt EPEL và Remi repository theo phiên bản Enterprise Linux (bắt buộc cho certbot, đa phiên bản PHP Remi trên RHEL/AlmaLinux)
    EL_MAJ="${OS_VERSION_ID%%.*}"
    [ -z "$EL_MAJ" ] && EL_MAJ="9"
    echo "[+] Kích hoạt repository EPEL và Remi cho Enterprise Linux ${EL_MAJ} (AlmaLinux/RHEL)..."
    dnf install -y epel-release || true
    dnf install -y "https://rpms.remirepo.net/enterprise/remi-release-${EL_MAJ}.rpm" 2>/dev/null || \
    dnf install -y "https://rpms.remirepo.net/enterprise/remi-release-9.rpm" 2>/dev/null || \
    dnf install -y "https://rpms.remirepo.net/enterprise/remi-release-8.rpm" 2>/dev/null || true
    dnf config-manager --set-enabled remi 2>/dev/null || true

    # 2. Kích hoạt CodeReady Builder (CRB) hoặc PowerTools
    echo "[+] Kích hoạt repository CRB / PowerTools..."
    dnf config-manager --set-enabled crb 2>/dev/null || \
    dnf config-manager --set-enabled powertools 2>/dev/null || \
    dnf config-manager --set-enabled "PowerTools" 2>/dev/null || true

    # 3. Cài đặt Development Tools và các công cụ biên dịch
    dnf groupinstall -y "Development Tools" || dnf install -y gcc gcc-c++ make || true
    dnf install -y gcc make sqlite-devel curl wget git quota tar ipset fail2ban || true

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

# Cấu hình sudoers cho presspanel thực hiện update, apps, service management và Web Terminal root
echo "$PANEL_USER ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/presspanel 2>/dev/null || true
chmod 440 /etc/sudoers.d/presspanel 2>/dev/null || true
echo "wpc ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/wpc 2>/dev/null || true
chmod 440 /etc/sudoers.d/wpc 2>/dev/null || true

# 5. Tạo cây thư mục triển khai
echo "[+] Khởi tạo cấu trúc thư mục tại $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"/{bin,app,conf,static,data,logs/tasks,scripts/apps,server,templates}
chown -R "$PANEL_USER":"$PANEL_GROUP" "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR"

# Luôn duy trì symlink tương thích ngược: /opt/wpc -> /opt/presspanel
ln -sfn "$INSTALL_DIR" /opt/wpc 2>/dev/null || true

# 6. Sao chép và cấu hình cổng tự động (Tránh xung đột cổng với dịch vụ khác)
PANEL_PORT=8888
if [ -f "$INSTALL_DIR/conf/presspanel.conf" ]; then
    PANEL_PORT=$(grep -E "^port=" "$INSTALL_DIR/conf/presspanel.conf" | cut -d'=' -f2 | tr -d ' \r\n' || echo "8888")
elif [ -f "$INSTALL_DIR/conf/wpc.conf" ]; then
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

    echo "[+] Khởi tạo file cấu hình trung tâm: $INSTALL_DIR/conf/presspanel.conf (Gán cổng an toàn: $PANEL_PORT)..."
    if [ -f "$SRC_DIR/conf/presspanel.conf" ]; then
        cp "$SRC_DIR/conf/presspanel.conf" "$INSTALL_DIR/conf/presspanel.conf"
    elif [ -f "$SRC_DIR/conf/wpc.conf" ]; then
        cp "$SRC_DIR/conf/wpc.conf" "$INSTALL_DIR/conf/presspanel.conf"
    fi
    sed -i "s/^port=.*/port=$PANEL_PORT/" "$INSTALL_DIR/conf/presspanel.conf"
    
    # Tự động sinh Secret Key 64 hex ngẫu nhiên bảo vệ an toàn cho JWT
    RANDOM_SECRET_KEY=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || echo "presspanel_sec_$(date +%s%N)_$RANDOM")
    sed -i "s/^secret_key=.*/secret_key=$RANDOM_SECRET_KEY/" "$INSTALL_DIR/conf/presspanel.conf"
    chmod 640 "$INSTALL_DIR/conf/presspanel.conf"
    chown "$PANEL_USER":"$PANEL_GROUP" "$INSTALL_DIR/conf/presspanel.conf"

    # Tạo wpc.conf tương thích ngược
    cp "$INSTALL_DIR/conf/presspanel.conf" "$INSTALL_DIR/conf/wpc.conf" 2>/dev/null || true
    chmod 640 "$INSTALL_DIR/conf/wpc.conf" 2>/dev/null || true
    chown "$PANEL_USER":"$PANEL_GROUP" "$INSTALL_DIR/conf/wpc.conf" 2>/dev/null || true
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
for admin_u in almalinux ubuntu rocky centos; do
    id "$admin_u" &>/dev/null && usermod -aG procview "$admin_u" 2>/dev/null || true
done
for u in $(getent group wheel 2>/dev/null | cut -d: -f4 | tr ',' ' '); do
    [ -n "$u" ] && usermod -aG procview "$u" 2>/dev/null || true
done
for u in $(getent group sudo 2>/dev/null | cut -d: -f4 | tr ',' ' '); do
    [ -n "$u" ] && usermod -aG procview "$u" 2>/dev/null || true
done
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
cp -r gatekeepers "$INSTALL_DIR/" 2>/dev/null || true
mkdir -p "$INSTALL_DIR/static"
cp -r static/* "$INSTALL_DIR/static/" 2>/dev/null || true
cp VERSION "$INSTALL_DIR/" 2>/dev/null || true
chmod +x "$INSTALL_DIR"/scripts/*.sh "$INSTALL_DIR"/scripts/apps/*.sh "$INSTALL_DIR"/scripts/presspanel "$INSTALL_DIR"/scripts/wpc 2>/dev/null || true
chown -R "$PANEL_USER":"$PANEL_GROUP" "$INSTALL_DIR"/app "$INSTALL_DIR"/conf "$INSTALL_DIR"/data "$INSTALL_DIR"/logs "$INSTALL_DIR"/static

# Cài đặt lệnh CLI presspanel và wpc nhị phân (Binary ELF) toàn hệ thống
if [ -f "$SRC_DIR/gatekeepers/presspanel.c" ]; then
    echo "[+] Biên dịch presspanel CLI thành file thực thi nhị phân (ELF Binary)..."
    gcc -O2 -fPIE -pie "$SRC_DIR/gatekeepers/presspanel.c" -o /usr/local/bin/presspanel || \
    (command -v shc >/dev/null 2>&1 && shc -f "$INSTALL_DIR/scripts/presspanel" -o /usr/local/bin/presspanel) || \
    cp "$INSTALL_DIR/scripts/presspanel" /usr/local/bin/presspanel
    strip /usr/local/bin/presspanel 2>/dev/null || true
    chmod 755 /usr/local/bin/presspanel
    ln -sf /usr/local/bin/presspanel /usr/bin/presspanel 2>/dev/null || true
elif [ -f "$INSTALL_DIR/scripts/presspanel" ]; then
    (command -v shc >/dev/null 2>&1 && shc -f "$INSTALL_DIR/scripts/presspanel" -o /usr/local/bin/presspanel) || cp "$INSTALL_DIR/scripts/presspanel" /usr/local/bin/presspanel
    chmod 755 /usr/local/bin/presspanel
    ln -sf /usr/local/bin/presspanel /usr/bin/presspanel 2>/dev/null || true
fi

# Luôn tạo symlink tương thích ngược: wpc -> presspanel
ln -sf /usr/local/bin/presspanel /usr/local/bin/wpc 2>/dev/null || true
ln -sf /usr/local/bin/presspanel /usr/bin/wpc 2>/dev/null || true

# Biên dịch trước mã nguồn Python sang bytecode (.pyc)
echo "[+] Tối ưu hóa & biên dịch trước bytecode Python (.pyc)..."
"$INSTALL_DIR/venv/bin/python" -m compileall -q "$INSTALL_DIR/app" 2>/dev/null || true

# 10. Khởi tạo Database SQLite
echo "[+] Khởi tạo cơ sở dữ liệu SQLite..."
cd "$INSTALL_DIR"
"$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/app/db/init_db.py"

# Đảm bảo quyền sở hữu presspanel và quyền an ninh 750 (Least-Privilege: chặn 100% người dùng web khác)
chown -R "$PANEL_USER":"$PANEL_GROUP" "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR"
chmod -R 750 "$INSTALL_DIR"/venv "$INSTALL_DIR"/app "$INSTALL_DIR"/static "$INSTALL_DIR"/conf
chmod -R 750 "$INSTALL_DIR"/scripts && chmod +x "$INSTALL_DIR"/scripts/*.sh "$INSTALL_DIR"/scripts/presspanel "$INSTALL_DIR"/scripts/wpc 2>/dev/null || true
chmod 700 "$INSTALL_DIR"/data
chmod 600 "$INSTALL_DIR"/data/panel.db* 2>/dev/null || true
[ -f "$INSTALL_DIR/conf/presspanel.conf" ] && chmod 600 "$INSTALL_DIR/conf/presspanel.conf" 2>/dev/null || true
[ -f "$INSTALL_DIR/conf/wpc.conf" ] && chmod 600 "$INSTALL_DIR/conf/wpc.conf" 2>/dev/null || true
if [ -d "$INSTALL_DIR/bin" ]; then
    chown -R root:"$PANEL_GROUP" "$INSTALL_DIR/bin" 2>/dev/null || true
    chmod 750 "$INSTALL_DIR/bin" 2>/dev/null || true
    chmod 4750 "$INSTALL_DIR"/bin/* 2>/dev/null || true
fi

# Đảm bảo Dynamic Linker (ld-linux.so, ld.so) có quyền 755 cho mọi tiến trình
chmod 755 /lib64/ld-*.so /usr/lib64/ld-*.so /lib64/ld-linux*.so.* /usr/lib64/ld-linux*.so.* /lib/ld-*.so /usr/lib/ld-*.so 2>/dev/null || true

# Chuẩn bị file log /var/log/presspanel.log và logrotate
touch /var/log/presspanel.log 2>/dev/null || true
chown "$PANEL_USER":"$PANEL_GROUP" /var/log/presspanel.log 2>/dev/null || true
chmod 640 /var/log/presspanel.log 2>/dev/null || true
ln -sf /var/log/presspanel.log /var/log/wpc.log 2>/dev/null || true

if [ -d "/etc/logrotate.d" ]; then
    cat << 'EOF_LOGROTATE' > /etc/logrotate.d/presspanel
/var/log/presspanel.log /var/log/wpc.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    copytruncate
}
EOF_LOGROTATE
fi

# 11. Cài đặt Systemd Service
echo "[+] Đăng ký dịch vụ systemd presspanel..."
if [ -f "$SRC_DIR/systemd/presspanel.service" ]; then
    cp "$SRC_DIR/systemd/presspanel.service" /etc/systemd/system/presspanel.service
elif [ -f "$INSTALL_DIR/systemd/presspanel.service" ]; then
    cp "$INSTALL_DIR/systemd/presspanel.service" /etc/systemd/system/presspanel.service
fi

if [ -f /etc/systemd/system/presspanel.service ]; then
    CURR_VER=$(cat "$INSTALL_DIR/VERSION" 2>/dev/null || cat "$SRC_DIR/VERSION" 2>/dev/null || echo "")
    if [ -n "$CURR_VER" ]; then
        sed -i "s/Description=.*/Description=PressPanel Server v${CURR_VER} (FastAPI + Asyncio)/" /etc/systemd/system/presspanel.service 2>/dev/null || true
    fi
    sed -i "s/--port 8888/--port $PANEL_PORT/g" /etc/systemd/system/presspanel.service 2>/dev/null || true
fi

# Tạo symlink tương thích ngược cho wpc.service
ln -sf /etc/systemd/system/presspanel.service /etc/systemd/system/wpc.service 2>/dev/null || true

systemctl daemon-reload
systemctl stop wpc 2>/dev/null || true
systemctl enable presspanel
systemctl restart presspanel

# 11.1 Cài đặt dịch vụ giám sát Cgroups v2 thời gian thực (presspanel-cgroup)
cat << 'EOF_CGSVC' > /etc/systemd/system/presspanel-cgroup.service
[Unit]
Description=PressPanel Cgroups v2 Realtime Process Enforcer
After=network.target

[Service]
Type=simple
ExecStart=/bin/bash /opt/presspanel/scripts/cgroup_attach.sh daemon
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF_CGSVC

systemctl daemon-reload 2>/dev/null || true
ln -sf /etc/systemd/system/presspanel-cgroup.service /etc/systemd/system/wpc-cgroup.service 2>/dev/null || true
systemctl enable presspanel-cgroup.service 2>/dev/null || true
systemctl restart presspanel-cgroup.service 2>/dev/null || true

# Kích hoạt Cgroups v2 cho OpenLiteSpeed nếu có tiện ích lsns
if [ -x "/usr/local/lsws/lsns/bin/lssetup" ]; then
    /usr/local/lsws/lsns/bin/lssetup -i 1 2>/dev/null || true
fi

# 12. Cấu hình bảo mật hệ thống: hidepid=2 và khóa quyền cô lập /home
echo "[+] Cấu hình bảo mật cách ly tiến trình (/proc hidepid=2)..."
groupadd -r procview 2>/dev/null || true
usermod -aG procview "$PANEL_USER" 2>/dev/null || true
usermod -aG procview root 2>/dev/null || true
for admin_u in almalinux ubuntu rocky centos; do
    id "$admin_u" &>/dev/null && usermod -aG procview "$admin_u" 2>/dev/null || true
done
for u in $(getent group wheel 2>/dev/null | cut -d: -f4 | tr ',' ' '); do
    [ -n "$u" ] && usermod -aG procview "$u" 2>/dev/null || true
done
for u in $(getent group sudo 2>/dev/null | cut -d: -f4 | tr ',' ' '); do
    [ -n "$u" ] && usermod -aG procview "$u" 2>/dev/null || true
done
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

# Khóa Linux SSH cho tất cả hosting users trong /home (bỏ qua tài khoản hệ thống / admin / wheel / sudo)
for user_home in /home/*; do
    [ -d "$user_home" ] || continue
    u_name=$(basename "$user_home")
    if [ "$u_name" = "root" ] || [ "$u_name" = "almalinux" ] || [ "$u_name" = "ubuntu" ] || [ "$u_name" = "rocky" ] || [ "$u_name" = "centos" ]; then
        continue
    fi
    if id -nG "$u_name" 2>/dev/null | grep -qE '\b(wheel|sudo)\b'; then
        usermod -aG procview "$u_name" 2>/dev/null || true
        continue
    fi
    if id "$u_name" &>/dev/null; then
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

# 15. Tự động kiểm tra và chuẩn hóa bảo mật toàn diện (CloudLinux Hardening)
if [ -f "$INSTALL_DIR/scripts/security_hardening.sh" ]; then
    echo "[+] Đang kích hoạt chế độ bảo mật toàn diện CloudLinux-Grade Hardening..."
    /bin/bash "$INSTALL_DIR/scripts/security_hardening.sh" fix || true
fi

# Dọn dẹp thư mục tạm nếu có
if [ -d "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
fi

echo "=========================================================="
echo "   PRESSPANEL CONTROL PANEL ĐÃ CÀI ĐẶT THÀNH CÔNG!       "
echo "   Giao diện quản trị: http://<IP_MAY_CHU>:$PANEL_PORT   "
echo "   Tài liệu Swagger API: http://<IP_MAY_CHU>:$PANEL_PORT/docs "
echo "   File cấu hình: /opt/presspanel/conf/presspanel.conf   "
echo "   Lệnh quản trị hệ thống CLI: presspanel (hoặc wpc)     "
echo "   Tài khoản mặc định: admin / admin123456               "
echo "=========================================================="
