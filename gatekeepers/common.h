#ifndef WPC_COMMON_H
#define WPC_COMMON_H

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <ctype.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <pwd.h>
#include <grp.h>
#include <errno.h>

#define PANEL_USER  "wpc"
#define PANEL_GROUP "wpc"
#define SAFE_PATH   "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

#define IGNORE_RESULT(fn) do { if (fn) {} } while (0)

static inline void send_json_ok(const char *extra_json) {
    if (extra_json && *extra_json) {
        printf("{\"status\":\"ok\",%s}\n", extra_json);
    } else {
        printf("{\"status\":\"ok\"}\n");
    }
    fflush(stdout);
}

static inline void send_json_error(const char *module, const char *message) {
    printf("{\"status\":\"error\",\"module\":\"%s\",\"message\":\"%s\"}\n",
           module ? module : "unknown",
           message ? message : "unspecified error");
    fflush(stdout);
}

static inline int is_safe_id(const char *str) {
    if (!str || !*str) return 0;
    size_t len = strlen(str);
    if (len > 32) return 0;
    for (size_t i = 0; i < len; i++) {
        char c = str[i];
        if (!isalnum((unsigned char)c) && c != '_' && c != '-') {
            return 0;
        }
    }
    return 1;
}

static inline int is_safe_token(const char *str) {
    if (!str || !*str) return 0;
    if (strstr(str, "..")) return 0; // Chống Directory Traversal
    size_t len = strlen(str);
    if (len > 255) return 0;
    for (size_t i = 0; i < len; i++) {
        char c = str[i];
        if (!isalnum((unsigned char)c) && c != '.' && c != '_' && c != '-' && c != ':') {
            return 0;
        }
    }
    return 1;
}

static inline int is_safe_home_path(const char *path) {
    if (!path || !*path) return 0;
    if (strncmp(path, "/home/", 6) != 0) return 0; // Bắt buộc phải nằm trong /home/
    if (strstr(path, "..")) return 0; // Chống traversal
    size_t len = strlen(path);
    if (len > 1024) return 0;
    for (size_t i = 0; i < len; i++) {
        char c = path[i];
        // Cấm tuyệt đối các ký tự command injection hoặc escape quote
        if (c == '\'' || c == '"' || c == '`' || c == '$' || c == ';' ||
            c == '&' || c == '|' || c == '\n' || c == '\r' || c == '\\') {
            return 0;
        }
    }
    return 1;
}

static inline int is_system_user(const char *username) {
    if (!username || !*username) return 1;
    const char *sys_users[] = {
        "root", "wpc", "nobody", "www-data", "bin", "daemon", "sys", "sync",
        "games", "man", "lp", "mail", "news", "uucp", "proxy", "backup",
        "list", "irc", "gnats", "systemd", "mysql", "mariadb", "nginx",
        "lsadm", "redis", "postfix", "dovecot", "bind", "named", "sshd", NULL
    };
    for (int i = 0; sys_users[i] != NULL; i++) {
        if (strcmp(username, sys_users[i]) == 0) return 1;
    }
    struct passwd *pw = getpwnam(username);
    if (pw && pw->pw_uid < 1000) {
        return 1; // Mọi UID < 1000 đều là tài khoản hệ thống của Linux
    }
    return 0;
}

static inline void init_security_env(void) {
    if (geteuid() != 0) {
        send_json_error("security", "Gatekeeper phai co quyen root SUID (chmod 4750)");
        exit(1);
    }
    if (setresuid(0, 0, 0) != 0 || setresgid(0, 0, 0) != 0) {
        send_json_error("security", "Khong the thiet lap dac quyen root");
        exit(1);
    }
    clearenv();
    setenv("PATH", SAFE_PATH, 1);
    setenv("LC_ALL", "C.UTF-8", 1);
    umask(0022);
}


#endif // WPC_COMMON_H
