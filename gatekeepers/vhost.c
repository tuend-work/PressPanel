/*
 * PressPanel: vhost.c - Quản lý VirtualHost OpenLiteSpeed/Nginx & SSL
 * SUID Binary (chmod 4750 root:presspanel)
 */

#include "common.h"

static const char* get_vhost_script(void) {
    if (access("/opt/presspanel/scripts/vhost_manager.sh", X_OK) == 0) return "/opt/presspanel/scripts/vhost_manager.sh";
    return "/opt/wpc/scripts/vhost_manager.sh";
}

int main(int argc, char *argv[]) {
    init_security_env();

    if (argc < 2) {
        send_json_error("vhost", "Cu phap: vhost <add|remove|rename|set-php|ssl|suspend|unsuspend|rewrite-all|apply-php-ini|reload-vhost> [args...]");
        return 1;
    }

    const char *action = argv[1];

    if (strcmp(action, "rewrite-all") == 0) {
        const char *webserver = (argc > 2) ? argv[2] : "ols";
        if (!is_safe_webserver(webserver)) {
            send_json_error("vhost", "Webserver khong hop le");
            return 1;
        }
        char cmd[512];
        snprintf(cmd, sizeof(cmd), "%s rewrite-all all all %s", get_vhost_script(), webserver);
        int ret = system(cmd);
        if (ret == 0) {
            send_json_ok("\"action\":\"rewrite-all\"");
            return 0;
        }
        send_json_error("vhost", "Thuc thi rewrite-all that bai");
        return 1;
    }

    if (argc < 4) {
        send_json_error("vhost", "Cu phap: vhost <add|remove|rename|set-php|ssl|suspend|unsuspend|apply-php-ini|reload-vhost> <username> <domain> [args...]");
        return 1;
    }

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
        const char *hosting_user = (argc > 6) ? argv[6] : username;
        if (!is_safe_webserver(webserver) || !is_safe_php_ver(php_ver)) {
            send_json_error("vhost", "Webserver hoac PHP version khong hop le");
            return 1;
        }
        if (argc > 6 && !is_safe_id(hosting_user)) {
            send_json_error("vhost", "Hosting user khong hop le");
            return 1;
        }
        snprintf(cmd, sizeof(cmd), "%s add %s %s %s %s %s",
                 get_vhost_script(), username, domain, webserver, php_ver, hosting_user);
    }
    else if (strcmp(action, "remove") == 0) {
        const char *webserver = (argc > 4) ? argv[4] : "ols";
        if (!is_safe_webserver(webserver)) {
            send_json_error("vhost", "Webserver khong hop le");
            return 1;
        }
        snprintf(cmd, sizeof(cmd), "%s remove %s %s %s",
                 get_vhost_script(), username, domain, webserver);
    }
    else if (strcmp(action, "set-php") == 0 || strcmp(action, "change-php") == 0) {
        const char *webserver = (argc > 4) ? argv[4] : "ols";
        const char *php_ver = (argc > 5) ? argv[5] : "8.2";
        const char *hosting_user = (argc > 6) ? argv[6] : username;
        if (!is_safe_webserver(webserver) || !is_safe_php_ver(php_ver)) {
            send_json_error("vhost", "Webserver hoac PHP version khong hop le");
            return 1;
        }
        if (argc > 6 && !is_safe_id(hosting_user)) {
            send_json_error("vhost", "Hosting user khong hop le");
            return 1;
        }
        snprintf(cmd, sizeof(cmd), "%s set-php %s %s %s %s %s",
                 get_vhost_script(), username, domain, webserver, php_ver, hosting_user);
    }
    else if (strcmp(action, "ssl") == 0) {
        snprintf(cmd, sizeof(cmd), "%s ssl %s %s",
                 get_vhost_script(), username, domain);
    }
    else if (strcmp(action, "apply-php-ini") == 0) {
        const char *webserver = (argc > 4) ? argv[4] : "ols";
        if (!is_safe_webserver(webserver)) {
            send_json_error("vhost", "Webserver khong hop le");
            return 1;
        }
        snprintf(cmd, sizeof(cmd), "%s apply-php-ini %s %s %s",
                 get_vhost_script(), username, domain, webserver);
    }
    else if (strcmp(action, "suspend") == 0) {
        const char *webserver = (argc > 4) ? argv[4] : "ols";
        if (!is_safe_webserver(webserver)) {
            send_json_error("vhost", "Webserver khong hop le");
            return 1;
        }
        snprintf(cmd, sizeof(cmd), "%s suspend %s %s %s",
                 get_vhost_script(), username, domain, webserver);
    }
    else if (strcmp(action, "unsuspend") == 0) {
        const char *webserver = (argc > 4) ? argv[4] : "ols";
        if (!is_safe_webserver(webserver)) {
            send_json_error("vhost", "Webserver khong hop le");
            return 1;
        }
        snprintf(cmd, sizeof(cmd), "%s unsuspend %s %s %s",
                 get_vhost_script(), username, domain, webserver);
    }
    else if (strcmp(action, "reload-vhost") == 0) {
        const char *webserver = (argc > 4) ? argv[4] : "ols";
        const char *php_ver = (argc > 5) ? argv[5] : "8.2";
        if (!is_safe_webserver(webserver)) {
            send_json_error("vhost", "Webserver khong hop le");
            return 1;
        }
        if (argc > 5 && !is_safe_php_ver(php_ver)) {
            send_json_error("vhost", "PHP version khong hop le");
            return 1;
        }
        snprintf(cmd, sizeof(cmd), "%s reload-vhost %s %s %s %s",
                 get_vhost_script(), username, domain, webserver, php_ver);
    }
    else if (strcmp(action, "rename") == 0) {
        if (argc < 5) {
            send_json_error("vhost", "Cu phap: vhost rename <username> <old_domain> <new_domain> [webserver] [php_ver] [hosting_user]");
            return 1;
        }
        const char *new_domain = argv[4];
        if (!is_safe_token(new_domain)) {
            send_json_error("vhost", "New Domain khong hop le");
            return 1;
        }
        const char *webserver = (argc > 5) ? argv[5] : "ols";
        const char *php_ver = (argc > 6) ? argv[6] : "8.2";
        const char *hosting_user = (argc > 7) ? argv[7] : username;
        if (!is_safe_webserver(webserver) || !is_safe_php_ver(php_ver)) {
            send_json_error("vhost", "Webserver hoac PHP version khong hop le");
            return 1;
        }
        if (argc > 7 && !is_safe_id(hosting_user)) {
            send_json_error("vhost", "Hosting user khong hop le");
            return 1;
        }
        snprintf(cmd, sizeof(cmd), "%s rename %s %s %s %s %s %s",
                 get_vhost_script(), username, domain, new_domain, webserver, php_ver, hosting_user);
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

