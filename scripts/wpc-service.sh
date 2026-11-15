#!/usr/bin/env bash
# ==============================================================================
# WPCloud Control Panel Service Runner
# Khởi chạy ứng dụng FastAPI + Uvicorn với cơ chế tự phục hồi môi trường & logging
# ==============================================================================

set -eo pipefail

INSTALL_DIR="/opt/wpc"
cd "$INSTALL_DIR" 2>/dev/null || cd /opt/wpc

# Đọc cổng cấu hình từ wpc.conf (mặc định 8888)
PANEL_PORT=$(grep -E "^port=" "$INSTALL_DIR/conf/wpc.conf" 2>/dev/null | cut -d'=' -f2 | tr -d ' \r\n' || echo "8888")
[ -z "$PANEL_PORT" ] && PANEL_PORT="8888"

# 1. Tìm Python binary trong venv hoặc hệ thống
PY_BIN=""
if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
    PY_BIN="$INSTALL_DIR/venv/bin/python"
elif [ -x "$INSTALL_DIR/venv/bin/python3" ]; then
    PY_BIN="$INSTALL_DIR/venv/bin/python3"
fi

# 2. Nếu venv không tồn tại hoặc hỏng, tìm python hệ thống
if [ -z "$PY_BIN" ] || ! "$PY_BIN" -c "import sys" &>/dev/null; then
    for py in python3.12 python3.11 python3.10 python3.9 python3; do
        if command -v "$py" &>/dev/null; then
            PY_BIN=$(command -v "$py")
            break
        fi
    done
fi

if [ -z "$PY_BIN" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [FATAL] Không tìm thấy Python 3 trên hệ thống!" >&2
    exit 1
fi

# 3. Kiểm tra uvicorn và fastapi
if ! "$PY_BIN" -c "import uvicorn, fastapi" &>/dev/null; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] Thiếu uvicorn hoặc fastapi trong $PY_BIN, đang kiểm tra cài đặt..." >&2
    if [ -x "$INSTALL_DIR/venv/bin/pip" ]; then
        "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet 2>/dev/null || true
    fi
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Khởi động WPCloud Server trên cổng $PANEL_PORT sử dụng $PY_BIN..."

# 4. Thực thi Uvicorn với exec để systemd quản lý PID trực tiếp
export PYTHONUNBUFFERED=1
export PYTHONPATH="$INSTALL_DIR"
exec "$PY_BIN" -m uvicorn app.main:app --host 0.0.0.0 --port "$PANEL_PORT" --workers 2
