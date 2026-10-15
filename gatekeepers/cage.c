/*
 * PressPanel: cage.c - Lồng Cách Ly Namespace & PID 1 Reaper
 * SUID Binary (chmod 4750 root:PressPanel)
 */

#define _GNU_SOURCE
#include "common.h"
#include <sched.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <signal.h>

static pid_t child_pid = 0;

static void forward_signal(int sig) {
    if (child_pid > 0) {
        kill(child_pid, sig);
    }
}

int main(int argc, char *argv[]) {
    init_security_env();

    if (argc < 5) {
        send_json_error("cage", "Cu phap: cage exec <reseller_id> <username> <cmd> [args...]");
        return 1;
    }

    const char *reseller_id = argv[2];
    const char *username = argv[3];
    const char *cmd = argv[4];

    if (!is_safe_id(reseller_id) || !is_safe_id(username)) {
        send_json_error("cage", "Tham so khong hop le");
        return 1;
    }

    struct passwd *pw = getpwnam(username);
    if (!pw) {
        send_json_error("cage", "Khong tim thay user Linux tuong ung");
        return 1;
    }

    // 1. Gán vào Cgroup của User
    char cgroup_procs[512];
    snprintf(cgroup_procs, sizeof(cgroup_procs), 
             "/sys/fs/cgroup/presspanel/reseller_%s/user_%s/cgroup.procs", reseller_id, username);
    FILE *fp = fopen(cgroup_procs, "w");
    if (fp) {
        fprintf(fp, "%d\n", getpid());
        fclose(fp);
    }

    // 2. Tách Namespaces
    if (unshare(CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWIPC | CLONE_NEWUTS) != 0) {
        // Fallback neu khong du quyen clone
    }

    // Fork de tien trinh con nhan PID 1 ben trong PID Namespace moi
    pid_t pid = fork();
    if (pid < 0) {
        send_json_error("cage", "Fork that bai");
        return 1;
    }

    if (pid > 0) {
        // Parent: dong vai tro PID 1 Reaper de chuyen tiep signal va don dep zombie processes
        child_pid = pid;
        signal(SIGTERM, forward_signal);
        signal(SIGINT, forward_signal);
        signal(SIGHUP, forward_signal);

        int status;
        while (1) {
            pid_t wpid = waitpid(-1, &status, 0);
            if (wpid == pid) {
                if (WIFEXITED(status)) return WEXITSTATUS(status);
                if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
                return 1;
            }
            if (wpid < 0 && errno == ECHILD) break;
        }
        return 0;
    }

    // Child Process: thiet lap moi truong bao ve
    // Mount /proc voi hidepid=2
    mount("proc", "/proc", "proc", MS_NOSUID | MS_NOEXEC | MS_NODEV, "hidepid=2");

    // Private /tmp bang tmpfs 256MB
    mount("tmpfs", "/tmp", "tmpfs", MS_NOSUID | MS_NODEV, "size=256M,mode=1777");

    // Chan leo thang dac quyen
    prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);

    // Ha quyen xuong UID/GID cua User
    if (setresgid(pw->pw_gid, pw->pw_gid, pw->pw_gid) != 0 ||
        setresuid(pw->pw_uid, pw->pw_uid, pw->pw_uid) != 0) {
        send_json_error("cage", "Khong the ha dac quyen xuong user");
        exit(1);
    }

    // Chuyen den thu muc /home/<user>/public_html hoac /home/<user>/domains neu ton tai
    char user_home[256];
    snprintf(user_home, sizeof(user_home), "/home/%s/public_html", pw->pw_name);
    if (chdir(user_home) != 0) {
        snprintf(user_home, sizeof(user_home), "/home/%s/domains", pw->pw_name);
        if (chdir(user_home) != 0 && chdir(pw->pw_dir) != 0) {
            IGNORE_RESULT(chdir("/"));
        }
    }

    // Thuc thi lenh muc tieu
    char **exec_args = &argv[4];
    execvp(cmd, exec_args);

    perror("execvp");
    exit(127);
}

