#!/usr/bin/env bash
# WPCloud: wp-cli.sh - Cài đặt WP-CLI chính thức
set -euo pipefail

echo "[+] Đang tải WP-CLI binary..."
curl -sSL -O https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar
chmod +x wp-cli.phar
mv wp-cli.phar /usr/local/bin/wp
echo "[+] Cài đặt WP-CLI thành công: $(wp --info 2>/dev/null | head -n 1 || echo 'ok')"
