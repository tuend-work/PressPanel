/*
 * WPCloud: db.c - Quản lý MariaDB/MySQL Database
 * SUID Binary (chmod 4750 root:wpc)
 */

#include "common.h"

int main(int argc, char *argv[]) {
    init_security_env();

    if (argc < 4) {
        send_json_error("db", "Cu phap: db <create|drop|backup> <db_name> <db_user/username> [filename]");
        return 1;
    }

    const char *action = argv[1];
    const char *db_name = argv[2];
    const char *db_user = argv[3];

    if (!is_safe_id(db_name) || !is_safe_id(db_user)) {
        send_json_error("db", "Database Name hoac User khong hop le");
        return 1;
    }

    if (strcmp(action, "create") == 0) {
        // Đọc mật khẩu an toàn từ stdin
        char password[256] = {0};
        if (!fgets(password, sizeof(password), stdin)) {
            send_json_error("db", "Khong the doc mat khau tu stdin");
            return 1;
        }
        password[strcspn(password, "\r\n")] = 0;

        // Escape các ký tự ' và \ trong mật khẩu trước khi đưa vào SQL
        char esc_pass[512] = {0};
        size_t j = 0;
        for (size_t i = 0; password[i] && j < sizeof(esc_pass) - 2; i++) {
            if (password[i] == '\'' || password[i] == '\\') {
                esc_pass[j++] = '\\';
            }
            esc_pass[j++] = password[i];
        }
        esc_pass[j] = '\0';

        // Truyền trực tiếp qua Pipe STDIN của tiến trình mysql (không qua shell bash)
        FILE *pipe_fp = popen("mysql -u root", "w");
        if (!pipe_fp) {
            send_json_error("db", "Khong the khoi dong tien trinh MariaDB/MySQL");
            return 1;
        }

        fprintf(pipe_fp, "CREATE DATABASE IF NOT EXISTS `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n", db_name);
        fprintf(pipe_fp, "CREATE USER IF NOT EXISTS '%s'@'localhost';\n", db_user);
        fprintf(pipe_fp, "ALTER USER '%s'@'localhost' IDENTIFIED BY '%s';\n", db_user, esc_pass);
        fprintf(pipe_fp, "GRANT ALL PRIVILEGES ON `%s`.* TO '%s'@'localhost';\n", db_name, db_user);
        fprintf(pipe_fp, "FLUSH PRIVILEGES;\n");

        int ret = pclose(pipe_fp);
        if (ret == 0) {
            char extra[256];
            snprintf(extra, sizeof(extra), "\"db_name\":\"%s\",\"db_user\":\"%s\"", db_name, db_user);
            send_json_ok(extra);
            return 0;
        }
        send_json_error("db", "Khong the khoi tao database MariaDB");
        return 1;
    }
    else if (strcmp(action, "drop") == 0) {
        FILE *pipe_fp = popen("mysql -u root", "w");
        if (!pipe_fp) {
            send_json_error("db", "Khong the ket noi MariaDB/MySQL");
            return 1;
        }

        fprintf(pipe_fp, "DROP DATABASE IF EXISTS `%s`;\n", db_name);
        fprintf(pipe_fp, "DROP USER IF EXISTS '%s'@'localhost';\n", db_user);
        fprintf(pipe_fp, "FLUSH PRIVILEGES;\n");

        int ret = pclose(pipe_fp);
        if (ret == 0) {
            send_json_ok("\"message\":\"Da xoa database thanh cong\"");
            return 0;
        }
        send_json_error("db", "Khong the xoa database");
        return 1;
    }
    else if (strcmp(action, "backup") == 0) {
        if (argc < 5) {
            send_json_error("db", "Thieu ten file backup. Cu phap: db backup <db_name> <username> <filename>");
            return 1;
        }
        const char *username = argv[3];
        const char *filename = argv[4];

        if (!is_safe_token(filename)) {
            send_json_error("db", "Ten file backup khong hop le");
            return 1;
        }

        char cmd[1024];
        snprintf(cmd, sizeof(cmd), "mysqldump -u root %s > /home/%s/backups/%s && chown %s:%s /home/%s/backups/%s",
                 db_name, username, filename, username, username, username, filename);

        if (system(cmd) == 0) {
            char extra[512];
            snprintf(extra, sizeof(extra), "\"file\":\"/home/%s/backups/%s\"", username, filename);
            send_json_ok(extra);
            return 0;
        }
        send_json_error("db", "Xuat dump database that bai");
        return 1;
    }

    send_json_error("db", "Hanh dong khong hop le");
    return 1;
}

