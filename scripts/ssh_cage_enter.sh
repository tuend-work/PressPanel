#!/usr/bin/env bash
# ==============================================================================
# PressPanel: ssh_cage_enter.sh - Ép toàn bộ phiên SSH / PuTTY / SFTP vào CageFS
# ==============================================================================
set -euo pipefail

if [ "${1:-}" = "--root-exec" ]; then
    # Chạy dưới quyền root qua sudoers, chỉ tin cậy $SUDO_USER (user thật đã xác thực SSH)
    TARGET="${SUDO_USER:-}"
    if [ -z "$TARGET" ] || [ "$TARGET" = "root" ] || [[ ! "$TARGET" =~ ^[a-zA-Z0-9_.-]{1,32}$ ]]; then
        echo "[-] Không xác định được tài khoản CageFS hợp lệ." >&2
        exit 1
    fi
    ORIG_CMD="${2:-}"
    exec /opt/presspanel/scripts/cage_terminal.sh "$TARGET" "$ORIG_CMD"
fi

# Entrypoint được OpenSSH ForceCommand gọi dưới quyền user thường
exec sudo -n /usr/local/bin/presspanel-ssh-cage --root-exec "${SSH_ORIGINAL_COMMAND:-}"
