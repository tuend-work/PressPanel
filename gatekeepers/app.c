/*
 * PressPanel: app.c - Quản lý Cài đặt Ứng dụng & Dịch vụ (App Store)
 * SUID Binary (chmod 4750 root:PressPanel)
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
        snprintf(cmd, sizeof(cmd), "/opt/presspanel/scripts/apps/%s.sh install %s", app_name, version);
    }
    else if (strcmp(action, "remove") == 0) {
        snprintf(cmd, sizeof(cmd), "/opt/presspanel/scripts/apps/%s.sh remove", app_name);
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
            send_json_error("app", "Cu phap: app service <start|stop|restart|reload> <app_name>");
            return 1;
        }
        const char *op = argv[2];
        const char *srv = argv[3];
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
        snprintf(cmd, sizeof(cmd), "/opt/presspanel/scripts/apps/php.sh %s-ext %s %s", op, php_ver, ext);
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

