/*
 * ==============================================================================
 * PressPanel Control Panel - Native CLI Binary (presspanel)
 * Bảo mật mã nguồn, chống đọc trần khi thương mại hoá.
 * Biên dịch: gcc -O2 -fPIE -pie gatekeepers/presspanel.c -o /usr/local/bin/presspanel && strip /usr/local/bin/presspanel
 * ==============================================================================
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <ctype.h>
#include <sys/types.h>
#include <sys/stat.h>

static const char* get_install_dir(void) {
    if (access("/opt/presspanel", F_OK) == 0) return "/opt/presspanel";
    return "/opt/wpc";
}

static const char* get_conf_file(void) {
    if (access("/opt/presspanel/conf/presspanel.conf", F_OK) == 0) return "/opt/presspanel/conf/presspanel.conf";
    if (access("/opt/wpc/conf/wpc.conf", F_OK) == 0) return "/opt/wpc/conf/wpc.conf";
    return "/opt/presspanel/conf/presspanel.conf";
}

static const char* get_version_file(void) {
    if (access("/opt/presspanel/VERSION", F_OK) == 0) return "/opt/presspanel/VERSION";
    return "/opt/wpc/VERSION";
}

static void show_help(void) {
    printf("==========================================================\n");
    printf("     PRESSPANEL CONTROL PANEL CLI - ĐIỀU KHIỂN HỆ THỐNG     \n");
    printf("==========================================================\n");
    printf("Sử dụng: presspanel <lệnh> [tham số]\n\n");
    printf("Các lệnh hỗ trợ:\n");
    printf("  update      - Cập nhật panel lên phiên bản mới nhất từ GitHub\n");
    printf("  reinstall   - Cài đặt lại toàn bộ panel (tự động sao lưu dữ liệu)\n");
    printf("  status      - Xem trạng thái dịch vụ panel\n");
    printf("  restart     - Khởi động lại dịch vụ panel\n");
    printf("  start       - Bật dịch vụ panel\n");
    printf("  stop        - Dừng dịch vụ panel\n");
    printf("  logs        - Theo dõi nhật ký hoạt động thời gian thực\n");
    printf("  version     - Xem phiên bản hiện tại của PressPanel\n");
    printf("  password    - Đổi mật khẩu tài khoản quản trị admin\n");
    printf("  port <số>   - Đổi cổng truy cập panel (tránh trùng cổng khác)\n");
    printf("  fix-perms   - Chuẩn hóa quyền 0711 toàn bộ /home/* chống xem trộm file và fix lỗi 403\n");
    printf("  security [check|fix] - Kiểm tra hoặc áp dụng bảo mật tầng sâu chuẩn CloudLinux\n");
    printf("  php <check|update|set-cli> [ver] - Kiểm tra và cập nhật PHP từ Remi/Ondřej PPA\n");
    printf("  help        - Hiển thị menu hướng dẫn này\n");
    printf("==========================================================\n");
}

static void cmd_version(void) {
    FILE *fp = fopen(get_version_file(), "r");
    if (fp) {
        char buf[128];
        if (fgets(buf, sizeof(buf), fp)) {
            buf[strcspn(buf, "\r\n")] = '\0';
            printf("PressPanel Version: %s\n", buf);
        } else {
            printf("PressPanel Version: Unknown\n");
        }
        fclose(fp);
    } else {
        printf("PressPanel Version: Unknown\n");
    }
}

static void cmd_update(void) {
    char cmd[2048];
    snprintf(cmd, sizeof(cmd),
        "TMP_RUNNER=$(mktemp /tmp/presspanel_update_runner_XXXXXX.sh 2>/dev/null || echo \"/tmp/presspanel_update_$$.sh\"); "
        "if command -v curl >/dev/null 2>&1; then "
        "    curl -sSL \"https://raw.githubusercontent.com/tuend-work/PressPanel/main/scripts/update.sh\" -o \"$TMP_RUNNER\" 2>/dev/null || true; "
        "fi; "
        "if [ ! -s \"$TMP_RUNNER\" ] && [ -f %s/scripts/update.sh ]; then "
        "    cp %s/scripts/update.sh \"$TMP_RUNNER\" 2>/dev/null || true; "
        "fi; "
        "chmod 700 \"$TMP_RUNNER\" 2>/dev/null || true; "
        "sudo bash \"$TMP_RUNNER\"; "
        "rm -f \"$TMP_RUNNER\" 2>/dev/null || true; "
        "echo '[+] Đang khởi động lại dịch vụ presspanel.service...'; "
        "sudo systemctl daemon-reload 2>/dev/null || true; "
        "sudo systemctl restart presspanel 2>/dev/null || sudo systemctl restart wpc 2>/dev/null || true; "
        "echo \"[+] Hoàn tất! Trạng thái: $(systemctl is-active presspanel 2>/dev/null || systemctl is-active wpc 2>/dev/null || echo 'active')\"",
        get_install_dir(), get_install_dir()
    );
    int ret = system(cmd);
    (void)ret;
}

static void cmd_reinstall(void) {
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "sudo bash %s/scripts/reinstall.sh", get_install_dir());
    int ret = system(cmd);
    (void)ret;
}

static void cmd_status(void) {
    int ret = system("sudo systemctl status presspanel 2>/dev/null || sudo systemctl status wpc");
    (void)ret;
}

static void cmd_restart(void) {
    printf("[+] Đang khởi động lại dịch vụ PressPanel (presspanel.service)...\n");
    int ret = system("sudo systemctl restart presspanel 2>/dev/null || sudo systemctl restart wpc");
    (void)ret;
    ret = system("echo \"[+] Hoàn tất! Trạng thái: $(systemctl is-active presspanel 2>/dev/null || systemctl is-active wpc)\"");
    (void)ret;
}

static void cmd_start(void) {
    printf("[+] Đang bật dịch vụ PressPanel (presspanel.service)...\n");
    int ret = system("sudo systemctl start presspanel 2>/dev/null || sudo systemctl start wpc");
    (void)ret;
    ret = system("echo \"[+] Hoàn tất! Trạng thái: $(systemctl is-active presspanel 2>/dev/null || systemctl is-active wpc)\"");
    (void)ret;
}

static void cmd_stop(void) {
    printf("[+] Đang dừng dịch vụ PressPanel (presspanel.service)...\n");
    int ret = system("sudo systemctl stop presspanel 2>/dev/null || sudo systemctl stop wpc 2>/dev/null || true");
    (void)ret;
    printf("[+] Hoàn tất!\n");
}

static void cmd_logs(void) {
    int ret = system("sudo journalctl -u presspanel -u wpc -f");
    (void)ret;
}

static void cmd_port(const char *port_str) {
    if (geteuid() != 0) {
        fprintf(stderr, "[-] Vui lòng chạy lệnh này với quyền root: sudo presspanel port <cổng_mới>\n");
        exit(1);
    }
    if (!port_str || !*port_str) {
        fprintf(stderr, "[-] Vui lòng nhập số cổng hợp lệ (từ 1 đến 65535). Ví dụ: sudo presspanel port 8889\n");
        exit(1);
    }
    for (const char *p = port_str; *p; p++) {
        if (!isdigit((unsigned char)*p)) {
            fprintf(stderr, "[-] Cổng phải là số nguyên dương hợp lệ: %s\n", port_str);
            exit(1);
        }
    }
    int p = atoi(port_str);
    if (p < 1 || p > 65535) {
        fprintf(stderr, "[-] Số cổng không hợp lệ: %d (cho phép từ 1 đến 65535)\n", p);
        exit(1);
    }

    char check_cmd[512];
    snprintf(check_cmd, sizeof(check_cmd),
             "ss -tulpn 2>/dev/null | grep ':%d ' || netstat -tulpn 2>/dev/null | grep ':%d ' || true", p, p);
    FILE *fp = popen(check_cmd, "r");
    char in_use[512] = {0};
    if (fp) {
        size_t n = fread(in_use, 1, sizeof(in_use) - 1, fp);
        in_use[n] = '\0';
        pclose(fp);
    }

    if (strlen(in_use) > 0) {
        printf("[!] Cảnh báo: Cổng %d dường như đang bị tiến trình khác chiếm dụng!\n%s\n", p, in_use);
        printf("Bạn có chắc chắn vẫn muốn gán cổng này? (y/N): ");
        char confirm[16] = {0};
        if (!fgets(confirm, sizeof(confirm), stdin) || (confirm[0] != 'y' && confirm[0] != 'Y')) {
            printf("[-] Đã hủy thao tác đổi cổng.\n");
            exit(1);
        }
    }

    const char *conf = get_conf_file();
    printf("[+] Đang cập nhật cổng sang %d trong %s...\n", p, conf);
    char sed_cmd[1024];
    snprintf(sed_cmd, sizeof(sed_cmd),
             "if [ -f \"%s\" ]; then "
             "  if grep -q '^port=' \"%s\"; then "
             "    sed -i 's/^port=.*/port=%d/' \"%s\"; "
             "  else "
             "    echo 'port=%d' >> \"%s\"; "
             "  fi; "
             "fi; "
             "systemctl restart presspanel 2>/dev/null || systemctl restart wpc 2>/dev/null || true",
             conf, conf, p, conf, p, conf);
    int ret = system(sed_cmd);
    (void)ret;

    printf("==========================================================\n");
    printf("   ĐÃ ĐỔI CỔNG THÀNH CÔNG SANG: %d                 \n", p);
    printf("   Đường dẫn truy cập mới: http://<IP_MAY_CHU>:%d \n", p);
    printf("==========================================================\n");
}

static void cmd_password(void) {
    if (geteuid() != 0) {
        fprintf(stderr, "[-] Vui lòng chạy lệnh này với quyền root: sudo presspanel password\n");
        exit(1);
    }
    char *pass = getpass("Nhập mật khẩu mới cho tài khoản admin: ");
    if (!pass || strlen(pass) < 6) {
        fprintf(stderr, "[-] Mật khẩu phải có ít nhất 6 ký tự!\n");
        return;
    }

    const char *idir = get_install_dir();
    setenv("PP_NEW_PASS", pass, 1);
    char cmd[2048];
    snprintf(cmd, sizeof(cmd),
             "PYTHONPATH=\"%s\" \"%s/venv/bin/python\" -c \""
             "import os, sqlite3; "
             "from app.core.security import get_password_hash; "
             "from app.core.config import settings; "
             "h = get_password_hash(os.environ['PP_NEW_PASS']); "
             "c = sqlite3.connect(settings.DB_PATH); "
             "c.execute('UPDATE users SET password_hash = ?, token_version = COALESCE(token_version, 1) + 1 WHERE role = ?', (h, 'admin')); "
             "c.commit(); c.close(); "
             "print('[+] Đã đổi mật khẩu tài khoản admin thành công!')\"",
             idir, idir);
    int ret = system(cmd);
    unsetenv("PP_NEW_PASS");
    (void)ret;
}

static void cmd_username(void) {
    if (geteuid() != 0) {
        fprintf(stderr, "[-] Vui lòng chạy lệnh này với quyền root\n");
        return;
    }
    char new_user[64] = {0};
    printf("Nhập tên đăng nhập (username) mới cho Admin (3-32 ký tự a-z, 0-9, _, -): ");
    if (!fgets(new_user, sizeof(new_user), stdin)) return;
    new_user[strcspn(new_user, "\r\n")] = '\0';
    if (strlen(new_user) < 3 || strlen(new_user) > 32) {
        fprintf(stderr, "[-] Độ dài username phải từ 3 đến 32 ký tự!\n");
        return;
    }
    for (size_t i = 0; new_user[i]; i++) {
        char c = new_user[i];
        if (!isalnum((unsigned char)c) && c != '_' && c != '-') {
            fprintf(stderr, "[-] Username chỉ được chứa ký tự tiếng Anh, số, _ hoặc -!\n");
            return;
        }
    }
    const char *idir = get_install_dir();
    char cmd[1024];
    snprintf(cmd, sizeof(cmd),
             "PYTHONPATH=\"%s\" \"%s/venv/bin/python\" -c \""
             "import sqlite3, sys; "
             "from app.core.config import settings; "
             "conn = sqlite3.connect(settings.DB_PATH); "
             "cur = conn.cursor(); "
             "cur.execute('UPDATE users SET username = ?, token_version = COALESCE(token_version, 1) + 1 WHERE role = ?', (sys.argv[1].lower(), 'admin')); "
             "conn.commit(); conn.close(); "
             "print('[+] Đã đổi username admin thành:', sys.argv[1].lower())\" '%s'",
             idir, idir, new_user);
    int ret = system(cmd);
    (void)ret;
}

static void cmd_mysql_password(void) {
    if (geteuid() != 0) {
        fprintf(stderr, "[-] Vui lòng chạy lệnh này với quyền root\n");
        return;
    }
    char *pass = getpass("Nhập mật khẩu mới cho MySQL/MariaDB root: ");
    if (!pass || strlen(pass) < 6) {
        fprintf(stderr, "[-] Mật khẩu MySQL root phải có ít nhất 6 ký tự!\n");
        return;
    }
    char cmd[1024];
    snprintf(cmd, sizeof(cmd),
             "mysql -u root -e \"ALTER USER 'root'@'localhost' IDENTIFIED BY '%s'; FLUSH PRIVILEGES;\" 2>/dev/null || "
             "mariadb -u root -e \"ALTER USER 'root'@'localhost' IDENTIFIED BY '%s'; FLUSH PRIVILEGES;\" 2>/dev/null; "
             "printf '[client]\\nuser=root\\npassword=%s\\n' > /root/.my.cnf && chmod 600 /root/.my.cnf && "
             "echo '[+] Đã cập nhật mật khẩu MySQL root và lưu /root/.my.cnf!'",
             pass, pass, pass);
    int ret = system(cmd);
    (void)ret;
}

static void cmd_clear_cache(void) {
    printf("[+] Đang dọn dẹp bộ nhớ đệm và file tạm PressPanel...\n");
    int ret = system(
        "find /opt/presspanel/app -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true; "
        "rm -rf /tmp/presspanel_* /tmp/wpc_* /tmp/vhost_*.conf 2>/dev/null || true; "
        "echo '[+] Đã xóa sạch bộ nhớ đệm và tệp tạm!'"
    );
    (void)ret;
}

static void cmd_clear_login_limit(void) {
    printf("[+] Đang mở khóa giới hạn đăng nhập (Brute-force Lockout)...\n");
    int ret = system(
        "systemctl restart presspanel 2>/dev/null || systemctl restart wpc 2>/dev/null || true; "
        "command -v fail2ban-client >/dev/null 2>&1 && fail2ban-client unban --all 2>/dev/null || true; "
        "echo '[+] Đã xóa toàn bộ giới hạn đăng nhập!'"
    );
    (void)ret;
}

static void cmd_toggle_ssl(void) {
    if (geteuid() != 0) {
        fprintf(stderr, "[-] Vui lòng chạy lệnh này với quyền root\n");
        return;
    }
    const char *conf = get_conf_file();
    char cmd[1024];
    snprintf(cmd, sizeof(cmd),
             "CUR=$(grep -E '^ssl=' \"%s\" | cut -d'=' -f2 | tr -d ' \\r\\n'); "
             "if [ \"$CUR\" = \"1\" ] || [ \"$CUR\" = \"true\" ]; then "
             "  sed -i 's/^ssl=.*/ssl=0/' \"%s\"; echo '[+] Đã TẮT SSL cho Panel (HTTP).'; "
             "else "
             "  if grep -q '^ssl=' \"%s\"; then sed -i 's/^ssl=.*/ssl=1/' \"%s\"; else echo 'ssl=1' >> \"%s\"; fi; "
             "  echo '[+] Đã BẬT SSL cho Panel (HTTPS).'; "
             "fi; "
             "systemctl restart presspanel 2>/dev/null || systemctl restart wpc 2>/dev/null || true",
             conf, conf, conf, conf, conf);
    int ret = system(cmd);
    (void)ret;
}

static void cmd_default_info(void) {
    const char *idir = get_install_dir();
    const char *conf = get_conf_file();
    char cmd[2048];
    snprintf(cmd, sizeof(cmd),
             "PORT=$(grep -E '^port=' \"%s\" 2>/dev/null | cut -d'=' -f2 | tr -d ' \\r\\n'); [ -z \"$PORT\" ] && PORT=8888; "
             "SSL=$(grep -E '^ssl=' \"%s\" 2>/dev/null | cut -d'=' -f2 | tr -d ' \\r\\n'); "
             "SCHEME=http; [ \"$SSL\" = \"1\" ] && SCHEME=https; "
             "DB_INFO=$(\"%s/venv/bin/python\" -c \"import sqlite3; c=sqlite3.connect('%s/data/panel.db'); cur=c.cursor(); "
             "cur.execute(\\\"SELECT value FROM system_settings WHERE key='panel_secret_uri'\\\"); r1=cur.fetchone(); "
             "cur.execute(\\\"SELECT username FROM users WHERE role='admin' ORDER BY id ASC LIMIT 1\\\"); r2=cur.fetchone(); "
             "print((r1[0] if r1 and r1[0] else '') + '|' + (r2[0] if r2 and r2[0] else 'admin'))\" 2>/dev/null || echo '|admin'); "
             "URI=$(echo \"$DB_INFO\" | cut -d'|' -f1); USR=$(echo \"$DB_INFO\" | cut -d'|' -f2); "
             "INT_IP=$(hostname -I 2>/dev/null | awk '{print $1}'); [ -z \"$INT_IP\" ] && INT_IP=127.0.0.1; "
             "EXT_IP=$(curl -sS --max-time 3 https://api.ipify.org 2>/dev/null || echo \"$INT_IP\"); "
             "echo '=========================================================='; "
             "echo '  THÔNG TIN TRUY CẬP PRESSPANEL CONTROL PANEL'; "
             "echo '=========================================================='; "
             "echo \"  URL Local (WSL) : ${SCHEME}://localhost:${PORT}/${URI}\"; "
             "echo \"  URL Mạng Nội Bộ : ${SCHEME}://${INT_IP}:${PORT}/${URI}\"; "
             "echo \"  URL Công Khai   : ${SCHEME}://${EXT_IP}:${PORT}/${URI}\"; "
             "echo \"  Username Admin  : ${USR}\"; "
             "echo '=========================================================='",
             conf, conf, idir, idir);
    int ret = system(cmd);
    (void)ret;
}

static void cmd_secret_uri(void) {
    if (geteuid() != 0) {
        fprintf(stderr, "[-] Vui lòng chạy lệnh này với quyền root\n");
        return;
    }
    char new_uri[80] = {0};
    printf("Nhập Secret URI mới (4-64 ký tự chỉ gồm chữ tiếng Anh a-z, A-Z và số 0-9, không có dấu /): ");
    if (!fgets(new_uri, sizeof(new_uri), stdin)) return;
    new_uri[strcspn(new_uri, "\r\n")] = '\0';
    if (strlen(new_uri) < 4 || strlen(new_uri) > 64) {
        fprintf(stderr, "[-] Độ dài Secret URI phải từ 4 đến 64 ký tự!\n");
        return;
    }
    for (size_t i = 0; new_uri[i]; i++) {
        if (!isalnum((unsigned char)new_uri[i])) {
            fprintf(stderr, "[-] Secret URI chỉ được phép chứa chữ cái tiếng Anh và số (a-z, A-Z, 0-9)!\n");
            return;
        }
    }
    const char *idir = get_install_dir();
    char cmd[1024];
    snprintf(cmd, sizeof(cmd),
             "PYTHONPATH=\"%s\" \"%s/venv/bin/python\" -c \""
             "import sqlite3, sys; "
             "from app.core.config import settings; "
             "conn = sqlite3.connect(settings.DB_PATH); "
             "cur = conn.cursor(); "
             "cur.execute(\\\"INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('panel_secret_uri', ?, CURRENT_TIMESTAMP)\\\", (sys.argv[1],)); "
             "conn.commit(); conn.close(); "
             "print('[+] Đã đổi Secret Login URI thành: /' + sys.argv[1])\" '%s'",
             idir, idir, new_uri);
    int ret = system(cmd);
    (void)ret;
}

static void cmd_error_logs(void) {
    int ret = system("journalctl -u presspanel -u wpc -n 60 --no-pager 2>/dev/null || tail -n 60 /var/log/presspanel.log 2>/dev/null || echo 'Chưa có log lỗi.'");
    (void)ret;
}

static void cmd_fix_perms(void) {
    if (geteuid() != 0) {
        fprintf(stderr, "[-] Vui lòng chạy lệnh này với quyền root: sudo presspanel fix-perms\n");
        exit(1);
    }
    printf("[+] Đang rà soát và chuẩn hóa phân quyền thư mục người dùng (/home/*)...\n");
    int ret = system(
        "chmod 711 /home 2>/dev/null || true; "
        "mkdir -p /tmp/lshttpd 2>/dev/null || true; "
        "chmod 1777 /tmp/lshttpd 2>/dev/null || true; "
        "if systemctl is-active --quiet lshttpd 2>/dev/null || systemctl is-active --quiet lsws 2>/dev/null; then "
        "  systemctl stop 'presspanel-php*-fpm@*' 2>/dev/null || true; "
        "  systemctl stop 'wpc-php*-fpm@*' 2>/dev/null || true; "
        "  pkill -9 -f 'php-fpm' 2>/dev/null || true; "
        "fi; "
        "if systemctl is-active --quiet nginx 2>/dev/null && ! systemctl is-active --quiet lshttpd 2>/dev/null; then "
        "  pkill -9 -f 'openlitespeed' 2>/dev/null || true; "
        "  pkill -9 -f 'lshttpd' 2>/dev/null || true; "
        "  pkill -9 -f 'lsphp' 2>/dev/null || true; "
        "  rm -rf /tmp/lshttpd/*.sock 2>/dev/null || true; "
        "fi; "
        "for user_home in /home/*; do "
        "  [ -d \"$user_home\" ] || continue; "
        "  u_name=$(basename \"$user_home\"); "
        "  chmod 711 \"$user_home\" 2>/dev/null || true; "
        "  [ -d \"$user_home/domains\" ] && chmod 711 \"$user_home/domains\" 2>/dev/null || true; "
        "  for priv_dir in \"$user_home/logs\" \"$user_home/ssl\" \"$user_home/backups\" \"$user_home/.tmp\" \"$user_home/.sessions\" \"$user_home/.ssh\" \"$user_home/tmp\"; do "
        "    [ -d \"$priv_dir\" ] && chmod 700 \"$priv_dir\" 2>/dev/null || true; "
        "  done; "
        "  if [ -d \"$user_home/domains\" ]; then "
        "    find \"$user_home/domains\" -type d -exec chmod 755 {} + 2>/dev/null || true; "
        "    find \"$user_home/domains\" -type f -exec chmod 644 {} + 2>/dev/null || true; "
        "    find \"$user_home/domains\" -type d \\( -name 'logs' -o -name 'ssl' -o -name 'backups' \\) -exec chmod 700 {} + 2>/dev/null || true; "
        "  fi; "
        "  if [ -d \"$user_home/public_html\" ]; then "
        "    chmod 755 \"$user_home/public_html\" 2>/dev/null || true; "
        "    find \"$user_home/public_html\" -type d -exec chmod 755 {} + 2>/dev/null || true; "
        "    find \"$user_home/public_html\" -type f -exec chmod 644 {} + 2>/dev/null || true; "
        "  fi; "
        "  echo \"    [-] Đã chuẩn hoá phân quyền bảo mật cho: $u_name\"; "
        "done; "
        "if systemctl is-active --quiet lsws 2>/dev/null; then systemctl restart lsws 2>/dev/null || true; fi; "
        "if systemctl is-active --quiet nginx 2>/dev/null; then systemctl restart nginx 2>/dev/null || true; fi; "
        "echo '[+] Hoàn tất! Tất cả thư mục người dùng đã được cô lập an toàn.'"
    );
    (void)ret;
}

static void cmd_security(int argc, char *argv[]) {
    const char *sub = (argc >= 3) ? argv[2] : "check";
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "sudo bash %s/scripts/security_hardening.sh '%s'", get_install_dir(), sub);
    int ret = system(cmd);
    (void)ret;
}

static void cmd_php(int argc, char *argv[]) {
    const char *sub = (argc >= 3) ? argv[2] : "check-update";
    const char *ver = (argc >= 4) ? argv[3] : "8.2";
    if (strcmp(sub, "check") == 0) sub = "check-update";
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "sudo bash %s/scripts/apps/php.sh '%s' '%s'", get_install_dir(), sub, ver);
    int ret = system(cmd);
    (void)ret;
}

static void show_interactive_menu(void) {
    while (1) {
        printf("\n==================== PressPanel CLI ====================\n");
        printf("(1)  Khởi động lại Panel          (10) Mở khóa giới hạn đăng nhập\n");
        printf("(2)  Dừng dịch vụ Panel           (11) Bật/Tắt SSL cho Panel\n");
        printf("(3)  Bật dịch vụ Panel            (12) Xem thông tin truy cập Panel\n");
        printf("(4)  Kiểm tra trạng thái Panel    (13) Đổi đường dẫn bí mật (Secret URI)\n");
        printf("(5)  Đổi mật khẩu Admin           (14) Sửa lỗi & Cập nhật Panel\n");
        printf("(6)  Đổi tên đăng nhập Admin      (15) Xem nhật ký lỗi Panel\n");
        printf("(7)  Đổi mật khẩu MySQL root      (16) Chuẩn hóa phân quyền (/home/*)\n");
        printf("(8)  Đổi cổng (Port) Panel        (17) Kiểm tra/Áp dụng bảo mật hệ thống\n");
        printf("(9)  Xóa bộ nhớ đệm & tệp tạm     (18) Cài đặt lại Panel (Reinstall)\n");
        printf("(0)  Thoát (Cancel)\n");
        printf("========================================================\n");
        printf("Vui lòng nhập số lệnh: ");
        fflush(stdout);

        char buf[32] = {0};
        if (!fgets(buf, sizeof(buf), stdin)) break;
        buf[strcspn(buf, "\r\n")] = '\0';
        if (strlen(buf) == 0) continue;

        int choice = atoi(buf);
        if (strcmp(buf, "0") == 0) {
            printf("Đã thoát PressPanel CLI.\n");
            break;
        }
        switch (choice) {
            case 1: cmd_restart(); break;
            case 2: cmd_stop(); break;
            case 3: cmd_start(); break;
            case 4: cmd_status(); break;
            case 5: cmd_password(); break;
            case 6: cmd_username(); break;
            case 7: cmd_mysql_password(); break;
            case 8: {
                char pbuf[16] = {0};
                printf("Nhập số cổng mới cho Panel (1 - 65535): ");
                if (fgets(pbuf, sizeof(pbuf), stdin)) {
                    pbuf[strcspn(pbuf, "\r\n")] = '\0';
                    cmd_port(pbuf);
                }
                break;
            }
            case 9: cmd_clear_cache(); break;
            case 10: cmd_clear_login_limit(); break;
            case 11: cmd_toggle_ssl(); break;
            case 12: cmd_default_info(); break;
            case 13: cmd_secret_uri(); break;
            case 14: cmd_update(); break;
            case 15: cmd_error_logs(); break;
            case 16: cmd_fix_perms(); break;
            case 17: {
                char *s_argv[] = {"presspanel", "security", "fix"};
                cmd_security(3, s_argv);
                break;
            }
            case 18: cmd_reinstall(); break;
            default:
                fprintf(stderr, "[-] Lựa chọn không hợp lệ: %s\n", buf);
                break;
        }
    }
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        show_interactive_menu();
        return 0;
    }

    const char *action = argv[1];

    if (strcmp(action, "menu") == 0) {
        show_interactive_menu();
    } else if (strcmp(action, "update") == 0) {
        cmd_update();
    } else if (strcmp(action, "reinstall") == 0) {
        cmd_reinstall();
    } else if (strcmp(action, "status") == 0) {
        cmd_status();
    } else if (strcmp(action, "restart") == 0) {
        cmd_restart();
    } else if (strcmp(action, "start") == 0) {
        cmd_start();
    } else if (strcmp(action, "stop") == 0) {
        cmd_stop();
    } else if (strcmp(action, "logs") == 0) {
        cmd_logs();
    } else if (strcmp(action, "version") == 0) {
        cmd_version();
    } else if (strcmp(action, "port") == 0) {
        cmd_port(argc >= 3 ? argv[2] : NULL);
    } else if (strcmp(action, "password") == 0) {
        cmd_password();
    } else if (strcmp(action, "fix-perms") == 0) {
        cmd_fix_perms();
    } else if (strcmp(action, "security") == 0) {
        cmd_security(argc, argv);
    } else if (strcmp(action, "php") == 0) {
        cmd_php(argc, argv);
    } else if (strcmp(action, "help") == 0 || strcmp(action, "--help") == 0 || strcmp(action, "-h") == 0) {
        show_help();
    } else {
        fprintf(stderr, "[-] Lệnh không hợp lệ: %s\n", action);
        show_help();
        return 1;
    }

    return 0;
}
