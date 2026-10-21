#!/usr/bin/env bash
# ==============================================================================
# WPCloud: cgroup_attach.sh - Cgroups v2 Realtime Process Enforcer
# Khóa trần tài nguyên nghiêm ngặt cấp Hosting User (Single Tenant Ceiling):
# - Gom toàn bộ tiến trình của tất cả website con (isolated_user) vào Cgroup
#   duy nhất của Hosting User cha (/sys/fs/cgroup/wpc/reseller_*/user_<hosting_user>)
# - Đảm bảo tổng mọi website của user chỉ được dùng tối đa CPU, RAM của gói hosting.
# - Tự động dọn dẹp và xóa các cgroup rác độc lập của website con để tránh x2/x3 hạn mức.
# ==============================================================================

set -u

WPC_DB="/opt/wpc/data/panel.db"
PYTHON_BIN="/opt/wpc/venv/bin/python"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="/usr/bin/python3"

# Hàm lấy mapping từ SQLite DB:
# Định dạng xuất ra:
# HOSTING_USER:RESELLER_ID:CPU_PCT:MEM_MB:CHILD_USERS_COMMA_SEPARATED
fetch_user_mappings() {
    if [ ! -f "$WPC_DB" ]; then
        return
    fi

    "$PYTHON_BIN" - << 'EOF' 2>/dev/null || true
import sqlite3, os, re

db_path = "/opt/wpc/data/panel.db"
if not os.path.exists(db_path):
    exit(0)

try:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # Lấy thông tin hosting users (role user, reseller, admin)
    cur.execute("SELECT id, username, parent_id, cpu_pct, mem_mb FROM users WHERE status != 'deleted'")
    users = {}
    for r in cur.fetchall():
        uid, uname, parent, cpu, mem = r[0], r[1], r[2] or 1, r[3] or 100, r[4] or 1024
        users[uid] = {
            "username": uname,
            "reseller_id": str(parent),
            "cpu": int(cpu),
            "mem": int(mem),
            "sites": {}
        }

    # Lấy tất cả các website thuộc user và hạn mức riêng của website (nếu có package_id con)
    cur.execute("""
        SELECT d.user_id, d.domain_name,
               COALESCE(p_dom.cpu_pct, 0) as dom_cpu,
               COALESCE(p_dom.mem_mb, 0) as dom_mem
        FROM domains d
        LEFT JOIN packages p_dom ON d.package_id = p_dom.id
    """)
    for uid, dname, d_cpu, d_mem in cur.fetchall():
        if uid in users:
            clean_d = re.sub(r"[^a-z0-9]", "", (dname or "").lower())[:30]
            if clean_d and clean_d != users[uid]["username"]:
                # Nếu domain có package riêng thì lấy d_cpu, nếu không thì lấy cpu của user cha
                eff_cpu = int(d_cpu) if d_cpu and d_cpu > 0 else users[uid]["cpu"]
                eff_mem = int(d_mem) if d_mem and d_mem > 0 else users[uid]["mem"]
                users[uid]["sites"][clean_d] = (eff_cpu, eff_mem)

    conn.close()

    for u_info in users.values():
        u = u_info["username"]
        r_id = u_info["reseller_id"]
        cpu = u_info["cpu"]
        mem = u_info["mem"]
        site_tokens = []
        for s_name, (s_c, s_m) in sorted(u_info["sites"].items()):
            site_tokens.append(f"{s_name}@{s_c}@{s_m}")
        sites_str = ",".join(site_tokens)
        print(f"{u}:{r_id}:{cpu}:{mem}:{sites_str}")
except Exception:
    pass
EOF
}

# Đảm bảo cấu trúc Cgroup gốc
init_cgroup_root() {
    mkdir -p /sys/fs/cgroup/wpc 2>/dev/null || true
    for c in cpu memory io pids; do
        echo "+$c" > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true
        echo "+$c" > /sys/fs/cgroup/wpc/cgroup.subtree_control 2>/dev/null || true
    done
}

# Đồng bộ Cgroup cho một Hosting User cụ thể (Hỗ trợ Sub-cgroups phân cấp 2 tầng)
enforce_hosting_user() {
    local h_user="$1"
    local reseller_id="$2"
    local cpu_pct="$3"
    local mem_mb="$4"
    local child_sites="$5"

    local cg_dir="/sys/fs/cgroup/wpc/reseller_${reseller_id}/user_${h_user}"
    local cg_flat="/sys/fs/cgroup/wpc/user_${h_user}"

    # 1. Tạo thư mục Cgroup trần cha của Hosting User
    mkdir -p "$cg_dir" "$cg_flat" 2>/dev/null || true

    # 2. Thiết lập trần cha (Hard Ceiling của gói Hosting): CPU và RAM
    local cpu_max="$((cpu_pct * 1000)) 100000"
    local mem_max="$((mem_mb * 1024 * 1024))"

    [ -f "$cg_dir/cpu.max" ] && echo "$cpu_max" > "$cg_dir/cpu.max" 2>/dev/null || true
    [ -f "$cg_dir/memory.max" ] && echo "$mem_max" > "$cg_dir/memory.max" 2>/dev/null || true
    [ -f "$cg_dir/pids.max" ] && echo "150" > "$cg_dir/pids.max" 2>/dev/null || true
    [ -f "$cg_flat/cpu.max" ] && echo "$cpu_max" > "$cg_flat/cpu.max" 2>/dev/null || true
    [ -f "$cg_flat/memory.max" ] && echo "$mem_max" > "$cg_flat/memory.max" 2>/dev/null || true
    [ -f "$cg_flat/pids.max" ] && echo "150" > "$cg_flat/pids.max" 2>/dev/null || true

    # Kích hoạt controller cho cây con bên trong Cgroup của Hosting User
    for c in cpu memory io pids; do
        echo "+$c" > "$cg_dir/cgroup.subtree_control" 2>/dev/null || true
        echo "+$c" > "$cg_flat/cgroup.subtree_control" 2>/dev/null || true
    done

    # Thiết lập Systemd Slice tương ứng
    systemctl set-property "wpc-hosting_${h_user}.slice" CPUQuota="${cpu_pct}%" MemoryMax="${mem_mb}M" TasksMax=150 2>/dev/null || true

    # 3. Quản lý tiến trình của chính Hosting User (nếu có SSH/cron)
    if id "$h_user" &>/dev/null; then
        mapfile -t h_pids < <(pgrep -u "$h_user" 2>/dev/null || true)
        if [ ${#h_pids[@]} -gt 0 ]; then
            local main_cg="$cg_dir/main"
            mkdir -p "$main_cg" 2>/dev/null || true
            for p in "${h_pids[@]}"; do
                [ -n "$p" ] && echo "$p" > "$main_cg/cgroup.procs" 2>/dev/null || echo "$p" > "$cg_dir/cgroup.procs" 2>/dev/null || true
            done
        fi
    fi

    # 4. Quản lý Sub-cgroups phân cấp 2 tầng cho từng Website con
    if [ -n "$child_sites" ]; then
        IFS=',' read -r -a site_arr <<< "$child_sites"
        for site_info in "${site_arr[@]}"; do
            [ -n "$site_info" ] || continue
            local s_user=""
            local s_cpu=""
            local s_mem=""
            IFS='@' read -r s_user s_cpu s_mem <<< "$site_info"
            [ -n "$s_user" ] || continue

            # Hạn mức của Sub-cgroup website con:
            # - Nếu website có Website Plan riêng (vd 150% hay 50%): giới hạn đúng mức đó
            # - Nhưng vẫn chịu sự khống chế của trần cha (cpu_pct) bên ngoài
            local sub_cpu="${s_cpu:-$cpu_pct}"
            local sub_mem="${s_mem:-$mem_mb}"
            local sub_cpu_max="$((sub_cpu * 1000)) 100000"
            local sub_mem_max="$((sub_mem * 1024 * 1024))"

            local sub_cg="$cg_dir/site_${s_user}"
            mkdir -p "$sub_cg" 2>/dev/null || true

            [ -f "$sub_cg/cpu.max" ] && echo "$sub_cpu_max" > "$sub_cg/cpu.max" 2>/dev/null || true
            [ -f "$sub_cg/memory.max" ] && echo "$sub_mem_max" > "$sub_cg/memory.max" 2>/dev/null || true
            [ -f "$sub_cg/pids.max" ] && echo "100" > "$sub_cg/pids.max" 2>/dev/null || true

            # Thu thập toàn bộ PID của website con này (LSPHP, PHP-FPM, pool worker)
            mapfile -t s_pids < <(
                {
                    pgrep -u "$s_user" 2>/dev/null || true
                    pgrep -f "lsphp.*${s_user}" 2>/dev/null || true
                    pgrep -f "pool ${s_user}" 2>/dev/null || true
                } | sort -u
            )

            # Gán vào Sub-cgroup con của website (hoặc fallback vào cha nếu subtree_control chưa bật)
            for p in "${s_pids[@]}"; do
                if [ -n "$p" ] && [ -d "/proc/$p" ]; then
                    echo "$p" > "$sub_cg/cgroup.procs" 2>/dev/null || echo "$p" > "$cg_dir/cgroup.procs" 2>/dev/null || true
                fi
            done

            # 5. Xóa bỏ / Dọn sạch cgroup rác ngang hàng của website con ngoài root nếu lỡ bị tạo trước đó
            for stray_cg in "/sys/fs/cgroup/wpc/reseller_${reseller_id}/user_${s_user}" "/sys/fs/cgroup/wpc/user_${s_user}"; do
                if [ -d "$stray_cg" ] && [ "$stray_cg" != "$cg_dir" ] && [ "$stray_cg" != "$cg_flat" ]; then
                    if [ -f "$stray_cg/cgroup.procs" ]; then
                        while read -r stray_pid; do
                            [ -n "$stray_pid" ] && echo "$stray_pid" > "$sub_cg/cgroup.procs" 2>/dev/null || true
                        done < "$stray_cg/cgroup.procs"
                    fi
                    rmdir "$stray_cg" 2>/dev/null || true
                fi
            done
        done
    fi
}

# Quét toàn bộ hệ thống
sync_all_cgroups() {
    init_cgroup_root

    local mappings
    mappings=$(fetch_user_mappings)

    if [ -n "$mappings" ]; then
        while IFS=':' read -r u r_id cpu mem sites; do
            [ -n "$u" ] || continue
            enforce_hosting_user "$u" "$r_id" "$cpu" "$mem" "$sites"
        done <<< "$mappings"
    else
        # Fallback nếu DB chưa sẵn sàng: quét theo thư mục cgroup hiện có
        for user_cg in /sys/fs/cgroup/wpc/reseller_*/user_* /sys/fs/cgroup/wpc/user_*; do
            [ -d "$user_cg" ] || continue
            local cg_name
            cg_name=$(basename "$user_cg")
            local u="${cg_name#user_}"
            [ -n "$u" ] || continue
            id -u "$u" &>/dev/null || continue

            {
                pgrep -u "$u" 2>/dev/null || true
                pgrep -f "lsphp.*${u}" 2>/dev/null || true
                pgrep -f "pool ${u}" 2>/dev/null || true
            } | sort -u | while read -r p; do
                [ -n "$p" ] && echo "$p" > "$user_cg/cgroup.procs" 2>/dev/null || true
            done
        done
    fi
}

MODE="${1:-once}"
TARGET_USER="${2:-}"

if [ -n "$TARGET_USER" ]; then
    # Đồng bộ tức thì cho 1 user được chỉ định
    init_cgroup_root
    mappings=$(fetch_user_mappings)
    matched=0
    if [ -n "$mappings" ]; then
        while IFS=':' read -r u r_id cpu mem sites; do
            if [ "$u" = "$TARGET_USER" ]; then
                enforce_hosting_user "$u" "$r_id" "$cpu" "$mem" "$sites"
                matched=1
                break
            fi
        done <<< "$mappings"
    fi
    if [ "$matched" -eq 0 ]; then
        sync_all_cgroups
    fi
    exit 0
fi

if [ "$MODE" = "daemon" ]; then
    while true; do
        sync_all_cgroups
        sleep 2
    done
else
    sync_all_cgroups
fi

exit 0

