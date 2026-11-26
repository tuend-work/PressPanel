#!/usr/bin/env bash
# ==============================================================================
# PressPanel: reinstall.sh - Cài đặt lại toàn bộ Panel từ GitHub
# Tự động sao lưu panel.db và cấu hình trước khi cài đặt lại
# Cách chạy: sudo bash /opt/presspanel/scripts/reinstall.sh
# Hoặc 1 dòng: curl -sSL https://raw.githubusercontent.com/tuend-work/PressPanel/main/scripts/reinstall.sh | sudo bash
# ==============================================================================

set -euo pipefail

INSTALL_DIR="/opt/presspanel"
REPO_URL="https://github.com/tuend-work/PressPanel.git"
TMP_DIR="/tmp/presspanel-reinstall-$$"
BACKUP_DIR="/tmp/presspanel-backup-$(date +%s)"

echo "=========================================================="
echo "        CÀI ĐẶT LẠI (REINSTALL) PRESSPANEL               "
echo "=========================================================="

if [ "$EUID" -ne 0 ]; then
    echo "[-] Vui lòng chạy lệnh với quyền root: sudo bash reinstall.sh"
    exit 1
fi

# 1. Sao lưu tạm dữ liệu quan trọng
mkdir -p "$BACKUP_DIR"
for p in "$INSTALL_DIR" "/opt/presspanel"; do
    if [ -f "$p/data/panel.db" ] && [ ! -f "$BACKUP_DIR/panel.db" ]; then
        echo "[+] Đang sao lưu cơ sở dữ liệu hiện tại vào $BACKUP_DIR/panel.db..."
        cp "$p/data/panel.db" "$BACKUP_DIR/panel.db"
    fi
    if [ -f "$p/conf/presspanel.conf" ] && [ ! -f "$BACKUP_DIR/presspanel.conf" ]; then
        echo "[+] Đang sao lưu cấu hình hiện tại vào $BACKUP_DIR/presspanel.conf..."
        cp "$p/conf/presspanel.conf" "$BACKUP_DIR/presspanel.conf"
    fi
    if [ -f "$p/conf/wpc.conf" ] && [ ! -f "$BACKUP_DIR/wpc.conf" ]; then
        cp "$p/conf/wpc.conf" "$BACKUP_DIR/wpc.conf"
    fi
done

# Dừng dịch vụ nếu đang chạy
systemctl stop presspanel 2>/dev/null || true
systemctl stop wpc 2>/dev/null || true

# 2. Tải bản sạch từ GitHub
echo "[+] Đang tải mã nguồn mới nhất từ $REPO_URL..."
git clone --depth 1 "$REPO_URL" "$TMP_DIR"
cd "$TMP_DIR"

# 3. Chạy script cài đặt
echo "[+] Tiến hành cài đặt lại PressPanel..."
bash install.sh

# 4. Khôi phục lại dữ liệu nếu trước đó đã có
PANEL_USER="presspanel"
! id "presspanel" &>/dev/null && id "wpc" &>/dev/null && PANEL_USER="wpc"

if [ -f "$BACKUP_DIR/panel.db" ]; then
    echo "[+] Khôi phục lại cơ sở dữ liệu panel.db..."
    cp "$BACKUP_DIR/panel.db" "$INSTALL_DIR/data/panel.db"
    chown "$PANEL_USER":"$PANEL_USER" "$INSTALL_DIR/data/panel.db" 2>/dev/null || true
fi
if [ -f "$BACKUP_DIR/presspanel.conf" ]; then
    echo "[+] Khôi phục lại cấu hình presspanel.conf..."
    cp "$BACKUP_DIR/presspanel.conf" "$INSTALL_DIR/conf/presspanel.conf"
    chmod 600 "$INSTALL_DIR/conf/presspanel.conf"
    chown "$PANEL_USER":"$PANEL_USER" "$INSTALL_DIR/conf/presspanel.conf" 2>/dev/null || true
fi
if [ -f "$BACKUP_DIR/wpc.conf" ]; then
    cp "$BACKUP_DIR/wpc.conf" "$INSTALL_DIR/conf/wpc.conf"
    chmod 600 "$INSTALL_DIR/conf/wpc.conf"
    chown "$PANEL_USER":"$PANEL_USER" "$INSTALL_DIR/conf/wpc.conf" 2>/dev/null || true
fi

# 5. Tự động kiểm tra và chuẩn hóa bảo mật toàn diện (CloudLinux Hardening)
chmod 755 /lib64/ld-*.so /usr/lib64/ld-*.so /lib64/ld-linux*.so.* /usr/lib64/ld-linux*.so.* /lib/ld-*.so /usr/lib/ld-*.so 2>/dev/null || true
if [ -f "$INSTALL_DIR/scripts/security_hardening.sh" ]; then
    echo "[+] Đang kích hoạt chế độ bảo mật toàn diện CloudLinux-Grade Hardening..."
    /bin/bash "$INSTALL_DIR/scripts/security_hardening.sh" fix || true
fi

# 6. Khởi động lại dịch vụ
systemctl restart presspanel 2>/dev/null || systemctl restart wpc 2>/dev/null || true

# 7. Dọn dẹp thư mục tạm
rm -rf "$TMP_DIR" "$BACKUP_DIR"

echo "=========================================================="
echo "   REINSTALL THÀNH CÔNG!                                 "
echo "   Trạng thái dịch vụ: $(systemctl is-active presspanel 2>/dev/null || systemctl is-active wpc 2>/dev/null || echo 'inactive') "
echo "   Giao diện quản trị: http://<IP_MAY_CHU>:8888          "
echo "=========================================================="
