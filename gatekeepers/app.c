/*
 * WPCloud: app.c - Quản lý Cài đặt Ứng dụng & Dịch vụ (App Store)
 * SUID Binary (chmod 4750 root:wpc)
 */

#include "common.h"

static const char* detect_os_family(void) {
    FILE *fp = fopen("/etc/os-release", "r");
    if (!fp) return "debian";
    char line[256];
    static char family[32] = "debian";
    while (fgets(line, sizeof(line), fp)) {
        if (strncmp(line, "ID_LIKE=", 8) == 0 || strncmp(line, "ID=", 3) == 0) {
            if (strstr(line, "rhel") || strstr(line, "centos") || strstr(line, "almalinux") || strstr(line, "rocky")) {
                strncpy(family, "rhel", sizeof(family));
                break;
            }
        }
    }
    fclose(fp);
    return family;
}

int main(int argc, char *argv[]) {
    init_security_env();

    if (argc < 3) {
        send_json_error("app", "Cu phap: app <install|remove|status|php-ext|service> <app_name> [args...]");
        return 1;
    }

    const char *action = argv[1];
    const char *app_name = argv[2];
    const char *os_family = detect_os_family();
    setenv("PANEL_OS_FAMILY", os_family, 1);

    char cmd[1024];

    if (strcmp(action, "install") == 0) {
        const char *version = (argc > 3) ? argv[3] : "latest";
        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/apps/%s.sh install %s", app_name, version);
    }
    else if (strcmp(action, "remove") == 0) {
        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/apps/%s.sh remove", app_name);
    }
    else if (strcmp(action, "status") == 0) {
        snprintf(cmd, sizeof(cmd), "systemctl is-active --quiet %s", app_name);
        int active = (system(cmd) == 0);
        char extra[128];
        snprintf(extra, sizeof(extra), "\"app\":\"%s\",\"active\":%s", app_name, active ? "true" : "false");
        send_json_ok(extra);
        return 0;
    }
    else if (strcmp(action, "service") == 0) {
        if (argc < 4) {
            send_json_error("app", "Cu phap: app service <start|stop|restart|reload|enable|disable> <service_name>");
            return 1;
        }
        const char *op = argv[2];
        const char *srv = argv[3];
        if (!is_safe_id(op) || !is_safe_token(srv)) {
            send_json_error("app", "Tham so service khong hop le");
            return 1;
        }
        snprintf(cmd, sizeof(cmd), "systemctl %s %s", op, srv);
    }
    else if (strcmp(action, "php-ext") == 0) {
        if (argc < 5) {
            send_json_error("app", "Cu phap: app php-ext <install|remove> <php_ver> <ext>");
            return 1;
        }
        const char *op = argv[2];
        const char *php_ver = argv[3];
        const char *ext = argv[4];
        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/apps/php.sh %s-ext %s %s", op, php_ver, ext);
    }
    else if (strcmp(action, "config-read") == 0) {
        // Doc file cau hinh an toan bang dac quyen root
        const char *filepath = argv[2];
        if (strstr(filepath, "..")) {
            send_json_error("app", "Duong dan khong hop le");
            return 1;
        }
        FILE *fp = fopen(filepath, "r");
        if (!fp) {
            send_json_error("app", "Khong the mo file cau hinh");
            return 1;
        }
        char buf[4096];
        size_t n;
        while ((n = fread(buf, 1, sizeof(buf), fp)) > 0) {
            fwrite(buf, 1, n, stdout);
        }
        fclose(fp);
        return 0;
    }
    else if (strcmp(action, "config-write") == 0) {
        // Ghi file cau hinh an toan bang dac quyen root tu src_tmp sang dest_path
        if (argc < 4) {
            send_json_error("app", "Cu phap: app config-write <src_tmp> <dest_path>");
            return 1;
        }
        const char *src_tmp = argv[2];
        const char *dest_path = argv[3];
        if (strstr(src_tmp, "..") || strstr(dest_path, "..")) {
            send_json_error("app", "Duong dan khong hop le");
            return 1;
        }
        FILE *fsrc = fopen(src_tmp, "r");
        if (!fsrc) {
            send_json_error("app", "Khong mo duoc file tam de doc");
            return 1;
        }
        // Lưu lại UID/GID của file đích nếu đã tồn tại để tránh biến file thành root:root
        struct stat st_dest;
        int has_dest_stat = (stat(dest_path, &st_dest) == 0);

        FILE *fdest = fopen(dest_path, "w");
        if (!fdest) {
            fclose(fsrc);
            send_json_error("app", "Khong mo duoc file dich de ghi");
            return 1;
        }
        char buf[4096];
        size_t n;
        while ((n = fread(buf, 1, sizeof(buf), fsrc)) > 0) {
            fwrite(buf, 1, n, fdest);
        }
        fclose(fsrc);
        fclose(fdest);
        unlink(src_tmp);

        // Khôi phục quyền sở hữu cho Linux Web User nếu đã tồn tại trước đó
        if (has_dest_stat) {
            IGNORE_RESULT(chown(dest_path, st_dest.st_uid, st_dest.st_gid));
            IGNORE_RESULT(chmod(dest_path, st_dest.st_mode));
        }

        send_json_ok("\"message\":\"Da luu cau hinh thanh cong\"");
        return 0;
    }
    else if (strcmp(action, "file-touch") == 0) {
        if (argc < 3) {
            send_json_error("app", "Cu phap: app file-touch <path>");
            return 1;
        }
        const char *filepath = argv[2];
        if (!is_safe_home_path(filepath)) {
            send_json_error("app", "Duong dan khong thuoc /home hoac chua ky tu nguy hiem");
            return 1;
        }
        FILE *fp = fopen(filepath, "a");
        if (!fp) {
            send_json_error("app", "Khong the tao file");
            return 1;
        }
        fclose(fp);
        // Gan owner theo thu muc cha neu thu muc cha thuoc user khac root
        char parent[512];
        strncpy(parent, filepath, sizeof(parent) - 1);
        parent[sizeof(parent) - 1] = '\0';
        char *slash = strrchr(parent, '/');
        if (slash) {
            *slash = '\0';
            struct stat st_parent;
            if (stat(parent, &st_parent) == 0) {
                IGNORE_RESULT(chown(filepath, st_parent.st_uid, st_parent.st_gid));
            }
        }
        IGNORE_RESULT(chmod(filepath, 0644));
        send_json_ok("\"message\":\"Da tao file thanh cong\"");
        return 0;
    }
    else if (strcmp(action, "file-mkdir") == 0) {
        if (argc < 3) {
            send_json_error("app", "Cu phap: app file-mkdir <path>");
            return 1;
        }
        const char *dirpath = argv[2];
        if (!is_safe_home_path(dirpath)) {
            send_json_error("app", "Duong dan khong thuoc /home hoac chua ky tu nguy hiem");
            return 1;
        }
        char mkdir_cmd[1024];
        snprintf(mkdir_cmd, sizeof(mkdir_cmd), "mkdir -p '%s'", dirpath);
        if (system(mkdir_cmd) == 0) {
            char parent[512];
            strncpy(parent, dirpath, sizeof(parent) - 1);
            parent[sizeof(parent) - 1] = '\0';
            char *slash = strrchr(parent, '/');
            if (slash) {
                *slash = '\0';
                struct stat st_parent;
                if (stat(parent, &st_parent) == 0) {
                    IGNORE_RESULT(chown(dirpath, st_parent.st_uid, st_parent.st_gid));
                }
            }
            IGNORE_RESULT(chmod(dirpath, 0755));
            send_json_ok("\"message\":\"Da tao thu muc thanh cong\"");
            return 0;
        }
        send_json_error("app", "Khong the tao thu muc");
        return 1;
    }
    else if (strcmp(action, "file-rm") == 0) {
        if (argc < 3) {
            send_json_error("app", "Cu phap: app file-rm <path>");
            return 1;
        }
        const char *target = argv[2];
        if (!is_safe_home_path(target)) {
            send_json_error("app", "Duong dan khong thuoc /home hoac chua ky tu nguy hiem");
            return 1;
        }
        // Cấm tuyệt đối xóa thư mục /home/<user> trực tiếp (phải có ít nhất 1 cấp con bên trong)
        const char *rest = target + 6; // Bỏ qua '/home/'
        const char *next_slash = strchr(rest, '/');
        if (!next_slash || *(next_slash + 1) == '\0') {
            send_json_error("app", "Khong duoc phep xoa truc tiep thu muc goc cua user");
            return 1;
        }

        char rm_cmd[1024];
        snprintf(rm_cmd, sizeof(rm_cmd), "rm -rf '%s'", target);
        if (system(rm_cmd) == 0) {
            send_json_ok("\"message\":\"Da xoa muc thanh cong\"");
            return 0;
        }
        send_json_error("app", "Khong the xoa muc");
        return 1;
    }
    else if (strcmp(action, "file-archive") == 0) {
        // Cu phap: app file-archive <work_dir> <archive_name> <format> <item1,item2,...>
        if (argc < 6) {
            send_json_error("app", "Cu phap: app file-archive <work_dir> <archive_name> <zip|tar|tar.gz> <items>");
            return 1;
        }
        const char *work_dir = argv[2];
        const char *archive_name = argv[3];
        const char *format = argv[4];
        const char *items = argv[5];

        if (!is_safe_home_path(work_dir) || !is_safe_token(archive_name)) {
            send_json_error("app", "Thu muc lam viec hoac ten file nen khong hop le");
            return 1;
        }

        // Kiểm tra an toàn cho danh sách items
        if (strstr(items, "..")) {
            send_json_error("app", "Danh sach muc nen chua ky tu traversal nguy hiem");
            return 1;
        }
        for (size_t i = 0; items[i]; i++) {
            char c = items[i];
            if (c == '\'' || c == '"' || c == '`' || c == '$' || c == ';' ||
                c == '&' || c == '|' || c == '\n' || c == '\r' || c == '\\') {
                send_json_error("app", "Danh sach muc nen chua ky tu command injection");
                return 1;
            }
        }

        char archive_cmd[4096];
        if (strcmp(format, "zip") == 0) {
            snprintf(archive_cmd, sizeof(archive_cmd), "cd '%s' && zip -r -q '%s' %s", work_dir, archive_name, items);
        } else if (strcmp(format, "tar") == 0) {
            snprintf(archive_cmd, sizeof(archive_cmd), "cd '%s' && tar -cf '%s' %s", work_dir, archive_name, items);
        } else if (strcmp(format, "tar.gz") == 0 || strcmp(format, "tgz") == 0 || strcmp(format, "gz") == 0) {
            snprintf(archive_cmd, sizeof(archive_cmd), "cd '%s' && tar -czf '%s' %s", work_dir, archive_name, items);
        } else {
            send_json_error("app", "Dinh dang nen khong duoc ho tro");
            return 1;
        }

        if (system(archive_cmd) == 0) {
            char archive_full[1024];
            snprintf(archive_full, sizeof(archive_full), "%s/%s", work_dir, archive_name);
            struct stat st_work;
            if (stat(work_dir, &st_work) == 0) {
                IGNORE_RESULT(chown(archive_full, st_work.st_uid, st_work.st_gid));
                IGNORE_RESULT(chmod(archive_full, 0644));
            }
            send_json_ok("\"message\":\"Nen tap tin thanh cong\"");
            return 0;
        }
        send_json_error("app", "Khong the thuc hien lenh nen");
        return 1;
    }
    else {
        send_json_error("app", "Hanh dong khong hop le");
        return 1;
    }

    if (system(cmd) == 0) {
        send_json_ok("\"message\":\"Thuc thi tac vu app thanh cong\"");
        return 0;
    }

    send_json_error("app", "Thuc thi lenh app that bai");
    return 1;
}

