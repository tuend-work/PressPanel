#!/usr/bin/env bash
# ==============================================================================
# WPCloud: cgroup_attach.sh - Tự động quét và di chuyển PID của các Hosting User
# vào đúng Cgroup v2 (/sys/fs/cgroup/wpc/reseller_*/user_* hoặc user.slice)
# Được kích hoạt định kỳ qua systemd timer hoặc hook sau khi PHP-FPM / Vhost reload
# ==============================================================================

set -u

# 1. Tìm tất cả các cgroup của user dưới /sys/fs/cgroup/wpc
for user_cg in /sys/fs/cgroup/wpc/reseller_*/user_* /sys/fs/cgroup/wpc/user_*; do
    [ -d "$user_cg" ] || continue
    
    # Lấy username từ tên thư mục user_<username>
    CG_NAME=$(basename "$user_cg")
    USERNAME="${CG_NAME#user_}"
    [ -n "$USERNAME" ] || continue

    # Kiểm tra xem user có tồn tại trên Linux không
    id -u "$USERNAME" &>/dev/null || continue

    # Gán tất cả PID đang chạy của user này vào cgroup.procs
    pgrep -u "$USERNAME" 2>/dev/null | while read -r pid; do
        [ -n "$pid" ] && echo "$pid" > "$user_cg/cgroup.procs" 2>/dev/null || true
    done
done

exit 0
