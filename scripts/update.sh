#!/usr/bin/env bash
# ==============================================================================
# PressPanel: update.sh - Cập nhật Panel lên phiên bản mới nhất từ GitHub
# Bảo toàn 100% dữ liệu database, website người dùng và file cấu hình
# Cách chạy: sudo bash /opt/presspanel/scripts/update.sh
# Hoặc 1 dòng: curl -sSL https://raw.githubusercontent.com/tuend-work/PressPanel/main/scripts/update.sh | sudo bash
# ==============================================================================

set -euo pipefail

INSTALL_DIR="/opt/presspanel"
REPO_URL="https://github.com/tuend-work/PressPanel.git"
TMP_DIR="/tmp/presspanel-update-$$"

echo "=========================================================="
echo "         CẬP NHẬT PRESSPANEL TỪ GITHUB                   "
echo "=========================================================="

if [ "$EUID" -ne 0 ]; then
    echo "[-] Vui lòng chạy lệnh với quyền root: sudo bash update.sh"
    exit 1
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
cp -r "$TMP_DIR/app" "$INSTALL_DIR/"
cp -r "$TMP_DIR/scripts"/* "$INSTALL_DIR/scripts/" 2>/dev/null || true
cp -r "$TMP_DIR/templates"/* "$INSTALL_DIR/templates/" 2>/dev/null || true
cp "$TMP_DIR/VERSION" "$INSTALL_DIR/" 2>/dev/null || true
chmod +x "$INSTALL_DIR"/scripts/*.sh 2>/dev/null || true

# Cập nhật CLI tool /usr/local/bin/presspanel
if [ -f "$TMP_DIR/scripts/presspanel" ]; then
    cp "$TMP_DIR/scripts/presspanel" /usr/local/bin/presspanel
    chmod +x /usr/local/bin/presspanel
fi

# 4. Nâng cấp các thư viện Python nếu có bổ sung
echo "[+] Kiểm tra và cập nhật dependencies Python..."
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$TMP_DIR/requirements.txt"

# 5. Chạy database migration (nếu có bảng/cột mới)
echo "[+] Kiểm tra và cập nhật cấu trúc cơ sở dữ liệu SQLite..."
"$INSTALL_DIR/venv/bin/python" -c "
import asyncio
from app.db.init_db import init_database
asyncio.run(init_database())
"

# 6. Đảm bảo phân quyền chính xác
chown -R presspanel:presspanel "$INSTALL_DIR/app" "$INSTALL_DIR/data" "$INSTALL_DIR/logs"

# 7. Khởi động lại dịch vụ
echo "[+] Khởi động lại dịch vụ PressPanel..."
systemctl daemon-reload
systemctl restart presspanel

# 8. Dọn dẹp thư mục tạm
rm -rf "$TMP_DIR"

NEW_VER=$(cat "$INSTALL_DIR/VERSION" 2>/dev/null || echo "latest")

echo "=========================================================="
echo "   CẬP NHẬT THÀNH CÔNG LÊN PHIÊN BẢN: $NEW_VER           "
echo "   Trạng thái dịch vụ: $(systemctl is-active presspanel) "
echo "   Giao diện quản trị: http://<IP_MAY_CHU>:8888          "
echo "=========================================================="
