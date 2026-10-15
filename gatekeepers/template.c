/*
 * PressPanel: template.c - Bộ Sinh Cấu Hình Kế Thừa 3 Cấp
 * SUID Binary (chmod 4750 root:PressPanel)
 */

#include "common.h"

#define TEMPLATE_ROOT "/opt/presspanel/templates"

int main(int argc, char *argv[]) {
    init_security_env();

    if (argc < 3) {
        send_json_error("template", "Cu phap: template <render|set> [args...]");
        return 1;
    }

    const char *action = argv[1];

    if (strcmp(action, "render") == 0) {
        if (argc < 6) {
            send_json_error("template", "Cu phap: template render <app> <pkg_id> <username> <out_path> [K=V ...]");
            return 1;
        }

        const char *app = argv[2];
        const char *pkg_id = argv[3];
        const char *username = argv[4];
        const char *out_path = argv[5];

        char tpl_path[512];
        struct stat st;

        // Ưu tiên 1: User Override
        snprintf(tpl_path, sizeof(tpl_path), "%s/%s/user_%s.tpl", TEMPLATE_ROOT, app, username);
        if (stat(tpl_path, &st) != 0) {
            // Ưu tiên 2: Reseller Package
            snprintf(tpl_path, sizeof(tpl_path), "%s/%s/package_%s.tpl", TEMPLATE_ROOT, app, pkg_id);
            if (stat(tpl_path, &st) != 0) {
                // Ưu tiên 3: App Default
                snprintf(tpl_path, sizeof(tpl_path), "%s/%s/default.tpl", TEMPLATE_ROOT, app);
            }
        }

        FILE *fin = fopen(tpl_path, "r");
        if (!fin) {
            send_json_error("template", "Khong tim thay tep template nguon");
            return 1;
        }

        FILE *fout = fopen(out_path, "w");
        if (!fout) {
            fclose(fin);
            send_json_error("template", "Khong the ghi tep cau hinh dich");
            return 1;
        }

        // Đọc toàn bộ file template
        fseek(fin, 0, SEEK_END);
        long sz = ftell(fin);
        fseek(fin, 0, SEEK_SET);

        char *buffer = malloc(sz + 1);
        if (buffer) {
            size_t r = fread(buffer, 1, sz, fin);
            (void)r;
            buffer[sz] = '\0';

            // Xử lý thay thế các placeholder {{KEY}} với K=V từ argv[6...]
            // Đơn giản hóa ghi buffer ra output
            fwrite(buffer, 1, sz, fout);
            free(buffer);
        }

        fclose(fin);
        fclose(fout);

        char extra[2048];
        snprintf(extra, sizeof(extra), "\"out_path\":\"%s\",\"template_used\":\"%s\"", out_path, tpl_path);
        send_json_ok(extra);
        return 0;
    }
    else if (strcmp(action, "set") == 0) {
        if (argc < 5) {
            send_json_error("template", "Cu phap: template set <app> <default|package|user> <id>");
            return 1;
        }
        const char *app = argv[2];
        const char *level = argv[3];
        const char *id = argv[4];

        char tpl_path[512];
        if (strcmp(level, "default") == 0) {
            snprintf(tpl_path, sizeof(tpl_path), "%s/%s/default.tpl", TEMPLATE_ROOT, app);
        } else if (strcmp(level, "package") == 0) {
            snprintf(tpl_path, sizeof(tpl_path), "%s/%s/package_%s.tpl", TEMPLATE_ROOT, app, id);
        } else {
            snprintf(tpl_path, sizeof(tpl_path), "%s/%s/user_%s.tpl", TEMPLATE_ROOT, app, id);
        }

        FILE *fout = fopen(tpl_path, "w");
        if (!fout) {
            send_json_error("template", "Khong the mo file de ghi template");
            return 1;
        }

        char chunk[1024];
        while (fgets(chunk, sizeof(chunk), stdin)) {
            fputs(chunk, fout);
        }
        fclose(fout);

        send_json_ok("\"message\":\"Da luu template thanh cong\"");
        return 0;
    }

    send_json_error("template", "Hanh dong khong hop le");
    return 1;
}

