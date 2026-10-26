; ==============================================================================
; WPC Standalone PHP-FPM Service Configuration
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
php_admin_value[disable_functions] = {{PHP_DISABLE_FUNCTIONS}}
php_admin_flag[log_errors] = on
php_admin_value[error_log] = /home/{{USERNAME}}/logs/php-fpm.error.log
{{CUSTOM_DIRECTIVES}}
{{EXT_CONFIG}}
