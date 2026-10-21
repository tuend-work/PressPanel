#!/usr/bin/env bash
# ==============================================================================
# WPC: cage_terminal.sh - Lồng cách ly Mount & PID Namespace cho Terminal SSH
# Giấu hoàn toàn thư mục /home, /tmp, /var/tmp, /root của User khác & System
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
# Khởi tạo Mount & PID Namespace riêng biệt cách ly hoàn toàn cây thư mục & tiến trình
if [ -x "/usr/bin/unshare" ] && [ "$EUID" -eq 0 ]; then
    UNSHARE_BIN="/usr/bin/unshare --mount"
    if /usr/bin/unshare --mount --pid --fork true 2>/dev/null; then
        UNSHARE_BIN="/usr/bin/unshare --mount --pid --fork"
    fi

    exec $UNSHARE_BIN /bin/bash -c '
        TARGET_USER="$1"
        REAL_HOME="$2"

        # 1. Cách ly mount event không lan ngược ra namespace cha
        mount --make-rprivate / 2>/dev/null || true

        # 2. Tái tạo /proc riêng biệt nếu có PID namespace (Ẩn toàn bộ process của root & user khác)
        mount -t proc proc /proc 2>/dev/null || true

        # 3. Cách ly /home: Ảo hóa tmpfs sạch, chỉ hiển thị đúng thư mục của user này
        MNT_TMP_HOME="/run/cage_home_$$"
        mkdir -p "$MNT_TMP_HOME"
        mount --bind /home "$MNT_TMP_HOME" 2>/dev/null || true

        mount -t tmpfs -o mode=711,size=64M tmpfs /home 2>/dev/null || true
        mkdir -p "$REAL_HOME"
        mount --bind "$MNT_TMP_HOME/$TARGET_USER" "$REAL_HOME" 2>/dev/null || true

        umount -l "$MNT_TMP_HOME" 2>/dev/null || true
        rmdir "$MNT_TMP_HOME" 2>/dev/null || true

        # 4. Cách ly /tmp và /var/tmp: Ảo hóa tmpfs riêng biệt cho session (Ẩn toàn bộ file hệ thống/root trong /tmp)
        # Giữ lại socket MariaDB/MySQL nếu có
        MYSQL_SOCK=""
        if [ -S "/var/run/mysqld/mysqld.sock" ]; then
            MYSQL_SOCK="/var/run/mysqld/mysqld.sock"
        elif [ -S "/run/mysqld/mysqld.sock" ]; then
            MYSQL_SOCK="/run/mysqld/mysqld.sock"
        elif [ -S "/tmp/mysql.sock" ]; then
            MNT_TMP_SOCK="/run/cage_sock_$$"
            mkdir -p "$MNT_TMP_SOCK"
            mount --bind /tmp/mysql.sock "$MNT_TMP_SOCK" 2>/dev/null || true
            MYSQL_SOCK="$MNT_TMP_SOCK"
        fi

        # Giữ lại socket PHP/LSWS riêng của user này nếu có
        USER_LSWS_SOCKS=()
        if [ -d "/tmp/lshttpd" ]; then
            for s in /tmp/lshttpd/*"$TARGET_USER"*.sock; do
                if [ -S "$s" ]; then
                    USER_LSWS_SOCKS+=("$s")
                fi
            done
        fi

        MNT_TMP_LS="/run/cage_ls_$$"
        if [ "${#USER_LSWS_SOCKS[@]}" -gt 0 ]; then
            mkdir -p "$MNT_TMP_LS"
            for s in "${USER_LSWS_SOCKS[@]}"; do
                fname=$(basename "$s")
                touch "$MNT_TMP_LS/$fname" 2>/dev/null || true
                mount --bind "$s" "$MNT_TMP_LS/$fname" 2>/dev/null || true
            done
        fi

        # Ảo hóa /tmp & /var/tmp bằng tmpfs riêng biệt sạch 100%
        mount -t tmpfs -o mode=1777,strictatime,nodev,nosuid,size=256M tmpfs /tmp 2>/dev/null || true
        mount -t tmpfs -o mode=1777,strictatime,nodev,nosuid,size=256M tmpfs /var/tmp 2>/dev/null || true

        # Tái mount socket MySQL vào /tmp riêng
        if [ -n "$MYSQL_SOCK" ] && [ -e "$MYSQL_SOCK" ]; then
            ln -sf "$MYSQL_SOCK" /tmp/mysql.sock 2>/dev/null || true
        fi

        # Tái mount socket LSWS riêng của user nếu có
        if [ "${#USER_LSWS_SOCKS[@]}" -gt 0 ] && [ -d "$MNT_TMP_LS" ]; then
            mkdir -p /tmp/lshttpd 2>/dev/null || true
            chmod 1777 /tmp/lshttpd 2>/dev/null || true
            for s in "$MNT_TMP_LS"/*; do
                if [ -e "$s" ]; then
                    fname=$(basename "$s")
                    touch "/tmp/lshttpd/$fname" 2>/dev/null || true
                    mount --bind "$s" "/tmp/lshttpd/$fname" 2>/dev/null || true
                fi
            done
            umount -l "$MNT_TMP_LS" 2>/dev/null || true
            rmdir "$MNT_TMP_LS" 2>/dev/null || true
        fi

        # 5. Cách ly /root: Ảo hóa thư mục trống bảo vệ toàn diện
        mount -t tmpfs -o mode=700,size=1M tmpfs /root 2>/dev/null || true

        # 6. Chuyển vào thư mục home và spawn login shell
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
