/*
 * WPCloud: wp.c - Quản lý Vòng Đời WordPress (WP Toolkit Engine)
 * SUID Binary (chmod 4750 root:wpc)
 */

#include "common.h"

int main(int argc, char *argv[]) {
    init_security_env();

    if (argc < 4) {
        send_json_error("wp", "Cu phap: wp <install|clone|backup|restore|secure|plugin|theme|sso> <username> <domain> [args...]");
        return 1;
    }

    const char *action = argv[1];
    const char *username = argv[2];
    const char *domain = argv[3];

    if (!is_safe_id(username) || !is_safe_token(domain)) {
        send_json_error("wp", "Username hoac Domain khong hop le");
        return 1;
    }

    char cmd[1024];

    if (strcmp(action, "install") == 0) {
        const char *title = (argc > 4) ? argv[4] : "My WordPress Site";
        const char *admin_user = (argc > 5) ? argv[5] : "admin";

        // Kiểm tra không chứa ký tự phá vỡ lệnh shell
        if (!is_safe_id(admin_user)) {
            send_json_error("wp", "Admin user khong hop le");
            return 1;
        }
        for (size_t i = 0; title[i]; i++) {
            char c = title[i];
            if (c == '\'' || c == '"' || c == '`' || c == '$' || c == ';' ||
                c == '&' || c == '|' || c == '\n' || c == '\r' || c == '\\') {
                send_json_error("wp", "Tieu de WordPress chua ky tu nguy hiem");
                return 1;
            }
        }

        // Nhận mật khẩu qua stdin
        char password[256] = {0};
        if (!fgets(password, sizeof(password), stdin)) {
            send_json_error("wp", "Khong the doc mat khau qua stdin");
            return 1;
        }
        password[strcspn(password, "\r\n")] = 0;

        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh install '%s' '%s' '%s' '%s'",
                 username, domain, title, admin_user);

        FILE *pipe_wp = popen(cmd, "w");
        if (!pipe_wp) {
            send_json_error("wp", "Khong the khoi dong wp_manager");
            return 1;
        }
        fprintf(pipe_wp, "%s\n", password);
        int ret = pclose(pipe_wp);
        if (ret == 0) {
            send_json_ok("\"message\":\"Cai dat WordPress thanh cong\"");
            return 0;
        }
        send_json_error("wp", "Cai dat WordPress that bai");
        return 1;
    }
    else if (strcmp(action, "clone") == 0) {
        if (argc < 5) {
            send_json_error("wp", "Cu phap: wp clone <username> <src_domain> <dest_domain>");
            return 1;
        }
        const char *dest_domain = argv[4];
        if (!is_safe_token(dest_domain)) {
            send_json_error("wp", "Ten mien dich khong hop le");
            return 1;
        }
        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh clone '%s' '%s' '%s'",
                 username, domain, dest_domain);
    }
    else if (strcmp(action, "secure") == 0) {
        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh secure '%s' '%s'",
                 username, domain);
    }
    else if (strcmp(action, "sso") == 0) {
        const char *admin_user = (argc > 4) ? argv[4] : "admin";
        if (!is_safe_id(admin_user)) {
            send_json_error("wp", "Admin user khong hop le");
            return 1;
        }
        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh sso '%s' '%s' '%s'",
                 username, domain, admin_user);
    }
    else if (strcmp(action, "plugin") == 0 || strcmp(action, "theme") == 0) {
        const char *sub_action = (argc > 4) ? argv[4] : "list";
        const char *slug = (argc > 5) ? argv[5] : "";
        if (!is_safe_id(sub_action) || (*slug && !is_safe_token(slug))) {
            send_json_error("wp", "Tham so plugin/theme khong hop le");
            return 1;
        }
        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh %s '%s' '%s' '%s' '%s'",
                 action, sub_action, username, domain, slug);
    }
    else {
        send_json_error("wp", "Hanh dong khong hop le");
        return 1;
    }

    if (system(cmd) == 0) {
        send_json_ok("\"message\":\"Thuc thi tac vu WordPress thanh cong\"");
        return 0;
    }

    send_json_error("wp", "Thuc thi lenh WordPress that bai");
    return 1;
}

