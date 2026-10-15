/*
 * PressPanel: vhost.c - Quản lý VirtualHost OpenLiteSpeed/Nginx & SSL
 * SUID Binary (chmod 4750 root:PressPanel)
 */

#include "common.h"

int main(int argc, char *argv[]) {
    init_security_env();

    if (argc < 4) {
        send_json_error("vhost", "Cu phap: vhost <add|remove|set-php|ssl> <username> <domain> [args...]");
        return 1;
    }

    const char *action = argv[1];
    const char *username = argv[2];
    const char *domain = argv[3];

    if (!is_safe_id(username) || !is_safe_token(domain)) {
        send_json_error("vhost", "Username hoac Domain khong hop le");
        return 1;
    }

    char cmd[1024];

    if (strcmp(action, "add") == 0) {
        const char *webserver = (argc > 4) ? argv[4] : "ols";
        const char *php_ver = (argc > 5) ? argv[5] : "8.2";
        snprintf(cmd, sizeof(cmd), "/opt/presspanel/scripts/vhost_manager.sh add %s %s %s %s",
                 username, domain, webserver, php_ver);
    }
    else if (strcmp(action, "remove") == 0) {
        const char *webserver = (argc > 4) ? argv[4] : "ols";
        snprintf(cmd, sizeof(cmd), "/opt/presspanel/scripts/vhost_manager.sh remove %s %s %s",
                 username, domain, webserver);
    }
    else if (strcmp(action, "set-php") == 0) {
        const char *webserver = (argc > 4) ? argv[4] : "ols";
        const char *php_ver = (argc > 5) ? argv[5] : "8.2";
        snprintf(cmd, sizeof(cmd), "/opt/presspanel/scripts/vhost_manager.sh set-php %s %s %s %s",
                 username, domain, webserver, php_ver);
    }
    else if (strcmp(action, "ssl") == 0) {
        snprintf(cmd, sizeof(cmd), "/opt/presspanel/scripts/vhost_manager.sh ssl %s %s",
                 username, domain);
    }
    else {
        send_json_error("vhost", "Hanh dong khong hop le");
        return 1;
    }

    int ret = system(cmd);
    if (ret == 0) {
        char extra[256];
        snprintf(extra, sizeof(extra), "\"domain\":\"%s\",\"action\":\"%s\"", domain, action);
        send_json_ok(extra);
        return 0;
    }

    send_json_error("vhost", "Thuc thi vhost that bai");
    return 1;
}

