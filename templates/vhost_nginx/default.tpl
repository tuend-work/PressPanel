server {
    listen 80;
    server_name {{DOMAIN}} www.{{DOMAIN}};
    root {{DOCUMENT_ROOT}};
    index index.php index.html index.htm;

    access_log /home/{{USERNAME}}/domains/{{DOMAIN}}/logs/access.log;
    error_log /home/{{USERNAME}}/domains/{{DOMAIN}}/logs/error.log;

    # ========================================================
    # 1. HTTP Security Headers chuẩn OWASP
    # ========================================================
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "geolocation=(), camera=(), microphone=()" always;

    # ========================================================
    # 2. Giới hạn HTTP Request Methods an toàn
    # ========================================================
    if ($request_method !~ ^(GET|POST|HEAD|OPTIONS|PUT|DELETE)$) {
        return 405;
    }

    location / {
        try_files $uri $uri/ /index.php?$args;
    }

    # ========================================================
    # 3. Xử lý PHP qua PHP-FPM socket độc lập của User
    # ========================================================
    location ~ \.php$ {
        try_files $uri =404;
        fastcgi_split_path_info ^(.+\.php)(/.+)$;
        fastcgi_pass unix:{{PHP_SOCK}};
        fastcgi_index index.php;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        fastcgi_param HTTP_PROXY ""; # Chống HTTPoxy Vulnerability
        include fastcgi_params;
        fastcgi_read_timeout 300;
        fastcgi_buffer_size 128k;
        fastcgi_buffers 256 16k;
        fastcgi_busy_buffers_size 256k;
        fastcgi_temp_file_write_size 256k;
    }

    # ========================================================
    # 4. Bảo mật: Cấm truy cập các file ẩn (trừ Let's Encrypt)
    # ========================================================
    location ~ /\.(?!well-known).* {
        deny all;
        access_log off;
        log_not_found off;
    }

    # ========================================================
    # 5. Chặn truy cập file cấu hình nhạy cảm và source code
    # ========================================================
    location ~* /(?:wp-config\.php|\.env|\.git|\.svn|composer\.(?:json|lock)|package\.json|\.sql|\.bak|\.log) {
        deny all;
        access_log off;
        log_not_found off;
    }

    # ========================================================
    # 6. Chống thực thi PHP webshell trong thư mục tĩnh / uploads
    # ========================================================
    location ~* /(?:uploads|files|wp-content/uploads|cache)/.*\.php$ {
        deny all;
    }

    # ========================================================
    # 7. Chặn XML-RPC chống tấn công DDoS & Brute-force WordPress
    # ========================================================
    location = /xmlrpc.php {
        deny all;
        access_log off;
        log_not_found off;
    }
}
