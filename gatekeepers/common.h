#ifndef PRESSPANEL_COMMON_H
#define PRESSPANEL_COMMON_H

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

#define PANEL_USER  "presspanel"
#define PANEL_GROUP "presspanel"
#define SAFE_PATH   "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

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


#endif // PRESSPANEL_COMMON_H
