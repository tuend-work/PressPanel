#!/usr/bin/env bash
# PressPanel: mariadb.sh - Cài đặt MariaDB Database Server (Forwarder sang mysql.sh)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/mysql.sh" "$@"

