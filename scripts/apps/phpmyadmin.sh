#!/usr/bin/env bash
# PressPanel: phpmyadmin.sh - Cài đặt phpMyAdmin
set -euo pipefail

TARGET_DIR="/opt/presspanel/apps/phpmyadmin"

echo "[+] Đang tải và cấu hình phpMyAdmin..."
mkdir -p "$TARGET_DIR"
cd /tmp
wget -q https://www.phpmyadmin.net/downloads/phpMyAdmin-latest-all-languages.tar.gz -O pma.tar.gz
tar -xzf pma.tar.gz --strip-components=1 -C "$TARGET_DIR"
rm -f pma.tar.gz
chown -R presspanel:presspanel "$TARGET_DIR" 2>/dev/null || true
echo "[+] Cài đặt phpMyAdmin thành công tại $TARGET_DIR"
