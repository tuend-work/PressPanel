#!/usr/bin/env bash
# ==============================================================================
# WPCloud: wp_manager.sh - Bộ điều khiển WordPress Manager chuyên sâu
# Thực thi các tác vụ WordPress: Cài đặt, Bảo mật, Dọn dẹp, Tải Zip trực tiếp, SSO
# Được thực thi an toàn bởi C Gatekeeper SUID: /opt/wpc/bin/wp
# ==============================================================================

set -uo pipefail

ACTION="${1:-}"
USERNAME="${2:-}"
DOMAIN="${3:-}"

# Xác định document root chính xác của website
TARGET_DIR="/home/$USERNAME/domains/$DOMAIN/public_html"
if [ ! -d "$TARGET_DIR" ] && [ -d "/home/$USERNAME/httpdocs/$DOMAIN" ]; then
    TARGET_DIR="/home/$USERNAME/httpdocs/$DOMAIN"
fi
if [ ! -d "$TARGET_DIR" ] && [ -d "/home/$USERNAME/public_html" ]; then
    TARGET_DIR="/home/$USERNAME/public_html"
fi

get_wp_config_val() {
    local key="$1"
    local file="$TARGET_DIR/wp-config.php"
    if [ -f "$file" ]; then
        grep -E "define\(\s*['\"]$key['\"]" "$file" 2>/dev/null | head -n 1 | sed -E "s/.*['\"]$key['\"]\s*,\s*['\"]([^'\"]*)['\"].*/\1/"
    fi
}

get_wp_prefix() {
    local file="$TARGET_DIR/wp-config.php"
    if [ -f "$file" ]; then
        grep -E "^\s*\\\$table_prefix\s*=" "$file" 2>/dev/null | head -n 1 | sed -E "s/.*=\s*['\"]([^'\"]*)['\"].*/\1/" || echo "wp_"
    else
        echo "wp_"
    fi
}

case "$ACTION" in
    install)
        TITLE="${4:-My WordPress}"
        ADMIN_USER="${5:-admin}"
        ADMIN_EMAIL="${6:-admin@$DOMAIN}"
        ADMIN_PASS=""
        if IFS= read -r line; then
            ADMIN_PASS="$line"
        fi
        if [ -z "$ADMIN_PASS" ]; then
            ADMIN_PASS="${7:-admin123456}"
        fi

        echo "[+] Bat dau cai dat WordPress cho $DOMAIN (User: $USERNAME)..."
        mkdir -p "$TARGET_DIR"

        # 1. Tải WordPress core ZIP trực tiếp từ wordpress.org (không chạy WP hooks)
        TEMP_ZIP="/tmp/wp_latest_${USERNAME}_$$.zip"
        TEMP_EXTRACT="/tmp/wp_ext_${USERNAME}_$$"
        echo "[+] Dang tai ban WordPress core moi nhat tu wordpress.org..."
        curl -fsSL --connect-timeout 15 --max-time 180 "https://wordpress.org/latest.zip" -o "$TEMP_ZIP" || {
            echo "[-] Khong the tai WordPress zip tu wordpress.org"
            exit 1
        }

        mkdir -p "$TEMP_EXTRACT"
        unzip -q -o "$TEMP_ZIP" -d "$TEMP_EXTRACT"
        cp -rf "$TEMP_EXTRACT"/wordpress/* "$TARGET_DIR/"
        rm -rf "$TEMP_ZIP" "$TEMP_EXTRACT"

        # 2. Tao Database MariaDB
        SAFE_USER_PREFIX=$(echo "$USERNAME" | tr -dc 'a-zA-Z0-9' | cut -c 1-8)
        SAFE_DOM_PREFIX=$(echo "$DOMAIN" | tr -dc 'a-zA-Z0-9' | cut -c 1-8)
        RAND_SUFFIX=$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n' | cut -c 1-4)
        DB_NAME="wp_${SAFE_USER_PREFIX}_${RAND_SUFFIX}"
        DB_USER="usr_${SAFE_USER_PREFIX}_${RAND_SUFFIX}"
        DB_PASS=$(head -c 16 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 16)
        DB_PREFIX="wp_"

        echo "[+] Tao co so du lieu MariaDB: $DB_NAME..."
        mysql -e "CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;" 2>/dev/null || true
        mysql -e "CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';" 2>/dev/null || true
        mysql -e "GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost'; FLUSH PRIVILEGES;" 2>/dev/null || true

        # 3. Tao file wp-config.php chuan an toan
        SALT_KEYS=$(curl -fsSL https://api.wordpress.org/secret-key/1.1/salt/ 2>/dev/null || true)
        if [ -z "$SALT_KEYS" ]; then
            SALT_KEYS="define('AUTH_KEY',         '$(head -c 32 /dev/urandom | base64)');
define('SECURE_AUTH_KEY',  '$(head -c 32 /dev/urandom | base64)');
define('LOGGED_IN_KEY',    '$(head -c 32 /dev/urandom | base64)');
define('NONCE_KEY',        '$(head -c 32 /dev/urandom | base64)');
define('AUTH_SALT',        '$(head -c 32 /dev/urandom | base64)');
define('SECURE_AUTH_SALT', '$(head -c 32 /dev/urandom | base64)');
define('LOGGED_IN_SALT',   '$(head -c 32 /dev/urandom | base64)');
define('NONCE_SALT',       '$(head -c 32 /dev/urandom | base64)');"
        fi

        cat <<EOF > "$TARGET_DIR/wp-config.php"
<?php
/**
 * The base configuration for WordPress
 * Generated automatically by PressPanel (WPCloud)
 */

define( 'DB_NAME', '$DB_NAME' );
define( 'DB_USER', '$DB_USER' );
define( 'DB_PASSWORD', '$DB_PASS' );
define( 'DB_HOST', 'localhost' );
define( 'DB_CHARSET', 'utf8mb4' );
define( 'DB_COLLATE', '' );

$SALT_KEYS

\$table_prefix = '$DB_PREFIX';

/* PressPanel Security & Performance Defaults */
define( 'WP_DEBUG', false );
define( 'WP_DEBUG_LOG', false );
define( 'WP_DEBUG_DISPLAY', false );
define( 'DISALLOW_FILE_EDIT', true );
define( 'FS_METHOD', 'direct' );
define( 'WP_MEMORY_LIMIT', '256M' );
define( 'WP_MAX_MEMORY_LIMIT', '512M' );

if ( ! defined( 'ABSPATH' ) ) {
	define( 'ABSPATH', __DIR__ . '/' );
}

require_once ABSPATH . 'wp-settings.php';
EOF

        # 4. Khoi tao schema WordPress ban dau (WP-CLI an toan vi core sach 100% moi tai tu wp.org)
        if command -v wp &>/dev/null; then
            echo "[+] Khoi tao schema WordPress qua wp core install..."
            wp core install \
                --path="$TARGET_DIR" \
                --url="http://$DOMAIN" \
                --title="$TITLE" \
                --admin_user="$ADMIN_USER" \
                --admin_password="$ADMIN_PASS" \
                --admin_email="$ADMIN_EMAIL" \
                --skip-plugins \
                --skip-themes \
                --allow-root >/dev/null 2>&1 || true
        fi

        # 5. Cai dat MU-Plugins toi uu san neu co
        mkdir -p "$TARGET_DIR/wp-content/mu-plugins"
        if [ -d "/opt/wpc/templates/mu-plugins" ]; then
            cp -f /opt/wpc/templates/mu-plugins/*.php "$TARGET_DIR/wp-content/mu-plugins/" 2>/dev/null || true
        fi

        # 6. Phan quyen chuan Linux
        if id "$USERNAME" &>/dev/null; then
            chown -R "$USERNAME:$USERNAME" "$TARGET_DIR" 2>/dev/null || true
            find "$TARGET_DIR" -type d -exec chmod 755 {} + 2>/dev/null || true
            find "$TARGET_DIR" -type f -exec chmod 644 {} + 2>/dev/null || true
            chmod 600 "$TARGET_DIR/wp-config.php" 2>/dev/null || true
        fi

        echo "[+] Cai dat WordPress thanh cong tren $DOMAIN!"
        exit 0
        ;;

    download_zip)
        TYPE="${4:-plugin}"
        SLUG="${5:-}"
        ZIP_URL="${6:-}"

        if [ -z "$SLUG" ]; then
            echo "[-] Slug khong duoc de trong"
            exit 1
        fi

        # Xác định URL zip nếu không được truyền
        if [ -z "$ZIP_URL" ]; then
            if [ "$TYPE" = "plugin" ]; then
                ZIP_URL="https://downloads.wordpress.org/plugin/${SLUG}.zip"
            elif [ "$TYPE" = "theme" ]; then
                ZIP_URL="https://downloads.wordpress.org/theme/${SLUG}.zip"
            elif [ "$TYPE" = "core" ]; then
                ZIP_URL="https://wordpress.org/latest.zip"
            fi
        fi

        echo "[+] Dang tai $TYPE [$SLUG] tu: $ZIP_URL..."
        TEMP_ZIP="/tmp/wpc_dl_${TYPE}_${SLUG}_$$.zip"
        curl -fsSL --connect-timeout 15 --max-time 180 "$ZIP_URL" -o "$TEMP_ZIP" || {
            echo "[-] Khong the tai file ZIP tu URL: $ZIP_URL"
            rm -f "$TEMP_ZIP"
            exit 1
        }

        # Kiem tra tinh toan ven cua file ZIP
        unzip -tq "$TEMP_ZIP" >/dev/null 2>&1 || {
            echo "[-] File ZIP tai ve bi loi hoac khong dung dinh dang"
            rm -f "$TEMP_ZIP"
            exit 1
        }

        if [ "$TYPE" = "plugin" ]; then
            DEST_DIR="$TARGET_DIR/wp-content/plugins"
            mkdir -p "$DEST_DIR"
            # Xoa thu muc cu de lam sach source sach 100%, diet bo backdoor cu
            if [ -d "$DEST_DIR/$SLUG" ]; then
                echo "[+] Xoa thu muc cu $DEST_DIR/$SLUG de lam sach ma nguon..."
                rm -rf "$DEST_DIR/$SLUG"
            fi
            echo "[+] Giai nen ZIP de vao $DEST_DIR..."
            unzip -q -o "$TEMP_ZIP" -d "$DEST_DIR"
            if id "$USERNAME" &>/dev/null; then
                chown -R "$USERNAME:$USERNAME" "$DEST_DIR/$SLUG" 2>/dev/null || true
                find "$DEST_DIR/$SLUG" -type d -exec chmod 755 {} + 2>/dev/null || true
                find "$DEST_DIR/$SLUG" -type f -exec chmod 644 {} + 2>/dev/null || true
            fi
            echo "[+] Lam sach va cai dat Plugin [$SLUG] thanh cong!"

        elif [ "$TYPE" = "theme" ]; then
            DEST_DIR="$TARGET_DIR/wp-content/themes"
            mkdir -p "$DEST_DIR"
            if [ -d "$DEST_DIR/$SLUG" ]; then
                echo "[+] Xoa thu muc theme cu $DEST_DIR/$SLUG de lam sach..."
                rm -rf "$DEST_DIR/$SLUG"
            fi
            echo "[+] Giai nen ZIP de vao $DEST_DIR..."
            unzip -q -o "$TEMP_ZIP" -d "$DEST_DIR"
            if id "$USERNAME" &>/dev/null; then
                chown -R "$USERNAME:$USERNAME" "$DEST_DIR/$SLUG" 2>/dev/null || true
                find "$DEST_DIR/$SLUG" -type d -exec chmod 755 {} + 2>/dev/null || true
                find "$DEST_DIR/$SLUG" -type f -exec chmod 644 {} + 2>/dev/null || true
            fi
            echo "[+] Lam sach va cai dat Theme [$SLUG] thanh cong!"

        elif [ "$TYPE" = "core" ]; then
            TEMP_CORE="/tmp/wpc_core_extract_$$"
            mkdir -p "$TEMP_CORE"
            unzip -q -o "$TEMP_ZIP" -d "$TEMP_CORE"
            echo "[+] Chep de ma nguon Core WordPress sach (bao ve wp-config.php va wp-content)..."
            cp -rf "$TEMP_CORE"/wordpress/wp-admin "$TARGET_DIR/"
            cp -rf "$TEMP_CORE"/wordpress/wp-includes "$TARGET_DIR/"
            cp -f "$TEMP_CORE"/wordpress/*.php "$TARGET_DIR/" 2>/dev/null || true
            rm -rf "$TEMP_CORE"

            if id "$USERNAME" &>/dev/null; then
                chown -R "$USERNAME:$USERNAME" "$TARGET_DIR/wp-admin" "$TARGET_DIR/wp-includes" 2>/dev/null || true
                find "$TARGET_DIR/wp-admin" "$TARGET_DIR/wp-includes" -type d -exec chmod 755 {} + 2>/dev/null || true
                find "$TARGET_DIR/wp-admin" "$TARGET_DIR/wp-includes" -type f -exec chmod 644 {} + 2>/dev/null || true
            fi
            echo "[+] Lam sach WordPress Core thanh cong!"
        fi

        rm -f "$TEMP_ZIP"
        exit 0
        ;;

    delete_item)
        TYPE="${4:-plugin}"
        SLUG="${5:-}"
        if [ -z "$SLUG" ]; then
            echo "[-] Slug khong hop le"
            exit 1
        fi
        TARGET_ITEM="$TARGET_DIR/wp-content/${TYPE}s/$SLUG"
        if [ -d "$TARGET_ITEM" ]; then
            rm -rf "$TARGET_ITEM"
            echo "[+] Da xoa $TYPE [$SLUG] thanh cong"
        else
            echo "[-] Khong tim thay $TYPE de xoa: $TARGET_ITEM"
        fi
        exit 0
        ;;

    lock)
        echo "[+] Kich hoat WordPress Lockdown (chattr +i) tai $TARGET_DIR..."
        if [ -f "$TARGET_DIR/wp-config.php" ]; then
            chmod 0444 "$TARGET_DIR/wp-config.php" 2>/dev/null || true
            chattr +i "$TARGET_DIR/wp-config.php" 2>/dev/null || true
        fi
        if [ -d "$TARGET_DIR/wp-admin" ]; then
            chattr -R +i "$TARGET_DIR/wp-admin" 2>/dev/null || true
        fi
        if [ -d "$TARGET_DIR/wp-includes" ]; then
            chattr -R +i "$TARGET_DIR/wp-includes" 2>/dev/null || true
        fi
        echo "[+] Da khoa WordPress Lockdown thanh cong!"
        exit 0
        ;;

    unlock)
        echo "[+] Mo khoa WordPress Lockdown tai $TARGET_DIR..."
        if [ -f "$TARGET_DIR/wp-config.php" ]; then
            chattr -i "$TARGET_DIR/wp-config.php" 2>/dev/null || true
            chmod 0600 "$TARGET_DIR/wp-config.php" 2>/dev/null || true
        fi
        if [ -d "$TARGET_DIR/wp-admin" ]; then
            chattr -R -i "$TARGET_DIR/wp-admin" 2>/dev/null || true
            chmod -R 755 "$TARGET_DIR/wp-admin" 2>/dev/null || true
        fi
        if [ -d "$TARGET_DIR/wp-includes" ]; then
            chattr -R -i "$TARGET_DIR/wp-includes" 2>/dev/null || true
            chmod -R 755 "$TARGET_DIR/wp-includes" 2>/dev/null || true
        fi
        echo "[+] Da mo khoa WordPress Lockdown thanh cong!"
        exit 0
        ;;

    fixperms)
        echo "[+] Chuan hoa quyen tep tin WordPress tai $TARGET_DIR..."
        if id "$USERNAME" &>/dev/null; then
            chown -R "$USERNAME:$USERNAME" "$TARGET_DIR" 2>/dev/null || true
            find "$TARGET_DIR" -type d -exec chmod 755 {} + 2>/dev/null || true
            find "$TARGET_DIR" -type f -exec chmod 644 {} + 2>/dev/null || true
            if [ -f "$TARGET_DIR/wp-config.php" ]; then
                chmod 600 "$TARGET_DIR/wp-config.php" 2>/dev/null || true
            fi
        fi
        echo "[+] Da phan quyen chuan thanh cong!"
        exit 0
        ;;

    sso_create)
        TARGET_USER_ID="${4:-0}"
        TOKEN="${5:-}"
        ADMIN_USER="${6:-admin}"
        NOW_TS=$(date +%s)

        if [ -z "$TOKEN" ]; then
            echo "[-] Token khong hop le"
            exit 1
        fi

        MU_DIR="$TARGET_DIR/wp-content/mu-plugins"
        mkdir -p "$MU_DIR"
        SSO_FILE="$MU_DIR/wpc_sso_${TOKEN}.php"

        cat <<EOF > "$SSO_FILE"
<?php
/**
 * WPC Magic Login Single-Use MU-Plugin
 * Tu dong huy sach khoi he thong ngay sau khi dang nhap hoac het han (60s)
 */
if (isset(\$_GET['wpc_sso_token']) && \$_GET['wpc_sso_token'] === '$TOKEN') {
    if (time() - $NOW_TS > 60) {
        @unlink(__FILE__);
        wp_die('Lien ket Magic Login da het han (60 giay). Vui long tao lai tu PressPanel.');
    }
    add_action('init', function() {
        @unlink(__FILE__);
        \$target_id = intval($TARGET_USER_ID);
        \$user = null;
        if (\$target_id > 0) {
            \$user = get_user_by('id', \$target_id);
        }
        if (!\$user) {
            \$user = get_user_by('login', '$ADMIN_USER');
        }
        if (!\$user) {
            \$admins = get_users(['role' => 'administrator', 'number' => 1]);
            \$user = !empty(\$admins) ? \$admins[0] : null;
        }
        if (\$user) {
            wp_clear_auth_cookie();
            wp_set_current_user(\$user->ID);
            wp_set_auth_cookie(\$user->ID, true);
            wp_safe_redirect(admin_url());
            exit;
        }
        wp_die('Khong tim thay tai khoan WordPress phu hop de dang nhap SSO.');
    });
}
EOF

        if id "$USERNAME" &>/dev/null; then
            chown "$USERNAME:$USERNAME" "$SSO_FILE" 2>/dev/null || true
            chmod 644 "$SSO_FILE" 2>/dev/null || true
        fi
        echo "[+] Da tao SSO MU-Plugin thanh cong: wpc_sso_${TOKEN}.php"
        exit 0
        ;;

    wpc_optimize)
        FILE_NAME="${4:-}"
        ENABLE="${5:-1}"
        MU_DIR="$TARGET_DIR/wp-content/mu-plugins"
        SRC_FILE="/opt/wpc/templates/mu-plugins/$FILE_NAME"

        if [ -z "$FILE_NAME" ]; then
            echo "[-] File name khong hop le"
            exit 1
        fi

        if [ "$ENABLE" = "1" ]; then
            mkdir -p "$MU_DIR"
            if [ -f "$SRC_FILE" ]; then
                cp -f "$SRC_FILE" "$MU_DIR/$FILE_NAME"
                if id "$USERNAME" &>/dev/null; then
                    chown "$USERNAME:$USERNAME" "$MU_DIR/$FILE_NAME" 2>/dev/null || true
                    chmod 644 "$MU_DIR/$FILE_NAME" 2>/dev/null || true
                fi
                echo "[+] Da kich hoat WPC Optimize: $FILE_NAME"
            else
                echo "[-] Khong tim thay file mau: $SRC_FILE"
                exit 1
            fi
        else
            rm -f "$MU_DIR/$FILE_NAME"
            echo "[+] Da go bo WPC Optimize: $FILE_NAME"
        fi
        exit 0
        ;;

    config_define)
        CONSTANT="${4:-}"
        VALUE="${5:-}"
        ENABLE="${6:-1}"
        FILE="$TARGET_DIR/wp-config.php"

        if [ ! -f "$FILE" ]; then
            echo "[-] Khong tim thay wp-config.php"
            exit 1
        fi

        # Tam mo khoa neu dang lockdown
        chattr -i "$FILE" 2>/dev/null || true
        chmod 600 "$FILE" 2>/dev/null || true

        # Xoa constant cu neu co
        sed -i -E "/define\(\s*['\"]$CONSTANT['\"]/d" "$FILE"

        if [ "$ENABLE" = "1" ]; then
            # Chen constant moi truoc dong stop editing
            if grep -q "stop editing" "$FILE"; then
                sed -i "/stop editing/i define( '$CONSTANT', $VALUE );" "$FILE"
            elif grep -q "wp-settings.php" "$FILE"; then
                sed -i "/wp-settings.php/i define( '$CONSTANT', $VALUE );" "$FILE"
            else
                echo "define( '$CONSTANT', $VALUE );" >> "$FILE"
            fi
            echo "[+] Da cap nhat define('$CONSTANT', $VALUE) trong wp-config.php"
        else
            echo "[+] Da loai bo define('$CONSTANT') khoi wp-config.php"
        fi
        exit 0
        ;;

    db_cleanup)
        DB_NAME=$(get_wp_config_val "DB_NAME")
        DB_USER=$(get_wp_config_val "DB_USER")
        DB_PASS=$(get_wp_config_val "DB_PASSWORD")
        DB_HOST=$(get_wp_config_val "DB_HOST")
        [ -z "$DB_HOST" ] && DB_HOST="localhost"
        PREFIX=$(get_wp_prefix)

        if [ -z "$DB_NAME" ]; then
            echo "[-] Khong the doc thong tin database tu wp-config.php"
            exit 1
        fi

        echo "[+] Don dep co so du lieu: $DB_NAME (Prefix: $PREFIX)..."
        SQL="
        DELETE FROM \`${PREFIX}posts\` WHERE post_type = 'revision';
        DELETE FROM \`${PREFIX}posts\` WHERE post_status = 'auto-draft';
        DELETE FROM \`${PREFIX}comments\` WHERE comment_approved = 'spam' OR comment_approved = 'trash';
        DELETE FROM \`${PREFIX}commentmeta\` WHERE comment_id NOT IN (SELECT comment_ID FROM \`${PREFIX}comments\`);
        DELETE FROM \`${PREFIX}postmeta\` WHERE post_id NOT IN (SELECT ID FROM \`${PREFIX}posts\`);
        DELETE FROM \`${PREFIX}options\` WHERE option_name LIKE '_transient_%';
        "
        mysql -h "$DB_HOST" -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -e "$SQL" 2>/dev/null || true
        echo "[+] Da don dep toi uu database thanh cong!"
        exit 0
        ;;

    clone)
        DEST_DOMAIN="${4:-}"
        if [ -z "$DEST_DOMAIN" ]; then
            echo "[-] Ten mien dich khong hop le"
            exit 1
        fi
        DEST_DIR="/home/$USERNAME/domains/$DEST_DOMAIN/public_html"
        mkdir -p "$DEST_DIR"

        echo "[+] Sao chep ma nguon tu $DOMAIN sang $DEST_DOMAIN..."
        rsync -av --exclude 'wp-content/cache' --exclude '*.zip' --exclude '*.tar.gz' --exclude '*.log' "$TARGET_DIR/" "$DEST_DIR/"

        # Tao DB moi va dump sang DB moi
        SRC_DB=$(get_wp_config_val "DB_NAME")
        SAFE_USER_PREFIX=$(echo "$USERNAME" | tr -dc 'a-zA-Z0-9' | cut -c 1-8)
        RAND_SUFFIX=$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n' | cut -c 1-4)
        DEST_DB="wp_${SAFE_USER_PREFIX}_${RAND_SUFFIX}"
        DEST_USER="usr_${SAFE_USER_PREFIX}_${RAND_SUFFIX}"
        DEST_PASS=$(head -c 16 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 16)

        mysql -e "CREATE DATABASE IF NOT EXISTS \`$DEST_DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;" 2>/dev/null || true
        mysql -e "CREATE USER IF NOT EXISTS '$DEST_USER'@'localhost' IDENTIFIED BY '$DEST_PASS';" 2>/dev/null || true
        mysql -e "GRANT ALL PRIVILEGES ON \`$DEST_DB\`.* TO '$DEST_USER'@'localhost'; FLUSH PRIVILEGES;" 2>/dev/null || true

        if [ -n "$SRC_DB" ]; then
            mysqldump "$SRC_DB" 2>/dev/null | mysql "$DEST_DB" 2>/dev/null || true
        fi

        # Cap nhat wp-config.php o dest
        sed -i -E "s/define\(\s*['\"]DB_NAME['\"]\s*,\s*['\"][^'\"]*['\"]\s*\)/define( 'DB_NAME', '$DEST_DB' )/" "$DEST_DIR/wp-config.php"
        sed -i -E "s/define\(\s*['\"]DB_USER['\"]\s*,\s*['\"][^'\"]*['\"]\s*\)/define( 'DB_USER', '$DEST_USER' )/" "$DEST_DIR/wp-config.php"
        sed -i -E "s/define\(\s*['\"]DB_PASSWORD['\"]\s*,\s*['\"][^'\"]*['\"]\s*\)/define( 'DB_PASSWORD', '$DEST_PASS' )/" "$DEST_DIR/wp-config.php"

        # WP-CLI search-replace voi co --skip-plugins --skip-themes
        if command -v wp &>/dev/null; then
            wp search-replace "http://$DOMAIN" "http://$DEST_DOMAIN" --path="$DEST_DIR" --skip-plugins --skip-themes --allow-root >/dev/null 2>&1 || true
            wp search-replace "https://$DOMAIN" "https://$DEST_DOMAIN" --path="$DEST_DIR" --skip-plugins --skip-themes --allow-root >/dev/null 2>&1 || true
        fi

        if id "$USERNAME" &>/dev/null; then
            chown -R "$USERNAME:$USERNAME" "$DEST_DIR" 2>/dev/null || true
            find "$DEST_DIR" -type d -exec chmod 755 {} + 2>/dev/null || true
            find "$DEST_DIR" -type f -exec chmod 644 {} + 2>/dev/null || true
            chmod 600 "$DEST_DIR/wp-config.php" 2>/dev/null || true
        fi

        echo "[+] Clone website thanh cong: $DEST_DOMAIN!"
        exit 0
        ;;

    *)
        echo "[-] Lenh khong hop le: $ACTION"
        exit 1
        ;;
esac
