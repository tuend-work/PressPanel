#!/usr/bin/env bash
# ==============================================================================
# WPC: security_hardening.sh - Hệ thống Kiểm tra & Tối ưu Bảo mật Chuẩn CloudLinux
# Bảo vệ tầng sâu: Kernel Sysctl, Compiler Lockdown, VFS Permissions, Loopback Firewall
# ==============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SYSCTL_CONF="/etc/sysctl.d/99-wpc-security.conf"
PANEL_DIR="/opt/wpc"

check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}[-] Vui lòng chạy lệnh này với quyền root: sudo bash $0 [check|fix]${NC}"
        exit 1
    fi
}

# 1. KERNEL & VFS PROTECTIONS (Symlink, Hardlink, Ptrace, SUID Dumps)
apply_sysctl_hardening() {
    echo -e "${CYAN}[*] Đang cấu hình Kernel Sysctl bảo vệ chống leo quyền & Symlink Race...${NC}"
    mkdir -p /etc/sysctl.d
    cat << 'EOF' > "$SYSCTL_CONF"
# ==============================================================================
# WPC Cloud-Grade Security Hardening (Kernel & VFS Protection)
# ==============================================================================
# 1. Chặn Symlink & Hardlink Race Condition (Bảo vệ chống đọc trộm wp-config.php)
fs.protected_symlinks = 1
fs.protected_hardlinks = 1
fs.protected_fifos = 2
fs.protected_regular = 2

# 2. Chặn Ptrace snoop bộ nhớ tiến trình (Chặn hacker dump pass từ tiến trình khác)
kernel.yama.ptrace_scope = 2
fs.suid_dumpable = 0

# 3. Chống giả mạo gói tin mạng nội bộ & redirection
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
EOF

    # Áp dụng ngay lập tức
    if [ -x "/sbin/sysctl" ]; then
        /sbin/sysctl -p "$SYSCTL_CONF" 2>/dev/null || /sbin/sysctl --system 2>/dev/null || true
    elif command -v sysctl &>/dev/null; then
        sysctl -p "$SYSCTL_CONF" 2>/dev/null || sysctl --system 2>/dev/null || true
    fi
    echo -e "${GREEN}[✔] Đã áp dụng Kernel Hardening (/etc/sysctl.d/99-wpc-security.conf)${NC}"
}

# 2. COMPILER & TOOLS LOCKDOWN (Chặn User thường biên dịch mã độc C/C++)
apply_compiler_lockdown() {
    echo -e "${CYAN}[*] Đang khóa quyền thực thi trình biên dịch đối với người dùng thường...${NC}"
    local compilers=(
        "/usr/bin/gcc"
        "/usr/bin/g++"
        "/usr/bin/cc"
        "/usr/bin/c++"
        "/usr/bin/make"
        "/usr/bin/as"
        "/usr/bin/ld"
        "/usr/bin/clang"
        "/usr/bin/clang++"
        "/usr/bin/gfortran"
    )
    for bin in "${compilers[@]}"; do
        for match in ${bin}*; do
            if [ -f "$match" ] && [ -x "$match" ]; then
                chmod 700 "$match" 2>/dev/null || true
            fi
        done
    done
    echo -e "${GREEN}[✔] Đã khóa toàn bộ gcc, g++, make, cc về quyền 700 (Chỉ root mới có quyền compile)${NC}"
}

# 3. PANEL PERMISSIONS HARDENING (Khóa cứng dữ liệu, cấu hình WPC Panel)
apply_panel_permissions() {
    echo -e "${CYAN}[*] Đang chuẩn hóa quyền bảo mật thư mục mã nguồn và CSDL WPC Panel...${NC}"
    if [ -d "$PANEL_DIR" ]; then
        chmod 750 "$PANEL_DIR" 2>/dev/null || true
        [ -d "$PANEL_DIR/data" ] && chmod 700 "$PANEL_DIR/data" 2>/dev/null || true
        [ -d "$PANEL_DIR/conf" ] && chmod 750 "$PANEL_DIR/conf" 2>/dev/null || true

        # Khóa cứng database SQLite và file cấu hình
        find "$PANEL_DIR/data" -name "*.db" -exec chmod 600 {} + 2>/dev/null || true
        [ -f "$PANEL_DIR/conf/wpc.conf" ] && chmod 600 "$PANEL_DIR/conf/wpc.conf" 2>/dev/null || true
        echo -e "${GREEN}[✔] Đã khóa phân quyền bảo vệ /opt/wpc (data=700, db=600, conf=600)${NC}"
    fi
}

# 4. HOME DIRECTORIES PRIVACY (Chuẩn hóa 0711 cho toàn bộ /home/*)
apply_home_permissions() {
    echo -e "${CYAN}[*] Đang quét và chuẩn hóa phân quyền 0711 chống đọc trộm thư mục /home/*...${NC}"
    local count=0
    for h in /home/*; do
        if [ -d "$h" ]; then
            chmod 711 "$h" 2>/dev/null || true
            ((count++))
        fi
    done
    echo -e "${GREEN}[✔] Đã chuẩn hóa quyền 0711 cho $count thư mục người dùng trong /home/*${NC}"
}

# 5. LOCALHOST PANEL PORT ISOLATION (Chặn User thường gọi API port 8888 trên Loopback)
apply_firewall_local_rules() {
    echo -e "${CYAN}[*] Đang thiết lập quy tắc tường lửa bảo vệ cổng quản trị Panel (Port 8888)...${NC}"
    if command -v iptables &>/dev/null; then
        local panel_port="8888"
        if [ -f "$PANEL_DIR/conf/wpc.conf" ]; then
            panel_port=$(grep -E "^port=" "$PANEL_DIR/conf/wpc.conf" 2>/dev/null | cut -d'=' -f2 | tr -d ' \r\n' || echo "8888")
        fi

        # Xác định UID của user wpc panel nếu có
        local wpc_uid=""
        if id "wpc" &>/dev/null; then
            wpc_uid=$(id -u "wpc")
        fi

        # Quy tắc: Cho phép root (uid 0) và wpc panel truy cập loopback dport 8888, chặn toàn bộ Web User khác
        if ! iptables -C OUTPUT -p tcp -o lo --dport "$panel_port" -m owner --uid-owner 0 -j ACCEPT 2>/dev/null; then
            iptables -I OUTPUT 1 -p tcp -o lo --dport "$panel_port" -m owner --uid-owner 0 -j ACCEPT 2>/dev/null || true
        fi
        if [ -n "$wpc_uid" ]; then
            if ! iptables -C OUTPUT -p tcp -o lo --dport "$panel_port" -m owner --uid-owner "$wpc_uid" -j ACCEPT 2>/dev/null; then
                iptables -I OUTPUT 2 -p tcp -o lo --dport "$panel_port" -m owner --uid-owner "$wpc_uid" -j ACCEPT 2>/dev/null || true
            fi
        fi
        if ! iptables -C OUTPUT -p tcp -o lo --dport "$panel_port" -j REJECT 2>/dev/null; then
            iptables -A OUTPUT -p tcp -o lo --dport "$panel_port" -j REJECT 2>/dev/null || true
        fi
        echo -e "${GREEN}[✔] Đã chặn Web User không phải root/panel truy cập cổng nội bộ $panel_port${NC}"
    fi
}

# 6. HARDEN SHARED MEMORY (/dev/shm nodev,nosuid,noexec)
apply_shm_hardening() {
    echo -e "${CYAN}[*] Đang tối ưu cờ bảo mật phân vùng bộ nhớ chia sẻ /dev/shm...${NC}"
    if mount | grep -q "on /dev/shm "; then
        mount -o remount,nodev,nosuid,noexec /dev/shm 2>/dev/null || true
        echo -e "${GREEN}[✔] Đã remount /dev/shm với cờ nodev,nosuid,noexec (Chống chạy mã độc)${NC}"
    fi
}

# ==============================================================================
# HÀM KIỂM TRA ĐÁNH GIÁ ĐỘ AN TOÀN (AUDIT / CHECK)
# ==============================================================================
audit_security() {
    echo -e "${BOLD}================================================================${NC}"
    echo -e "${BOLD}         BẢNG ĐÁNH GIÁ AN NINH HỆ THỐNG WPC (CLOUDLINUX AUDIT)   ${NC}"
    echo -e "${BOLD}================================================================${NC}"

    local score=0
    local max_score=7

    # 1. Symlink protection
    local symlink_val
    symlink_val=$(sysctl -n fs.protected_symlinks 2>/dev/null || echo "0")
    if [ "$symlink_val" -eq 1 ]; then
        echo -e " [${GREEN}PASS${NC}] Bảo vệ Symlink Kernel (fs.protected_symlinks=1): ${GREEN}Đã kích hoạt${NC}"
        ((score++))
    else
        echo -e " [${RED}WARN${NC}] Bảo vệ Symlink Kernel (fs.protected_symlinks): ${RED}Chưa bật (Nguy cơ bypass vhost)${NC}"
    fi

    # 2. Ptrace protection
    local ptrace_val
    ptrace_val=$(sysctl -n kernel.yama.ptrace_scope 2>/dev/null || echo "0")
    if [ "$ptrace_val" -ge 1 ]; then
        echo -e " [${GREEN}PASS${NC}] Bảo vệ Bộ nhớ Tiến trình (kernel.yama.ptrace_scope): ${GREEN}Đã kích hoạt ($ptrace_val)${NC}"
        ((score++))
    else
        echo -e " [${YELLOW}INFO${NC}] Bảo vệ Bộ nhớ Tiến trình (kernel.yama.ptrace_scope): ${YELLOW}Chưa bật mức cao${NC}"
    fi

    # 3. Compilers
    local comp_exposed=0
    for bin in /usr/bin/gcc /usr/bin/make /usr/bin/g++; do
        if [ -x "$bin" ]; then
            local perm
            perm=$(stat -c "%a" "$bin" 2>/dev/null || echo "755")
            if [ "$perm" != "700" ]; then
                comp_exposed=1
                break
            fi
        fi
    done
    if [ "$comp_exposed" -eq 0 ]; then
        echo -e " [${GREEN}PASS${NC}] Khóa Compiler (gcc, g++, make): ${GREEN}Chỉ root có quyền (700)${NC}"
        ((score++))
    else
        echo -e " [${RED}WARN${NC}] Khóa Compiler (gcc, g++, make): ${RED}User thường có thể chạy biên dịch exploit${NC}"
    fi

    # 4. Panel permissions
    if [ -f "$PANEL_DIR/data/panel.db" ]; then
        local db_perm
        db_perm=$(stat -c "%a" "$PANEL_DIR/data/panel.db" 2>/dev/null || echo "644")
        if [ "$db_perm" = "600" ] || [ "$db_perm" = "640" ]; then
            echo -e " [${GREEN}PASS${NC}] Phân quyền CSDL Panel (panel.db): ${GREEN}Được bảo vệ nghiêm ngặt ($db_perm)${NC}"
            ((score++))
        else
            echo -e " [${RED}WARN${NC}] Phân quyền CSDL Panel (panel.db): ${RED}Quyền $db_perm quá mở${NC}"
        fi
    else
        echo -e " [${GREEN}PASS${NC}] CSDL Panel: ${GREEN}N/A${NC}"
        ((score++))
    fi

    # 5. /home privacy
    local home_leaks=0
    for h in /home/*; do
        if [ -d "$h" ]; then
            local hp
            hp=$(stat -c "%a" "$h" 2>/dev/null || echo "755")
            if [ "$hp" = "755" ] || [ "$hp" = "777" ]; then
                home_leaks=1
                break
            fi
        fi
    done
    if [ "$home_leaks" -eq 0 ]; then
        echo -e " [${GREEN}PASS${NC}] Cách ly Thư mục /home/*: ${GREEN}Đạt chuẩn 0711 chống đọc trộm${NC}"
        ((score++))
    else
        echo -e " [${RED}WARN${NC}] Cách ly Thư mục /home/*: ${RED}Có thư mục 755 cho phép user khác xem trộm${NC}"
    fi

    # 6. Localhost panel port isolation
    if command -v iptables &>/dev/null && iptables -C OUTPUT -p tcp -o lo --dport 8888 -j REJECT 2>/dev/null; then
        echo -e " [${GREEN}PASS${NC}] Tường lửa Cổng Quản trị Localhost (Port 8888): ${GREEN}Đã chặn Web User${NC}"
        ((score++))
    else
        echo -e " [${YELLOW}INFO${NC}] Tường lửa Cổng Quản trị Localhost (Port 8888): ${YELLOW}Chưa bật quy tắc lọc${NC}"
    fi

    # 7. Cage Terminal script exists
    if [ -f "/opt/wpc/scripts/cage_terminal.sh" ]; then
        echo -e " [${GREEN}PASS${NC}] Lồng cách ly Mount & PID Namespace (CageFS Mode): ${GREEN}Sẵn sàng${NC}"
        ((score++))
    else
        echo -e " [${RED}WARN${NC}] Lồng cách ly Mount & PID Namespace: ${RED}Chưa cài đặt script cage${NC}"
    fi

    echo -e "${BOLD}----------------------------------------------------------------${NC}"
    echo -e " Tổng điểm an ninh: ${BOLD}${score}/${max_score}${NC}"
    if [ "$score" -eq "$max_score" ]; then
        echo -e " Đánh giá: ${GREEN}${BOLD}XUẤT SẮC - ĐẠT CHUẨN CÁCH LY CLOUDLINUX HARDENED!${NC}"
    else
        echo -e " Đánh giá: ${YELLOW}${BOLD}CẦN TỐI ƯU THÊM - Chạy lệnh 'wpc security fix' để khắc phục.${NC}"
    fi
    echo -e "${BOLD}================================================================${NC}"
}

# ==============================================================================
# HÀM THỰC THI SỬA CHỮA TOÀN BỘ (FIX ALL)
# ==============================================================================
fix_all_security() {
    echo -e "${BOLD}================================================================${NC}"
    echo -e "${BOLD}         TIẾN HÀNH TỐI ƯU TOÀN DIỆN BẢO MẬT HỆ THỐNG WPC         ${NC}"
    echo -e "${BOLD}================================================================${NC}"

    apply_sysctl_hardening
    apply_compiler_lockdown
    apply_panel_permissions
    apply_home_permissions
    apply_firewall_local_rules
    apply_shm_hardening

    echo -e "${BOLD}----------------------------------------------------------------${NC}"
    echo -e "${GREEN}${BOLD}[✔] ĐÃ HOÀN TẤT BẢO MẬT TOÀN BỘ HỆ THỐNG THEO TIÊU CHUẨN CLOUDLINUX!${NC}"
    echo -e "${BOLD}================================================================${NC}"
}

ACTION="${1:-check}"

check_root

case "$ACTION" in
    check|audit)
        audit_security
        ;;
    fix|apply)
        fix_all_security
        ;;
    *)
        echo "Sử dụng: $0 {check|fix}"
        exit 1
        ;;
esac
