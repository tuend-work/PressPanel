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
    install)
        echo "[+] Đang cài đặt Nginx / OpenResty ($VERSION)..."
        if [ "$VERSION" = "openresty" ]; then
            # Dừng Nginx thường nếu đang chiếm cổng 80
            systemctl stop nginx 2>/dev/null || true
            systemctl disable nginx 2>/dev/null || true

            if command -v apt-get &>/dev/null; then
                export DEBIAN_FRONTEND=noninteractive
                apt-get update -y || true
                apt-get install -y --no-install-recommends wget gnupg ca-certificates lsb-release || true
                wget -O - https://openresty.org/package/pubkey.gpg | gpg --dearmor -o /etc/apt/trusted.gpg.d/openresty.gpg 2>/dev/null || true
                CODENAME=$(lsb_release -sc 2>/dev/null || echo "jammy")
                echo "deb http://openresty.org/package/ubuntu $CODENAME main" > /etc/apt/sources.list.d/openresty.list 2>/dev/null || true
                apt-get update -y || true
                apt-get install -y openresty || apt-get install -y nginx
            elif command -v dnf &>/dev/null; then
                dnf install -y yum-utils || true
                yum-config-manager --add-repo https://openresty.org/package/centos/openresty.repo 2>/dev/null || true
                dnf install -y openresty || dnf install -y nginx
            fi

            # Áp dụng cấu hình và user tối ưu
            configure_openresty_env

            systemctl enable openresty 2>/dev/null || true
            systemctl enable nginx 2>/dev/null || true
            systemctl restart openresty 2>/dev/null || systemctl restart nginx 2>/dev/null || true
        else
            if command -v apt-get &>/dev/null; then
                export DEBIAN_FRONTEND=noninteractive
                apt-get update -y || true
                apt-get install -y nginx
            elif command -v dnf &>/dev/null; then
                dnf install -y nginx
            fi
            systemctl enable nginx 2>/dev/null || true
            systemctl restart nginx 2>/dev/null || true
        fi
        echo "[+] Cài đặt Nginx ($VERSION) hoàn tất thành công!"
        ;;

    configure)
        configure_openresty_env
        systemctl restart openresty 2>/dev/null || systemctl restart nginx 2>/dev/null || true
        echo "[+] Đã cấu hình và khởi động lại OpenResty/Nginx thành công!"
        ;;

    build)
        echo "[+] Đang chuẩn bị biên dịch Nginx từ source với các đối số tùy chỉnh..."
        local ARGS_FILE="/opt/wpc/config/build/nginx.args"
        local BUILD_ARGS=""
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

        local NGINX_VER="1.26.2"
        local BUILD_DIR="/tmp/nginx_src_build"
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
        echo "[+] Đang gỡ bỏ Nginx / OpenResty..."
        systemctl stop openresty 2>/dev/null || true
        systemctl stop nginx 2>/dev/null || true
        systemctl disable openresty 2>/dev/null || true
        systemctl disable nginx 2>/dev/null || true
        rm -f /etc/systemd/system/nginx.service 2>/dev/null || true
        systemctl daemon-reload 2>/dev/null || true
        if command -v apt-get &>/dev/null; then
            apt-get remove -y nginx openresty 2>/dev/null || true
        elif command -v dnf &>/dev/null; then
            dnf remove -y nginx openresty 2>/dev/null || true
        fi
        echo "[+] Đã gỡ bỏ Nginx thành công!"
        ;;

    *)
        echo "[-] Lệnh không hợp lệ"
        exit 1
        ;;
esac
