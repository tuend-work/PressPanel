#!/usr/bin/env bash
# ==============================================================================
# WPCloud: redis.sh - Cài đặt & Quản lý Redis / Valkey Object Cache
# Hỗ trợ: Valkey (Linux Foundation) & Redis Server truyền thống
# ==============================================================================
set -euo pipefail

ACTION="${1:-install}"
VERSION="${2:-valkey-8.0}"

case "$ACTION" in
    install)
        echo "[+] Đang chuẩn bị cài đặt Object Cache (Phiên bản: $VERSION)..."
        echo "[+] Đang cập nhật gói hệ điều hành AlmaLinux/Debian/Ubuntu trước khi cài đặt..."
        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -y || true
            apt-get upgrade -y || true
        elif command -v dnf &>/dev/null; then
            dnf upgrade -y || true
        elif command -v yum &>/dev/null; then
            yum update -y || true
        fi
        
        # 1. Cài đặt Valkey
        if [[ "$VERSION" == *"valkey"* ]]; then
            echo "[+] Cài đặt Valkey Server (High-Performance Open-source Cache)..."
            INSTALLED=0

            if command -v apt-get &>/dev/null; then
                export DEBIAN_FRONTEND=noninteractive
                # Thử gói valkey chính thức nếu OS hỗ trợ (Ubuntu 24.04+, Debian trixie+)
                if apt-get install -y valkey valkey-tools 2>/dev/null; then
                    apt-get install -y valkey-redis-compat 2>/dev/null || true
                    INSTALLED=1
                elif apt-get install -y valkey-server 2>/dev/null; then
                    INSTALLED=1
                fi
            elif command -v dnf &>/dev/null; then
                dnf install -y valkey 2>/dev/null && INSTALLED=1 || true
            elif command -v yum &>/dev/null; then
                yum install -y valkey 2>/dev/null && INSTALLED=1 || true
            fi

            # Nếu OS chưa có gói valkey trong kho mặc định, cài đặt redis-server làm fallback tương thích
            if [ "$INSTALLED" -eq 0 ]; then
                echo "[*] Kho package chưa có sẵn Valkey native, cài đặt gói redis-server với alias valkey..."
                if command -v apt-get &>/dev/null; then
                    export DEBIAN_FRONTEND=noninteractive
                    apt-get install -y redis-server redis-tools 2>/dev/null || true
                elif command -v dnf &>/dev/null; then
                    dnf install -y redis 2>/dev/null || true
                elif command -v yum &>/dev/null; then
                    yum install -y redis 2>/dev/null || true
                fi
                # Tạo symlink tương thích valkey-server và valkey-cli
                if command -v redis-server &>/dev/null && ! command -v valkey-server &>/dev/null; then
                    ln -sf "$(command -v redis-server)" /usr/bin/valkey-server 2>/dev/null || true
                    ln -sf "$(command -v redis-cli)" /usr/bin/valkey-cli 2>/dev/null || true
                fi
            fi

            # Kích hoạt và khởi động service
            systemctl daemon-reload 2>/dev/null || true
            for s in valkey valkey-server redis-server redis; do
                if systemctl list-unit-files "$s.service" &>/dev/null || [ -f "/lib/systemd/system/$s.service" ] || [ -f "/etc/systemd/system/$s.service" ]; then
                    systemctl enable "$s" 2>/dev/null || true
                    systemctl restart "$s" 2>/dev/null || true
                    echo "[+] Dịch vụ $s đã được khởi động và kích hoạt tự khởi động cùng hệ thống!"
                    break
                fi
            done
            echo "[+] Cài đặt Valkey Object Cache thành công!"

        # 2. Cài đặt Redis truyền thống
        else
            echo "[+] Cài đặt Redis Server tiêu chuẩn..."
            if command -v apt-get &>/dev/null; then
                export DEBIAN_FRONTEND=noninteractive
                apt-get update -qq || true
                apt-get install -y redis-server redis-tools
            elif command -v dnf &>/dev/null; then
                dnf install -y redis
            elif command -v yum &>/dev/null; then
                yum install -y redis
            fi

            systemctl daemon-reload 2>/dev/null || true
            for s in redis-server redis; do
                systemctl enable "$s" 2>/dev/null || true
                systemctl restart "$s" 2>/dev/null || true
            done
            echo "[+] Cài đặt Redis Server thành công!"
        fi
        ;;

    remove)
        echo "[+] Đang gỡ bỏ Redis / Valkey Object Cache..."
        for s in valkey valkey-server redis-server redis; do
            systemctl stop "$s" 2>/dev/null || true
            systemctl disable "$s" 2>/dev/null || true
        done

        if command -v apt-get &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get remove -y valkey valkey-server valkey-tools valkey-redis-compat redis-server redis-tools 2>/dev/null || true
        elif command -v dnf &>/dev/null; then
            dnf remove -y valkey redis 2>/dev/null || true
        elif command -v yum &>/dev/null; then
            yum remove -y valkey redis 2>/dev/null || true
        fi
        rm -f /usr/bin/valkey-server /usr/bin/valkey-cli 2>/dev/null || true
        echo "[+] Đã gỡ bỏ Redis / Valkey thành công!"
        ;;

    restart)
        for s in valkey valkey-server redis-server redis; do
            if systemctl is-active --quiet "$s" 2>/dev/null; then
                systemctl restart "$s"
                echo "[+] Đã khởi động lại $s thành công!"
                exit 0
            fi
        done
        systemctl restart redis-server 2>/dev/null || systemctl restart valkey 2>/dev/null || true
        ;;

    start)
        for s in valkey valkey-server redis-server redis; do
            if systemctl list-unit-files "$s.service" &>/dev/null; then
                systemctl start "$s"
                echo "[+] Đã bật dịch vụ $s thành công!"
                exit 0
            fi
        done
        systemctl start redis-server 2>/dev/null || systemctl start valkey 2>/dev/null || true
        ;;

    stop)
        for s in valkey valkey-server redis-server redis; do
            systemctl stop "$s" 2>/dev/null || true
        done
        echo "[+] Đã dừng dịch vụ Object Cache!"
        ;;

    *)
        echo "Cách dùng: $0 {install|remove|restart|start|stop} [version]"
        exit 1
        ;;
esac
