/*
 * ==============================================================================
 * WPCloud Control Panel - Native CLI Binary (wpc)
 * Bảo mật mã nguồn, chống đọc trần khi thương mại hoá.
 * Biên dịch: gcc -O2 -fPIE -pie gatekeepers/wpc.c -o /usr/local/bin/wpc && strip /usr/local/bin/wpc
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

#define INSTALL_DIR "/opt/wpc"
#define CONF_FILE   INSTALL_DIR "/conf/wpc.conf"
#define VERSION_FILE INSTALL_DIR "/VERSION"

static void show_help(void) {
    printf("==========================================================\n");
    printf("     WPCLOUD CONTROL PANEL CLI - ĐIỀU KHIỂN HỆ THỐNG     \n");
    printf("==========================================================\n");
    printf("Sử dụng: wpc <lệnh> [tham số]\n\n");
    printf("Các lệnh hỗ trợ:\n");
    printf("  update      - Cập nhật panel lên phiên bản mới nhất từ GitHub\n");
    printf("  reinstall   - Cài đặt lại toàn bộ panel (tự động sao lưu dữ liệu)\n");
    printf("  status      - Xem trạng thái dịch vụ panel\n");
    printf("  restart     - Khởi động lại dịch vụ panel\n");
    printf("  start       - Bật dịch vụ panel\n");
    printf("  stop        - Dừng dịch vụ panel\n");
    printf("  logs        - Theo dõi nhật ký hoạt động thời gian thực\n");
    printf("  version     - Xem phiên bản hiện tại của WPCloud Panel\n");
    printf("  password    - Đổi mật khẩu tài khoản quản trị admin\n");
    printf("  port <số>   - Đổi cổng truy cập panel (tránh trùng cổng khác)\n");
    printf("  fix-perms   - Chuẩn hóa quyền 0711 toàn bộ /home/* chống xem trộm file và fix lỗi 403\n");
    printf("  help        - Hiển thị menu hướng dẫn này\n");
    printf("==========================================================\n");
}

static void cmd_version(void) {
    FILE *fp = fopen(VERSION_FILE, "r");
    if (fp) {
        char buf[128];
        if (fgets(buf, sizeof(buf), fp)) {
            buf[strcspn(buf, "\r\n")] = '\0';
            printf("WPCloud Control Panel Version: %s\n", buf);
        } else {
            printf("WPCloud Control Panel Version: Unknown\n");
        }
        fclose(fp);
    } else {
        printf("WPCloud Control Panel Version: Unknown\n");
    }
}

static void cmd_update(void) {
    int ret = system(
        "TMP_RUNNER=$(mktemp /tmp/wpc_update_runner_XXXXXX.sh 2>/dev/null || echo \"/tmp/wpc_update_$$.sh\"); "
        "if command -v curl >/dev/null 2>&1; then "
        "    curl -sSL \"https://raw.githubusercontent.com/tuend-work/PressPanel/main/scripts/update.sh\" -o \"$TMP_RUNNER\" 2>/dev/null || true; "
        "fi; "
        "if [ ! -s \"$TMP_RUNNER\" ] && [ -f /opt/wpc/scripts/update.sh ]; then "
        "    cp /opt/wpc/scripts/update.sh \"$TMP_RUNNER\" 2>/dev/null || true; "
        "fi; "
        "chmod 700 \"$TMP_RUNNER\" 2>/dev/null || true; "
        "sudo bash \"$TMP_RUNNER\"; "
        "rm -f \"$TMP_RUNNER\" 2>/dev/null || true; "
        "echo '[+] Đang khởi động lại dịch vụ wpc.service...'; "
        "sudo systemctl daemon-reload 2>/dev/null || true; "
        "sudo systemctl restart wpc 2>/dev/null || true; "
        "echo \"[+] Hoàn tất! Trạng thái: $(systemctl is-active wpc 2>/dev/null || echo 'active')\""
    );
    (void)ret;
}

static void cmd_reinstall(void) {
    int ret = system("sudo bash " INSTALL_DIR "/scripts/reinstall.sh");
    (void)ret;
}

static void cmd_status(void) {
    int ret = system("sudo systemctl status wpc");
    (void)ret;
}

static void cmd_restart(void) {
    printf("[+] Đang khởi động lại dịch vụ WPCloud Control Panel (wpc.service)...\n");
    int ret = system("sudo systemctl restart wpc");
    (void)ret;
    ret = system("echo \"[+] Hoàn tất! Trạng thái: $(systemctl is-active wpc)\"");
    (void)ret;
}

static void cmd_start(void) {
    printf("[+] Đang bật dịch vụ WPCloud Control Panel (wpc.service)...\n");
    int ret = system("sudo systemctl start wpc");
    (void)ret;
    ret = system("echo \"[+] Hoàn tất! Trạng thái: $(systemctl is-active wpc)\"");
    (void)ret;
}

static void cmd_stop(void) {
    printf("[+] Đang dừng dịch vụ WPCloud Control Panel (wpc.service)...\n");
    int ret = system("sudo systemctl stop wpc 2>/dev/null || true");
    (void)ret;
    printf("[+] Hoàn tất!\n");
}

static void cmd_logs(void) {
    int ret = system("sudo journalctl -u wpc -f");
    (void)ret;
}

static void cmd_port(const char *port_str) {
    if (geteuid() != 0) {
        fprintf(stderr, "[-] Vui lòng chạy lệnh này với quyền root: sudo wpc port <cổng_mới>\n");
        exit(1);
    }
    if (!port_str || !*port_str) {
        fprintf(stderr, "[-] Vui lòng nhập số cổng hợp lệ (từ 1 đến 65535). Ví dụ: sudo wpc port 8889\n");
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

    printf("[+] Đang cập nhật cổng sang %d trong %s...\n", p, CONF_FILE);
    char sed_cmd[512];
    snprintf(sed_cmd, sizeof(sed_cmd),
             "if [ -f \"%s\" ]; then "
             "  if grep -q '^port=' \"%s\"; then "
             "    sed -i 's/^port=.*/port=%d/' \"%s\"; "
             "  else "
             "    echo 'port=%d' >> \"%s\"; "
             "  fi; "
             "fi; "
             "systemctl restart wpc",
             CONF_FILE, CONF_FILE, p, CONF_FILE, p, CONF_FILE);
    int ret = system(sed_cmd);
    (void)ret;

    printf("==========================================================\n");
    printf("   ĐÃ ĐỔI CỔNG THÀNH CÔNG SANG: %d                 \n", p);
    printf("   Đường dẫn truy cập mới: http://<IP_MAY_CHU>:%d \n", p);
    printf("==========================================================\n");
}

static void cmd_password(void) {
    if (geteuid() != 0) {
        fprintf(stderr, "[-] Vui lòng chạy lệnh này với quyền root: sudo wpc password\n");
        exit(1);
    }
    char *pass = getpass("Nhập mật khẩu mới cho tài khoản admin: ");
    if (!pass || strlen(pass) == 0) {
        fprintf(stderr, "[-] Mật khẩu không được để trống!\n");
        exit(1);
    }

    /* Đổi mật khẩu an toàn qua Python venv */
    char cmd[2048];
    snprintf(cmd, sizeof(cmd),
             "PYTHONPATH=\"%s\" \"%s/venv/bin/python\" -c \""
             "import asyncio, aiosqlite; "
             "from app.core.security import get_password_hash; "
             "from app.core.config import settings; "
             "async def reset_pass(): "
             "    hashed = get_password_hash('%s'); "
             "    async with aiosqlite.connect(settings.DB_PATH) as db: "
             "        await db.execute('UPDATE users SET password_hash = ? WHERE username = ?', (hashed, 'admin')); "
             "        await db.commit(); "
             "    print('[+] Đã đổi mật khẩu tài khoản admin thành công!'); "
             "asyncio.run(reset_pass())\"",
             INSTALL_DIR, INSTALL_DIR, pass);
    int ret = system(cmd);
    (void)ret;
}

static void cmd_fix_perms(void) {
    if (geteuid() != 0) {
        fprintf(stderr, "[-] Vui lòng chạy lệnh này với quyền root: sudo wpc fix-perms\n");
        exit(1);
    }
    printf("[+] Đang rà soát và chuẩn hóa phân quyền thư mục người dùng (/home/*)...\n");
    int ret = system(
        "chmod 711 /home 2>/dev/null || true; "
        "mkdir -p /tmp/lshttpd 2>/dev/null || true; "
        "chmod 1777 /tmp/lshttpd 2>/dev/null || true; "
        "if systemctl is-active --quiet lshttpd 2>/dev/null || systemctl is-active --quiet lsws 2>/dev/null; then "
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

int main(int argc, char *argv[]) {
    if (argc < 2) {
        show_help();
        return 0;
    }

    const char *action = argv[1];

    if (strcmp(action, "update") == 0) {
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
    } else if (strcmp(action, "help") == 0 || strcmp(action, "--help") == 0 || strcmp(action, "-h") == 0) {
        show_help();
    } else {
        fprintf(stderr, "[-] Lệnh không hợp lệ: %s\n", action);
        show_help();
        return 1;
    }

    return 0;
}
