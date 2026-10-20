server {
    listen 80;
    listen [::]:80;
    server_name {{DOMAIN}} www.{{DOMAIN}};

    # Cho phép xác thực SSL Certbot ACME Challenge qua HTTP
    location ^~ /.well-known/acme-challenge/ {
        root {{DOCUMENT_ROOT}};
        allow all;
        default_type "text/plain";
    }

    # Tự động chuyển hướng sang HTTPS để sử dụng HTTP/2 và HTTP/3
    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    # HTTP/2 qua TLS
    listen 443 ssl;
    listen [::]:443 ssl;

    # HTTP/3 qua QUIC (UDP)
    listen 443 quic;
    listen [::]:443 quic;

    server_name {{DOMAIN}} www.{{DOMAIN}};
    root {{DOCUMENT_ROOT}};
    index index.php index.html index.htm;

    # Bật HTTP/2 và HTTP/3
    http2 on;
    http3 on;

    # Quảng bá giao thức HTTP/3 tới trình duyệt web (nếu bật)
    add_header Alt-Svc 'h3=":443"; ma=86400' always;

    # Chứng chỉ SSL (Tự ký hoặc Let's Encrypt CA)
    ssl_certificate {{SSL_CERT}};
    ssl_certificate_key {{SSL_KEY}};

    # Cấu hình TLS 1.2 & TLS 1.3 tối ưu bảo mật và tốc độ
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

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
