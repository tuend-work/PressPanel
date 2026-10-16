#!/usr/bin/env bash
# ==============================================================================
# WPCloud: reinstall.sh - Cài đặt lại toàn bộ Panel từ GitHub
# Tự động sao lưu panel.db và wpc.conf trước khi cài đặt lại
# Cách chạy: sudo bash /opt/wpc/scripts/reinstall.sh
# Hoặc 1 dòng: curl -sSL https://raw.githubusercontent.com/tuend-work/PressPanel/main/scripts/reinstall.sh | sudo bash
# ==============================================================================

set -euo pipefail

INSTALL_DIR="/opt/wpc"
REPO_URL="https://github.com/tuend-work/PressPanel.git"
TMP_DIR="/tmp/wpc-reinstall-$$"
BACKUP_DIR="/tmp/wpc-backup-$(date +%s)"

echo "=========================================================="
echo "         CÀI ĐẶT LẠI (REINSTALL) WPCLOUD PANEL           "
echo "=========================================================="

if [ "$EUID" -ne 0 ]; then
    echo "[-] Vui lòng chạy lệnh với quyền root: sudo bash reinstall.sh"
    exit 1
fi

# 1. Sao lưu tạm dữ liệu quan trọng
mkdir -p "$BACKUP_DIR"
if [ -f "$INSTALL_DIR/data/panel.db" ]; then
    echo "[+] Đang sao lưu cơ sở dữ liệu hiện tại vào $BACKUP_DIR/panel.db..."
    cp "$INSTALL_DIR/data/panel.db" "$BACKUP_DIR/panel.db"
fi
if [ -f "$INSTALL_DIR/conf/wpc.conf" ]; then
    echo "[+] Đang sao lưu cấu hình hiện tại vào $BACKUP_DIR/wpc.conf..."
    cp "$INSTALL_DIR/conf/wpc.conf" "$BACKUP_DIR/wpc.conf"
fi

# Dừng dịch vụ nếu đang chạy
systemctl stop wpc 2>/dev/null || true

# 2. Tải bản sạch từ GitHub
echo "[+] Đang tải mã nguồn mới nhất từ $REPO_URL..."
git clone --depth 1 "$REPO_URL" "$TMP_DIR"
cd "$TMP_DIR"

# 3. Chạy script cài đặt
echo "[+] Tiến hành cài đặt lại WPCloud..."
bash install.sh

# 4. Khôi phục lại dữ liệu nếu trước đó đã có
if [ -f "$BACKUP_DIR/panel.db" ]; then
    echo "[+] Khôi phục lại cơ sở dữ liệu panel.db..."
    cp "$BACKUP_DIR/panel.db" "$INSTALL_DIR/data/panel.db"
    chown wpc:wpc "$INSTALL_DIR/data/panel.db" 2>/dev/null || true
fi
if [ -f "$BACKUP_DIR/wpc.conf" ]; then
    echo "[+] Khôi phục lại cấu hình wpc.conf..."
    cp "$BACKUP_DIR/wpc.conf" "$INSTALL_DIR/conf/wpc.conf"
    chmod 640 "$INSTALL_DIR/conf/wpc.conf"
    chown wpc:wpc "$INSTALL_DIR/conf/wpc.conf" 2>/dev/null || true
fi

# 5. Khởi động lại dịch vụ
systemctl restart wpc

# 6. Dọn dẹp thư mục tạm
rm -rf "$TMP_DIR" "$BACKUP_DIR"

echo "=========================================================="
echo "   REINSTALL THÀNH CÔNG!                                 "
echo "   Trạng thái dịch vụ: $(systemctl is-active wpc 2>/dev/null || echo 'inactive') "
echo "   Giao diện quản trị: http://<IP_MAY_CHU>:8888          "
echo "=========================================================="
