#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>

int main(int argc, char *argv[]) {
    /* Forward directly to presspanel CLI binary */
    if (access("/usr/local/bin/presspanel", X_OK) == 0) {
        execv("/usr/local/bin/presspanel", argv);
    }
    if (access("/opt/presspanel/bin/presspanel", X_OK) == 0) {
        execv("/opt/presspanel/bin/presspanel", argv);
    }
    if (access("/usr/bin/presspanel", X_OK) == 0) {
        execv("/usr/bin/presspanel", argv);
    }

    /* Fallback: invoke via PATH */
    char cmd[2048] = "presspanel";
    for (int i = 1; i < argc; i++) {
        strncat(cmd, " \"", sizeof(cmd) - strlen(cmd) - 1);
        strncat(cmd, argv[i], sizeof(cmd) - strlen(cmd) - 1);
        strncat(cmd, "\"", sizeof(cmd) - strlen(cmd) - 1);
    }
    return system(cmd);
}
