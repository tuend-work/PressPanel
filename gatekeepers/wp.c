/*
 * WPCloud: wp.c - Quản lý Vòng Đời WordPress (WP Toolkit Engine)
 * SUID Binary (chmod 4750 root:wpc)
 */

#include "common.h"

static int is_safe_num_string(const char *str) {
    if (!str || !*str) return 0;
    for (size_t i = 0; str[i]; i++) {
        if (!isdigit((unsigned char)str[i])) return 0;
    }
    return 1;
}

int main(int argc, char *argv[]) {
    init_security_env();

    if (argc < 4) {
        send_json_error("wp", "Cu phap: wp <action> <username> <domain> [args...]");
        return 1;
    }

    const char *action = argv[1];
    const char *username = argv[2];
    const char *domain = argv[3];

    if (!is_safe_id(username) || !is_safe_token(domain)) {
        send_json_error("wp", "Username hoac Domain khong hop le");
        return 1;
    }

    char cmd[2048];
    memset(cmd, 0, sizeof(cmd));

    if (strcmp(action, "install") == 0) {
        const char *title = (argc > 4) ? argv[4] : "My WordPress Site";
        const char *admin_user = (argc > 5) ? argv[5] : "admin";
        const char *admin_email = (argc > 6) ? argv[6] : "admin@wpc.local";
        const char *locale = (argc > 7) ? argv[7] : "en_US";
        const char *custom_db_name = (argc > 8) ? argv[8] : "";
        const char *custom_db_pass = (argc > 9) ? argv[9] : "";
        const char *custom_db_prefix = (argc > 10) ? argv[10] : "wp_";
        const char *sub_dir = (argc > 11) ? argv[11] : "";
        const char *enable_cache = (argc > 12) ? argv[12] : "1";
        const char *wp_ver = (argc > 13) ? argv[13] : "latest";
        const char *create_html = (argc > 14) ? argv[14] : "0";

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
        for (size_t i = 0; admin_email[i]; i++) {
            char c = admin_email[i];
            if (c == '\'' || c == '"' || c == '`' || c == '$' || c == ';' ||
                c == '&' || c == '|' || c == '\n' || c == '\r' || c == '\\' || c == ' ') {
                send_json_error("wp", "Admin email khong hop le");
                return 1;
            }
        }
        if (*custom_db_name && !is_safe_token(custom_db_name)) {
            send_json_error("wp", "Ten database khong hop le");
            return 1;
        }
        if (*custom_db_prefix && !is_safe_token(custom_db_prefix)) {
            send_json_error("wp", "Table prefix khong hop le");
            return 1;
        }
        if (*sub_dir && !is_safe_token(sub_dir)) {
            send_json_error("wp", "Subdirectory khong hop le");
            return 1;
        }

        // Nhận mật khẩu qua stdin
        char password[256] = {0};
        if (!fgets(password, sizeof(password), stdin)) {
            send_json_error("wp", "Khong the doc mat khau qua stdin");
            return 1;
        }
        password[strcspn(password, "\r\n")] = 0;

        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh install '%s' '%s' '%s' '%s' '%s' '%s' '%s' '%s' '%s' '%s' '%s' '%s' '%s'",
                 username, domain, title, admin_user, admin_email, locale, custom_db_name, custom_db_pass, custom_db_prefix, sub_dir, enable_cache, wp_ver, create_html);

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
        send_json_error("wp", "Cai dat WordPress that bai. Kiem tra MariaDB hoac quyen thu muc!");
        return 1;
    }
    else if (strcmp(action, "download_zip") == 0) {
        if (argc < 6) {
            send_json_error("wp", "Cu phap: wp download_zip <username> <domain> <plugin|theme|core> <slug> [zip_url]");
            return 1;
        }
        const char *type = argv[4];
        const char *slug = argv[5];
        const char *zip_url = (argc > 6) ? argv[6] : "";

        if (strcmp(type, "plugin") != 0 && strcmp(type, "theme") != 0 && strcmp(type, "core") != 0) {
            send_json_error("wp", "Loai tai ve khong hop le (chi chap nhan plugin, theme hoac core)");
            return 1;
        }
        if (!is_safe_token(slug)) {
            send_json_error("wp", "Slug khong hop le");
            return 1;
        }
        if (*zip_url && !is_safe_url(zip_url)) {
            send_json_error("wp", "URL ZIP khong hop le hoac chua ky tu nguy hiem");
            return 1;
        }

        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh download_zip '%s' '%s' '%s' '%s' '%s'",
                 username, domain, type, slug, zip_url);
    }
    else if (strcmp(action, "delete_item") == 0) {
        if (argc < 6) {
            send_json_error("wp", "Cu phap: wp delete_item <username> <domain> <plugin|theme> <slug>");
            return 1;
        }
        const char *type = argv[4];
        const char *slug = argv[5];
        if (strcmp(type, "plugin") != 0 && strcmp(type, "theme") != 0) {
            send_json_error("wp", "Loai xoa khong hop le (plugin hoac theme)");
            return 1;
        }
        if (!is_safe_token(slug)) {
            send_json_error("wp", "Slug khong hop le");
            return 1;
        }
        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh delete_item '%s' '%s' '%s' '%s'",
                 username, domain, type, slug);
    }
    else if (strcmp(action, "lock") == 0) {
        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh lock '%s' '%s'",
                 username, domain);
    }
    else if (strcmp(action, "unlock") == 0) {
        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh unlock '%s' '%s'",
                 username, domain);
    }
    else if (strcmp(action, "fixperms") == 0) {
        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh fixperms '%s' '%s'",
                 username, domain);
    }
    else if (strcmp(action, "sso_create") == 0) {
        if (argc < 6) {
            send_json_error("wp", "Cu phap: wp sso_create <username> <domain> <target_user_id> <token>");
            return 1;
        }
        const char *target_user_id = argv[4];
        const char *token = argv[5];

        if (!is_safe_num_string(target_user_id) && !is_safe_id(target_user_id)) {
            send_json_error("wp", "Target user ID khong hop le");
            return 1;
        }
        if (!is_safe_id(token)) {
            send_json_error("wp", "SSO token khong hop le");
            return 1;
        }

        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh sso_create '%s' '%s' '%s' '%s'",
                 username, domain, target_user_id, token);
    }
    else if (strcmp(action, "wpc_optimize") == 0) {
        if (argc < 6) {
            send_json_error("wp", "Cu phap: wp wpc_optimize <username> <domain> <file_name> <1|0>");
            return 1;
        }
        const char *file_name = argv[4];
        const char *enable = argv[5];

        if (!is_safe_token(file_name) || (strcmp(enable, "0") != 0 && strcmp(enable, "1") != 0)) {
            send_json_error("wp", "Tham so wpc_optimize khong hop le");
            return 1;
        }

        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh wpc_optimize '%s' '%s' '%s' '%s'",
                 username, domain, file_name, enable);
    }
    else if (strcmp(action, "db_cleanup") == 0) {
        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh db_cleanup '%s' '%s'",
                 username, domain);
    }
    else if (strcmp(action, "config_define") == 0) {
        if (argc < 7) {
            send_json_error("wp", "Cu phap: wp config_define <username> <domain> <constant> <value> <1|0>");
            return 1;
        }
        const char *constant = argv[4];
        const char *val = argv[5];
        const char *enable = argv[6];

        if (!is_safe_id(constant) || (strcmp(enable, "0") != 0 && strcmp(enable, "1") != 0)) {
            send_json_error("wp", "Tham so config_define khong hop le");
            return 1;
        }
        for (size_t i = 0; val[i]; i++) {
            char c = val[i];
            if (c == '\'' || c == '"' || c == '`' || c == '$' || c == ';' ||
                c == '&' || c == '|' || c == '\n' || c == '\r' || c == '\\') {
                send_json_error("wp", "Gia tri define chua ky tu khong an toan");
                return 1;
            }
        }

        snprintf(cmd, sizeof(cmd), "/opt/wpc/scripts/wp_manager.sh config_define '%s' '%s' '%s' '%s' '%s'",
                 username, domain, constant, val, enable);
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
