/*
 * PressPanel: user.c - Quản lý tài khoản Linux & Disk Quota
 * SUID Binary (chmod 4750 root:PressPanel)
 */

#include "common.h"

int main(int argc, char *argv[]) {
    init_security_env();

    if (argc < 3) {
        send_json_error("user", "Sai cu phap. Cu phap: user <create|delete|suspend|unsuspend|set-quota> <username> [args...]");
        return 1;
    }

    const char *action = argv[1];
    const char *username = argv[2];

    if (!is_safe_id(username)) {
        send_json_error("user", "Ten username khong hop le (chi chap nhan a-z, 0-9, _, -)");
        return 1;
    }

    if (strcmp(action, "create") == 0) {
        if (argc < 4) {
            send_json_error("user", "Thieu tham so disk_mb. Cu phap: user create <username> <disk_mb>");
            return 1;
        }
        int disk_mb = atoi(argv[3]);
        if (disk_mb <= 0) disk_mb = 5120; // Default 5GB

        char cmd[1024];
        snprintf(cmd, sizeof(cmd), "useradd -m -s /bin/bash %s", username);
        if (system(cmd) != 0) {
            send_json_error("user", "Khong the tao Linux user");
            return 1;
        }

        // Tạo cấu trúc thư mục chuẩn
        snprintf(cmd, sizeof(cmd), 
            "mkdir -p /home/%s/{httpdocs,logs,tmp,ssl,backups} && "
            "chown -R %s:%s /home/%s && "
            "chmod 0750 /home/%s && "
            "chmod 0755 /home/%s/httpdocs && "
            "chmod 0700 /home/%s/{logs,tmp,ssl,backups}",
            username, username, username, username, username, username, username);
        (void)system(cmd);

        // Thiết lập quota ổ cứng
        snprintf(cmd, sizeof(cmd), "setquota -u %s 0 %dM 0 0 -a 2>/dev/null || true", username, disk_mb);
        (void)system(cmd);

        char extra[128];
        snprintf(extra, sizeof(extra), "\"username\":\"%s\",\"disk_mb\":%d", username, disk_mb);
        send_json_ok(extra);
        return 0;
    } 
    else if (strcmp(action, "delete") == 0) {
        char cmd[512];
        snprintf(cmd, sizeof(cmd), "userdel -r %s 2>/dev/null || userdel -f %s", username, username);
        if (system(cmd) != 0) {
            send_json_error("user", "Khong the xoa Linux user");
            return 1;
        }
        send_json_ok("\"message\":\"User da bi xoa\"");
        return 0;
    }
    else if (strcmp(action, "suspend") == 0) {
        char cmd[256];
        snprintf(cmd, sizeof(cmd), "usermod -L %s", username);
        if (system(cmd) != 0) {
            send_json_error("user", "Khong the khoa tai khoan");
            return 1;
        }
        send_json_ok("\"message\":\"Tai khoan da bi khoa\"");
        return 0;
    }
    else if (strcmp(action, "unsuspend") == 0) {
        char cmd[256];
        snprintf(cmd, sizeof(cmd), "usermod -U %s", username);
        if (system(cmd) != 0) {
            send_json_error("user", "Khong the mo khoa tai khoan");
            return 1;
        }
        send_json_ok("\"message\":\"Tai khoan da duoc mo khoa\"");
        return 0;
    }
    else if (strcmp(action, "set-quota") == 0) {
        if (argc < 4) {
            send_json_error("user", "Thieu tham so disk_mb. Cu phap: user set-quota <username> <disk_mb>");
            return 1;
        }
        int disk_mb = atoi(argv[3]);
        char cmd[256];
        snprintf(cmd, sizeof(cmd), "setquota -u %s 0 %dM 0 0 -a 2>/dev/null || true", username, disk_mb);
        (void)system(cmd);

        char extra[128];
        snprintf(extra, sizeof(extra), "\"username\":\"%s\",\"disk_mb\":%d", username, disk_mb);
        send_json_ok(extra);
        return 0;
    }

    send_json_error("user", "Hanh dong khong duoc ho tro");
    return 1;
}

