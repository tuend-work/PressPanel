import os
import time
import json
import logging
import asyncio
import subprocess
from typing import Dict, Any, List, Optional, Set
from app.core.config import settings
from app.db.session import get_db_connection
from app.executor.gatekeeper import run_gatekeeper

logger = logging.getLogger("wpc.mysql_controller")

RESERVED_DB_USERS = {
    "root", "debian-sys-maint", "mysql.sys", "mysql.session",
    "mysql.infoschema", "mariadb.sys", "pma", "phpmyadmin"
}

# In-memory fast telemetry cache
_prev_mysql_stats: Dict[str, Dict[str, Any]] = {}
_prev_stats_ts: float = 0.0
_live_user_stats: Dict[str, Dict[str, Any]] = {}
_user_streaks: Dict[str, int] = {}
_user_shield_state: Dict[str, Dict[str, Any]] = {}
_sniped_queries_history: List[Dict[str, Any]] = []
_total_sniped_count: int = 0
_controller_initialized: bool = False

def get_mysql_root_password() -> Optional[str]:
    """Tìm mật khẩu root MariaDB/MySQL từ /root/.my.cnf hoặc /opt/wpc/conf/wpc.conf"""
    try:
        if os.path.exists("/root/.my.cnf"):
            with open("/root/.my.cnf", "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("password"):
                        return line.split("=", 1)[1].strip().strip('"\'')
    except Exception:
        pass
    try:
        if os.path.exists("/opt/wpc/conf/wpc.conf"):
            with open("/opt/wpc/conf/wpc.conf", "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("mysql_root_password"):
                        return line.split("=", 1)[1].strip().strip('"\'')
    except Exception:
        pass
    return None

def execute_mysql_direct(sql: str) -> tuple[bool, str]:
    """Thực thi SQL an toàn thông qua batch client của MariaDB/MySQL"""
    if os.name == "nt":
        return True, "Dev Mock Success"

    root_pass = get_mysql_root_password()
    cmd_candidates = [
        ["sudo", "mysql", "--batch"],
        ["mysql", "--batch"]
    ]
    if root_pass:
        cmd_candidates.append(["mysql", "-u", "root", f"-p{root_pass}", "--batch"])
        cmd_candidates.append(["sudo", "mysql", "-u", "root", f"-p{root_pass}", "--batch"])

    last_err = ""
    for cmd in cmd_candidates:
        try:
            proc = subprocess.run(
                cmd,
                input=sql,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10
            )
            if proc.returncode == 0:
                return True, proc.stdout
            last_err = proc.stderr.strip()
        except Exception as e:
            last_err = str(e)

    return False, last_err

def ensure_mysql_controller_settings() -> bool:
    """
    Kích hoạt cấu hình chuẩn của WPC MySQL Resource Controller:
    1. Ghi file /etc/mysql/mariadb.conf.d/99-wpc-mysql-controller.cnf
    2. Kích hoạt runtime: userstat=1, innodb_old_blocks_pct=37, innodb_old_blocks_time=1000
    """
    global _controller_initialized
    if os.name == "nt":
        _controller_initialized = True
        return True

    cnf_content = (
        "[mysqld]\n"
        "# Managed by WPC MySQL Resource Controller\n"
        "userstat = 1\n"
        "max_statement_time = 15\n"
        "innodb_old_blocks_pct = 37\n"
        "innodb_old_blocks_time = 1000\n"
        "tmp_table_size = 32M\n"
        "max_heap_table_size = 32M\n"
    )

    written = False
    for target in [
        "/etc/mysql/mariadb.conf.d/99-wpc-mysql-controller.cnf",
        "/etc/my.cnf.d/99-wpc-mysql-controller.cnf",
        "/etc/mysql/conf.d/99-wpc-mysql-controller.cnf"
    ]:
        pdir = os.path.dirname(target)
        if os.path.isdir(pdir):
            try:
                with open(target, "w", encoding="utf-8") as f:
                    f.write(cnf_content)
                os.chmod(target, 0o644)
                written = True
                break
            except Exception:
                pass

    sql = (
        "SET GLOBAL userstat = 1;\n"
        "SET GLOBAL innodb_old_blocks_pct = 37;\n"
        "SET GLOBAL innodb_old_blocks_time = 1000;\n"
    )
    ok, _ = execute_mysql_direct(sql)
    _controller_initialized = True
    return written or ok

def collect_raw_user_statistics() -> Dict[str, Dict[str, Any]]:
    """Đọc dữ liệu thống kê tích lũy per-user từ information_schema.USER_STATISTICS"""
    if os.name == "nt":
        return {}

    sql = (
        "SELECT USER, IFNULL(CONCURRENT_CONNECTIONS, 0), IFNULL(BUSY_TIME, 0), IFNULL(CPU_TIME, 0), "
        "IFNULL(ROWS_READ, 0), IFNULL(ROWS_SENT, 0), IFNULL(SELECT_COMMANDS, 0), IFNULL(UPDATE_COMMANDS, 0), "
        "IFNULL(OTHER_COMMANDS, 0), IFNULL(DENIED_CONNECTIONS, 0) "
        "FROM information_schema.USER_STATISTICS;\n"
    )
    ok, stdout = execute_mysql_direct(sql)
    if not ok or not stdout:
        return {}

    stats: Dict[str, Dict[str, Any]] = {}
    lines = stdout.strip().splitlines()
    for idx, line in enumerate(lines):
        parts = line.split("\t")
        if len(parts) < 10:
            continue
        if idx == 0 and parts[0].upper() == "USER":
            continue
        u_name = parts[0].strip()
        if not u_name or u_name.lower() in RESERVED_DB_USERS:
            continue
        try:
            stats[u_name] = {
                "concurrent_connections": int(float(parts[1] or 0)),
                "busy_time": float(parts[2] or 0.0),
                "cpu_time": float(parts[3] or 0.0),
                "rows_read": int(float(parts[4] or 0)),
                "rows_sent": int(float(parts[5] or 0)),
                "select_commands": int(float(parts[6] or 0)),
                "update_commands": int(float(parts[7] or 0)),
                "other_commands": int(float(parts[8] or 0)),
                "denied_connections": int(float(parts[9] or 0)),
            }
        except ValueError:
            continue
    return stats

def snipe_slow_queries(target_users: Optional[List[str] | Set[str]] = None, max_time_sec: int = 3) -> List[Dict[str, Any]]:
    """
    Active Slow-Query Sniper:
    Quét PROCESSLIST và lập tức KILL QUERY đối với các truy vấn SELECT/WITH chạy quá max_time_sec.
    """
    global _total_sniped_count, _sniped_queries_history
    if os.name == "nt":
        return []

    sec = max(1, min(60, int(max_time_sec or 3)))
    sql = (
        "SELECT ID, USER, IFNULL(DB, ''), IFNULL(TIME, 0), IFNULL(STATE, ''), "
        "LEFT(REPLACE(REPLACE(IFNULL(INFO, ''), '\n', ' '), '\t', ' '), 160) "
        f"FROM information_schema.PROCESSLIST WHERE COMMAND = 'Query' AND TIME >= {sec};\n"
    )
    ok, stdout = execute_mysql_direct(sql)
    if not ok or not stdout:
        return []

    safe_targets = {u.strip().lower() for u in target_users} if target_users else None

    killed: List[Dict[str, Any]] = []
    kill_stmts: List[str] = []

    for idx, line in enumerate(stdout.strip().splitlines()):
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        if idx == 0 and parts[0].upper() == "ID":
            continue
        qid_str, quser, qdb, qtime_str, qstate, qinfo = parts[:6]
        quser_clean = quser.strip()
        if quser_clean.lower() in RESERVED_DB_USERS:
            continue
        if safe_targets is not None and quser_clean.lower() not in safe_targets:
            continue
        if not qid_str.strip().isdigit():
            continue

        qinfo_clean = qinfo.strip()
        # Chỉ bắn tỉa các lệnh đọc dữ liệu nặng SELECT hoặc WITH, không ngắt transaction ghi dữ liệu dở dang
        if not qinfo_clean.upper().startswith(("SELECT", "WITH")):
            continue

        qid = int(qid_str.strip())
        kill_stmts.append(f"KILL QUERY {qid};")
        record = {
            "query_id": qid,
            "db_user": quser_clean,
            "db_name": qdb.strip(),
            "time_sec": int(float(qtime_str or 0)),
            "state": qstate.strip(),
            "query": qinfo_clean,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        killed.append(record)

    if kill_stmts:
        execute_mysql_direct("\n".join(kill_stmts) + "\n")
        _total_sniped_count += len(killed)
        _sniped_queries_history = (killed + _sniped_queries_history)[:50]
        logger.warning(f"[MySQL Resource Controller] Đã bắn tỉa (KILL) {len(killed)} câu query chậm nghẽn MariaDB!")

    return killed

def apply_db_user_limits(
    db_user: str,
    max_user_connections: int = 25,
    max_statement_time: int = 15,
    max_queries_per_hour: int = 0,
    max_updates_per_hour: int = 0,
    max_connections_per_hour: int = 0
) -> bool:
    """Áp dụng giới hạn tài nguyên cấp DB User qua lệnh ALTER USER chuẩn của MariaDB/MySQL"""
    if os.name == "nt":
        return True

    conns = max(2, min(500, int(max_user_connections or 25)))
    stmt_sec = max(1, min(300, int(max_statement_time or 15)))
    q_hour = max(0, int(max_queries_per_hour or 0))
    u_hour = max(0, int(max_updates_per_hour or 0))
    c_hour = max(0, int(max_connections_per_hour or 0))

    sql = (
        f"ALTER USER IF EXISTS '{db_user}'@'localhost' WITH "
        f"MAX_USER_CONNECTIONS {conns} "
        f"MAX_STATEMENT_TIME {stmt_sec} "
        f"MAX_QUERIES_PER_HOUR {q_hour} "
        f"MAX_UPDATES_PER_HOUR {u_hour} "
        f"MAX_CONNECTIONS_PER_HOUR {c_hour};\n"
        f"ALTER USER IF EXISTS '{db_user}'@'127.0.0.1' WITH "
        f"MAX_USER_CONNECTIONS {conns} "
        f"MAX_STATEMENT_TIME {stmt_sec} "
        f"MAX_QUERIES_PER_HOUR {q_hour} "
        f"MAX_UPDATES_PER_HOUR {u_hour} "
        f"MAX_CONNECTIONS_PER_HOUR {c_hour};\n"
        f"FLUSH PRIVILEGES;\n"
    )
    ok, _ = execute_mysql_direct(sql)
    return ok

def set_user_shield_mode(db_user: str, enabled: bool, reason: str = "") -> Dict[str, Any]:
    """
    Kích hoạt hoặc gỡ bỏ Anti-Scraping / Attack Shield cho Database User:
    Khi bật: Siết MAX_USER_CONNECTIONS=8, MAX_STATEMENT_TIME=3s và snipe ngay các query > 2s.
    Khi tắt: Khôi phục MAX_USER_CONNECTIONS=25, MAX_STATEMENT_TIME=15s.
    """
    global _user_shield_state, _user_streaks
    if enabled:
        apply_db_user_limits(db_user, max_user_connections=8, max_statement_time=3)
        snipe_slow_queries(target_users=[db_user], max_time_sec=2)
        state_obj = {
            "enabled": True,
            "reason": reason or "MANUAL: MySQL Resource Controller Anti-Scraping Shield kích hoạt",
            "activated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "activated_ts": time.time(),
        }
        _user_shield_state[db_user] = state_obj
        logger.warning(f"[MySQL Resource Controller] Kích hoạt Shield bảo vệ cho DB User {db_user}: {reason}")
        return state_obj
    else:
        apply_db_user_limits(db_user, max_user_connections=25, max_statement_time=15)
        _user_shield_state.pop(db_user, None)
        _user_streaks[db_user] = 0
        logger.info(f"[MySQL Resource Controller] Đã gỡ bỏ Shield, khôi phục giới hạn bình thường cho DB User {db_user}")
        return {"enabled": False, "reason": None, "activated_at": None}

def run_controller_cycle() -> None:
    """
    Một chu kỳ đo lường Telemetry và kiểm soát ngắt mạch (Circuit Breaker):
    1. Đo sai phân CPU, Rows/s, QPS per user
    2. Phát hiện hành vi cào dữ liệu hoặc quá tải MySQL
    3. Tự động bắn tỉa (Snipe) query chậm
    4. Tự động siết Shield bảo vệ khi bị cào liên tục
    5. Tự động gỡ Shield khi tài nguyên hạ về mức an toàn sau cooldown
    """
    global _prev_mysql_stats, _prev_stats_ts, _live_user_stats, _user_streaks

    if not _controller_initialized:
        ensure_mysql_controller_settings()

    now_mono = time.monotonic()
    curr_stats = collect_raw_user_statistics()
    dt = now_mono - _prev_stats_ts if _prev_stats_ts > 0 else 0.0

    prev_stats = dict(_prev_mysql_stats)
    _prev_mysql_stats = curr_stats
    _prev_stats_ts = now_mono

    if dt < 1.0 or not prev_stats:
        return

    computed_stats: Dict[str, Dict[str, Any]] = {}
    now_wall = time.time()

    for u_name, cur in curr_stats.items():
        prv = prev_stats.get(u_name)
        if not prv:
            continue

        d_cpu = max(0.0, float(cur.get("cpu_time", 0.0)) - float(prv.get("cpu_time", 0.0)))
        d_busy = max(0.0, float(cur.get("busy_time", 0.0)) - float(prv.get("busy_time", 0.0)))
        eff_cpu_sec = d_cpu if d_cpu > 0 else (d_busy * 0.6)

        d_rows = max(0, int(cur.get("rows_read", 0)) - int(prv.get("rows_read", 0)))
        d_q = max(
            0,
            (int(cur.get("select_commands", 0)) + int(cur.get("update_commands", 0)))
            - (int(prv.get("select_commands", 0)) + int(prv.get("update_commands", 0)))
        )

        mysql_cpu_pct = round((eff_cpu_sec / dt) * 100.0, 1)
        mysql_qps = round(d_q / dt, 1)
        mysql_rows_rate = int(round(d_rows / dt))
        conns = int(cur.get("concurrent_connections", 0))

        shield_info = _user_shield_state.get(u_name, {"enabled": False})

        computed_stats[u_name] = {
            "db_user": u_name,
            "mysql_cpu_pct": mysql_cpu_pct,
            "mysql_qps": mysql_qps,
            "mysql_rows_rate": mysql_rows_rate,
            "concurrent_connections": conns,
            "under_shield": shield_info.get("enabled", False),
            "shield_reason": shield_info.get("reason"),
            "shield_activated_at": shield_info.get("activated_at"),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        # Kiểm tra bất thường scraping / quá tải
        is_scraping = (
            mysql_cpu_pct >= 45.0
            or mysql_rows_rate >= 40000
            or mysql_qps >= 75.0
        )
        is_calm = (
            mysql_cpu_pct < 15.0
            and mysql_rows_rate < 10000
            and mysql_qps < 30.0
        )

        # Sniper phản ứng tức thì: Nếu MySQL CPU >= 25% hoặc phát hiện scraping, bắn tỉa ngay query > 3s
        if is_scraping or mysql_cpu_pct >= 25.0:
            try:
                snipe_slow_queries(target_users=[u_name], max_time_sec=3)
            except Exception:
                pass

        streak = _user_streaks.get(u_name, 0)
        if is_scraping:
            streak += 1
        elif is_calm:
            streak = 0
        _user_streaks[u_name] = streak

        # Tự động kích hoạt Circuit Breaker Shield khi cào liên tục >= 2 chu kỳ đo
        if streak >= 2 and not shield_info.get("enabled"):
            reason_str = (
                f"AUTO: MySQL Scraping anomaly ({mysql_rows_rate:,} rows/s, "
                f"{mysql_qps} QPS, {mysql_cpu_pct}% CPU) — MySQL Resource Controller engaged"
            )
            set_user_shield_mode(u_name, True, reason=reason_str)

        # Tự động gỡ Shield khi đã êm dịu (calm) và hết thời gian cooldown (ít nhất 300s = 5 phút)
        elif (
            shield_info.get("enabled")
            and str(shield_info.get("reason") or "").startswith("AUTO:")
            and is_calm
            and (now_wall - float(shield_info.get("activated_ts", 0))) >= 300
        ):
            set_user_shield_mode(u_name, False)

    _live_user_stats.update(computed_stats)

async def start_mysql_controller_worker():
    """
    Tiến trình bất đồng bộ nền của MySQL Resource Controller.
    Chạy định kỳ mỗi 10 giây để thu thập telemetry, bắn tỉa query chậm và kiểm soát mạch bảo vệ.
    """
    logger.info("[+] Khởi động MySQL Resource Controller Worker (10s cycle)...")
    await asyncio.sleep(6)  # Chờ database và API khởi động ổn định

    try:
        ensure_mysql_controller_settings()
    except Exception as e:
        logger.debug(f"Could not ensure controller settings on start: {e}")

    while True:
        try:
            # Chạy chu kỳ controller trong executor để không block asyncio event loop
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, run_controller_cycle)
        except Exception as e:
            logger.debug(f"MySQL Resource Controller cycle error: {e}")

        await asyncio.sleep(10)

def get_controller_status() -> Dict[str, Any]:
    """Trả về trạng thái tổng quan của MySQL Resource Controller"""
    active_shields = sum(1 for s in _user_shield_state.values() if s.get("enabled"))
    return {
        "status": "active" if _controller_initialized else "standby",
        "name": "MySQL Resource Controller",
        "initialized": _controller_initialized,
        "engine_settings": {
            "userstat": 1,
            "max_statement_time": 15,
            "innodb_old_blocks_pct": 37,
            "innodb_old_blocks_time": 1000,
            "buffer_pool_anti_pollution": True
        },
        "total_sniped_queries": _total_sniped_count,
        "active_shields_count": active_shields,
        "recent_sniped_queries": _sniped_queries_history[:10],
        "monitored_db_users_count": len(_live_user_stats)
    }

def get_all_user_stats() -> List[Dict[str, Any]]:
    """Trả về danh sách chỉ số thời gian thực của tất cả database user"""
    return list(_live_user_stats.values())
