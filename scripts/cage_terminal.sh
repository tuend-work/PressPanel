#!/usr/bin/env bash
# ==============================================================================
# WPC: cage_terminal.sh - Lồng cách ly Mount & PID Namespace cho Terminal SSH
# Bảo mật toàn diện chuẩn CloudLinux: Giấu /home, /tmp, /root, /opt/wpc,
# ảo hóa /etc/passwd, /etc/group, /var/log, /dev/shm và chống Fork Bomb
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
# Khởi tạo Mount Namespace riêng biệt cách ly hoàn toàn cây thư mục & bảo mật /proc
if [ -x "/usr/bin/unshare" ] && [ "$EUID" -eq 0 ]; then
    # LƯU Ý BẢO MẬT & TRẢI NGHIỆM TIẾN TRÌNH:
    # KHÔNG dùng cờ --pid --fork vì --pid tạo PID Namespace con mới toanh.
    # Trong PID Namespace con, lệnh htop/ps chỉ nhìn thấy bash của terminal và KHÔNG THỂ
    # nhìn thấy các tiến trình website nền (lsphp, php-fpm, cron, worker) chạy ở Host namespace!
    # Thay vào đó, dùng mount namespace kết hợp /proc hidepid=2 (chuẩn bảo mật CloudLinux CageFS).
    # Kernel sẽ tự động ẩn toàn bộ process của root và các user khác, chỉ cho phép user nhìn thấy
    # đúng tiến trình thuộc UID của chính họ (bao gồm cả lsphp, php-fpm, bash, htop,...).
    UNSHARE_BIN="/usr/bin/unshare --mount"

    exec $UNSHARE_BIN /bin/bash -c '
        TARGET_USER="$1"
        REAL_HOME="$2"

        # 1. Cách ly mount event không lan ngược ra namespace cha
        mount --make-rprivate / 2>/dev/null || true

        # 2. Bảo vệ /proc: hidepid=2 giúp user thấy được mọi tiến trình của chính mình (lsphp, php-fpm, cron, bash)
        # nhưng tuyệt đối KHÔNG THỂ xem trộm tiến trình của root hoặc các website user khác
        mount -o remount,hidepid=2 /proc 2>/dev/null || mount -t proc -o hidepid=2 proc /proc 2>/dev/null || mount -t proc proc /proc 2>/dev/null || true

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

        # 5. Cách ly /dev/shm: Ảo hóa riêng biệt có cờ noexec
        mount -t tmpfs -o mode=1777,nodev,nosuid,noexec,size=64M tmpfs /dev/shm 2>/dev/null || true

        # 6. Cách ly /root & /opt/wpc: Ẩn hoàn toàn thư mục quản trị và database
        mount -t tmpfs -o mode=700,size=1M tmpfs /root 2>/dev/null || true
        mount -t tmpfs -o mode=700,size=1M tmpfs /opt/wpc 2>/dev/null || true

        # 7. Ảo hóa /etc/passwd & /etc/group (Chuẩn CloudLinux CageFS: Tuyệt đối không để lộ danh sách user khác)
        MNT_TMP_ETC="/run/cage_etc_$$"
        mkdir -p "$MNT_TMP_ETC"
        grep -E "^(root|bin|daemon|sys|adm|nobody|systemd-[^:]+|$TARGET_USER):" /etc/passwd > "$MNT_TMP_ETC/passwd" 2>/dev/null || true
        if [ -s "$MNT_TMP_ETC/passwd" ]; then
            mount --bind "$MNT_TMP_ETC/passwd" /etc/passwd 2>/dev/null || true
        fi

        TARGET_GROUP=$(id -gn "$TARGET_USER" 2>/dev/null || echo "$TARGET_USER")
        grep -E "^(root|bin|daemon|sys|adm|nobody|users|$TARGET_GROUP):" /etc/group > "$MNT_TMP_ETC/group" 2>/dev/null || true
        if [ -s "$MNT_TMP_ETC/group" ]; then
            mount --bind "$MNT_TMP_ETC/group" /etc/group 2>/dev/null || true
        fi

        # 8. Ẩn toàn bộ cấu hình vhost web server và logs hệ thống
        if [ -d "/etc/nginx" ]; then
            mount -t tmpfs -o mode=755,size=5M tmpfs /etc/nginx/conf.d 2>/dev/null || true
            mount -t tmpfs -o mode=755,size=5M tmpfs /etc/nginx/sites-enabled 2>/dev/null || true
        fi
        if [ -d "/usr/local/lsws/conf" ]; then
            mount -t tmpfs -o mode=700,size=1M tmpfs /usr/local/lsws/conf/vhosts 2>/dev/null || true
        fi
        mount -t tmpfs -o mode=755,size=10M tmpfs /var/log 2>/dev/null || true

        # 9. Giới hạn tài nguyên tiến trình chống Fork Bomb
        ulimit -u 150 2>/dev/null || true

        # 10. Chuyển vào thư mục home và spawn login shell
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
