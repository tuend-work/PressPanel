#!/usr/bin/env bash
# ==============================================================================
# WPCloud: db_root_password.sh
# Cập nhật an toàn mật khẩu root MariaDB/MySQL cho cả Terminal & phpMyAdmin
# ==============================================================================
set -uo pipefail

NEW_PASS="${1:-}"
CURRENT_PASS="${2:-}"

if [ -z "$NEW_PASS" ]; then
    echo "[-] Vui lòng cung cấp mật khẩu mới"
    exit 1
fi

# 1. Tìm mật khẩu hiện tại nếu chưa truyền vào
if [ -z "$CURRENT_PASS" ]; then
    if [ -f "/root/.my.cnf" ]; then
        CURRENT_PASS=$(grep -E "^password" /root/.my.cnf | head -n 1 | awk -F'=' '{print $2}' | tr -d ' "')
    elif [ -f "/opt/wpc/conf/wpc.conf" ]; then
        CURRENT_PASS=$(grep -E "^mysql_root_password" /opt/wpc/conf/wpc.conf | head -n 1 | awk -F'=' '{print $2}' | tr -d ' "')
    fi
fi

# 2. Xây dựng các mẫu lệnh SQL (MariaDB native password & Standard MySQL 8)
# Escape an toàn dấu nháy đơn và gạch chéo ngược cho câu lệnh SQL
SAFE_SQL_PASS="${NEW_PASS//\\/\\\\}"
SAFE_SQL_PASS="${SAFE_SQL_PASS//\'/\'\'}"

SQL_MARIADB=$(mktemp)
cat <<EOF > "$SQL_MARIADB"
ALTER USER 'root'@'localhost' IDENTIFIED VIA mysql_native_password USING PASSWORD('$SAFE_SQL_PASS');
CREATE USER IF NOT EXISTS 'root'@'127.0.0.1' IDENTIFIED VIA mysql_native_password USING PASSWORD('$SAFE_SQL_PASS');
ALTER USER 'root'@'127.0.0.1' IDENTIFIED VIA mysql_native_password USING PASSWORD('$SAFE_SQL_PASS');
CREATE USER IF NOT EXISTS 'root'@'::1' IDENTIFIED VIA mysql_native_password USING PASSWORD('$SAFE_SQL_PASS');
ALTER USER 'root'@'::1' IDENTIFIED VIA mysql_native_password USING PASSWORD('$SAFE_SQL_PASS');
GRANT ALL PRIVILEGES ON *.* TO 'root'@'localhost' WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON *.* TO 'root'@'127.0.0.1' WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON *.* TO 'root'@'::1' WITH GRANT OPTION;
FLUSH PRIVILEGES;
EOF

SQL_STANDARD=$(mktemp)
cat <<EOF > "$SQL_STANDARD"
CREATE USER IF NOT EXISTS 'root'@'localhost' IDENTIFIED BY '$SAFE_SQL_PASS';
ALTER USER 'root'@'localhost' IDENTIFIED BY '$SAFE_SQL_PASS';
CREATE USER IF NOT EXISTS 'root'@'127.0.0.1' IDENTIFIED BY '$SAFE_SQL_PASS';
ALTER USER 'root'@'127.0.0.1' IDENTIFIED BY '$SAFE_SQL_PASS';
CREATE USER IF NOT EXISTS 'root'@'::1' IDENTIFIED BY '$SAFE_SQL_PASS';
ALTER USER 'root'@'::1' IDENTIFIED BY '$SAFE_SQL_PASS';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'localhost' WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON *.* TO 'root'@'127.0.0.1' WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON *.* TO 'root'@'::1' WITH GRANT OPTION;
FLUSH PRIVILEGES;
EOF

# 3. Hàm thực thi SQL với các client và phương thức kết nối
execute_sql_file() {
    local target_file="$1"
    
    # Cách 1: debian-sys-maint trên Debian / Ubuntu (đặc quyền tối cao hệ thống)
    if [ -f "/etc/mysql/debian.cnf" ]; then
        if mysql --defaults-file=/etc/mysql/debian.cnf < "$target_file" 2>/dev/null; then
            return 0
        fi
    fi

    # Cách 2: File cấu hình /root/.my.cnf
    if [ -f "/root/.my.cnf" ]; then
        if mysql --defaults-file=/root/.my.cnf < "$target_file" 2>/dev/null; then
            return 0
        fi
    fi

    # Cách 3: Socket trực tiếp không mật khẩu (root UID 0 qua unix_socket)
    if mysql -u root < "$target_file" 2>/dev/null; then
        return 0
    elif mariadb -u root < "$target_file" 2>/dev/null; then
        return 0
    fi

    # Cách 4: Sử dụng mật khẩu hiện tại nếu có
    if [ -n "$CURRENT_PASS" ]; then
        if mysql -u root -p"$CURRENT_PASS" < "$target_file" 2>/dev/null; then
            return 0
        elif mariadb -u root -p"$CURRENT_PASS" < "$target_file" 2>/dev/null; then
            return 0
        fi
    fi

    # Cách 5: Thử chính mật khẩu mới (trường hợp terminal đã đổi sang mật khẩu mới trước đó)
    if mysql -u root -p"$NEW_PASS" < "$target_file" 2>/dev/null; then
        return 0
    elif mariadb -u root -p"$NEW_PASS" < "$target_file" 2>/dev/null; then
        return 0
    fi

    return 1
}

SUCCESS=0

# Thử với cú pháp MariaDB mysql_native_password trước (đặc trị lỗi unix_socket chặn phpMyAdmin)
if execute_sql_file "$SQL_MARIADB"; then
    SUCCESS=1
# Nếu không thành công (ví dụ trên MySQL 8.0/8.4), thử cú pháp Standard
elif execute_sql_file "$SQL_STANDARD"; then
    SUCCESS=1
fi

rm -f "$SQL_MARIADB" "$SQL_STANDARD"

if [ "$SUCCESS" -eq 1 ]; then
    # 4. Ghi file /root/.my.cnf để CLI mysql / mysqldump luôn hoạt động
    cat <<EOF > /root/.my.cnf
[client]
user = root
password = "$NEW_PASS"
host = localhost

[mysql]
user = root
password = "$NEW_PASS"

[mysqldump]
user = root
password = "$NEW_PASS"
EOF
    chmod 600 /root/.my.cnf 2>/dev/null || true

    # 5. Cập nhật /opt/wpc/conf/wpc.conf nếu tồn tại
    if [ -f "/opt/wpc/conf/wpc.conf" ]; then
        if grep -q "^mysql_root_password" /opt/wpc/conf/wpc.conf; then
            sed -i "s/^mysql_root_password[[:space:]]*=.*/mysql_root_password = $NEW_PASS/" /opt/wpc/conf/wpc.conf
        else
            echo "mysql_root_password = $NEW_PASS" >> /opt/wpc/conf/wpc.conf
        fi
    fi

    # 6. Đảm bảo cấu hình phpMyAdmin (127.0.0.1) và dọn sạch session cũ
    PMA_DIR="/opt/wpc/apps/phpmyadmin"
    if [ -d "$PMA_DIR" ]; then
        # Xóa các file session cũ trong thư mục tạm của phpMyAdmin để tránh kẹt phiên đăng nhập cũ
        rm -f "$PMA_DIR/tmp/sess_"* 2>/dev/null || true
        chmod 777 "$PMA_DIR/tmp" 2>/dev/null || true

        # Dọn dẹp symlink tạm cũ trong /tmp nếu có để bảo vệ an toàn
        rm -f /tmp/mysql.sock 2>/dev/null || true
    fi

    echo "[+] Đã cập nhật mật khẩu root MariaDB/MySQL cho cả Terminal SSH và phpMyAdmin thành công!"
    exit 0
else
    echo "[-] Không thể kết nối MariaDB/MySQL để đổi mật khẩu root. Vui lòng kiểm tra lại mật khẩu hiện tại hoặc dịch vụ CSDL."
    exit 1
fi
