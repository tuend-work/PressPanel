#!/bin/bash
# ==============================================================================
# WPC: Redis / Valkey Object Cache Socket Per User Manager
# Quản lý Socket Redis/Valkey cách ly riêng biệt cho từng tài khoản Website User
# Socket: /home/{user}/.redis/redis.sock
# ==============================================================================

set -e

ACTION="${1:-status}"
TARGET_USER="${2:-}"
MAX_MEM="${3:-64}" # MB

if [ -z "$TARGET_USER" ]; then
    echo '{"status":"error","message":"Target username is required"}'
    exit 1
fi

# 1. Tìm binary server và cli (Ưu tiên valkey, fallback sang redis)
SERVER_BIN=""
CLI_BIN=""
ENGINE="redis"

if command -v valkey-server &>/dev/null; then
    SERVER_BIN=$(command -v valkey-server)
    CLI_BIN=$(command -v valkey-cli || echo "valkey-cli")
    ENGINE="valkey"
elif [ -f /usr/bin/valkey-server ]; then
    SERVER_BIN="/usr/bin/valkey-server"
    CLI_BIN="/usr/bin/valkey-cli"
    ENGINE="valkey"
elif command -v redis-server &>/dev/null; then
    SERVER_BIN=$(command -v redis-server)
    CLI_BIN=$(command -v redis-cli || echo "redis-cli")
    ENGINE="redis"
elif [ -f /usr/bin/redis-server ]; then
    SERVER_BIN="/usr/bin/redis-server"
    CLI_BIN="/usr/bin/redis-cli"
    ENGINE="redis"
elif [ -f /usr/local/bin/redis-server ]; then
    SERVER_BIN="/usr/local/bin/redis-server"
    CLI_BIN="/usr/local/bin/redis-cli"
    ENGINE="redis"
fi

# 2. Xác định thư mục người dùng
USER_HOME="/home/$TARGET_USER"
if [ ! -d "$USER_HOME" ]; then
    if [ "$TARGET_USER" = "root" ] || [ "$TARGET_USER" = "admin" ]; then
        USER_HOME="/root"
    else
        USER_HOME="/home/$TARGET_USER"
        mkdir -p "$USER_HOME" 2>/dev/null || true
    fi
fi

REDIS_DIR="$USER_HOME/.redis"
CONF_FILE="$REDIS_DIR/redis.conf"
SOCK_FILE="$REDIS_DIR/redis.sock"
PID_FILE="$REDIS_DIR/redis.pid"
LOG_FILE="$REDIS_DIR/redis.log"

check_running() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE" 2>/dev/null || true)
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            if [ -S "$SOCK_FILE" ]; then
                return 0
            fi
        fi
    fi
    if [ -S "$SOCK_FILE" ] && [ -n "$CLI_BIN" ]; then
        if $CLI_BIN -s "$SOCK_FILE" ping 2>/dev/null | grep -q "PONG"; then
            return 0
        fi
    fi
    return 1
}

generate_conf() {
    mkdir -p "$REDIS_DIR"
    chown -R "$TARGET_USER:$TARGET_USER" "$REDIS_DIR" 2>/dev/null || true
    chmod 775 "$REDIS_DIR" 2>/dev/null || true

    cat <<EOF > "$CONF_FILE"
# WPC Redis/Valkey Socket Per User
port 0
unixsocket $SOCK_FILE
unixsocketperm 777
pidfile $PID_FILE
logfile $LOG_FILE
dir $REDIS_DIR
daemonize yes
maxmemory ${MAX_MEM}mb
maxmemory-policy allkeys-lru
save ""
protected-mode no
timeout 300
tcp-keepalive 0
EOF
    chown "$TARGET_USER:$TARGET_USER" "$CONF_FILE" 2>/dev/null || true
    chmod 644 "$CONF_FILE" 2>/dev/null || true
}

case "$ACTION" in
    status)
        INSTALLED="false"
        [ -n "$SERVER_BIN" ] && INSTALLED="true"

        RUNNING="false"
        PID=0
        USED_MEM="0 B"
        PEAK_MEM="0 B"
        TOTAL_KEYS=0
        HIT_RATE="0.0%"
        CLIENTS=0
        UPTIME="0s"

        if check_running; then
            RUNNING="true"
            PID=$(cat "$PID_FILE" 2>/dev/null || pgrep -f "$CONF_FILE" | head -n 1 || echo 0)
            if [ -n "$CLI_BIN" ] && [ -S "$SOCK_FILE" ]; then
                INFO_OUT=$($CLI_BIN -s "$SOCK_FILE" info 2>/dev/null || true)
                USED_MEM=$(echo "$INFO_OUT" | grep "used_memory_human:" | cut -d: -f2 | tr -d '\r')
                PEAK_MEM=$(echo "$INFO_OUT" | grep "used_memory_peak_human:" | cut -d: -f2 | tr -d '\r')
                CLIENTS=$(echo "$INFO_OUT" | grep "connected_clients:" | cut -d: -f2 | tr -d '\r')
                UPTIME_SEC=$(echo "$INFO_OUT" | grep "uptime_in_seconds:" | cut -d: -f2 | tr -d '\r')
                if [ -n "$UPTIME_SEC" ] && [ "$UPTIME_SEC" -gt 0 ] 2>/dev/null; then
                    HOURS=$(( UPTIME_SEC / 3600 ))
                    MINS=$(( (UPTIME_SEC % 3600) / 60 ))
                    UPTIME="${HOURS}h ${MINS}m"
                fi

                HITS=$(echo "$INFO_OUT" | grep "keyspace_hits:" | cut -d: -f2 | tr -d '\r')
                MISSES=$(echo "$INFO_OUT" | grep "keyspace_misses:" | cut -d: -f2 | tr -d '\r')
                HITS=${HITS:-0}
                MISSES=${MISSES:-0}
                TOTAL_REQ=$(( HITS + MISSES ))
                if [ "$TOTAL_REQ" -gt 0 ]; then
                    HIT_RATE=$(awk "BEGIN {printf \"%.1f%%\", ($HITS / $TOTAL_REQ) * 100}")
                else
                    HIT_RATE="100.0%"
                fi

                TOTAL_KEYS=$($CLI_BIN -s "$SOCK_FILE" dbsize 2>/dev/null | tr -d '\r' || echo 0)
            fi
        fi

        CONF_MEM="$MAX_MEM"
        if [ -f "$CONF_FILE" ]; then
            PARSED_MEM=$(grep -E "^maxmemory " "$CONF_FILE" | awk '{print $2}' | tr -d 'mbMB\r' || true)
            [ -n "$PARSED_MEM" ] && CONF_MEM="$PARSED_MEM"
        fi

        cat <<EOF
{
    "status": "ok",
    "engine": "$ENGINE",
    "installed": $INSTALLED,
    "running": $RUNNING,
    "pid": $PID,
    "user": "$TARGET_USER",
    "socket_path": "$SOCK_FILE",
    "memory_used": "${USED_MEM:-0 B}",
    "memory_peak": "${PEAK_MEM:-0 B}",
    "maxmemory_mb": ${CONF_MEM:-64},
    "total_keys": ${TOTAL_KEYS:-0},
    "hit_rate": "${HIT_RATE:-100%}",
    "clients": ${CLIENTS:-0},
    "uptime": "${UPTIME:-0s}"
}
EOF
        ;;

    start)
        if [ -z "$SERVER_BIN" ]; then
            echo '{"status":"error","message":"Redis or Valkey server is not installed on the system"}'
            exit 1
        fi

        if check_running; then
            echo '{"status":"ok","message":"Redis socket is already running","socket_path":"'$SOCK_FILE'"}'
            exit 0
        fi

        generate_conf

        if id "$TARGET_USER" &>/dev/null; then
            su -s /bin/bash "$TARGET_USER" -c "$SERVER_BIN $CONF_FILE" 2>/dev/null || $SERVER_BIN "$CONF_FILE"
        else
            $SERVER_BIN "$CONF_FILE"
        fi

        TRIES=0
        while [ ! -S "$SOCK_FILE" ] && [ $TRIES -lt 15 ]; do
            sleep 0.2
            TRIES=$((TRIES + 1))
        done

        if [ -S "$SOCK_FILE" ]; then
            chmod 777 "$SOCK_FILE" 2>/dev/null || true
            echo '{"status":"ok","message":"Started Redis user socket successfully","socket_path":"'$SOCK_FILE'"}'
        else
            echo '{"status":"error","message":"Failed to start Redis socket daemon"}'
            exit 1
        fi
        ;;

    stop)
        if [ -S "$SOCK_FILE" ] && [ -n "$CLI_BIN" ]; then
            $CLI_BIN -s "$SOCK_FILE" shutdown nosave 2>/dev/null || true
        fi
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE" 2>/dev/null || true)
            if [ -n "$PID" ]; then
                kill -9 "$PID" 2>/dev/null || true
            fi
            rm -f "$PID_FILE"
        fi
        rm -f "$SOCK_FILE"
        echo '{"status":"ok","message":"Stopped Redis user socket successfully"}'
        ;;

    restart)
        "$0" stop "$TARGET_USER" >/dev/null 2>&1 || true
        sleep 0.5
        "$0" start "$TARGET_USER" "$MAX_MEM"
        ;;

    flush)
        if [ ! -S "$SOCK_FILE" ]; then
            echo '{"status":"error","message":"Redis user socket is not running"}'
            exit 1
        fi
        $CLI_BIN -s "$SOCK_FILE" flushdb
        echo '{"status":"ok","message":"Flushed user Redis cache successfully"}'
        ;;

    set-memory)
        NEW_MEM="${3:-64}"
        if [ -f "$CONF_FILE" ]; then
            sed -i "s/^maxmemory .*/maxmemory ${NEW_MEM}mb/" "$CONF_FILE"
        fi
        if check_running && [ -n "$CLI_BIN" ]; then
            $CLI_BIN -s "$SOCK_FILE" config set maxmemory "${NEW_MEM}mb" 2>/dev/null || true
        fi
        echo '{"status":"ok","message":"Updated max memory to '${NEW_MEM}'MB successfully"}'
        ;;

    *)
        echo '{"status":"error","message":"Invalid action"}'
        exit 1
        ;;
esac
