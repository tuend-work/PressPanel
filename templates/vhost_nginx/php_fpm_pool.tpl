[{{USERNAME}}]
user = {{USERNAME}}
group = {{USERNAME}}

listen = /run/php/php{{PHP_VER}}-fpm-{{USERNAME}}.sock
listen.owner = {{WORKER_USER}}
listen.group = {{WORKER_GRP}}
listen.mode = 0666

pm = dynamic
pm.max_children = 10
pm.start_servers = 2
pm.min_spare_servers = 1
pm.max_spare_servers = 3
pm.max_requests = 500

pm.status_path = /status-{{USERNAME}}
ping.path = /ping-{{USERNAME}}

; Tách biệt môi trường và bảo vệ thư mục gốc của user
php_admin_value[open_basedir] = /home/{{USERNAME}}/:/tmp/:/var/tmp/
php_admin_value[upload_tmp_dir] = /home/{{USERNAME}}/.tmp
php_admin_value[session.save_path] = /home/{{USERNAME}}/.sessions
php_admin_value[disable_functions] = exec,passthru,shell_exec,system,proc_open,popen,curl_multi_exec,parse_ini_file,show_source
php_admin_flag[log_errors] = on
php_admin_value[error_log] = /home/{{USERNAME}}/logs/php-fpm.error.log
