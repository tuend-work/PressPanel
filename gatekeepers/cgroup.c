/*
 * WPCloud: cgroup.c - Quản lý Cgroups v2 Phân Cấp & Khóa Trần 2 Lớp
 * SUID Binary (chmod 4750 root:wpc)
 */

#include "common.h"

#define CGROUP_ROOT "/sys/fs/cgroup/wpc"

static void write_cgroup_file(const char *dir, const char *file, const char *val) {
    char fpath[512];
    snprintf(fpath, sizeof(fpath), "%s/%s", dir, file);
    FILE *fp = fopen(fpath, "w");
    if (fp) {
        fputs(val, fp);
        fclose(fp);
    }
}

int main(int argc, char *argv[]) {
    init_security_env();

    if (argc < 2) {
        send_json_error("cgroup", "Thieu lenh cgroup. Cu phap: cgroup <set-reseller|set-user|sync|attach|stats|freeze> [args...]");
        return 1;
    }

    const char *action = argv[1];

    if (strcmp(action, "set-reseller") == 0) {
        if (argc < 7) {
            send_json_error("cgroup", "Cu phap: cgroup set-reseller <reseller_id> <cpu> <mem_mb> <io> <pids>");
            return 1;
        }
        const char *reseller_id = argv[2];
        int cpu = atoi(argv[3]);
        long long mem_mb = atoll(argv[4]);
        int pids = atoi(argv[6]);

        if (!is_safe_id(reseller_id)) {
            send_json_error("cgroup", "Reseller ID khong hop le");
            return 1;
        }

        char path[512];
        snprintf(path, sizeof(path), "%s/reseller_%s", CGROUP_ROOT, reseller_id);
        
        char cmd[4096];
        snprintf(cmd, sizeof(cmd), 
            "mkdir -p %s && "
            "echo '+cpu +memory +io +pids' > %s/cgroup.subtree_control 2>/dev/null || true; "
            "echo '%lld' > %s/memory.max 2>/dev/null; "
            "echo '%d 100000' > %s/cpu.max 2>/dev/null; "
            "echo '%d' > %s/pids.max 2>/dev/null",
            path, path, mem_mb * 1024 * 1024, path, cpu * 1000, path, pids, path);
        IGNORE_RESULT(system(cmd));

        send_json_ok("\"message\":\"Da cap nhat cgroup reseller thanh cong\"");
        return 0;
    }
    else if (strcmp(action, "set-user") == 0) {
        if (argc < 8) {
            send_json_error("cgroup", "Cu phap: cgroup set-user <reseller_id> <username> <cpu> <mem_mb> <io> <pids>");
            return 1;
        }
        const char *reseller_id = argv[2];
        const char *username = argv[3];
        int cpu = atoi(argv[4]);
        long long mem_mb = atoll(argv[5]);
        int pids = atoi(argv[7]);

        if (!is_safe_id(reseller_id) || !is_safe_id(username)) {
            send_json_error("cgroup", "Tham so ID hoac Username khong hop le");
            return 1;
        }

        char path[512];
        snprintf(path, sizeof(path), "%s/reseller_%s/user_%s", CGROUP_ROOT, reseller_id, username);

        char reseller_path[512];
        snprintf(reseller_path, sizeof(reseller_path), "%s/reseller_%s", CGROUP_ROOT, reseller_id);

        char user_flat_path[512];
        snprintf(user_flat_path, sizeof(user_flat_path), "%s/user_%s", CGROUP_ROOT, username);

        struct passwd *pw = getpwnam(username);
        uid_t uid = pw ? pw->pw_uid : 0;

        /* 
         * KHÓA TRẦN PHÂN CẤP SYSTEMD SLICE & CGROUP V2:
         * 1. Reseller cấp cha: wpc.slice -> wpc-reseller_%s.slice
         * 2. Hosting User (Khách hàng): wpc-hosting_%s.slice (đặt CPUQuota và MemoryMax của gói hosting)
         * 3. Website con của Hosting User: Kế thừa hoặc nằm trong wpc-hosting_%s-site_%s.slice
         */
        char init_cmd[4096];
        snprintf(init_cmd, sizeof(init_cmd),
            "mkdir -p /sys/fs/cgroup/wpc && "
            "for c in cpu memory io pids; do "
            "  echo \"+$c\" > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true; "
            "  echo \"+$c\" > /sys/fs/cgroup/wpc/cgroup.subtree_control 2>/dev/null || true; "
            "done; "
            "mkdir -p %s && "
            "for c in cpu memory io pids; do "
            "  echo \"+$c\" > %s/cgroup.subtree_control 2>/dev/null || true; "
            "done; "
            "mkdir -p %s %s",
            reseller_path, reseller_path, path, user_flat_path);
        IGNORE_RESULT(system(init_cmd));

        /* Ghi trực tiếp các file quota qua fopen/fputs (an toàn tuyệt đối, không sợ tràn chuỗi) */
        char val_buf[128];
        snprintf(val_buf, sizeof(val_buf), "%lld\n", (long long)mem_mb * 1024 * 1024);
        write_cgroup_file(path, "memory.max", val_buf);
        write_cgroup_file(user_flat_path, "memory.max", val_buf);

        snprintf(val_buf, sizeof(val_buf), "%d 100000\n", cpu * 1000);
        write_cgroup_file(path, "cpu.max", val_buf);
        write_cgroup_file(user_flat_path, "cpu.max", val_buf);

        snprintf(val_buf, sizeof(val_buf), "%d\n", pids);
        write_cgroup_file(path, "pids.max", val_buf);
        write_cgroup_file(user_flat_path, "pids.max", val_buf);

        /* Systemd Slices và OpenLiteSpeed native lscgctl */
        char sys_cmd[4096];
        snprintf(sys_cmd, sizeof(sys_cmd),
            "if [ %u -gt 0 ]; then systemctl set-property user-%u.slice CPUQuota=%d%% MemoryMax=%lldM TasksMax=%d 2>/dev/null || true; fi; "
            "systemctl set-property wpc-user-%s.slice CPUQuota=%d%% MemoryMax=%lldM TasksMax=%d 2>/dev/null || true; "
            "systemctl set-property wpc-hosting_%s.slice CPUQuota=%d%% MemoryMax=%lldM TasksMax=%d 2>/dev/null || true; "
            "for lscg in /usr/local/lsws/lsns/bin/lscgctl /usr/local/lsws/bin/lscgctl; do "
            "  if [ -x \"$lscg\" ]; then \"$lscg\" set %s --cpu=%d 2>/dev/null || true; fi; "
            "done",
            (unsigned int)uid, (unsigned int)uid, cpu, mem_mb, pids,
            username, cpu, mem_mb, pids,
            username, cpu, mem_mb, pids,
            username, cpu);
        IGNORE_RESULT(system(sys_cmd));

        /* Đồng bộ các tiến trình đang chạy của user vào cgroup.procs */
        char sync_cmd[4096];
        snprintf(sync_cmd, sizeof(sync_cmd),
            "for cg in %s/cgroup.procs %s/cgroup.procs; do "
            "  if [ -f \"$cg\" ]; then "
            "    pgrep -u %s | while read -r p; do [ -n \"$p\" ] && echo \"$p\" > \"$cg\" 2>/dev/null || true; done; "
            "    pgrep -f 'lsphp.*%s' | while read -r p; do [ -n \"$p\" ] && echo \"$p\" > \"$cg\" 2>/dev/null || true; done; "
            "    pgrep -f 'pool %s' | while read -r p; do [ -n \"$p\" ] && echo \"$p\" > \"$cg\" 2>/dev/null || true; done; "
            "  fi; "
            "done",
            path, user_flat_path, username, username, username);
        IGNORE_RESULT(system(sync_cmd));

        send_json_ok("\"message\":\"Da cap nhat cgroup user thanh cong\"");
        return 0;
    }
    else if (strcmp(action, "sync") == 0) {
        /*
         * Tự động quét và di chuyển TẤT CẢ tiến trình của user (LSPHP, PHP-FPM, worker)
         * vào đúng Cgroups v2 slice của user đó thông qua quyền SUID root
         */
        const char *username = (argc >= 3) ? argv[2] : NULL;
        char cmd[4096];
        if (username && strlen(username) > 0) {
            if (!is_safe_id(username)) {
                send_json_error("cgroup", "Username khong hop le");
                return 1;
            }
            snprintf(cmd, sizeof(cmd),
                "for cg in /sys/fs/cgroup/wpc/reseller_*/user_%s/cgroup.procs /sys/fs/cgroup/wpc/user_%s/cgroup.procs; do "
                "  if [ -f \"$cg\" ]; then "
                "    pgrep -u %s | while read -r p; do [ -n \"$p\" ] && echo \"$p\" > \"$cg\" 2>/dev/null || true; done; "
                "    pgrep -f 'lsphp.*%s' | while read -r p; do [ -n \"$p\" ] && echo \"$p\" > \"$cg\" 2>/dev/null || true; done; "
                "    pgrep -f 'pool %s' | while read -r p; do [ -n \"$p\" ] && echo \"$p\" > \"$cg\" 2>/dev/null || true; done; "
                "  fi; "
                "done",
                username, username, username, username, username);
        } else {
            snprintf(cmd, sizeof(cmd),
                "for cg in /sys/fs/cgroup/wpc/reseller_*/user_*/cgroup.procs /sys/fs/cgroup/wpc/user_*/cgroup.procs; do "
                "  [ -f \"$cg\" ] || continue; "
                "  u=$(basename $(dirname \"$cg\") | sed 's/^user_//'); "
                "  [ -n \"$u\" ] || continue; "
                "  pgrep -u \"$u\" | while read -r p; do [ -n \"$p\" ] && echo \"$p\" > \"$cg\" 2>/dev/null || true; done; "
                "  pgrep -f \"lsphp.*$u\" | while read -r p; do [ -n \"$p\" ] && echo \"$p\" > \"$cg\" 2>/dev/null || true; done; "
                "  pgrep -f \"pool $u\" | while read -r p; do [ -n \"$p\" ] && echo \"$p\" > \"$cg\" 2>/dev/null || true; done; "
                "done");
        }
        IGNORE_RESULT(system(cmd));
        send_json_ok("\"message\":\"Da dong bo tien trinh vao cgroups thanh cong\"");
        return 0;
    }
    else if (strcmp(action, "attach") == 0) {
        if (argc < 4) {
            send_json_error("cgroup", "Cu phap: cgroup attach <username> <pid>");
            return 1;
        }
        const char *username = argv[2];
        int pid = atoi(argv[3]);

        if (!is_safe_id(username) || pid <= 0) {
            send_json_error("cgroup", "Tham so Username hoac PID khong hop le");
            return 1;
        }

        char cmd[1024];
        snprintf(cmd, sizeof(cmd),
            "for p in /sys/fs/cgroup/wpc/reseller_*/user_%s/cgroup.procs /sys/fs/cgroup/wpc/user_%s/cgroup.procs; do "
            "  if [ -f \"$p\" ]; then echo '%d' > \"$p\" 2>/dev/null || true; fi; "
            "done",
            username, username, pid);
        IGNORE_RESULT(system(cmd));

        send_json_ok("\"message\":\"Da gan pid vao cgroup\"");
        return 0;
    }
    else if (strcmp(action, "stats") == 0) {
        if (argc < 4) {
            send_json_error("cgroup", "Cu phap: cgroup stats <reseller_id> <username>");
            return 1;
        }
        const char *reseller_id = argv[2];
        const char *username = argv[3];

        if (!is_safe_id(reseller_id) || !is_safe_id(username)) {
            send_json_error("cgroup", "Tham so ID hoac Username khong hop le");
            return 1;
        }

        char path[512];
        snprintf(path, sizeof(path), "%s/reseller_%s/user_%s/memory.current", CGROUP_ROOT, reseller_id, username);
        
        long long mem_current = 0;
        FILE *fp = fopen(path, "r");
        if (fp) {
            if (fscanf(fp, "%lld", &mem_current) != 1) mem_current = 0;
            fclose(fp);
        }

        char extra[256];
        snprintf(extra, sizeof(extra), "\"username\":\"%s\",\"memory_bytes\":%lld,\"pids\":0", username, mem_current);
        send_json_ok(extra);
        return 0;
    }
    else if (strcmp(action, "freeze") == 0) {
        if (argc < 5) {
            send_json_error("cgroup", "Cu phap: cgroup freeze <reseller_id> <username> <0|1>");
            return 1;
        }
        const char *reseller_id = argv[2];
        const char *username = argv[3];
        int freeze_state = atoi(argv[4]);

        if (!is_safe_id(reseller_id) || !is_safe_id(username)) {
            send_json_error("cgroup", "Tham so ID hoac Username khong hop le");
            return 1;
        }

        char path[512];
        snprintf(path, sizeof(path), "%s/reseller_%s/user_%s/cgroup.freeze", CGROUP_ROOT, reseller_id, username);
        FILE *fp = fopen(path, "w");
        if (fp) {
            fprintf(fp, "%d\n", freeze_state ? 1 : 0);
            fclose(fp);
            send_json_ok(freeze_state ? "\"message\":\"Da dong bang tien trinh\"" : "\"message\":\"Da giai phong tien trinh\"");
            return 0;
        }
        send_json_error("cgroup", "Khong the thay doi trang thai dong bang cgroup");
        return 1;
    }

    send_json_error("cgroup", "Hanh dong khong hop le");
    return 1;
}

