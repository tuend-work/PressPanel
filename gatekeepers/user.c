/*
 * WPCloud: user.c - Quản lý tài khoản Linux & Disk Quota
 * SUID Binary (chmod 4750 root:wpc)
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

    // Bảo vệ tuyệt đối tài khoản hệ thống (root, wpc, nobody, www-data, UID < 1000)
    if (strcmp(action, "delete") == 0 || strcmp(action, "suspend") == 0 ||
        strcmp(action, "unsuspend") == 0 || strcmp(action, "set-quota") == 0) {
        if (is_system_user(username)) {
            send_json_error("user", "Tu choi thao tac: Khong duoc phep thay doi tai khoan he thong");
            return 1;
        }
    }

    if (strcmp(action, "create") == 0) {
        if (argc < 4) {
            send_json_error("user", "Thieu tham so disk_mb. Cu phap: user create <username> <disk_mb>");
            return 1;
        }
        int disk_mb = atoi(argv[3]);
        if (disk_mb <= 0) disk_mb = 5120; // Default 5GB

        char cmd[1024];
        // 1. Kiểm tra xem user Linux đã tồn tại chưa (ví dụ tài khoản hệ thống hoặc tạo trước đó)
        snprintf(cmd, sizeof(cmd), "id -u %s >/dev/null 2>&1", username);
        if (system(cmd) != 0) {
            // Chưa tồn tại -> tạo mới
            snprintf(cmd, sizeof(cmd), "useradd -m -s /bin/bash %s 2>/dev/null", username);
            if (system(cmd) != 0) {
                // Thử tạo không cần -m nếu thư mục /home/username đã có
                snprintf(cmd, sizeof(cmd), "useradd -s /bin/bash %s", username);
                if (system(cmd) != 0) {
                    send_json_error("user", "Khong the tao Linux user (ten da bi trung hoac he thong tu choi)");
                    return 1;
                }
            }
        }

        // Tạo cấu trúc thư mục chuẩn (liệt kê rõ ràng từng thư mục vì /bin/sh không hỗ trợ brace expansion {})
        snprintf(cmd, sizeof(cmd), 
            "mkdir -p /home/%s/domains /home/%s/logs /home/%s/tmp /home/%s/ssl /home/%s/backups && "
            "chown -R %s:%s /home/%s 2>/dev/null || true && "
            "chmod 0750 /home/%s && "
            "chmod 0750 /home/%s/domains && "
            "chmod 0700 /home/%s/logs /home/%s/tmp /home/%s/ssl /home/%s/backups",
            username, username, username, username, username,
            username, username, username,
            username,
            username,
            username, username, username, username);
        IGNORE_RESULT(system(cmd));


        // Thiết lập quota ổ cứng
        snprintf(cmd, sizeof(cmd), "setquota -u %s 0 %dM 0 0 -a 2>/dev/null || true", username, disk_mb);
        IGNORE_RESULT(system(cmd));

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
        IGNORE_RESULT(system(cmd));

        char extra[128];
        snprintf(extra, sizeof(extra), "\"username\":\"%s\",\"disk_mb\":%d", username, disk_mb);
        send_json_ok(extra);
        return 0;
    }

    send_json_error("user", "Hanh dong khong duoc ho tro");
    return 1;
}

