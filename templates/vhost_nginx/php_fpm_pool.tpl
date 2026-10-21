; ==============================================================================
; PressPanel Standalone PHP-FPM Service Configuration
; Website / User: {{USERNAME}}
; Service: wpc-php{{PHP_VER}}-fpm@{{USERNAME}}.service
; ==============================================================================

[global]
pid = {{PID_FILE}}
error_log = {{ERROR_LOG}}
log_level = {{LOG_LEVEL}}
daemonize = no

[{{USERNAME}}]
user = {{USERNAME}}
group = {{FPM_GRP}}

listen = {{SOCK_FILE}}
listen.owner = {{WORKER_USER}}
listen.group = {{WORKER_GRP}}
listen.mode = {{LISTEN_MODE}}
listen.backlog = {{LISTEN_BACKLOG}}

{{PM_DIRECTIVES}}

request_terminate_timeout = {{REQUEST_TERMINATE_TIMEOUT}}
request_slowlog_timeout = {{REQUEST_SLOWLOG_TIMEOUT}}
slowlog = {{SLOWLOG}}
catch_workers_output = {{CATCH_WORKERS_OUTPUT}}
decorate_workers_output = {{DECORATE_WORKERS_OUTPUT}}
clear_env = {{CLEAR_ENV}}

pm.status_path = {{PM_STATUS_PATH}}
ping.path = {{PING_PATH}}
ping.response = {{PING_RESPONSE}}

rlimit_files = {{RLIMIT_FILES}}
rlimit_core = {{RLIMIT_CORE}}

; Tách biệt môi trường và bảo vệ thư mục gốc của user
php_admin_value[open_basedir] = {{PHP_OPEN_BASEDIR}}
php_admin_value[upload_tmp_dir] = /home/{{USERNAME}}/.tmp
php_admin_value[session.save_path] = /home/{{USERNAME}}/.sessions
php_admin_value[disable_functions] = passthru,exec,system,putenv,chroot,chgrp,chown,shell_exec,popen,proc_open,pcntl_exec,ini_alter,ini_restore,dl,openlog,syslog,readlink,symlink,popepassthru,pcntl_alarm,pcntl_fork,pcntl_waitpid,pcntl_wait,pcntl_wifexited,pcntl_wifstopped,pcntl_wifsignaled,pcntl_wifcontinued,pcntl_wexitstatus,pcntl_wtermsig,pcntl_wstopsig,pcntl_signal,pcntl_signal_dispatch,pcntl_get_last_error,pcntl_strerror,pcntl_sigprocmask,pcntl_sigwaitinfo,pcntl_sigtimedwait,pcntl_exec,pcntl_getpriority,pcntl_setpriority,imap_open,apache_setenv
php_admin_value[memory_limit] = {{PHP_MEMORY_LIMIT}}
php_admin_value[max_execution_time] = {{PHP_MAX_EXECUTION_TIME}}
php_admin_value[upload_max_filesize] = {{PHP_UPLOAD_MAX_FILESIZE}}
php_admin_value[post_max_size] = {{PHP_POST_MAX_SIZE}}
php_admin_flag[log_errors] = on
php_admin_value[error_log] = /home/{{USERNAME}}/logs/php-fpm.error.log
{{CUSTOM_DIRECTIVES}}
{{EXT_CONFIG}}
