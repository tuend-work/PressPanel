#!/usr/bin/env bash
# PressPanel Installer Script
# Hỗ trợ: Debian 11/12, Ubuntu 22.04/24.04, AlmaLinux 9, Rocky Linux 9
# Cài đặt 1 dòng: curl -sSL https://raw.githubusercontent.com/tuend-work/PressPanel/main/install.sh | sudo bash

set -euo pipefail

INSTALL_DIR="/opt/presspanel"
PANEL_USER="presspanel"
PANEL_GROUP="presspanel"
REPO_URL="https://github.com/tuend-work/PressPanel.git"
TMP_DIR="/tmp/presspanel-install-$$"

echo "=========================================================="
echo "      CÀI ĐẶT PRESSPANEL: CONTROL PANEL TỐI ƯU WORDPRESS  "
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
                       sqlite3 libsqlite3-dev curl wget git quota certbot
elif [[ "$OS_ID" =~ ^(almalinux|rocky|rhel|centos)$ ]]; then
    dnf groupinstall -y "Development Tools"
    dnf install -y gcc make python3 python3-pip sqlite-devel curl wget git quota certbot
fi

# 3. Chuẩn bị mã nguồn dự án (nếu chạy qua curl | bash)
SRC_DIR="$(pwd)"
if [ ! -d "$SRC_DIR/gatekeepers" ] || [ ! -d "$SRC_DIR/app" ]; then
    echo "[+] Đang tải mã nguồn PressPanel từ GitHub..."
    git clone --depth 1 "$REPO_URL" "$TMP_DIR"
    SRC_DIR="$TMP_DIR"
fi
cd "$SRC_DIR"

# 4. Tạo tài khoản hệ thống cho PressPanel
if ! id -u "$PANEL_USER" >/dev/null 2>&1; then
    echo "[+] Tạo user hệ thống: $PANEL_USER..."
    useradd -r -s /usr/sbin/nologin -d "$INSTALL_DIR" "$PANEL_USER"
fi

# 5. Tạo cây thư mục triển khai
echo "[+] Khởi tạo cấu trúc thư mục tại $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"/{bin,app,conf,static,data,logs/tasks,scripts/apps,server,templates}
chown -R "$PANEL_USER":"$PANEL_GROUP" "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR"

# 6. Sao chép file cấu hình trung tâm (presspanel.conf giống directadmin.conf)
if [ ! -f "$INSTALL_DIR/conf/presspanel.conf" ] && [ -f "conf/presspanel.conf" ]; then
    echo "[+] Khởi tạo file cấu hình trung tâm: $INSTALL_DIR/conf/presspanel.conf..."
    cp conf/presspanel.conf "$INSTALL_DIR/conf/presspanel.conf"
    chmod 640 "$INSTALL_DIR/conf/presspanel.conf"
    chown "$PANEL_USER":"$PANEL_GROUP" "$INSTALL_DIR/conf/presspanel.conf"
fi

# 7. Biên dịch 8 C Gatekeeper
echo "[+] Biên dịch các module C Gatekeeper..."
cd gatekeepers
make clean
make all
make install PREFIX="$INSTALL_DIR"
cd "$SRC_DIR"

# 8. Thiết lập môi trường Python venv
echo "[+] Thiết lập Python Virtualenv..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r requirements.txt

# 9. Sao chép mã nguồn ứng dụng
echo "[+] Cài đặt mã nguồn ứng dụng..."
cp -r app "$INSTALL_DIR/"
cp -r scripts/* "$INSTALL_DIR/scripts/" 2>/dev/null || true
cp -r templates/* "$INSTALL_DIR/templates/" 2>/dev/null || true
chmod +x "$INSTALL_DIR"/scripts/*.sh 2>/dev/null || true
chown -R "$PANEL_USER":"$PANEL_GROUP" "$INSTALL_DIR"/app "$INSTALL_DIR"/conf "$INSTALL_DIR"/data "$INSTALL_DIR"/logs

# 10. Khởi tạo Database SQLite
echo "[+] Khởi tạo cơ sở dữ liệu SQLite..."
"$INSTALL_DIR/venv/bin/python" -c "
import asyncio
from app.db.init_db import init_database
asyncio.run(init_database())
"

# 11. Cài đặt Systemd Service
echo "[+] Đăng ký dịch vụ systemd presspanel..."
cp systemd/presspanel.service /etc/systemd/system/presspanel.service
systemctl daemon-reload
systemctl enable presspanel
systemctl restart presspanel

# Dọn dẹp thư mục tạm nếu có
if [ -d "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
fi

echo "=========================================================="
echo "   PRESSPANEL ĐÃ CÀI ĐẶT VÀ KHỞI ĐỘNG THÀNH CÔNG!         "
echo "   Giao diện quản trị: http://<IP_MAY_CHU>:8888          "
echo "   Tài liệu Swagger API: http://<IP_MAY_CHU>:8888/docs    "
echo "   File cấu hình: /opt/presspanel/conf/presspanel.conf   "
echo "   Tài khoản mặc định: admin / admin123456               "
echo "=========================================================="
