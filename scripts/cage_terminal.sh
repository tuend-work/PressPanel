#!/usr/bin/env bash
# ==============================================================================
# WPC: cage_terminal.sh - Lồng cách ly Mount Namespace cho Terminal SSH
# Giấu hoàn toàn thư mục /home của các User khác, chỉ hiển thị home của User này
# ==============================================================================
set -euo pipefail

TARGET_USER="${1:-}"
if [ -z "$TARGET_USER" ] || [[ ! "$TARGET_USER" =~ ^[a-zA-Z0-9_.-]{1,32}$ ]]; then
    echo "[-] Thieu tham so hoac username khong hop le cho cage terminal: '$TARGET_USER'"
    exit 1
fi

# Kiểm tra xem user có tồn tại trên Linux không
if ! id "$TARGET_USER" &>/dev/null; then
    # Thử chuyển domain thành linux user (bỏ ký tự đặc biệt, lấy tối đa 16 ký tự)
    CLEAN_USER=$(echo "$TARGET_USER" | tr -cd '[:alnum:]' | cut -c 1-16)
    if id "$CLEAN_USER" &>/dev/null; then
        TARGET_USER="$CLEAN_USER"
    else
        echo "[-] Nguoi dung Linux '$TARGET_USER' khong ton tai tren he thong."
        exit 1
    fi
fi

REAL_HOME="/home/$TARGET_USER"
if [ ! -d "$REAL_HOME" ]; then
    mkdir -p "$REAL_HOME" 2>/dev/null || true
    chown "$TARGET_USER:$TARGET_USER" "$REAL_HOME" 2>/dev/null || true
    chmod 750 "$REAL_HOME" 2>/dev/null || true
fi

# Nếu hệ thống có unshare và script đang chạy dưới quyền root:
# Khởi tạo Mount Namespace riêng biệt cách ly hoàn toàn thư mục /home
if [ -x "/usr/bin/unshare" ] && [ "$EUID" -eq 0 ]; then
    exec /usr/bin/unshare --mount /bin/bash -c '
        TARGET_USER="$1"
        REAL_HOME="$2"

        # Cách ly mount event không lan ra namespace cha
        mount --make-rprivate / 2>/dev/null || true
        
        # Lưu lại mount point của home thật trước khi bị che
        MNT_TMP="/run/cage_$$"
        mkdir -p "$MNT_TMP"
        mount --bind /home "$MNT_TMP" 2>/dev/null || true
        
        # Ảo hóa /home bằng tmpfs sạch (mode 711)
        mount -t tmpfs -o mode=711,size=64M tmpfs /home 2>/dev/null || true
        
        # Gắn đúng thư mục thật của user vào /home/$TARGET_USER
        mkdir -p "$REAL_HOME"
        mount --bind "$MNT_TMP/$TARGET_USER" "$REAL_HOME" 2>/dev/null || true
        
        # Tháo gỡ mount tạm để không để lại dấu vết
        umount -l "$MNT_TMP" 2>/dev/null || true
        rmdir "$MNT_TMP" 2>/dev/null || true
        
        cd "$REAL_HOME" 2>/dev/null || cd /
        if command -v runuser &>/dev/null; then
            exec runuser -l "$TARGET_USER" -s /bin/bash
        elif [ -x /sbin/runuser ]; then
            exec /sbin/runuser -l "$TARGET_USER" -s /bin/bash
        elif [ -x /usr/sbin/runuser ]; then
            exec /usr/sbin/runuser -l "$TARGET_USER" -s /bin/bash
        else
            exec su -l "$TARGET_USER" -s /bin/bash
        fi
    ' _ "$TARGET_USER" "$REAL_HOME"
else
    # Fallback tiêu chuẩn nếu môi trường không hỗ trợ unshare
    cd "$REAL_HOME" 2>/dev/null || cd /
    if [ "$EUID" -eq 0 ]; then
        if command -v runuser &>/dev/null; then
            exec runuser -l "$TARGET_USER" -s /bin/bash
        elif [ -x /sbin/runuser ]; then
            exec /sbin/runuser -l "$TARGET_USER" -s /bin/bash
        elif [ -x /usr/sbin/runuser ]; then
            exec /usr/sbin/runuser -l "$TARGET_USER" -s /bin/bash
        else
            exec su -l "$TARGET_USER" -s /bin/bash
        fi
    else
        exec sudo -n -u "$TARGET_USER" /bin/bash --login
    fi
fi
