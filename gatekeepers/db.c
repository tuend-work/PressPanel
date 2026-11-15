/*
 * WPCloud: db.c - Quản lý MariaDB/MySQL Database & MySQL Resource Controller
 * SUID Binary (chmod 4750 root:wpc)
 */

#include "common.h"

static int is_number_str(const char *str) {
    if (!str || !*str) return 0;
    for (size_t i = 0; str[i]; i++) {
        if (!isdigit((unsigned char)str[i])) return 0;
    }
    return 1;
}

int main(int argc, char *argv[]) {
    init_security_env();

    if (argc < 2) {
        send_json_error("db", "Cu phap: db <create|drop|backup|ensure-controller|kill-query|apply-limits> ...");
        return 1;
    }

    const char *action = argv[1];

    /* --- Action: ensure-controller (Kích hoạt WPC MySQL Resource Controller) --- */
    if (strcmp(action, "ensure-controller") == 0) {
        const char *cnf_content = 
            "[mysqld]\n"
            "# Managed by WPC MySQL Resource Controller\n"
            "userstat = 1\n"
            "max_statement_time = 15\n"
            "innodb_old_blocks_pct = 37\n"
            "innodb_old_blocks_time = 1000\n"
            "tmp_table_size = 32M\n"
            "max_heap_table_size = 32M\n";

        struct stat st;
        const char *target_file = NULL;
        if (stat("/etc/mysql/mariadb.conf.d", &st) == 0 && S_ISDIR(st.st_mode)) {
            target_file = "/etc/mysql/mariadb.conf.d/99-wpc-mysql-controller.cnf";
        } else if (stat("/etc/my.cnf.d", &st) == 0 && S_ISDIR(st.st_mode)) {
            target_file = "/etc/my.cnf.d/99-wpc-mysql-controller.cnf";
        } else if (stat("/etc/mysql/conf.d", &st) == 0 && S_ISDIR(st.st_mode)) {
            target_file = "/etc/mysql/conf.d/99-wpc-mysql-controller.cnf";
        }

        if (target_file) {
            FILE *cnf_fp = fopen(target_file, "w");
            if (cnf_fp) {
                fputs(cnf_content, cnf_fp);
                fclose(cnf_fp);
                chmod(target_file, 0644);
            }
        }

        FILE *pipe_fp = popen("mysql -u root", "w");
        if (pipe_fp) {
            fprintf(pipe_fp, "SET GLOBAL userstat = 1;\n");
            fprintf(pipe_fp, "SET GLOBAL innodb_old_blocks_pct = 37;\n");
            fprintf(pipe_fp, "SET GLOBAL innodb_old_blocks_time = 1000;\n");
            pclose(pipe_fp);
        }

        send_json_ok("\"message\":\"MySQL Resource Controller settings ensured successfully\"");
        return 0;
    }

    /* --- Action: kill-query (Active Slow-Query Sniper) --- */
    if (strcmp(action, "kill-query") == 0) {
        if (argc < 3) {
            send_json_error("db", "Cu phap: db kill-query <query_id>");
            return 1;
        }
        const char *qid_str = argv[2];
        if (!is_number_str(qid_str)) {
            send_json_error("db", "Query ID phai la so nguyen");
            return 1;
        }

        FILE *pipe_fp = popen("mysql -u root", "w");
        if (!pipe_fp) {
            send_json_error("db", "Khong the ket noi MySQL");
            return 1;
        }
        fprintf(pipe_fp, "KILL QUERY %s;\n", qid_str);
        int ret = pclose(pipe_fp);
        if (ret == 0) {
            char extra[128];
            snprintf(extra, sizeof(extra), "\"killed_query_id\":%s", qid_str);
            send_json_ok(extra);
            return 0;
        }
        send_json_error("db", "Khong the kill query");
        return 1;
    }

    /* --- Action: apply-limits (Giới hạn tài nguyên MySQL cho User) --- */
    if (strcmp(action, "apply-limits") == 0) {
        if (argc < 5) {
            send_json_error("db", "Cu phap: db apply-limits <db_user> <max_conns> <max_time> [max_queries_per_hour] [max_updates_per_hour] [max_connections_per_hour]");
            return 1;
        }
        const char *u_name = argv[2];
        const char *max_conns_str = argv[3];
        const char *max_time_str = argv[4];
        const char *max_q_str = (argc > 5) ? argv[5] : "0";
        const char *max_upd_str = (argc > 6) ? argv[6] : "0";
        const char *max_cph_str = (argc > 7) ? argv[7] : "0";

        if (!is_safe_id(u_name)) {
            send_json_error("db", "User khong hop le");
            return 1;
        }
        if (!is_number_str(max_conns_str) || !is_number_str(max_time_str) ||
            !is_number_str(max_q_str) || !is_number_str(max_upd_str) || !is_number_str(max_cph_str)) {
            send_json_error("db", "Thong so gioi han phai la so nguyen");
            return 1;
        }

        FILE *pipe_fp = popen("mysql -u root", "w");
        if (!pipe_fp) {
            send_json_error("db", "Khong the ket noi MySQL");
            return 1;
        }

        fprintf(pipe_fp,
            "ALTER USER IF EXISTS '%s'@'localhost' WITH "
            "MAX_USER_CONNECTIONS %s "
            "MAX_STATEMENT_TIME %s "
            "MAX_QUERIES_PER_HOUR %s "
            "MAX_UPDATES_PER_HOUR %s "
            "MAX_CONNECTIONS_PER_HOUR %s;\n",
            u_name, max_conns_str, max_time_str, max_q_str, max_upd_str, max_cph_str
        );
        fprintf(pipe_fp,
            "ALTER USER IF EXISTS '%s'@'127.0.0.1' WITH "
            "MAX_USER_CONNECTIONS %s "
            "MAX_STATEMENT_TIME %s "
            "MAX_QUERIES_PER_HOUR %s "
            "MAX_UPDATES_PER_HOUR %s "
            "MAX_CONNECTIONS_PER_HOUR %s;\n",
            u_name, max_conns_str, max_time_str, max_q_str, max_upd_str, max_cph_str
        );
        fprintf(pipe_fp, "FLUSH PRIVILEGES;\n");

        int ret = pclose(pipe_fp);
        if (ret == 0) {
            char extra[256];
            snprintf(extra, sizeof(extra), "\"db_user\":\"%s\",\"max_conns\":%s,\"max_time\":%s", u_name, max_conns_str, max_time_str);
            send_json_ok(extra);
            return 0;
        }
        send_json_error("db", "Khong the ap dung gioi han tai nguyen MySQL");
        return 1;
    }

    /* --- Các tác vụ cũ: create, drop, backup yêu cầu ít nhất 4 tham số --- */
    if (argc < 4) {
        send_json_error("db", "Cu phap: db <create|drop|backup> <db_name> <db_user/username> [filename]");
        return 1;
    }

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
