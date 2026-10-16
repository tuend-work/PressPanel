#!/usr/bin/env bash
# ==============================================================================
# PressPanel: reinstall.sh - Cài đặt lại toàn bộ Panel từ GitHub
# Tự động sao lưu panel.db và presspanel.conf trước khi cài đặt lại
# Cách chạy: sudo bash /opt/wpc/scripts/reinstall.sh
# Hoặc 1 dòng: curl -sSL https://raw.githubusercontent.com/tuend-work/PressPanel/main/scripts/reinstall.sh | sudo bash
# ==============================================================================

set -euo pipefail

INSTALL_DIR="/opt/wpc"
[ -d "/opt/presspanel" ] && [ ! -d "/opt/wpc" ] && INSTALL_DIR="/opt/presspanel"
REPO_URL="https://github.com/tuend-work/PressPanel.git"
TMP_DIR="/tmp/presspanel-reinstall-$$"
BACKUP_DIR="/tmp/presspanel-backup-$(date +%s)"

echo "=========================================================="
echo "         CÀI ĐẶT LẠI (REINSTALL) PRESSPANEL              "
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
if [ -f "$INSTALL_DIR/conf/presspanel.conf" ]; then
    echo "[+] Đang sao lưu cấu hình hiện tại vào $BACKUP_DIR/presspanel.conf..."
    cp "$INSTALL_DIR/conf/presspanel.conf" "$BACKUP_DIR/presspanel.conf"
fi

# Dừng dịch vụ nếu đang chạy
systemctl stop presspanel 2>/dev/null || true

# 2. Tải bản sạch từ GitHub
echo "[+] Đang tải mã nguồn mới nhất từ $REPO_URL..."
git clone --depth 1 "$REPO_URL" "$TMP_DIR"
cd "$TMP_DIR"

# 3. Chạy script cài đặt
echo "[+] Tiến hành cài đặt lại PressPanel..."
bash install.sh

# 4. Khôi phục lại dữ liệu nếu trước đó đã có
if [ -f "$BACKUP_DIR/panel.db" ]; then
    echo "[+] Khôi phục lại cơ sở dữ liệu panel.db..."
    cp "$BACKUP_DIR/panel.db" "$INSTALL_DIR/data/panel.db"
    chown wpc:wpc "$INSTALL_DIR/data/panel.db" 2>/dev/null || chown presspanel:presspanel "$INSTALL_DIR/data/panel.db" 2>/dev/null || true
fi
if [ -f "$BACKUP_DIR/presspanel.conf" ]; then
    echo "[+] Khôi phục lại cấu hình presspanel.conf..."
    cp "$BACKUP_DIR/presspanel.conf" "$INSTALL_DIR/conf/presspanel.conf"
    chmod 640 "$INSTALL_DIR/conf/presspanel.conf"
    chown wpc:wpc "$INSTALL_DIR/conf/presspanel.conf" 2>/dev/null || chown presspanel:presspanel "$INSTALL_DIR/conf/presspanel.conf" 2>/dev/null || true
fi

# 5. Khởi động lại dịch vụ
systemctl restart wpc 2>/dev/null || systemctl restart presspanel

# 6. Dọn dẹp thư mục tạm
rm -rf "$TMP_DIR" "$BACKUP_DIR"

echo "=========================================================="
echo "   REINSTALL THÀNH CÔNG!                                 "
echo "   Trạng thái dịch vụ: $(systemctl is-active wpc 2>/dev/null || systemctl is-active presspanel) "
echo "   Giao diện quản trị: http://<IP_MAY_CHU>:8888          "
echo "=========================================================="
