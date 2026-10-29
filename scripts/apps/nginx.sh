#!/usr/bin/env bash
# WPCloud: nginx.sh - Cài đặt Nginx Web Server / OpenResty (hỗ trợ Debian, Ubuntu, AlmaLinux, RHEL, Rocky)
set -euo pipefail

ACTION="${1:-install}"
VERSION="${2:-1.26.x}"

function configure_openresty_env() {
    echo "[+] Thiết lập môi trường và cấu hình thông minh cho OpenResty..."
    
    # 1. Xác định worker user chuyên dụng cho Nginx/OpenResty
    local WORKER_USER="nginx"
    if command -v apt-get &>/dev/null; then
        WORKER_USER="www-data"
    else
        if ! id -u nginx &>/dev/null; then
            useradd -r -s /sbin/nologin -d /var/cache/nginx -c "Nginx Web Server" nginx 2>/dev/null || true
        fi
    fi

    # 2. Cấu hình file nginx.conf của OpenResty
    local OPENRESTY_CONF="/usr/local/openresty/nginx/conf/nginx.conf"
    if [ -f "$OPENRESTY_CONF" ]; then
        # Thiết lập worker process chạy bằng user chuyên biệt (nginx/www-data) thay vì nobody/root
        if grep -qE "^\s*user\s+" "$OPENRESTY_CONF"; then
            sed -i -E "s/^\s*user\s+[^;]+;/user ${WORKER_USER};/" "$OPENRESTY_CONF"
        else
            sed -i "1s/^/user ${WORKER_USER};\n/" "$OPENRESTY_CONF"
        fi

        # Tạo thư mục vhosts chuẩn /etc/nginx/conf.d
        mkdir -p /etc/nginx/conf.d

        # Tự động include /etc/nginx/conf.d/*.conf vào khối http {}
        if ! grep -q "/etc/nginx/conf.d/\*\.conf" "$OPENRESTY_CONF"; then
            sed -i '/http\s*{/a \    include /etc/nginx/conf.d/*.conf;' "$OPENRESTY_CONF"
        fi
    fi

    # 3. Tạo symlink thực thi nhị phân /usr/sbin/nginx
    if [ -x "/usr/local/openresty/nginx/sbin/nginx" ]; then
        ln -sf /usr/local/openresty/nginx/sbin/nginx /usr/sbin/nginx 2>/dev/null || true
    fi

    # 4. Tạo alias systemd nginx.service trỏ vào openresty.service để lệnh nginx/openresty dùng chung
    local SVC_SRC=""
    for p in "/usr/lib/systemd/system/openresty.service" "/lib/systemd/system/openresty.service"; do
        if [ -f "$p" ]; then
            SVC_SRC="$p"
            break
        fi
    done
    if [ -n "$SVC_SRC" ]; then
        ln -sf "$SVC_SRC" /etc/systemd/system/nginx.service 2>/dev/null || true
        systemctl daemon-reload 2>/dev/null || true
    fi

    # 5. Phân quyền worker user vào nhóm hosting để đọc static files
    for user_home in /home/*; do
        [ -d "$user_home" ] || continue
        local u_name
        u_name=$(basename "$user_home")
        if getent group "$u_name" &>/dev/null; then
            usermod -a -G "$u_name" "$WORKER_USER" 2>/dev/null || true
        fi
    done
}

case "$ACTION" in
    install|switch|reinstall|update)
        echo "[+] Đang thực thi $ACTION Nginx / OpenResty ($VERSION) kèm gói tăng tốc WordPress tối đa..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            # Dọn dẹp PPA nginx lỗi nếu từng được thêm trước đó
            rm -f /etc/apt/sources.list.d/*ondrej*nginx*.list 2>/dev/null || true
            apt-get update -y || true
        elif command -v dnf &>/dev/null; then
            dnf check-update -y 2>/dev/null || true
            dnf install -y epel-release 2>/dev/null || true
        elif command -v yum &>/dev/null; then
            yum check-update -y 2>/dev/null || true
            yum install -y epel-release 2>/dev/null || true
        fi

        WORKER_USER="nginx"
        if command -v apt-get &>/dev/null; then
            WORKER_USER="www-data"
        else
            id -u nginx &>/dev/null || useradd -r -s /sbin/nologin -d /var/cache/nginx -c "Nginx Web Server" nginx 2>/dev/null || true
        fi

        if [ "$VERSION" = "openresty" ]; then
            # 1. Chuyển đổi sang OpenResty: Dừng Nginx chuẩn nếu đang chạy
            systemctl stop nginx 2>/dev/null || true
            systemctl disable nginx 2>/dev/null || true
            pkill -9 -f "nginx: master" 2>/dev/null || true

            if command -v apt-get &>/dev/null; then
                export DEBIAN_FRONTEND=noninteractive
                apt-get install -y --no-install-recommends wget gnupg ca-certificates lsb-release || true
                wget -O - https://openresty.org/package/pubkey.gpg | gpg --dearmor -o /etc/apt/trusted.gpg.d/openresty.gpg 2>/dev/null || true
                CODENAME=$(lsb_release -sc 2>/dev/null || echo "jammy")
                echo "deb http://openresty.org/package/ubuntu $CODENAME main" > /etc/apt/sources.list.d/openresty.list 2>/dev/null || true
                apt-get update -y || true
                apt-get install -y openresty openresty-resty openresty-opm || apt-get install -y openresty
            elif command -v dnf &>/dev/null; then
                dnf install -y yum-utils || true
                yum-config-manager --add-repo https://openresty.org/package/centos/openresty.repo 2>/dev/null || true
                dnf install -y openresty openresty-resty openresty-opm || dnf install -y openresty
            fi

            # Áp dụng cấu hình và user tối ưu
            configure_openresty_env

            systemctl enable openresty 2>/dev/null || true
            systemctl enable nginx 2>/dev/null || true
            systemctl restart openresty 2>/dev/null || systemctl restart nginx 2>/dev/null || true
        else
            # 2. Chuyển đổi sang Nginx chuẩn kèm trọn bộ Modules tăng tốc WordPress
            systemctl stop openresty 2>/dev/null || true
            systemctl disable openresty 2>/dev/null || true
            pkill -9 -f "openresty" 2>/dev/null || true
            
            # Xóa symlink đè lên Nginx service
            rm -f /etc/systemd/system/nginx.service 2>/dev/null || true
            [ -L "/usr/sbin/nginx" ] && rm -f /usr/sbin/nginx 2>/dev/null || true
            systemctl daemon-reload 2>/dev/null || true

            echo "[+] Đang cài đặt Nginx cùng các module tăng tốc tối đa (Brotli, Cache-Purge, Headers-More, Redis2, Srcache)..."
            if command -v apt-get &>/dev/null; then
                export DEBIAN_FRONTEND=noninteractive
                # Cài đặt Nginx và các module tăng tốc hiệu năng WordPress
                apt-get install -y --reinstall nginx nginx-common nginx-core \
                    libnginx-mod-http-brotli-filter libnginx-mod-http-brotli-static \
                    libnginx-mod-http-cache-purge libnginx-mod-http-headers-more-filter \
                    libnginx-mod-http-redis2 libnginx-mod-http-srcache-filter 2>/dev/null || \
                apt-get install -y nginx nginx-extras 2>/dev/null || \
                apt-get install -y nginx

                # Cài đặt đồng thời các PHP runtime extensions tối ưu WordPress nếu có PHP
                apt-get install -y php-opcache php-redis php-imagick php-curl php-mysql php-intl php-mbstring php-xml php-zip 2>/dev/null || true
            elif command -v dnf &>/dev/null; then
                dnf install -y nginx nginx-all-modules nginx-mod-brotli nginx-mod-http-cache-purge 2>/dev/null || dnf install -y nginx
            fi
            
            mkdir -p /etc/nginx/conf.d

            # 2.1 CẤU HÌNH FASTCGI CACHE ZONE CHUYÊN DỤNG CHO WORDPRESS
            echo "[+] Thiết lập vùng nhớ đệm FastCGI Cache cho WordPress (Plugin Nginx Helper / FastCGI Microcache)..."
            mkdir -p /var/cache/nginx/wp
            chown -R "$WORKER_USER:$WORKER_USER" /var/cache/nginx 2>/dev/null || true
            chmod 700 /var/cache/nginx/wp 2>/dev/null || true

            cat << 'EOF_CACHE' > /etc/nginx/conf.d/00_wp_fastcgi_cache.conf
# WPCloud High Performance FastCGI Cache Zone for WordPress
fastcgi_cache_path /var/cache/nginx/wp levels=1:2 keys_zone=WORDPRESS:100m inactive=60m max_size=1g;
fastcgi_cache_key "$scheme$request_method$host$request_uri";
fastcgi_cache_use_stale error timeout updating invalid_header http_500 http_503;
fastcgi_ignore_headers Cache-Control Expires Set-Cookie;
EOF_CACHE

            # Dọn dẹp các tệp tối ưu thừa có thể gây xung đột buffer/directive với các vhost
            rm -f /etc/nginx/conf.d/00_wp_optimization.conf 2>/dev/null || true
            rm -f /etc/nginx/conf.d/00_wp_compression.conf 2>/dev/null || true

            # 2.3 CẤU HÌNH BROTLI NẾU ĐƯỢC HỖ TRỢ VÀ VALIDATE CÚ PHÁP
            HAS_BR="no"
            if nginx -V 2>&1 | grep -qE "ngx_brotli|brotli"; then
                HAS_BR="yes"
            elif ls /usr/lib/nginx/modules/*brotli* &>/dev/null || ls /etc/nginx/modules-enabled/*brotli* &>/dev/null; then
                HAS_BR="yes"
            fi

            if [ "$HAS_BR" = "yes" ]; then
                echo "[+] Phát hiện module ngx_brotli: Cấu hình nén Google Brotli an toàn..."
                cat << 'EOF_BR' > /etc/nginx/conf.d/00_wp_brotli.conf
# WPCloud Google Brotli Compression Engine
brotli on;
brotli_comp_level 6;
brotli_types text/plain text/css text/xml text/javascript application/json application/javascript application/x-javascript application/xml application/xml+rss application/rss+xml application/atom+xml image/svg+xml;
EOF_BR
                # Kiểm tra cú pháp, nếu brotli directive chưa được compile thì tự động dọn dẹp
                if ! nginx -t 2>/dev/null; then
                    echo "[-] Module Brotli chưa được nạp vào Nginx core, tự động loại bỏ để đảm bảo ổn định."
                    rm -f /etc/nginx/conf.d/00_wp_brotli.conf 2>/dev/null || true
                fi
            else
                rm -f /etc/nginx/conf.d/00_wp_brotli.conf 2>/dev/null || true
            fi

            # 2.4 TỰ ĐỘNG TỐI ƯU CÁC VHOST HIỆN CÓ VÀ WARM WORKER PHP-FPM
            echo "[+] Đang tối ưu kết nối FastCGI Keepalive và Warm Workers PHP-FPM..."
            
            # Dọn dẹp cấu hình thừa có thể gây lỗi
            rm -f /etc/nginx/conf.d/00_wp_compression.conf /etc/nginx/conf.d/00_wp_optimization.conf /etc/nginx/sites-enabled/00_phpmyadmin.conf 2>/dev/null || true

            # Tối ưu FastCGI Keep-Alive & TCP Nodelay cho toàn bộ VirtualHost hiện có
            for vf in /etc/nginx/conf.d/*.conf /etc/nginx/sites-available/*; do
                [ -f "$vf" ] || continue
                # Bổ sung fastcgi_keep_conn on nếu chưa có
                if grep -q "fastcgi_pass" "$vf" && ! grep -q "fastcgi_keep_conn" "$vf"; then
                    sed -i '/fastcgi_pass/a \        fastcgi_keep_conn on;' "$vf" 2>/dev/null || true
                fi
                # Bổ sung tcp_nodelay on nếu chưa có
                if grep -q "server {" "$vf" && ! grep -q "tcp_nodelay" "$vf"; then
                    sed -i '/server {/a \    tcp_nodelay on;' "$vf" 2>/dev/null || true
                fi
                # Loại bỏ Alt-Svc gây delay 200ms trên local / unverified QUIC nếu có
                sed -i '/Alt-Svc/d' "$vf" 2>/dev/null || true
            done

            # Tối ưu các PHP-FPM Pool: Giữ tối thiểu 2-4 worker luôn thường trực (Warm Workers) tránh độ trễ 200ms khi fork
            for pf in /etc/php/*/fpm/pool.d/*.conf /etc/php-fpm.d/*.conf; do
                [ -f "$pf" ] || continue
                sed -i 's/pm.process_idle_timeout = .*/pm.process_idle_timeout = 60s/' "$pf" 2>/dev/null || true
                sed -i 's/pm.min_spare_servers = 1/pm.min_spare_servers = 2/' "$pf" 2>/dev/null || true
                sed -i 's/pm.start_servers = 2/pm.start_servers = 4/' "$pf" 2>/dev/null || true
                sed -i 's/pm.max_requests = 500/pm.max_requests = 2000/' "$pf" 2>/dev/null || true
            done

            # Khởi động lại các dịch vụ PHP-FPM để áp dụng ngay
            systemctl restart "php*-fpm" 2>/dev/null || systemctl restart php-fpm 2>/dev/null || true

            systemctl unmask nginx 2>/dev/null || true
            systemctl enable nginx 2>/dev/null || true

            # Kiểm tra cú pháp an toàn trước khi restart
            if nginx -t 2>/dev/null; then
                systemctl restart nginx 2>/dev/null || true
            else
                # Tự động khắc phục nếu có directive không tương thích
                sed -i -E '/^[[:space:]]*brotli/d' /etc/nginx/conf.d/*.conf 2>/dev/null || true
                systemctl restart nginx 2>/dev/null || true
            fi
        fi
        echo "[+] $ACTION Nginx ($VERSION) kèm gói tăng tốc WordPress hoàn tất thành công!"
        ;;

    configure)
        configure_openresty_env
        systemctl restart openresty 2>/dev/null || systemctl restart nginx 2>/dev/null || true
        echo "[+] Đã cấu hình và khởi động lại OpenResty/Nginx thành công!"
        ;;

    build)
        echo "[+] Đang chuẩn bị biên dịch Nginx từ source với các đối số tùy chỉnh..."
        ARGS_FILE="/opt/wpc/config/build/nginx.args"
        BUILD_ARGS=""
        if [ -f "$ARGS_FILE" ]; then
            BUILD_ARGS=$(grep -v '^[[:space:]]*#' "$ARGS_FILE" | tr '\n' ' ' | sed 's/\\//g')
        fi
        if [ -z "$BUILD_ARGS" ]; then
            BUILD_ARGS="--prefix=/etc/nginx --sbin-path=/usr/sbin/nginx --modules-path=/usr/lib/nginx/modules --conf-path=/etc/nginx/nginx.conf --error-log-path=/var/log/nginx/error.log --http-log-path=/var/log/nginx/access.log --pid-path=/var/run/nginx.pid --lock-path=/var/run/nginx.lock --with-http_ssl_module --with-http_v2_module --with-http_v3_module --with-http_realip_module --with-http_gzip_static_module --with-http_stub_status_module --with-threads --with-stream --with-stream_ssl_module --with-pcre --with-pcre-jit"
        fi

        echo "[+] Cài đặt các thư viện phụ thuộc (build tools & libraries)..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -y || true
            apt-get install -y build-essential libpcre3 libpcre3-dev zlib1g zlib1g-dev libssl-dev libxml2-dev libxslt1-dev libgd-dev libgeoip-dev curl wget tar || true
        elif command -v dnf &>/dev/null || command -v yum &>/dev/null; then
            PKG_MGR="dnf"
            command -v dnf &>/dev/null || PKG_MGR="yum"
            $PKG_MGR install -y gcc gcc-c++ make pcre pcre-devel zlib zlib-devel openssl openssl-devel libxml2-devel libxslt-devel gd-devel curl wget tar || true
        fi

        NGINX_VER="1.26.2"
        BUILD_DIR="/tmp/nginx_src_build"
        rm -rf "$BUILD_DIR"
        mkdir -p "$BUILD_DIR"
        cd "$BUILD_DIR"

        echo "[+] Đang tải mã nguồn Nginx $NGINX_VER..."
        if ! curl -sSL "https://nginx.org/download/nginx-${NGINX_VER}.tar.gz" -o nginx.tar.gz; then
            wget -q "https://nginx.org/download/nginx-${NGINX_VER}.tar.gz" -O nginx.tar.gz || true
        fi

        if [ ! -f "nginx.tar.gz" ] || [ ! -s "nginx.tar.gz" ]; then
            echo "[-] Lỗi: Không thể tải mã nguồn Nginx từ nginx.org!"
            exit 1
        fi

        tar -xzf nginx.tar.gz
        cd "nginx-${NGINX_VER}"

        echo "[+] Cấu hình Configure với các đối số:"
        echo "$BUILD_ARGS"
        eval ./configure $BUILD_ARGS

        echo "[+] Tiến hành biên dịch (make -j$(nproc))..."
        make -j"$(nproc)"

        echo "[+] Sao lưu nhị phân hiện tại và cài đặt nhị phân mới..."
        [ -x "/usr/sbin/nginx" ] && cp -f "/usr/sbin/nginx" "/usr/sbin/nginx.bak.$(date +%s)" 2>/dev/null || true

        # Cài đặt binary mới vào /usr/sbin/nginx
        if [ -f "objs/nginx" ]; then
            cp -f "objs/nginx" "/usr/sbin/nginx"
            chmod 755 "/usr/sbin/nginx"
        else
            make install
        fi

        # Dọn dẹp mã nguồn tạm
        rm -rf "$BUILD_DIR"

        # Kiểm tra cú pháp nginx -t
        echo "[+] Kiểm tra tính toàn vẹn cú pháp:"
        nginx -V 2>&1 | head -n 5 || true
        nginx -t

        # Khởi động lại service
        systemctl daemon-reload 2>/dev/null || true
        systemctl restart nginx 2>/dev/null || true

        echo "[+] Quá trình build Nginx với đối số tùy chỉnh hoàn tất thành công!"
        ;;

    remove)
        echo "[+] Đang gỡ bỏ Nginx / OpenResty hoàn toàn..."
        systemctl stop openresty 2>/dev/null || true
        systemctl stop nginx 2>/dev/null || true
        systemctl disable openresty 2>/dev/null || true
        systemctl disable nginx 2>/dev/null || true
        pkill -9 -f openresty 2>/dev/null || true
        pkill -9 -f nginx 2>/dev/null || true

        echo "[+] Gỡ bỏ các gói phần mềm khỏi hệ điều hành..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get purge -y openresty openresty-resty openresty-opm nginx nginx-common nginx-core nginx-full 2>/dev/null || apt-get remove -y nginx openresty 2>/dev/null || true
            apt-get autoremove -y 2>/dev/null || true
        elif command -v dnf &>/dev/null; then
            dnf remove -y openresty openresty-resty openresty-opm nginx 2>/dev/null || true
        elif command -v yum &>/dev/null; then
            yum remove -y openresty openresty-resty openresty-opm nginx 2>/dev/null || true
        fi

        echo "[+] Dọn dẹp thư mục nhị phân và symlink thực thi..."
        rm -rf /usr/local/openresty
        rm -f /usr/sbin/nginx /usr/bin/nginx
        rm -f /etc/systemd/system/nginx.service /etc/systemd/system/openresty.service
        rm -f /etc/apt/sources.list.d/openresty.list /etc/yum.repos.d/openresty.repo
        systemctl daemon-reload 2>/dev/null || true

        echo "[+] Đã gỡ bỏ Nginx / OpenResty thành công!"
        ;;

    *)
        echo "[-] Lệnh không hợp lệ"
        exit 1
        ;;
esac
