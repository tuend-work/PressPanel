#!/usr/bin/env bash
# ==============================================================================
# PressPanel: ssl_manager.sh - Quản lý chứng chỉ SSL (Let's Encrypt / ZeroSSL / Custom)
# ==============================================================================
set -uo pipefail

ACTION="${1:-}"
DOMAIN="${2:-}"
USERNAME="${3:-}"
DOC_ROOT="${4:-}"
EXTRA_ARG1="${5:-}"
EXTRA_ARG2="${6:-}"
EXTRA_ARG3="${7:-}"

if [ -z "$ACTION" ] || [ -z "$DOMAIN" ] || [ -z "$USERNAME" ]; then
    echo "Usage: ssl_manager.sh <letsencrypt|zerossl|custom|renew|disable> <domain> <username> <doc_root> [args...]"
    exit 1
fi

[ -z "$DOC_ROOT" ] && DOC_ROOT="/home/$USERNAME/domains/$DOMAIN/public_html"
SSL_DIR="/home/$USERNAME/domains/$DOMAIN/ssl"
FULLCHAIN="$SSL_DIR/fullchain.pem"
PRIVKEY="$SSL_DIR/privkey.pem"

mkdir -p "$SSL_DIR" "$DOC_ROOT/.well-known/acme-challenge"
chmod 755 "$SSL_DIR" 2>/dev/null || true

reload_webservers() {
    # Đảm bảo Nginx vhost trỏ đúng về $SSL_DIR nếu trước đó đang trỏ selfsigned
    local nginx_conf="/etc/nginx/conf.d/${DOMAIN}.conf"
    if [ -f "$nginx_conf" ] && [ -s "$FULLCHAIN" ] && [ -s "$PRIVKEY" ]; then
        sed -i "s|/etc/ssl/presspanel/selfsigned.crt|${FULLCHAIN}|g" "$nginx_conf" 2>/dev/null || true
        sed -i "s|/etc/ssl/presspanel/selfsigned.key|${PRIVKEY}|g" "$nginx_conf" 2>/dev/null || true
        sed -i "s|/etc/ssl/wpc/selfsigned.crt|${FULLCHAIN}|g" "$nginx_conf" 2>/dev/null || true
        sed -i "s|/etc/ssl/wpc/selfsigned.key|${PRIVKEY}|g" "$nginx_conf" 2>/dev/null || true
        if nginx -t >/dev/null 2>&1; then
            nginx -s reload >/dev/null 2>&1 || systemctl reload nginx >/dev/null 2>&1 || true
        fi
    fi
    if [ -d "/usr/local/lsws/conf/vhosts/$DOMAIN" ]; then
        /usr/local/lsws/bin/lswsctrl reload >/dev/null 2>&1 || systemctl reload lsws >/dev/null 2>&1 || true
    fi
}

ensure_certbot() {
    if ! command -v certbot >/dev/null 2>&1; then
        echo "[+] Đang cài đặt Certbot..."
        if command -v apt-get >/dev/null 2>&1; then
            DEBIAN_FRONTEND=noninteractive apt-get update -y >/dev/null 2>&1 || true
            DEBIAN_FRONTEND=noninteractive apt-get install -y certbot >/dev/null 2>&1 || true
        elif command -v dnf >/dev/null 2>&1; then
            dnf install -y certbot >/dev/null 2>&1 || true
        fi
    fi
    command -v certbot >/dev/null 2>&1
}

ensure_acme_sh() {
    local acme_bin="/root/.acme.sh/acme.sh"
    if [ ! -x "$acme_bin" ]; then
        echo "[+] Đang cài đặt acme.sh cho ZeroSSL..."
        curl -sSL https://get.acme.sh | sh -s email="${1:-admin@$DOMAIN}" >/dev/null 2>&1 || true
    fi
    [ -x "$acme_bin" ]
}

case "$ACTION" in
    letsencrypt)
        EMAIL="${EXTRA_ARG1:-admin@$DOMAIN}"
        INCLUDE_WWW="${EXTRA_ARG2:-0}"
        if ! ensure_certbot; then
            echo "[-] Không thể cài đặt Certbot trên máy chủ này."
            exit 1
        fi
        DOM_ARGS=(-d "$DOMAIN")
        if [ "$INCLUDE_WWW" = "1" ] && [[ "$DOMAIN" != www.* ]]; then
            DOM_ARGS+=(-d "www.$DOMAIN")
        fi
        echo "[+] Đang xin cấp chứng chỉ Let's Encrypt cho $DOMAIN..."
        if ! certbot certonly --webroot -w "$DOC_ROOT" "${DOM_ARGS[@]}" --non-interactive --agree-tos -m "$EMAIL" --force-renewal; then
            echo "[-] Cấp phát Let's Encrypt thất bại. Vui lòng kiểm tra DNS của $DOMAIN đã trỏ về IP máy chủ và cổng 80 đang mở."
            exit 1
        fi
        if [ -s "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ] && [ -s "/etc/letsencrypt/live/$DOMAIN/privkey.pem" ]; then
            cp -fL "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$FULLCHAIN"
            cp -fL "/etc/letsencrypt/live/$DOMAIN/privkey.pem" "$PRIVKEY"
            chmod 644 "$FULLCHAIN"
            chmod 600 "$PRIVKEY"
            reload_webservers
            echo "[+] Cài đặt SSL Let's Encrypt thành công cho $DOMAIN!"
            exit 0
        fi
        echo "[-] Không tìm thấy tệp chứng chỉ sau khi chạy Certbot."
        exit 1
        ;;

    zerossl)
        EMAIL="${EXTRA_ARG1:-admin@$DOMAIN}"
        EAB_KID="${EXTRA_ARG2:-}"
        EAB_HMAC="${EXTRA_ARG3:-}"
        if ! ensure_acme_sh "$EMAIL"; then
            echo "[-] Không thể cài đặt acme.sh cho ZeroSSL."
            exit 1
        fi
        ACME="/root/.acme.sh/acme.sh"
        echo "[+] Đang đăng ký tài khoản ZeroSSL..."
        if [ -n "$EAB_KID" ] && [ -n "$EAB_HMAC" ]; then
            "$ACME" --register-account --server zerossl --eab-kid "$EAB_KID" --eab-hmac-key "$EAB_HMAC" -m "$EMAIL" || true
        else
            "$ACME" --register-account --server zerossl -m "$EMAIL" || true
        fi
        echo "[+] Đang xin cấp chứng chỉ ZeroSSL cho $DOMAIN..."
        if ! "$ACME" --issue --server zerossl -d "$DOMAIN" -w "$DOC_ROOT" --force; then
            echo "[-] Cấp phát ZeroSSL thất bại. Kiểm tra DNS của $DOMAIN hoặc thông tin EAB Key."
            exit 1
        fi
        "$ACME" --install-cert -d "$DOMAIN" --fullchain-file "$FULLCHAIN" --key-file "$PRIVKEY"
        chmod 644 "$FULLCHAIN"
        chmod 600 "$PRIVKEY"
        reload_webservers
        echo "[+] Cài đặt SSL ZeroSSL thành công cho $DOMAIN!"
        exit 0
        ;;

    custom)
        TMP_KEY="$EXTRA_ARG1"
        TMP_CRT="$EXTRA_ARG2"
        if [ ! -s "$TMP_KEY" ] || [ ! -s "$TMP_CRT" ]; then
            echo "[-] Thiếu tệp Private Key hoặc Certificate."
            exit 1
        fi
        if ! openssl x509 -in "$TMP_CRT" -noout >/dev/null 2>&1; then
            echo "[-] Định dạng Certificate (CRT/PEM) không hợp lệ."
            exit 1
        fi
        if ! openssl pkey -in "$TMP_KEY" -noout >/dev/null 2>&1; then
            echo "[-] Định dạng Private Key không hợp lệ."
            exit 1
        fi
        cp -f "$TMP_CRT" "$FULLCHAIN"
        cp -f "$TMP_KEY" "$PRIVKEY"
        chmod 644 "$FULLCHAIN"
        chmod 600 "$PRIVKEY"
        rm -f "$TMP_CRT" "$TMP_KEY" 2>/dev/null || true
        reload_webservers
        echo "[+] Cài đặt Custom SSL thành công cho $DOMAIN!"
        exit 0
        ;;

    renew)
        if [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ] && command -v certbot >/dev/null 2>&1; then
            certbot renew --cert-name "$DOMAIN" --quiet || true
            cp -fL "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$FULLCHAIN" 2>/dev/null || true
            cp -fL "/etc/letsencrypt/live/$DOMAIN/privkey.pem" "$PRIVKEY" 2>/dev/null || true
        elif [ -x "/root/.acme.sh/acme.sh" ] && [ -d "/root/.acme.sh/${DOMAIN}_ecc" -o -d "/root/.acme.sh/${DOMAIN}" ]; then
            /root/.acme.sh/acme.sh --renew -d "$DOMAIN" --force || true
            /root/.acme.sh/acme.sh --install-cert -d "$DOMAIN" --fullchain-file "$FULLCHAIN" --key-file "$PRIVKEY" || true
        else
            echo "[-] Không tìm thấy chứng chỉ Let's Encrypt hoặc ZeroSSL để gia hạn."
            exit 1
        fi
        chmod 644 "$FULLCHAIN" 2>/dev/null || true
        chmod 600 "$PRIVKEY" 2>/dev/null || true
        reload_webservers
        echo "[+] Gia hạn SSL cho $DOMAIN thành công!"
        exit 0
        ;;

    disable)
        rm -f "$FULLCHAIN" "$PRIVKEY" 2>/dev/null || true
        # Fallback về selfsigned để Nginx/OLS không bị lỗi syntax khi reload
        SELF_CRT="/etc/ssl/presspanel/selfsigned.crt"
        SELF_KEY="/etc/ssl/presspanel/selfsigned.key"
        if [ ! -f "$SELF_CRT" ] || [ ! -f "$SELF_KEY" ]; then
            mkdir -p /etc/ssl/presspanel
            openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
                -keyout "$SELF_KEY" -out "$SELF_CRT" \
                -subj "/CN=localhost" >/dev/null 2>&1 || true
        fi
        local_nginx="/etc/nginx/conf.d/${DOMAIN}.conf"
        if [ -f "$local_nginx" ]; then
            sed -i "s|${FULLCHAIN}|${SELF_CRT}|g" "$local_nginx" 2>/dev/null || true
            sed -i "s|${PRIVKEY}|${SELF_KEY}|g" "$local_nginx" 2>/dev/null || true
            nginx -t >/dev/null 2>&1 && (nginx -s reload >/dev/null 2>&1 || systemctl reload nginx >/dev/null 2>&1 || true)
        fi
        if [ -d "/usr/local/lsws/conf/vhosts/$DOMAIN" ]; then
            /usr/local/lsws/bin/lswsctrl reload >/dev/null 2>&1 || systemctl reload lsws >/dev/null 2>&1 || true
        fi
        echo "[+] Đã tắt SSL cho $DOMAIN!"
        exit 0
        ;;

    *)
        echo "[-] Action không hợp lệ: $ACTION"
        exit 1
        ;;
esac
