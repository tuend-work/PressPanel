#!/usr/bin/env bash
# PressPanel: ols.sh - Cài đặt OpenLiteSpeed Web Server
set -euo pipefail

ACTION="${1:-install}"

case "$ACTION" in
    install)
        echo "[+] Đang cài đặt OpenLiteSpeed Web Server..."
        if command -v apt-get &>/dev/null; then
            # Tải repo script của LiteSpeed
            wget -qO - https://repo.litespeed.sh | bash || true
            # Đảm bảo quyền đọc (chmod 644) cho keyring GPG để user _apt không bị từ chối
            chmod -R a+r /etc/apt/trusted.gpg.d/ 2>/dev/null || true
            # Nếu repo báo thiếu pubkey 011AA62DEDA1F085, import trực tiếp
            apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 011AA62DEDA1F085 2>/dev/null || true
            chmod -R a+r /etc/apt/trusted.gpg.d/ 2>/dev/null || true
            
            DEBIAN_FRONTEND=noninteractive apt-get update -y || true
            DEBIAN_FRONTEND=noninteractive apt-get install -y openlitespeed lsphp82 lsphp82-common lsphp82-mysql lsphp82-opcache lsphp82-curl || true
            
            # Cấu hình OpenLiteSpeed: Đổi port mặc định 8088 thành 80 cho HTTP listener
            OLS_CONF="/usr/local/lsws/conf/httpd_config.conf"
            if [ -f "$OLS_CONF" ]; then
                # Thay thế port 8088 thành 80 trong Default listener
                sed -i 's/address *:8088/address *:80/g' "$OLS_CONF" || true
                sed -i 's/listener Default {/listener HTTP_80 {/g' "$OLS_CONF" || true
                
                # Cấp quyền đọc an toàn cho nhóm/panel để giao diện có thể đọc cấu hình OLS
                chmod 755 /usr/local/lsws/conf 2>/dev/null || true
                chmod 644 /usr/local/lsws/conf/httpd_config.conf 2>/dev/null || true
                for p_user in wpc presspanel; do
                    if id "$p_user" &>/dev/null; then
                        usermod -a -G lsadm "$p_user" 2>/dev/null || true
                    fi
                done
            fi

            systemctl enable lshttpd 2>/dev/null || systemctl enable lsws 2>/dev/null || true
            systemctl restart lshttpd 2>/dev/null || systemctl restart lsws 2>/dev/null || true
        fi
        echo "[+] Cài đặt OpenLiteSpeed hoàn tất!"
        ;;

    remove)
        echo "[+] Đang gỡ cài đặt OpenLiteSpeed..."
        systemctl stop lshttpd 2>/dev/null || true
        apt-get remove -y openlitespeed 2>/dev/null || true
        ;;
    *)
        echo "Lệnh không hợp lệ"
        exit 1
        ;;
esac
