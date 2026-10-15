server {
    listen 80;
    server_name {{DOMAIN}} www.{{DOMAIN}};
    root {{DOCUMENT_ROOT}};
    index index.php index.html index.htm;

    access_log /home/{{USERNAME}}/domains/{{DOMAIN}}/logs/access.log;
    error_log /home/{{USERNAME}}/domains/{{DOMAIN}}/logs/error.log;

    location / {
        try_files $uri $uri/ /index.php?$args;
    }

    # Xử lý PHP qua PHP-FPM socket hoặc TCP
    location ~ \.php$ {
        try_files $uri =404;
        fastcgi_split_path_info ^(.+\.php)(/.+)$;
        fastcgi_pass unix:{{PHP_SOCK}};
        fastcgi_index index.php;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        include fastcgi_params;
        fastcgi_read_timeout 300;
        fastcgi_buffer_size 128k;
        fastcgi_buffers 256 16k;
        fastcgi_busy_buffers_size 256k;
        fastcgi_temp_file_write_size 256k;
    }

    # Bảo mật: Cấm truy cập các file ẩn, .htaccess, .user.ini
    location ~ /\.(?!well-known).* {
        deny all;
    }

    location ~* /(?:uploads|files)/.*\.php$ {
        deny all;
    }
}
