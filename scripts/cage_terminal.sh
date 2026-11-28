#!/usr/bin/env bash
# ==============================================================================
# WPC: cage_terminal.sh - Lồng cách ly Mount & PID Namespace cho Terminal SSH
# Bảo mật toàn diện chuẩn CloudLinux: Giấu /home, /tmp, /root, /opt/wpc,
# ảo hóa /etc/passwd, /etc/group, /var/log, /dev/shm và chống Fork Bomb
# ==============================================================================
set -euo pipefail

TARGET_USER="${1:-}"
EXEC_CMD="${2:-}"
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
        EXEC_CMD="${3:-}"

        # 1. Cách ly mount event không lan ngược ra namespace cha
        mount --make-rprivate / 2>/dev/null || true

        # 2. Gắn tiến trình vào Cgroup v2 của user trước khi chroot
        USER_CG_DIR=$(find /sys/fs/cgroup/presspanel /sys/fs/cgroup/wpc /sys/fs/cgroup/presspanel.slice -maxdepth 4 -type d \( -name "site_${TARGET_USER}" -o -name "user_${TARGET_USER}" -o -name "presspanel-user-${TARGET_USER}.slice" -o -name "presspanel-hosting_${TARGET_USER}.slice" \) 2>/dev/null | head -n 1 || true)
        if [ -n "$USER_CG_DIR" ] && [ -f "$USER_CG_DIR/cgroup.procs" ]; then
            echo $$ > "$USER_CG_DIR/cgroup.procs" 2>/dev/null || true
        fi

        # 3. Khởi tạo RootFS CageFS sạch tại /run/cage_root_$$ (chuẩn CloudLinux CageFS)
        CAGE="/run/cage_root_$$"
        mkdir -p "$CAGE"
        mount -t tmpfs -o mode=755,size=32M tmpfs "$CAGE"

        # 3.1 /usr (read-only) & các symlink chuẩn (/bin, /lib, /lib64, /sbin)
        mkdir -p "$CAGE/usr"
        mount --bind /usr "$CAGE/usr"
        mount -o remount,ro,bind "$CAGE/usr" 2>/dev/null || true

        for d in bin lib lib64 sbin; do
            if [ -L "/$d" ]; then
                ln -s "$(readlink "/$d")" "$CAGE/$d" 2>/dev/null || true
            elif [ -d "/$d" ]; then
                mkdir -p "$CAGE/$d"
                mount --bind "/$d" "$CAGE/$d"
                mount -o remount,ro,bind "$CAGE/$d" 2>/dev/null || true
            fi
        done

        # 3.2 /proc với hidepid=2 + LXCFS + ẩn thông số kernel/phần cứng
        mkdir -p "$CAGE/proc"
        mount -t proc -o hidepid=2 proc "$CAGE/proc" 2>/dev/null || mount -t proc proc "$CAGE/proc" 2>/dev/null || true

        if [ -d "/var/lib/lxcfs/proc" ]; then
            for lf in meminfo stat uptime loadavg diskstats swaps slabinfo; do
                if [ -e "/var/lib/lxcfs/proc/$lf" ] && [ -e "$CAGE/proc/$lf" ]; then
                    mount --bind "/var/lib/lxcfs/proc/$lf" "$CAGE/proc/$lf" 2>/dev/null || true
                fi
            done
        fi

        MNT_TMP_SYS="$CAGE/run/cage_sys"
        mkdir -p "$MNT_TMP_SYS"
        mount -t tmpfs -o mode=755,size=2M tmpfs "$MNT_TMP_SYS" 2>/dev/null || true

        LXCFS_PROC_CNT=1
        if [ -e "/var/lib/lxcfs/proc/cpuinfo" ]; then
            LXCFS_PROC_CNT=$(grep -c "^processor" /var/lib/lxcfs/proc/cpuinfo 2>/dev/null || echo "1")
            [ "$LXCFS_PROC_CNT" -lt 1 ] && LXCFS_PROC_CNT=1
            sed -E "s/^(model name[[:space:]]*:).*/\1 PressPanel Virtual vCPU/; s/^(vendor_id[[:space:]]*:).*/\1 PressPanel/; s/^(siblings[[:space:]]*:).*/\1 ${LXCFS_PROC_CNT}/; s/^(cpu cores[[:space:]]*:).*/\1 ${LXCFS_PROC_CNT}/; /^flags[[:space:]]*:/d; /^bugs[[:space:]]*:/d; /^microcode[[:space:]]*:/d" /var/lib/lxcfs/proc/cpuinfo > "$MNT_TMP_SYS/cpuinfo" 2>/dev/null || true
            [ -s "$MNT_TMP_SYS/cpuinfo" ] && mount --bind "$MNT_TMP_SYS/cpuinfo" "$CAGE/proc/cpuinfo" 2>/dev/null || true
        fi

        for pfile in interrupts iomem ioports modules cmdline version vmstat zoneinfo buddyinfo pagetypeinfo; do
            [ -e "$CAGE/proc/$pfile" ] && mount --bind /dev/null "$CAGE/proc/$pfile" 2>/dev/null || true
        done

        # 3.3 /sys ảo hóa tối giản: chỉ giữ đúng cpu0..cpu(N-1), ẩn hoàn toàn dmi/firmware/block/hypervisor
        LXCFS_RANGE="0"
        [ "$LXCFS_PROC_CNT" -gt 1 ] && LXCFS_RANGE="0-$((LXCFS_PROC_CNT - 1))"
        mkdir -p "$CAGE/sys/devices/system/cpu"
        echo "$LXCFS_RANGE" > "$CAGE/sys/devices/system/cpu/online" 2>/dev/null || true
        echo "$LXCFS_RANGE" > "$CAGE/sys/devices/system/cpu/possible" 2>/dev/null || true
        echo "$LXCFS_RANGE" > "$CAGE/sys/devices/system/cpu/present" 2>/dev/null || true
        echo "" > "$CAGE/sys/devices/system/cpu/offline" 2>/dev/null || true
        echo "$((LXCFS_PROC_CNT - 1))" > "$CAGE/sys/devices/system/cpu/kernel_max" 2>/dev/null || true
        i=0
        while [ "$i" -lt "$LXCFS_PROC_CNT" ]; do
            mkdir -p "$CAGE/sys/devices/system/cpu/cpu${i}/topology" 2>/dev/null || true
            echo "1" > "$CAGE/sys/devices/system/cpu/cpu${i}/online" 2>/dev/null || true
            echo "0" > "$CAGE/sys/devices/system/cpu/cpu${i}/topology/physical_package_id" 2>/dev/null || true
            echo "$i" > "$CAGE/sys/devices/system/cpu/cpu${i}/topology/core_id" 2>/dev/null || true
            i=$((i + 1))
        done

        # Bọc lệnh lscpu trong cage
        if [ -x "$CAGE/usr/bin/lscpu" ]; then
            cat << EOF > "$MNT_TMP_SYS/lscpu"
#!/usr/bin/env bash
echo "Architecture:                         \$(uname -m)"
echo "CPU(s):                               ${LXCFS_PROC_CNT}"
echo "On-line CPU(s) list:                  ${LXCFS_RANGE}"
echo "Model name:                           PressPanel Virtual vCPU"
echo "Thread(s) per core:                   1"
echo "Core(s) per socket:                   ${LXCFS_PROC_CNT}"
echo "Socket(s):                            1"
EOF
            chmod 755 "$MNT_TMP_SYS/lscpu" 2>/dev/null || true
            mount --bind "$MNT_TMP_SYS/lscpu" "$CAGE/usr/bin/lscpu" 2>/dev/null || true
        fi

        # 3.4 /dev tối giản (ẩn toàn bộ /dev/sd*, /dev/vd*, /dev/nvme*, /dev/mem)
        mkdir -p "$CAGE/dev/pts" "$CAGE/dev/shm"
        mount -t tmpfs -o mode=1777,nodev,nosuid,noexec,size=64M tmpfs "$CAGE/dev/shm" 2>/dev/null || true
        for devnode in null zero full random urandom tty; do
            if [ -e "/dev/$devnode" ]; then
                touch "$CAGE/dev/$devnode"
                mount --bind "/dev/$devnode" "$CAGE/dev/$devnode" 2>/dev/null || true
            fi
        done
        mount --bind /dev/pts "$CAGE/dev/pts" 2>/dev/null || true
        ln -s /proc/self/fd "$CAGE/dev/fd" 2>/dev/null || true
        ln -s /proc/self/fd/0 "$CAGE/dev/stdin" 2>/dev/null || true
        ln -s /proc/self/fd/1 "$CAGE/dev/stdout" 2>/dev/null || true
        ln -s /proc/self/fd/2 "$CAGE/dev/stderr" 2>/dev/null || true

        # 3.5 /etc tối giản (chỉ giữ passwd/group của user và các file hệ thống cần cho CLI/SSL/DNS/PHP)
        mkdir -p "$CAGE/etc"
        grep -E "^(root|bin|daemon|sys|adm|nobody|systemd-[^:]+|$TARGET_USER):" /etc/passwd > "$CAGE/etc/passwd" 2>/dev/null || true
        TARGET_GROUP=$(id -gn "$TARGET_USER" 2>/dev/null || echo "$TARGET_USER")
        grep -E "^(root|bin|daemon|sys|adm|nobody|users|$TARGET_GROUP):" /etc/group > "$CAGE/etc/group" 2>/dev/null || true

        for ef in resolv.conf hosts nsswitch.conf localtime ld.so.cache ld.so.conf bash.bashrc profile inputrc; do
            if [ -e "/etc/$ef" ]; then
                touch "$CAGE/etc/$ef" 2>/dev/null || true
                mount --bind "/etc/$ef" "$CAGE/etc/$ef" 2>/dev/null || true
                mount -o remount,ro,bind "$CAGE/etc/$ef" 2>/dev/null || true
            fi
        done
        for ed in ld.so.conf.d ssl ca-certificates alternatives php terminfo mysql; do
            if [ -d "/etc/$ed" ]; then
                mkdir -p "$CAGE/etc/$ed"
                mount --bind "/etc/$ed" "$CAGE/etc/$ed" 2>/dev/null || true
                mount -o remount,ro,bind "$CAGE/etc/$ed" 2>/dev/null || true
            fi
        done

        # 3.6 /home/<user>
        mkdir -p "$CAGE/home"
        chmod 711 "$CAGE/home"
        mkdir -p "$CAGE$REAL_HOME"
        mount --bind "$REAL_HOME" "$CAGE$REAL_HOME"

        # 3.7 /tmp, /var/tmp và socket MySQL/LSWS
        mkdir -p "$CAGE/tmp" "$CAGE/var/tmp"
        mount -t tmpfs -o mode=1777,strictatime,nodev,nosuid,size=256M tmpfs "$CAGE/tmp"
        mount -t tmpfs -o mode=1777,strictatime,nodev,nosuid,size=256M tmpfs "$CAGE/var/tmp"
        ln -s ../run "$CAGE/var/run" 2>/dev/null || true

        if [ -d "/run/mysqld" ]; then
            mkdir -p "$CAGE/run/mysqld"
            mount --bind /run/mysqld "$CAGE/run/mysqld" 2>/dev/null || true
        fi
        if [ -S "/tmp/mysql.sock" ]; then
            touch "$CAGE/tmp/mysql.sock" 2>/dev/null || true
            mount --bind /tmp/mysql.sock "$CAGE/tmp/mysql.sock" 2>/dev/null || true
        fi
        if [ -d "/tmp/lshttpd" ]; then
            for s in /tmp/lshttpd/*"$TARGET_USER"*.sock; do
                if [ -S "$s" ]; then
                    mkdir -p "$CAGE/tmp/lshttpd"
                    chmod 1777 "$CAGE/tmp/lshttpd"
                    fname=$(basename "$s")
                    touch "$CAGE/tmp/lshttpd/$fname" 2>/dev/null || true
                    mount --bind "$s" "$CAGE/tmp/lshttpd/$fname" 2>/dev/null || true
                fi
            done
        fi

        # 4. Giới hạn tài nguyên tiến trình chống Fork Bomb
        ulimit -u 150 2>/dev/null || true

        # 5. Chuyển vào CageFS bằng chroot + setpriv
        T_UID=$(id -u "$TARGET_USER" 2>/dev/null || echo "")
        T_GID=$(id -g "$TARGET_USER" 2>/dev/null || echo "")
        if command -v setpriv &>/dev/null && [ -n "$T_UID" ] && [ -n "$T_GID" ]; then
            if [ -n "$EXEC_CMD" ]; then
                exec chroot "$CAGE" /usr/bin/setpriv --reuid="$T_UID" --regid="$T_GID" --init-groups /usr/bin/env -i \
                    HOME="$REAL_HOME" USER="$TARGET_USER" LOGNAME="$TARGET_USER" \
                    SHELL=/bin/bash TERM="${TERM:-xterm-256color}" \
                    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" LANG="${LANG:-en_US.UTF-8}" \
                    /bin/bash -c "cd \"$REAL_HOME\" 2>/dev/null || cd /; exec /bin/bash -c \"\$1\"" _ "$EXEC_CMD"
            else
                exec chroot "$CAGE" /usr/bin/setpriv --reuid="$T_UID" --regid="$T_GID" --init-groups /usr/bin/env -i \
                    HOME="$REAL_HOME" USER="$TARGET_USER" LOGNAME="$TARGET_USER" \
                    SHELL=/bin/bash TERM="${TERM:-xterm-256color}" \
                    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" LANG="${LANG:-en_US.UTF-8}" \
                    /bin/bash -c "cd \"$REAL_HOME\" 2>/dev/null || cd /; exec /bin/bash --login"
            fi
        else
            if [ -n "$EXEC_CMD" ]; then
                exec chroot "$CAGE" /bin/su -l "$TARGET_USER" -s /bin/bash -c "$EXEC_CMD"
            else
                exec chroot "$CAGE" /bin/su -l "$TARGET_USER" -s /bin/bash
            fi
        fi
    ' _ "$TARGET_USER" "$REAL_HOME" "$EXEC_CMD"
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
