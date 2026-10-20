#!/usr/bin/env bash
# WPCloud: build_app.sh - Bộ điều phối biên dịch ứng dụng từ mã nguồn với đối số tùy chỉnh
set -euo pipefail

APP_ID="${1:-nginx}"
WPC_BASE="/opt/wpc"

mkdir -p "$WPC_BASE/config/build" /var/log/wpc 2>/dev/null || true
LOG_FILE="/var/log/wpc/build_${APP_ID}.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [BUILD] Bắt đầu tác vụ biên dịch ứng dụng: $APP_ID" | tee "$LOG_FILE"

case "$APP_ID" in
    nginx|openresty)
        SCRIPT="$WPC_BASE/scripts/apps/nginx.sh"
        if [ ! -f "$SCRIPT" ]; then
            SCRIPT="$(dirname "$0")/nginx.sh"
        fi
        if [ -f "$SCRIPT" ]; then
            bash "$SCRIPT" build 2>&1 | tee -a "$LOG_FILE"
        else
            echo "[-] Không tìm thấy kịch bản build nginx.sh tại $SCRIPT" | tee -a "$LOG_FILE"
            exit 1
        fi
        ;;

    php*)
        VER=$(echo "$APP_ID" | sed -E 's/^php//; s/^([0-9])([0-9])$/\1.\2/')
        [ -z "$VER" ] && VER="8.2"
        SCRIPT="$WPC_BASE/scripts/apps/php.sh"
        if [ ! -f "$SCRIPT" ]; then
            SCRIPT="$(dirname "$0")/php.sh"
        fi
        if [ -f "$SCRIPT" ]; then
            bash "$SCRIPT" build "$VER" 2>&1 | tee -a "$LOG_FILE"
        else
            echo "[-] Không tìm thấy kịch bản build php.sh tại $SCRIPT" | tee -a "$LOG_FILE"
            exit 1
        fi
        ;;

    ols)
        SCRIPT="$WPC_BASE/scripts/apps/ols.sh"
        if [ ! -f "$SCRIPT" ]; then
            SCRIPT="$(dirname "$0")/ols.sh"
        fi
        if [ -f "$SCRIPT" ]; then
            bash "$SCRIPT" build 2>&1 | tee -a "$LOG_FILE"
        else
            echo "[-] OpenLiteSpeed được khuyên dùng cài đặt qua kho lsws chính thức." | tee -a "$LOG_FILE"
            exit 1
        fi
        ;;

    *)
        echo "[-] Chưa hỗ trợ biên dịch tự động cho ứng dụng $APP_ID." | tee -a "$LOG_FILE"
        exit 1
        ;;
esac

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [BUILD] Tác vụ biên dịch hoàn tất thành công!" | tee -a "$LOG_FILE"
